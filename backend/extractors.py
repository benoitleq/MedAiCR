"""Extraction des identifiants depuis les champs etiquetes de chaque CR.

Pourquoi : dans ces CR structures, le nom du patient revient partout (en-tetes
repetes sur chaque page, pieds de page, parfois dans l'ordre inverse). Plutot
que d'esperer qu'une regex generique attrape toutes ces occurrences, on lit
d'abord la valeur dans son champ etiquete (ex. "Nom du patient : ..."), puis on
remplace cette valeur PARTOUT dans le document (voir anonymizer.py).

Chaque extracteur renvoie une liste de (categorie, valeur_litterale).
Les valeurs sont ensuite remplacees globalement (insensible a la casse).
"""
from __future__ import annotations

import re

# Un "mot de nom" : commence par une lettre, >= 2 caracteres (evite d'attraper
# une initiale isolee qui creerait trop de faux positifs a la substitution).
_NAME_TOKEN = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-]+")


def _name_tokens(full_name: str) -> list[str]:
    """Decoupe un nom complet en mots reutilisables pour la substitution."""
    return [t for t in _NAME_TOKEN.findall(full_name) if len(t) >= 2]


# --- Extraction generique pilotee par des libelles (types personnalises) ---
# Nom : suite de mots-lettres separes par un espace ou une virgule (gere le
# format "NOM, PRENOM" des ECG Schiller). On s'arrete avant un mot qui est
# lui-meme un libelle (mot suivi de ":" ou "-"), pour ne pas happer le champ
# voisin quand le PDF a colle les colonnes en espaces simples.
_NAME_VALUE = (
    r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]+"
    r"(?:[ ,]+(?![A-Za-zÀ-ÿ'’\-]+\s*[:\-])[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]+){0,3}"
)
_ID_VALUE = r"[A-Za-z0-9][A-Za-z0-9\-/]{2,}"
_DOB_VALUE = (
    r"\d{1,2}[\s/.\-]\w+[\s/.\-]\d{2,4}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
)


def _value_after_labels(text: str, labels: list[str], value_re: str) -> str | None:
    """Premiere valeur trouvee apres l'un des libelles donnes.

    - frontiere de mot avant le libelle : "N du patient" ne matche pas dans
      "identificatioN du patient" ;
    - separateur sans saut de ligne : la valeur reste sur la meme ligne que le
      libelle (evite de capturer la ligne suivante)."""
    for lab in labels:
        if not lab:
            continue
        pat = re.compile(
            r"(?<![A-Za-zÀ-ÿ0-9])" + re.escape(lab.strip())
            + r"[^\S\n]*[:\-]?[^\S\n]*(" + value_re + r")",
            re.IGNORECASE,
        )
        m = pat.search(text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def _value_before_anchors(text: str, anchors: list[str], value_re: str) -> str | None:
    """Valeur (nom) situee JUSTE AVANT l'un des ancrages (champ non etiquete).

    Ex. en-tete d'echo : "BARDON Date etude: ..." -> ancrage "Date etude"."""
    for a in anchors:
        if not a:
            continue
        pat = re.compile(r"(" + value_re + r")\s+" + re.escape(a.strip()), re.IGNORECASE)
        m = pat.search(text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def extract_custom(text: str, spec: dict) -> list[tuple[str, str]]:
    """Extracteur pilote par la config d'un type personnalise (appris via IA)."""
    found: list[tuple[str, str]] = []
    name = _value_after_labels(text, spec.get("name_labels", []), _NAME_VALUE)
    if not name:  # nom sans libelle : on le cherche juste avant un ancrage
        name = _value_before_anchors(text, spec.get("name_before", []), _NAME_VALUE)
    if name:
        for tok in _name_tokens(name):
            found.append(("Nom patient", tok))
    pid = _value_after_labels(text, spec.get("id_labels", []), _ID_VALUE)
    if pid:
        found.append(("N° patient", pid))
    dob = _value_after_labels(text, spec.get("dob_labels", []), _DOB_VALUE)
    if dob:
        found.append(("Date de naissance", dob))
    return found


def extract_ett(text: str) -> list[tuple[str, str]]:
    """Echographie cardiaque (Philips)."""
    found: list[tuple[str, str]] = []

    # Nom : ligne "<NOM> Date étude: ..." sous l'en-tete d'identification.
    m = re.search(r"\n\s*([A-ZÀ-Ý][A-ZÀ-Ý'\- ]+?)\s+Date\s+étude", text)
    if m:
        for tok in _name_tokens(m.group(1)):
            found.append(("Nom patient", tok))

    # N° du patient.
    m = re.search(r"N°\s*du\s*patient\s*:?\s*([0-9]{4,})", text, re.IGNORECASE)
    if m:
        found.append(("N° patient", m.group(1)))

    return found


def extract_pg(text: str) -> list[tuple[str, str]]:
    """Polygraphie ventilatoire (Nox T3)."""
    found: list[tuple[str, str]] = []

    # Nom : le nom suit le libelle "Nom" et precede le champ voisin (selon le
    # modele : "Age <date>" sur les Nox T3, ou "ID" sur d'autres exports). Le nom
    # peut etre en minuscules ("sylvie chauvet") ou en majuscules ("JEAN DUPONT").
    m = re.search(
        r"\bNom\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\- ]*?)\s+(?:ID|[ÂâAa]ge)\b",
        text,
    )
    if m:
        for tok in _name_tokens(m.group(1)):
            found.append(("Nom patient", tok))

    # Date de naissance : "Age <jj/mm/aaaa>" ou "Âge <jj/mm/aaaa>"
    m = re.search(r"[ÂâAa]ge\s+(\d{1,2}/\d{1,2}/\d{4})", text)
    if m:
        found.append(("Date de naissance", m.group(1)))

    return found


def extract_holter(text: str) -> list[tuple[str, str]]:
    """Holter ECG (Schiller MT-200, bilingue EN/FR)."""
    found: list[tuple[str, str]] = []
    names: set[str] = set()

    # Anglais : "Patient Name: franck berton ID: ..."
    m = re.search(r"Patient\s+Name\s*:\s*([A-Za-zÀ-ÿ'\- ]+?)\s*ID\s*:", text)
    if m:
        names.update(_name_tokens(m.group(1)))

    # Francais : "Nom du patient : berton franck  No. du patient :"  /  "Date de naiss."
    m = re.search(
        r"Nom\s+du\s+patient\s*:\s*([A-Za-zÀ-ÿ'\- ]+?)\s*(?:No\.|Date\s+de\s+naiss)",
        text,
    )
    if m:
        names.update(_name_tokens(m.group(1)))

    for tok in sorted(names):
        found.append(("Nom patient", tok))

    # Date de naissance, formats EN ("3 Mar 1972") et FR ("03/03/1972").
    m = re.search(r"DOB\s*:\s*(\d{1,2}\s+[A-Za-z]{3,}\.?\s+\d{4})", text)
    if m:
        found.append(("Date de naissance", m.group(1)))
    m = re.search(r"Date\s+de\s+naiss\.?\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if m:
        found.append(("Date de naissance", m.group(1)))

    return found


EXTRACTORS = {
    "echo_cardiaque": extract_ett,
    "polygraphie": extract_pg,
    "holter": extract_holter,
}


def detect_type(text: str) -> str | None:
    """Devine le type de CR a partir du contenu (pour cr_type='auto').

    Renvoie 'echo_cardiaque' | 'polygraphie' | 'holter' | None.
    """
    low = text.lower()
    if "polygraphie ventilatoire" in low or "index apn" in low or "nox t3" in low:
        return "polygraphie"
    if "holter" in low or "schiller" in low or "qrs count" in low or "nom du patient" in low:
        return "holter"
    if (
        "echo adulte" in low
        or "informations d'identification du patient" in low
        or ("date étude" in low or "date etude" in low)
    ):
        return "echo_cardiaque"
    # Types personnalises : reconnus si un de leurs libelles est present.
    import custom_types  # import tardif
    for tid, spec in custom_types.load().items():
        labs = spec.get("name_labels", []) + spec.get("id_labels", []) + spec.get("dob_labels", [])
        if any(lab and lab.lower() in low for lab in labs):
            return tid
    return None


def _dedupe(raw: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for cat, val in raw:
        key = val.lower()
        if key and key not in seen:
            seen.add(key)
            out.append((cat, val))
    return out


def extract_identifiers(text: str, cr_type: str) -> list[tuple[str, str]]:
    """Renvoie les identifiants litteraux a remplacer globalement.

    Types integres -> extracteurs codes ; sinon -> type personnalise (config).
    """
    fn = EXTRACTORS.get(cr_type)
    if fn:
        raw = fn(text)
    else:
        import custom_types  # import tardif pour eviter tout cycle
        spec = custom_types.load().get(cr_type)
        raw = extract_custom(text, spec) if spec else []
    return _dedupe(raw)


def extract_for_spec(text: str, spec: dict) -> list[tuple[str, str]]:
    """Comme extract_identifiers mais pour une spec non encore enregistree
    (utilise pendant l'apprentissage d'un type)."""
    return _dedupe(extract_custom(text, spec))
