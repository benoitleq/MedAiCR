"""Types de documents personnalises, appris via IA a partir d'un CR fictif.

Chaque type stocke les LIBELLES des champs identifiants (nom, n° patient, date
de naissance). L'extraction reelle reste 100% locale (voir extractors.py) :
ces libelles servent a localiser les valeurs, qui sont ensuite remplacees
partout par les regles generiques. Stocke dans custom_types.json (data_dir).

Format d'une entree :
  "custom_irm": {
    "label": "IRM cerebrale",
    "name_labels": ["Nom du patient", "Patient"],
    "id_labels": ["IPP", "No. patient"],
    "dob_labels": ["Date de naissance", "Ne(e) le"]
  }
"""
from __future__ import annotations

import json
import re
import unicodedata

from appconfig import CUSTOM_TYPES_FILE

# PDF FICTIF de reference stocke par type : permet de re-editer les zones sans
# re-uploader. Un fichier par type, a cote de custom_types.json.
SAMPLES_DIR = CUSTOM_TYPES_FILE.parent / "type_samples"


def sample_path(type_id: str):
    return SAMPLES_DIR / f"{type_id}.pdf"


def has_sample(type_id: str) -> bool:
    return sample_path(type_id).exists()


def save_sample(type_id: str, pdf_bytes: bytes) -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    sample_path(type_id).write_bytes(pdf_bytes)


def read_sample(type_id: str) -> bytes | None:
    p = sample_path(type_id)
    try:
        return p.read_bytes()
    except (FileNotFoundError, OSError):
        return None


def _delete_sample(type_id: str) -> None:
    try:
        sample_path(type_id).unlink()
    except (FileNotFoundError, OSError):
        pass


def load() -> dict:
    try:
        data = json.loads(CUSTOM_TYPES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    CUSTOM_TYPES_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def slugify(label: str) -> str:
    s = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return "custom_" + (s or "type")


def upsert(label: str, spec: dict, type_id: str | None = None) -> str:
    data = load()
    if not type_id:
        type_id = slugify(label)
        base, n = type_id, 2
        while type_id in data:  # unicite
            type_id = f"{base}_{n}"
            n += 1
    data[type_id] = {
        "label": label,
        "name_labels": list(spec.get("name_labels", [])),
        "name_before": list(spec.get("name_before", [])),
        "id_labels": list(spec.get("id_labels", [])),
        "dob_labels": list(spec.get("dob_labels", [])),
        # Zones d'anonymisation dessinees sur le PDF (selection visuelle).
        "zones": list(spec.get("zones", [])),
    }
    _save(data)
    return type_id


def delete(type_id: str) -> None:
    data = load()
    if data.pop(type_id, None) is not None:
        _save(data)
    _delete_sample(type_id)


def labels() -> dict:
    """type_id -> libelle lisible."""
    return {k: v.get("label", k) for k, v in load().items()}
