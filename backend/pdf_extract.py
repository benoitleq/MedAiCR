"""Extraction du texte d'un PDF, 100% en local (aucun envoi reseau).

On utilise pdfplumber qui lit la couche texte du PDF. Si le PDF est un scan
(image sans texte), l'extraction renverra peu ou pas de texte : on le signale
a l'appelant pour qu'il previenne l'utilisateur (OCR a ajouter plus tard).
"""
from __future__ import annotations

import io
import re
import unicodedata

import pdfplumber


def sanitize_text(text: str) -> str:
    """Nettoie le texte extrait des caracteres parasites d'encodage.

    Certains PDF (ex. ECG Schiller CS-104) inserent des octets de controle
    (NUL, ETX...) ou des glyphes de police en zone privee (Private Use Area)
    ENTRE les mots. Resultat : un libelle comme "N° patient" devient
    "N°\\x00patient", et les regexes pilotees par libelle ne matchent plus.
    On remplace tout caractere de controle / format / zone privee (categorie
    Unicode 'C*') par une espace, sauf le saut de ligne et la tabulation, puis on
    reduit les suites d'espaces (hors sauts de ligne) a une seule.
    """
    cleaned = []
    for ch in text:
        if ch in "\n\t":
            cleaned.append(ch)
        elif unicodedata.category(ch)[0] == "C":  # Cc, Cf, Cs, Co, Cn
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return re.sub(r"[^\S\n]+", " ", "".join(cleaned))


def extract_text(pdf_bytes: bytes) -> str:
    """Renvoie le texte concatene de toutes les pages du PDF."""
    pages_text: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            pages_text.append(txt)
    return sanitize_text("\n\n".join(pages_text)).strip()


def looks_like_scan(text: str) -> bool:
    """Heuristique : un PDF scanne renvoie un texte quasi vide."""
    return len(text.strip()) < 30
