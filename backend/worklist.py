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
import zones
from rules import TYPE_COLORS, TYPE_LABELS, default_color_for

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

# Ligne de base = "anteriorite" : ids des PDF deja presents A L'OUVERTURE du
# logiciel. Le Workflow ne prend ensuite en compte QUE les examens APPARUS apres ;
# l'anteriorite (souvent des milliers de fichiers sur un partage reseau) n'est
# jamais re-anonymisee. Comparaison par chemin (pas par mtime) -> robuste au
# decalage d'horloge poste/serveur.
#
# Figee PAR DOSSIER, et seulement quand le dossier est REELLEMENT lisible :
#   - partage reseau coupe au demarrage -> sa baseline n'est pas figee, on
#     retente ; a son retour on n'anonymise pas tout le stock ;
#   - dossier ajoute en cours de session -> son stock existant devient son
#     anteriorite (pas traite comme "nouveau").
_BASELINE: set[str] = set()          # eids de l'anteriorite (cumul tous dossiers)
_BASELINED_DIRS: set[str] = set()    # dossiers dont l'anteriorite est deja figee

# Optimisation du scan : reparcourir un dossier coute cher sur un partage reseau
# (des milliers de sous-dossiers). Or un nouvel examen = nouvelle entree dans le
# dossier surveille -> sa mtime racine change. On memorise donc, par dossier, la
# mtime de la racine + les examens (pairs) trouves au dernier parcours ; tant que
# la mtime racine ne bouge pas, on REUTILISE ce resultat sans reparcourir.
# Un parcours complet de securite est force periodiquement (deletions, fichiers
# deposes en profondeur dans un sous-dossier deja existant...).
_DIR_STATE: dict[str, dict] = {}     # root -> {"mtime": float, "pairs": [(mtime, eid)]}
_SCAN_COUNT = 0
_FORCE_FULL_EVERY = 40               # ~ toutes les ~2 min a 3s/scan : re-walk complet
_SCAN_IDLE_SECONDS = 3               # cadence : le scan "a vide" est quasi instantane


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


_SLOW_WALK_SECONDS = 1.0   # au-dela, un dossier est juge "lent" (reseau) -> optimise


def _resolve_type(raw: str, cr_type: str) -> str | None:
    if cr_type == "auto":
        return detect_type(raw)
    return cr_type if cr_type in _all_labels() else None


def _arrival_time(st) -> float:
    """Heure d'ARRIVEE de l'examen dans le dossier surveille = date de CREATION
    locale du fichier. On n'utilise PAS st_mtime : certains appareils (echographe
    Philips...) ecrivent le PDF avec l'horloge de la machine source, souvent
    decalee, et cette date de modification est conservee lors de la copie sur le
    partage -> elle fausserait l'ordre chronologique. La date de creation reflete
    le moment ou le fichier est apparu sur le poste/serveur (= ce que montre
    l'Explorateur pour le dossier d'examen)."""
    bt = getattr(st, "st_birthtime", None)  # Python 3.12+ sur Windows : vraie date de creation
    if bt:
        return bt
    return st.st_ctime  # Windows : date de creation ; repli si st_birthtime absent


def _new_record(path: Path, cr_type_cfg: str, root: str, st) -> dict:
    """Enregistrement LEGER (sans lecture du PDF). type_label connu si type explicite."""
    label = "" if cr_type_cfg == "auto" else _all_labels().get(cr_type_cfg, cr_type_cfg)
    return {
        "id": _exam_id(path), "path": str(path), "name": path.name,
        "folder": _rel_folder(path, root), "mtime": _arrival_time(st), "size": st.st_size,
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
        data = path.read_bytes()
        raw = extract_text(data)
        if looks_like_scan(raw):
            error = "PDF scanné (pas de couche texte)"
        else:
            cr = _resolve_type(raw, rec["cr_type_cfg"])
            if cr is None:
                error = "Type indéterminé (détection auto)"
            else:
                # Valeurs lues sous les zones dessinees du type (masquage global).
                extra = zones.values_for_type(data, cr)
                anon, masked = anonymize(raw, cr, extra_identifiers=extra)
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
    + l'ordre + la file d'anonymisation. Ne lit aucun PDF (metadonnees seules).

    A la 1ere lecture REUSSIE d'un dossier (= ouverture du logiciel, ou ajout du
    dossier), on fige son anteriorite et on ne liste rien de lui. Ensuite, seuls
    ses fichiers APPARUS depuis (absents de la baseline) entrent dans le Workflow.

    Cout maitrise : un dossier dont la mtime racine n'a pas bouge n'est PAS
    reparcouru (on reutilise le resultat precedent). Parcours complet force tous
    les _FORCE_FULL_EVERY scans (filet de securite)."""
    global _SCAN_DONE, _SCAN_COUNT
    _SCAN_COUNT += 1
    force_full = (_SCAN_COUNT % _FORCE_FULL_EVERY == 0)
    pairs: list[tuple[float, str]] = []   # (mtime, id) pour le tri
    seen_ids: set[str] = set()
    for entry in _enabled_watch():
        directory = Path(str(entry.get("directory", "")))
        if not directory.is_dir():
            continue  # injoignable : baseline NON figee, on retentera au prochain scan
        root = str(directory)
        try:
            root_mtime = directory.stat().st_mtime  # os.stat FRAIS = fiable (racine)
        except OSError:
            continue
        dir_first = root not in _BASELINED_DIRS  # 1ere lecture reussie de ce dossier
        state = _DIR_STATE.get(root)

        # Reutilisation SEULEMENT pour les dossiers LENTS (reseau), quand la mtime
        # racine n'a pas bouge : les nouveaux examens y arrivent comme de nouveaux
        # sous-dossiers -> la mtime racine change bien. Les dossiers RAPIDES (locaux)
        # sont TOUJOURS reparcourus : le walk y est quasi instantane, donc detection
        # fiable meme d'un fichier depose dans un sous-dossier existant (la mtime
        # d'un sous-dossier n'est PAS fiable via scandir sous Windows -> cache).
        if state is not None and not dir_first and not force_full \
                and state.get("slow") and state["mtime"] == root_mtime:
            for m, eid in state["pairs"]:
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    pairs.append((m, eid))
            continue

        cr_type = entry.get("cr_type", "auto")
        recursive = bool(entry.get("recursive", True))
        dir_pairs: list[tuple[float, str]] = []
        _t0 = time.time()
        for pdf in _candidates(directory, recursive):
            eid = _exam_id(pdf)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            if dir_first:
                _BASELINE.add(eid)   # present a l'ouverture -> anteriorite, jamais traite
                continue
            if eid in _BASELINE:
                continue
            try:
                st = pdf.stat()
            except OSError:
                continue
            arrival = _arrival_time(st)
            with _LOCK:
                rec = _CACHE.get(eid)
                if rec is None or rec["mtime"] != arrival or rec["size"] != st.st_size:
                    rec = _new_record(pdf, cr_type, root, st)
                    _CACHE[eid] = rec
                else:
                    rec["has_cr"] = _cr_path(pdf).exists()
            dir_pairs.append((arrival, eid))
        if dir_first:
            _BASELINED_DIRS.add(root)
        _DIR_STATE[root] = {"mtime": root_mtime, "pairs": dir_pairs,
                            "slow": (time.time() - _t0) > _SLOW_WALK_SECONDS}
        pairs.extend(dir_pairs)

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
    # Cadence courte : un scan sans changement ne reparcourt plus les dossiers
    # (reutilisation par mtime racine), il est donc quasi instantane. Les nouveaux
    # examens apparaissent ainsi en quelques secondes.
    return _SCAN_IDLE_SECONDS


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


def _type_colors() -> dict:
    """Couleurs (lisere) par type : valeurs de config.json + defauts integres."""
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("type_colors")
    except (FileNotFoundError, json.JSONDecodeError):
        saved = None
    colors = dict(TYPE_COLORS)
    if isinstance(saved, dict):
        colors.update({k: str(v) for k, v in saved.items()})
    return colors


def list_exams() -> list[dict]:
    """Renvoie INSTANTANEMENT le dernier instantane (le scan tourne en fond)."""
    _ensure_scanner()
    colors = _type_colors()
    with _ORDER_LOCK:
        ids = list(_ORDER)
    out: list[dict] = []
    with _LOCK:
        for eid in ids:
            r = _CACHE.get(eid)
            if not r:
                continue
            # Type effectif pour la couleur : resolu si dispo, sinon configure.
            tid = r["cr_type"] or (r["cr_type_cfg"] if r["cr_type_cfg"] != "auto" else None)
            color = colors.get(tid) or (default_color_for(tid) if tid else "#64748b")
            out.append({
                "id": r["id"], "name": r["name"], "folder": r["folder"], "mtime": r["mtime"],
                "anonymized": r["anonymized"], "pending": r["pending"], "error": r["error"],
                "type_label": r["type_label"], "summary": r["summary"], "has_cr": r["has_cr"],
                "type_color": color,
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

    report_md, reco_md = llm.generate_full(
        rec["anon_text"], rec["cr_type"], observations=observations,
        with_reco=llm.reco_enabled(rec["cr_type"]))
    report = llm.to_plain(report_md)  # .txt et apercu : sans gras markdown

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
    # report_md conserve le gras (**...**) -> sert au courrier Word cote frontend.
    return {"report": report, "report_md": report_md,
            "reco": llm.to_plain(reco_md), "reco_md": reco_md,
            "cr_filename": cr_file.name}


# Demarre le scan de fond des l'import (prechauffage) : la liste est prete avant
# meme que l'utilisateur n'ouvre l'onglet Workflow.
_ensure_scanner()
