"""Point d'entree unique de l'application empaquetee (.exe).

Demarre, dans un seul processus :
  - la surveillance des dossiers (thread de fond) ;
  - le serveur web local (FastAPI/uvicorn) ;
  - l'ouverture du navigateur sur l'interface.

Fermer la fenetre (la console) arrete tout.
"""
from __future__ import annotations

import sys
import threading
import time
import webbrowser
from pathlib import Path

# En developpement, expose backend/ ; en .exe les modules sont deja embarques.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn  # noqa: E402

import appconfig  # noqa: E402
import watcher  # noqa: E402
from main import app  # noqa: E402

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}/"


def _start_watcher() -> None:
    try:
        watcher.run_forever()
    except Exception as exc:  # ne doit jamais faire tomber l'app
        print(f"[surveillance] arretee : {exc}")


def _open_browser() -> None:
    time.sleep(2.5)  # laisse le serveur demarrer
    try:
        webbrowser.open(URL)
    except Exception:
        pass


def main() -> None:
    appconfig.ensure_default_config()
    threading.Thread(target=_start_watcher, daemon=True).start()
    threading.Thread(target=_open_browser, daemon=True).start()

    print("=" * 52)
    print("  MedAiCR - Anonymiseur de comptes rendus medicaux")
    print(f"  Interface : {URL}")
    print("  Surveillance des dossiers : active")
    print("  Fermez cette fenetre pour quitter l'application.")
    print("=" * 52)

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
