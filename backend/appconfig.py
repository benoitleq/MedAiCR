"""Chemins portables + configuration par defaut.

Permet a l'application de fonctionner aussi bien :
- en developpement (python), ou les ressources et la config sont a la racine du
  projet ;
- empaquetee en .exe (PyInstaller), ou les ressources sont extraites dans un
  dossier temporaire (sys._MEIPASS, lecture seule) et ou la config/les logs
  doivent etre ecrits dans un emplacement persistant et inscriptible.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "MedAiCR"


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """Dossier des ressources en LECTURE (frontend, icone)."""
    if _frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent  # racine du projet (dev)


def data_dir() -> Path:
    """Dossier INSCRIPTIBLE (config.json, watcher.log)."""
    if _frozen():
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    else:
        base = Path(__file__).resolve().parent.parent  # racine du projet (dev)
    base.mkdir(parents=True, exist_ok=True)
    return base


FRONTEND_DIR = resource_dir() / "frontend"
ICON_FILE = resource_dir() / "anonymiseur.ico"
CONFIG_FILE = data_dir() / "config.json"
LOG_FILE = data_dir() / "watcher.log"
STATE_FILE = data_dir() / "watcher_state.json"
LLM_FILE = data_dir() / "llm.json"  # cle API + system prompt + modele (local)
CUSTOM_TYPES_FILE = data_dir() / "custom_types.json"  # types appris via IA

# Dossiers surveilles par defaut : sous le "Documents" de l'utilisateur courant
# (fonctionne sur n'importe quel PC, quel que soit le nom d'utilisateur).
_DEFAULT_BASE = Path.home() / "Documents" / "CR_a_anonymiser"

_AIDE = (
    "Surveillance de dossiers : depose un PDF dans 'directory', une version "
    "'ANOM_<nom>.pdf' est creee a cote. cr_type : echo_cardiaque, polygraphie, "
    "holter, ou 'auto'. Edite via l'onglet Configuration de l'interface web."
)

DEFAULT_CONFIG = {
    "_aide": _AIDE,
    "poll_interval_seconds": 10,
    "watch": [
        {"directory": (_DEFAULT_BASE / "ETT").as_posix(),
         "cr_type": "echo_cardiaque", "enabled": True, "recursive": True},
        {"directory": (_DEFAULT_BASE / "Polygraphie").as_posix(),
         "cr_type": "polygraphie", "enabled": False, "recursive": True},
        {"directory": (_DEFAULT_BASE / "Holter").as_posix(),
         "cr_type": "holter", "enabled": False, "recursive": True},
    ],
}


def ensure_default_config() -> None:
    """Cree config.json + les dossiers par defaut au premier lancement."""
    if CONFIG_FILE.exists():
        return
    for w in DEFAULT_CONFIG["watch"]:
        try:
            Path(w["directory"]).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    CONFIG_FILE.write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
    )
