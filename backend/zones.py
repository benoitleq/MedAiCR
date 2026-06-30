"""Anonymisation par ZONES dessinees sur le PDF (selection visuelle au pinceau).

Une zone = un rectangle en coordonnees NORMALISEES (fractions 0..1 de la taille
de la page, origine en HAUT a GAUCHE, comme PDF.js et PyMuPDF) + un numero de
page + une categorie optionnelle. Approche HYBRIDE :
  - "intelligente" : on lit le texte present dans la zone -> il est renvoye comme
    valeur a masquer PARTOUT dans le document (en-tetes / pieds de page repetes) ;
  - "fixe" : la zone elle-meme est noircie sur le PDF (couvre aussi le cas d'une
    zone sans couche texte, ex. logo / tampon / scan).

Ce module ne fait que LIRE le PDF (positions). Le masquage global du texte
trouve passe ensuite par le pipeline habituel (anonymizer / pdf_redact).
"""
from __future__ import annotations

import fitz  # PyMuPDF


def normalize(zones) -> list[dict]:
    """Valide / nettoie une liste de zones venant de l'IHM ou d'un type stocke."""
    out: list[dict] = []
    if not isinstance(zones, list):
        return out
    for z in zones:
        if not isinstance(z, dict):
            continue
        try:
            page = int(z.get("page", 0))
            x = float(z.get("x", 0.0)); y = float(z.get("y", 0.0))
            w = float(z.get("w", 0.0)); h = float(z.get("h", 0.0))
        except (TypeError, ValueError):
            continue
        # On borne dans [0,1] et on ignore les rectangles vides/degeneres.
        x = min(max(x, 0.0), 1.0); y = min(max(y, 0.0), 1.0)
        w = min(max(w, 0.0), 1.0 - x); h = min(max(h, 0.0), 1.0 - y)
        if w <= 0.002 or h <= 0.002 or page < 0:
            continue
        out.append({
            "page": page, "x": x, "y": y, "w": w, "h": h,
            "cat": str(z.get("cat") or "Zone"),
        })
    return out


def zone_rect(page, z: dict) -> fitz.Rect:
    """Rectangle absolu d'une zone normalisee, dans le repere NON-TOURNE de la page.

    Les coordonnees normalisees sont exprimees dans le repere VISUEL (ce que voit
    l'utilisateur / PDF.js, page.rect). Or get_text / search_for / add_redact_annot
    travaillent dans le repere NON-TOURNE (mediabox). On applique donc la matrice
    de derotation. Page non tournee -> matrice identite (aucun effet)."""
    r = page.rect  # taille VISUELLE (rotation appliquee)
    vrect = fitz.Rect(
        r.x0 + z["x"] * r.width,
        r.y0 + z["y"] * r.height,
        r.x0 + (z["x"] + z["w"]) * r.width,
        r.y0 + (z["y"] + z["h"]) * r.height,
    )
    rect = vrect * page.derotation_matrix
    rect.normalize()
    return rect


def _words_in(page, rect: fitz.Rect) -> str:
    """Texte des mots dont le CENTRE tombe dans le rectangle (ordre de lecture)."""
    picked = []
    for x0, y0, x1, y1, word, *_rest in page.get_text("words"):
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
            picked.append(word)
    return " ".join(picked).strip()


def extract_values(pdf_bytes: bytes, zones) -> list[tuple[str, str]]:
    """Texte trouve dans chaque zone -> [(categorie, valeur)] a masquer globalement.

    Les zones sans texte exploitable (image/scan) ne renvoient rien ici : elles
    seront noircies positionnellement par la redaction (cf. pdf_redact)."""
    out: list[tuple[str, str]] = []
    zones = normalize(zones)
    if not zones:
        return out
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 — PDF illisible : pas de valeurs de zone
        return out
    try:
        for z in zones:
            if z["page"] >= doc.page_count:
                continue
            page = doc[z["page"]]
            val = _words_in(page, zone_rect(page, z))
            if val:
                out.append((z["cat"], val))
    finally:
        doc.close()
    return out


def values_for_type(pdf_bytes: bytes, cr_type: str) -> list[tuple[str, str]]:
    """Valeurs des zones d'un type ENREGISTRE (sinon liste vide)."""
    import custom_types  # import tardif (evite tout cycle)
    spec = custom_types.load().get(cr_type)
    return extract_values(pdf_bytes, spec.get("zones")) if spec else []


def zones_from_spans(pdf_bytes: bytes, spans, max_per: int = 4) -> list[dict]:
    """Localise des valeurs sur le PDF et renvoie des zones NORMALISEES.

    Sert a PRE-REMPLIR le pinceau a partir de la 1ere reconnaissance IA :
    'spans' = iterable de (categorie, valeur, ...). On cherche chaque valeur sur
    chaque page et on convertit les rectangles trouves en coordonnees 0..1."""
    out: list[dict] = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return out
    try:
        for pno, page in enumerate(doc):
            r = page.rect  # taille VISUELLE
            if not r.width or not r.height:
                continue
            for span in spans:
                cat = span[0] if len(span) > 0 else "Zone"
                value = span[1] if len(span) > 1 else ""
                if not value:
                    continue
                for rect in page.search_for(value)[:max_per]:
                    # search_for renvoie du NON-TOURNE -> repasser en VISUEL pour
                    # normaliser dans le repere ou l'utilisateur dessine.
                    vr = rect * page.rotation_matrix
                    vr.normalize()
                    out.append({
                        "page": pno,
                        "x": (vr.x0 - r.x0) / r.width,
                        "y": (vr.y0 - r.y0) / r.height,
                        "w": (vr.x1 - vr.x0) / r.width,
                        "h": (vr.y1 - vr.y0) / r.height,
                        "cat": cat,
                    })
    finally:
        doc.close()
    return normalize(out)
