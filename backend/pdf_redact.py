"""Redaction du PDF original en conservant la mise en page.

On localise chaque identifiant directement sur la page (PyMuPDF search_for),
puis on pose une annotation de redaction qui SUPPRIME le texte sous-jacent et
ecrit le jeton de remplacement ([NOM], [DATE]...) a la place. La structure du
document d'origine est ainsi preservee a l'identique.

Tout est local : aucune donnee ne sort du processus.
"""
from __future__ import annotations

import fitz  # PyMuPDF

from anonymizer import (
    Masked,
    identifier_spans,
    identifier_spans_for_spec,
    regex_spans,
)
from pdf_extract import extract_text, sanitize_text

# Champ anonymise : gris clair (rendu final).
GRAY = (0.93, 0.93, 0.93)
BLACK = (0.0, 0.0, 0.0)
# Surlignage rouge pour montrer ce qui a ete DETECTE (apercu de creation de type).
RED = (0.86, 0.15, 0.15)
WHITE = (1.0, 1.0, 1.0)


def _redact(doc, id_spans, fill, text_color) -> list[Masked]:
    masked: list[Masked] = []
    for page in doc:
        page_text = sanitize_text(page.get_text())
        spans = list(id_spans) + regex_spans(page_text)

        seen: set[str] = set()
        redacted_rects: list[fitz.Rect] = []
        for category, needle, token in spans:
            if not needle or needle in seen:
                continue
            seen.add(needle)
            for rect in page.search_for(needle):
                if any(r.contains(rect) for r in redacted_rects):
                    continue
                redacted_rects.append(rect)
                fontsize = max(6.0, min(11.0, rect.height * 0.8))
                page.add_redact_annot(
                    rect, text=token, fontname="helv", fontsize=fontsize,
                    text_color=text_color, fill=fill, cross_out=False,
                )
                masked.append(Masked(category, needle, token))
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        )
    return masked


def redact_pdf(pdf_bytes: bytes, cr_type: str) -> tuple[bytes, list[Masked]]:
    """Renvoie (pdf_anonymise, liste des elements rediges)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    # Extraction des VALEURS via pdfplumber : il conserve libelle et valeur sur
    # la meme ligne, la ou PyMuPDF linearise les pages tournees en separant les
    # colonnes (libelles puis valeurs), ce qui casse l'extraction par libelle.
    # Les valeurs trouvees sont ensuite localisees page par page (search_for).
    full_text = extract_text(pdf_bytes)
    masked = _redact(doc, identifier_spans(full_text, cr_type), GRAY, BLACK)
    out = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return out, masked


def redact_pdf_spec(pdf_bytes: bytes, spec: dict, highlight: bool = False
                    ) -> tuple[bytes, list[Masked]]:
    """Redaction a partir d'une spec non enregistree (apprentissage d'un type).

    highlight=True -> surligne les zones detectees en ROUGE (apercu visuel)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = extract_text(pdf_bytes)  # pdfplumber : cf. note dans redact_pdf
    id_spans = identifier_spans_for_spec(full_text, spec)
    fill, color = (RED, WHITE) if highlight else (GRAY, BLACK)
    masked = _redact(doc, id_spans, fill, color)
    out = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return out, masked
