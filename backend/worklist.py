"""Workflow : pilote la liste de travail des examens a interpreter.

Principe :
  - on scanne les dossiers ACTIVES en surveillance (config.json) ;
  - chaque PDF source (hors fichiers "ANOM_") devient un "examen" ;
  - le LISTING est rapide (parcours + metadonnees, AUCUNE lecture de PDF) ;
  - l'anonymisation (lecture + extraction + masquage) se fait EN TACHE DE FOND
    (thread worker, du plus recent au plus ancien) -> la coche "anonymise"
    apparait au fil de l'eau, sans jamais bloquer la liste ;
  - le medecin ouvre un examen, ajoute son interpretation, puis demande le CR.

Important : sur un partage reseau avec beaucoup de dossiers, anonymiser chaque
PDF a chaque scan bloquerait la requete. D'ou la separation listing / worker.

L'anonymisation reste locale. Seule la redaction du CR envoie le texte
ANONYMISE (+ le commentaire d'interpretation) au fournisseur d'IA choisi.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from anonymizer import anonymize, summarize
from appconfig import CONFIG_FILE
from extractors import detect_type
from pdf_extract import extract_text, looks_like_scan
from pdf_redact import redact_pdf
import custom_types
import llm
from rules import TYPE_LABELS

PREFIX = "ANOM_"
MAX_LIST = 400        # nb max d'examens renvoyes (les plus recents)

# Cache des examens : id -> enregistrement. Protege par _LOCK (worker + requetes).
_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()

# File d'attente d'anonymisation (du plus recent au plus ancien) + worker unique.
_QUEUE: list[str] = []          # ids a anonymiser
_QUEUED: set[str] = set()
_QLOCK = threading.Lock()
_WORKER_STARTED = False

# Scanner de fond : parcourt les dossiers (potentiellement lent sur reseau) HORS
# de la requete. L'endpoint renvoie alors l'instantane en cache, instantanement.
_ORDER: list[str] = []          # ids tries du plus recent au plus ancien
_ORDER_LOCK = threading.Lock()
_SCAN_DONE = False              # un premier scan a-t-il abouti ?
_SCANNER_STARTED = False


def _all_labels() -> dict:
    return {**TYPE_LABELS, **custom_types.labels()}


def _enabled_watch() -> list[dict]:
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [w for w in cfg.get("watch", []) if w.get("enabled", True)]


def _exam_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]


def _cr_path(source: Path) -> Path:
    return source.with_name(source.stem + "_CR.txt")


def _rel_folder(path: Path, root: str) -> str:
    try:
        rel = path.parent.relative_to(root)
        return str(rel) if str(rel) != "." else Path(root).name
    except ValueError:
        return path.parent.name


def _candidates(directory: Path, recursive: bool) -> list[Path]:
    out: list[Path] = []
    try:
        if recursive:
            for r, _d, files in os.walk(directory):
                for n in files:
                    if n.lower().endswith(".pdf") and not n.startswith(PREFIX):
                        out.append(Path(r) / n)
        else:
            for p in directory.iterdir():
                if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith(PREFIX):
                    out.append(p)
    except OSError:
        pass  # partage indisponible / droits : on ignore ce dossier
    return out


def _resolve_type(raw: str, cr_type: str) -> str | None:
    if cr_type == "auto":
        return detect_type(raw)
    return cr_type if cr_type in _all_labels() else None


def _new_record(path: Path, cr_type_cfg: str, root: str, st) -> dict:
    """Enregistrement LEGER (sans lecture du PDF). type_label connu si type explicite."""
    label = "" if cr_type_cfg == "auto" else _all_labels().get(cr_type_cfg, cr_type_cfg)
    return {
        "id": _exam_id(path), "path": str(path), "name": path.name,
        "folder": _rel_folder(path, root), "mtime": st.st_mtime, "size": st.st_size,
        "cr_type_cfg": cr_type_cfg, "cr_type": None, "type_label": label,
        "anonymized": False, "pending": True, "error": "",
        "anon_text": "", "summary": {}, "masked": [],
        "has_cr": _cr_path(path).exists(),
    }


def _anonymize_record(rec: dict) -> None:
    """Lecture + extraction + anonymisation (lourd). Met a jour le cache sous _LOCK."""
    path = Path(rec["path"])
    error = ""
    cr = anon = None
    masked: list = []
    try:
        raw = extract_text(path.read_bytes())
        if looks_like_scan(raw):
            error = "PDF scanné (pas de couche texte)"
        else:
            cr = _resolve_type(raw, rec["cr_type_cfg"])
            if cr is None:
                error = "Type indéterminé (détection auto)"
            else:
                anon, masked = anonymize(raw, cr)
    except Exception as exc:  # noqa: BLE001
        error = f"Lecture impossible ({exc})"

    with _LOCK:
        cur = _CACHE.get(rec["id"], rec)
        cur["pending"] = False
        if error:
            cur["error"] = error
            cur["anonymized"] = False
        else:
            cur["error"] = ""
            cur["cr_type"] = cr
            cur["type_label"] = _all_labels().get(cr, cr)
            cur["anon_text"] = anon
            cur["summary"] = summarize(masked)
            cur["masked"] = [(m.category, m.original, m.placeholder) for m in masked]
            cur["anonymized"] = True
        _CACHE[cur["id"]] = cur


def _enqueue(eid: str) -> None:
    with _QLOCK:
        if eid not in _QUEUED:
            _QUEUED.add(eid)
            _QUEUE.append(eid)
    _ensure_worker()


def _worker() -> None:
    while True:
        eid = None
        with _QLOCK:
            if _QUEUE:
                eid = _QUEUE.pop(0)
                _QUEUED.discard(eid)
        if eid is None:
            time.sleep(0.5)
            continue
        with _LOCK:
            rec = _CACHE.get(eid)
        if rec and rec.get("pending") and not rec.get("anonymized"):
            try:
                _anonymize_record(rec)
            except Exception:  # noqa: BLE001 — un echec ne doit pas tuer le worker
                with _LOCK:
                    if eid in _CACHE:
                        _CACHE[eid]["pending"] = False


def _ensure_worker() -> None:
    global _WORKER_STARTED
    if not _WORKER_STARTED:
        _WORKER_STARTED = True
        threading.Thread(target=_worker, daemon=True).start()


def _scan_once() -> None:
    """Parcourt les dossiers actives (potentiellement lent), met a jour le cache
    + l'ordre + la file d'anonymisation. Ne lit aucun PDF (metadonnees seules)."""
    global _SCAN_DONE
    pairs: list[tuple[float, str]] = []   # (mtime, id) pour le tri
    seen_ids: set[str] = set()
    for entry in _enabled_watch():
        directory = Path(str(entry.get("directory", "")))
        if not directory.is_dir():
            continue
        cr_type = entry.get("cr_type", "auto")
        recursive = bool(entry.get("recursive", True))
        root = str(directory)
        for pdf in _candidates(directory, recursive):
            eid = _exam_id(pdf)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            try:
                st = pdf.stat()
            except OSError:
                continue
            with _LOCK:
                rec = _CACHE.get(eid)
                if rec is None or rec["mtime"] != st.st_mtime or rec["size"] != st.st_size:
                    rec = _new_record(pdf, cr_type, root, st)
                    _CACHE[eid] = rec
                else:
                    rec["has_cr"] = _cr_path(pdf).exists()
            pairs.append((st.st_mtime, eid))

    pairs.sort(key=lambda x: x[0], reverse=True)
    order = [eid for _m, eid in pairs[:MAX_LIST]]
    with _ORDER_LOCK:
        _ORDER[:] = order
    _SCAN_DONE = True

    # Met en file (du plus recent au plus ancien) les examens pas encore anonymises.
    for eid in order:
        with _LOCK:
            rec = _CACHE.get(eid)
        if rec and rec["pending"] and not rec["anonymized"] and not rec["error"]:
            _enqueue(eid)


def _scan_interval() -> int:
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return max(4, int(cfg.get("poll_interval_seconds", 10)))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        return 10


def _scanner() -> None:
    while True:
        try:
            _scan_once()
        except Exception:  # noqa: BLE001 — un scan ne doit jamais tuer la boucle
            pass
        time.sleep(_scan_interval())


def _ensure_scanner() -> None:
    global _SCANNER_STARTED
    if not _SCANNER_STARTED:
        _SCANNER_STARTED = True
        threading.Thread(target=_scanner, daemon=True).start()


def scanning() -> bool:
    """True tant qu'aucun premier scan n'a abouti (affichage 'analyse en cours')."""
    return not _SCAN_DONE


def list_exams() -> list[dict]:
    """Renvoie INSTANTANEMENT le dernier instantane (le scan tourne en fond)."""
    _ensure_scanner()
    with _ORDER_LOCK:
        ids = list(_ORDER)
    out: list[dict] = []
    with _LOCK:
        for eid in ids:
            r = _CACHE.get(eid)
            if not r:
                continue
            out.append({
                "id": r["id"], "name": r["name"], "folder": r["folder"], "mtime": r["mtime"],
                "anonymized": r["anonymized"], "pending": r["pending"], "error": r["error"],
                "type_label": r["type_label"], "summary": r["summary"], "has_cr": r["has_cr"],
            })
    return out


def _find(eid: str) -> dict | None:
    with _LOCK:
        rec = _CACHE.get(eid)
    if rec and Path(rec["path"]).exists():
        return rec
    _scan_once()  # reconstruit le cache (id inconnu / serveur redemarre)
    with _LOCK:
        return _CACHE.get(eid)


def _ensure_anonymized(rec: dict) -> dict:
    """Anonymise tout de suite si le worker n'est pas encore passe."""
    if not rec.get("anonymized") and not rec.get("error"):
        _anonymize_record(rec)
        with _LOCK:
            rec = _CACHE.get(rec["id"], rec)
    return rec


def get_exam(eid: str) -> dict:
    """Detail complet d'un examen : texte anonymise + PDF source/anonymise (b64)."""
    rec = _find(eid)
    if rec is None:
        raise KeyError("Examen introuvable (a-t-il été déplacé ?).")
    rec = _ensure_anonymized(rec)
    path = Path(rec["path"])
    out = {
        "id": rec["id"], "name": rec["name"], "folder": rec["folder"],
        "type": rec["cr_type"], "type_label": rec["type_label"],
        "anonymized": rec["anonymized"], "error": rec["error"],
        "anonymized_text": rec["anon_text"], "summary": rec["summary"],
        "masked": [
            {"category": c, "original": o, "placeholder": p}
            for (c, o, p) in rec["masked"]
        ],
        "has_cr": _cr_path(path).exists(),
        "source_pdf_base64": "", "anon_pdf_base64": "", "cr_text": "",
    }
    if out["has_cr"]:
        try:
            out["cr_text"] = _cr_path(path).read_text(encoding="utf-8")
        except OSError:
            pass
    try:
        data = path.read_bytes()
        out["source_pdf_base64"] = base64.b64encode(data).decode("ascii")
        if rec["anonymized"] and rec["cr_type"]:
            anon_pdf, _ = redact_pdf(data, rec["cr_type"])
            out["anon_pdf_base64"] = base64.b64encode(anon_pdf).decode("ascii")
    except Exception:  # noqa: BLE001 — l'apercu PDF est optionnel
        pass
    return out


def generate_cr(eid: str, observations: str | None) -> dict:
    """Genere le CR (IA) a partir du texte anonymise + interpretation, et l'ecrit
    en .txt a cote du PDF source. Renvoie {report, cr_filename}."""
    rec = _find(eid)
    if rec is None:
        raise KeyError("Examen introuvable (a-t-il été déplacé ?).")
    rec = _ensure_anonymized(rec)
    if not rec["anonymized"]:
        raise ValueError(rec["error"] or "Examen non anonymisable.")

    report = llm.generate(rec["anon_text"], rec["cr_type"], observations=observations)

    cr_file = _cr_path(Path(rec["path"]))
    try:
        tmp = cr_file.with_suffix(".txt.tmp")
        tmp.write_text(report, encoding="utf-8")
        tmp.replace(cr_file)
        with _LOCK:
            if rec["id"] in _CACHE:
                _CACHE[rec["id"]]["has_cr"] = True
    except OSError:
        pass
    return {"report": report, "cr_filename": cr_file.name}


# Demarre le scan de fond des l'import (prechauffage) : la liste est prete avant
# meme que l'utilisateur n'ouvre l'onglet Workflow.
_ensure_scanner()
