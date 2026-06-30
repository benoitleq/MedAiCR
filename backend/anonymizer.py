"""Moteur d'anonymisation.

Pipeline :
  1) Extraction des identifiants depuis les champs etiquetes (extractors.py),
     puis remplacement LITTERAL et GLOBAL de chaque valeur dans tout le texte
     (gere les en-tetes/pieds de page repetes et les ordres de nom inverses).
  2) Application des regles regex generiques (dates, age, sexe, telephone,
     email, NIR, medecin/etablissement residuels).

Anonymisation PURE : remplacement par jetons fixes ([NOM], [DATE]...), aucune
table de correspondance n'est conservee. Le recap des elements masques sert
uniquement a la verification dans le navigateur (rien n'est stocke cote serveur).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from extractors import extract_for_spec, extract_identifiers
from rules import GENERIC_RULES, Rule

# Categorie -> jeton de remplacement pour les identifiants extraits.
_TOKEN = {
    "Nom patient": "[NOM]",
    "N° patient": "[ID]",
    "Date de naissance": "[DATE_NAISSANCE]",
}


def token_for(category: str) -> str:
    """Jeton de remplacement d'une categorie (defaut : [MASQUE])."""
    return _TOKEN.get(category, "[MASQUE]")


@dataclass
class Masked:
    category: str
    original: str
    placeholder: str


def anonymize(text: str, cr_type: str,
              extra_identifiers: list[tuple[str, str]] | None = None) -> tuple[str, list[Masked]]:
    """extra_identifiers : valeurs supplementaires a masquer globalement, p.ex.
    le texte lu sous les ZONES dessinees (extrait du PDF via zones.extract_values)."""
    ids = extract_identifiers(text, cr_type) + list(extra_identifiers or [])
    return _run(text, ids)


def anonymize_with_spec(text: str, spec: dict,
                        extra_identifiers: list[tuple[str, str]] | None = None) -> tuple[str, list[Masked]]:
    """Anonymise avec une spec de type non encore enregistree (apprentissage)."""
    ids = extract_for_spec(text, spec) + list(extra_identifiers or [])
    return _run(text, ids)


def _run(text: str, identifiers: list[tuple[str, str]]) -> tuple[str, list[Masked]]:
    masked: list[Masked] = []
    result = text

    # 1) Remplacement litteral global des identifiants extraits.
    for category, value in identifiers:
        token = _TOKEN.get(category, "[MASQUE]")
        # Frontiere de mot unicode pour ne pas couper un mot plus long.
        pattern = re.compile(
            rf"(?<![A-Za-zÀ-ÿ0-9]){re.escape(value)}(?![A-Za-zÀ-ÿ0-9])",
            re.IGNORECASE,
        )

        def _sub_literal(m, _cat=category, _tok=token):
            masked.append(Masked(_cat, m.group(0), _tok))
            return _tok

        result = pattern.sub(_sub_literal, result)

    # 2) Regles regex generiques.
    for rule in GENERIC_RULES:
        result = _apply_rule(result, rule, masked)

    return result, masked


def _apply_rule(text: str, rule: Rule, masked: list[Masked]) -> str:
    def _sub(match) -> str:
        if rule.label_val:
            label = match.group("label")
            val = match.group("val")
            masked.append(Masked(rule.name, val.strip(), rule.replacement))
            return f"{label}{rule.replacement}"
        masked.append(Masked(rule.name, match.group(0).strip(), rule.replacement))
        return rule.replacement

    return rule.compiled.sub(_sub, text)


def identifier_spans(text: str, cr_type: str) -> list[tuple[str, str, str]]:
    """Identifiants litteraux (nom, n° patient, date naissance) a rediger.

    Renvoie [(categorie, sous-chaine, jeton)]. Utilise pour la rredaction PDF :
    ces valeurs doivent etre cherchees/redigees sur TOUTES les pages.
    """
    out: list[tuple[str, str, str]] = []
    for category, value in extract_identifiers(text, cr_type):
        out.append((category, value, _TOKEN.get(category, "[MASQUE]")))
    return out


def identifier_spans_for_spec(text: str, spec: dict) -> list[tuple[str, str, str]]:
    """Comme identifier_spans mais pour une spec non enregistree (apprentissage)."""
    out: list[tuple[str, str, str]] = []
    for category, value in extract_for_spec(text, spec):
        out.append((category, value, _TOKEN.get(category, "[MASQUE]")))
    return out


def regex_spans(text: str) -> list[tuple[str, str, str]]:
    """Sous-chaines a rediger detectees par les regles regex generiques.

    Renvoie [(categorie, sous-chaine, jeton)]. On applique les regles en
    sequence sur une copie de travail (comme anonymize) pour eviter qu'une
    regle suivante re-detecte un fragment deja masque par une precedente.
    """
    out: list[tuple[str, str, str]] = []
    work = text
    for rule in GENERIC_RULES:
        def _sub(m, _r=rule):
            sub = m.group("val") if _r.label_val else m.group(0)
            sub = sub.strip()
            if sub:
                out.append((_r.name, sub, _r.replacement))
            return _r.replacement

        work = rule.compiled.sub(_sub, work)
    return out


def summarize(masked: list[Masked]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in masked:
        counts[m.category] = counts.get(m.category, 0) + 1
    return counts
