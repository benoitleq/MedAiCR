"""Regles d'anonymisation generiques, appliquees a TOUS les types de CR.

Elles completent l'approche par extraction (extractors.py) : les identifiants
nominatifs sont d'abord retires par remplacement litteral global, puis ces
regles attrapent le reste (dates, age, sexe, telephone, email, NIR, et les
champs etiquetes restants comme medecin / etablissement).

Choix valides avec l'utilisateur :
  - RETIRES : nom, n° patient, date de naissance, medecin, etablissement,
              AGE, SEXE, et TOUTES les dates calendaires + horodatages date+heure.
  - CONSERVES : mesures, conclusions, taille / poids / IMC / SC, et les heures
              "nues" (HH:MM / HH:MM:SS) car elles portent souvent des DUREES
              cliniques (duree d'enregistrement, duree d'episode) qu'il ne faut
              pas detruire. (Basculable si besoin.)

Ordre important : motifs specifiques (datetime, dates textuelles) avant motifs
generiques. Les regles sont appliquees dans l'ordre de la liste.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_I = re.IGNORECASE | re.MULTILINE

_MOIS_FR = (
    r"janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|"
    r"septembre|octobre|novembre|d[ée]cembre"
)
# Mois anglais : noms complets ET abreviations 3 lettres. PAS de "[a-z]*"
# apres : sinon "Mar" capturerait "markers". On borne par \b dans les regles.
_MONTH_EN = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)"
)

# Separateur de champ ":" qui NE traverse PAS les sauts de ligne (sinon un champ
# vide avalerait la ligne suivante).
_HSEP = r"[^\S\n]*[:\-][^\S\n]*"

# Valeur d'un champ etiquete : reste de la ligne, mais on s'arrete avant un
# eventuel autre libelle "Xxx :" (evite de capturer le champ voisin) et on exige
# au moins un caractere non-libelle (un champ vide ne capture rien).
_LABEL_AHEAD = r"(?![A-Za-zÀ-ÿ][\wÀ-ÿ'’ ]{0,40}[:\-])"
_VAL = (
    r"(?P<val>" + _LABEL_AHEAD + r"[^\s\n]"  # 1er car. : non-blanc, pas un libelle
    r"(?:" + _LABEL_AHEAD + r"[^\n])*)"        # suite : jusqu'au prochain libelle
)


@dataclass
class Rule:
    name: str
    pattern: str
    replacement: str
    flags: int = _I
    label_val: bool = False  # ne masquer que le groupe "val", garder "label"
    compiled: re.Pattern = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.compiled = re.compile(self.pattern, self.flags)


GENERIC_RULES: list[Rule] = [
    # --- Filet de securite : coordonnees ---
    Rule("Email", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "[EMAIL]"),
    Rule(
        "NIR (securite sociale)",
        r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b",
        "[NIR]",
    ),
    Rule("Telephone", r"\b0[1-9](?:[\s.\-]?\d{2}){4}\b", "[TELEPHONE]"),

    # --- Champs etiquetes residuels (valeur conservee = libelle) ---
    Rule(
        "Medecin",
        r"(?P<label>(?:m[ée]decin(?:\s+principal)?|r[ée]f[ée]r[ée]\s+par|"
        r"adress[ée]\s+par|effectu[ée]\s+par|referred\s+by)" + _HSEP + r")" + _VAL,
        "[MEDECIN]",
        label_val=True,
    ),
    Rule(
        "Etablissement",
        r"(?P<label>(?:etablissement|établissement|service|centre|h[ôo]pital|"
        r"clinique|r[ée]gion)" + _HSEP + r")" + _VAL,
        "[ETABLISSEMENT]",
        label_val=True,
    ),
    Rule(
        "Medecin (Dr/Pr)",
        r"\b(?:Dr|Docteur|Pr|Professeur)\.?\s+[A-ZÀ-Ý][\wÀ-ÿ'\-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'\-]+)?",
        "[MEDECIN]",
        flags=re.MULTILINE,
    ),

    # --- Dates : horodatages date+heure d'abord (on retire les deux) ---
    Rule(
        "Date+heure",
        r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\b",
        "[DATE]",
    ),
    # Dates numeriques : 16/06/2026, 03.03.1972, 27-01-26
    Rule("Date", r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b", "[DATE]"),
    # Dates textuelles anglaises avec annee : "3 Mar 1972", "20 Jun 2026"
    Rule("Date (EN)", rf"\b\d{{1,2}}\s+{_MONTH_EN}\.?\s+\d{{4}}\b", "[DATE]"),
    # Dates textuelles anglaises sans annee : "27 Jan", "26 Jan"
    Rule("Date (EN)", rf"\b\d{{1,2}}\s+{_MONTH_EN}\b", "[DATE]"),
    # Dates textuelles francaises : "12 mars 2024", "1er janvier 2023"
    Rule("Date (FR)", rf"\b\d{{1,2}}(?:er)?\s+(?:{_MOIS_FR})\s+\d{{4}}\b", "[DATE]"),
    # Heure AM/PM (prose anglaise) : "11:47 AM", "4:52 PM"
    Rule("Heure", r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", "[HEURE]"),

    # --- Age ---
    Rule("Age", r"\b\d{1,3}\s*ans\b", "[AGE]"),
    Rule("Age", r"\b\d{1,3}[\s\-]?year[\s\-]?old\b", "[AGE]"),

    # --- Sexe ---
    Rule(
        "Sexe",
        r"(?P<label>Sexe" + _HSEP + r")(?P<val>Homme|Femme|Masculin|F[ée]minin|M|F)\b",
        "[SEXE]",
        label_val=True,
    ),
    Rule("Sexe", r"\b(?:fe)?male\b", "[SEXE]"),
]


TYPE_LABELS: dict[str, str] = {
    "echo_cardiaque": "Echographie cardiaque",
    "polygraphie": "Polygraphie ventilatoire",
    "holter": "Holter ECG",
}

# Couleur du lisere (bord gauche) des cartes du Workflow, par type d'examen.
# Personnalisable dans l'onglet Configuration ; ces valeurs sont les defauts.
TYPE_COLORS: dict[str, str] = {
    "echo_cardiaque": "#3b82f6",  # bleu
    "polygraphie": "#22c55e",     # vert
    "holter": "#f59e0b",          # orange
}
# Palette pour les types PERSONNALISES sans couleur definie : chacun recoit une
# teinte stable derivee de son identifiant (distinctes des le depart).
TYPE_COLOR_PALETTE = [
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316",
    "#06b6d4", "#a3e635", "#eab308", "#ef4444",
]
DEFAULT_TYPE_COLOR = TYPE_COLOR_PALETTE[0]


def default_color_for(type_id: str) -> str:
    """Couleur par defaut d'un type : valeur integree si connue, sinon une teinte
    stable tiree de la palette d'apres l'identifiant (deterministe entre lancements)."""
    if type_id in TYPE_COLORS:
        return TYPE_COLORS[type_id]
    if not type_id:
        return DEFAULT_TYPE_COLOR
    h = int(hashlib.sha1(type_id.encode("utf-8")).hexdigest(), 16)
    return TYPE_COLOR_PALETTE[h % len(TYPE_COLOR_PALETTE)]
