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
    token_for,
)
from pdf_extract import extract_text, sanitize_text
import zones as zonelib

# Champ anonymise : gris clair (rendu final).
GRAY = (0.93, 0.93, 0.93)
BLACK = (0.0, 0.0, 0.0)
# Surlignage rouge pour montrer ce qui a ete DETECTE (apercu de creation de type).
RED = (0.86, 0.15, 0.15)
WHITE = (1.0, 1.0, 1.0)


def _scrub_metadata(doc) -> None:
    """Efface TOUTES les metadonnees pouvant contenir des donnees identifiantes.

    Defense en profondeur : certains appareils / imprimantes PDF inscrivent le nom
    ou l'identifiant patient hors du texte visible. On retire :
      - les champs standard /Info (titre, auteur, sujet, mots-cles, createur,
        producteur, dates) ;
      - le bloc XMP ;
      - les signets / plan (peuvent porter un nom) ;
      - les pieces jointes embarquees ;
      - les valeurs des champs de formulaire.
    Chaque etape est isolee : un echec ne bloque pas la redaction."""
    try:
        doc.set_metadata({})           # champs standard /Info
    except Exception:  # noqa: BLE001
        pass
    try:
        doc.del_xml_metadata()         # flux XMP
    except Exception:  # noqa: BLE001
        pass
    try:
        doc.set_toc([])                # signets / plan
    except Exception:  # noqa: BLE001
        pass
    try:
        for name in list(doc.embfile_names()):   # pieces jointes embarquees
            doc.embfile_del(name)
    except Exception:  # noqa: BLE001
        pass
    try:
        for page in doc:               # champs de formulaire : on supprime le widget
            w = page.first_widget
            while w:
                w = page.delete_widget(w)  # renvoie le widget suivant (ou None)
    except Exception:  # noqa: BLE001
        pass


def _redact(doc, id_spans, fill, text_color, zones=None) -> list[Masked]:
    masked: list[Masked] = []
    zones = zonelib.normalize(zones)
    for pno, page in enumerate(doc):
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

        # Zones dessinees sur CETTE page : noircissage positionnel (couvre aussi
        # ce que la recherche de texte n'attrape pas). Le texte sous la zone est
        # supprime (non extractible) et la zone peinte de la couleur de remplissage.
        for z in zones:
            if z["page"] != pno:
                continue
            rect = zonelib.zone_rect(page, z)
            token = token_for(z["cat"])
            fontsize = max(6.0, min(11.0, rect.height * 0.6))
            page.add_redact_annot(
                rect, text=token, fontname="helv", fontsize=fontsize,
                text_color=text_color, fill=fill, cross_out=False,
            )
            masked.append(Masked(z["cat"], "(zone)", token))

        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        )
    return masked


def _zone_spans(pdf_bytes: bytes, zones) -> list[tuple[str, str, str]]:
    """Valeurs lues sous les zones -> spans (cat, valeur, jeton) a masquer PARTOUT."""
    return [(cat, val, token_for(cat))
            for cat, val in zonelib.extract_values(pdf_bytes, zones)]


def redact_pdf(pdf_bytes: bytes, cr_type: str) -> tuple[bytes, list[Masked]]:
    """Renvoie (pdf_anonymise, liste des elements rediges)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    # Extraction des VALEURS via pdfplumber : il conserve libelle et valeur sur
    # la meme ligne, la ou PyMuPDF linearise les pages tournees en separant les
    # colonnes (libelles puis valeurs), ce qui casse l'extraction par libelle.
    # Les valeurs trouvees sont ensuite localisees page par page (search_for).
    full_text = extract_text(pdf_bytes)
    import custom_types  # import tardif (evite tout cycle a l'import)
    zones = custom_types.load().get(cr_type, {}).get("zones", [])
    id_spans = identifier_spans(full_text, cr_type) + _zone_spans(pdf_bytes, zones)
    masked = _redact(doc, id_spans, GRAY, BLACK, zones=zones)
    _scrub_metadata(doc)
    out = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return out, masked


def redact_pdf_spec(pdf_bytes: bytes, spec: dict, highlight: bool = False
                    ) -> tuple[bytes, list[Masked]]:
    """Redaction a partir d'une spec non enregistree (apprentissage d'un type).

    highlight=True -> surligne les zones detectees en ROUGE (apercu visuel)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = extract_text(pdf_bytes)  # pdfplumber : cf. note dans redact_pdf
    zones = spec.get("zones", [])
    id_spans = identifier_spans_for_spec(full_text, spec) + _zone_spans(pdf_bytes, zones)
    fill, color = (RED, WHITE) if highlight else (GRAY, BLACK)
    masked = _redact(doc, id_spans, fill, color, zones=zones)
    _scrub_metadata(doc)
    out = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return out, masked
