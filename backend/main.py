"""Application FastAPI : interface web locale d'anonymisation de CR.

Tout le traitement (extraction PDF + anonymisation) se fait en local, dans ce
processus. Aucune donnee n'est envoyee vers un service externe.

Lancer :  uvicorn main:app  (depuis le dossier backend)
ou bien le script run.bat a la racine du projet.
"""
from __future__ import annotations

import base64
import datetime
import json
import re
import threading
from pathlib import Path

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import custom_types
import llm
import worklist as workflow  # nom de fichier "worklist" : evite le hook PyInstaller du paquet PyPI "workflow"
import zones
from anonymizer import (
    anonymize,
    anonymize_with_spec,
    identifier_spans_for_spec,
    summarize,
)
from appconfig import CONFIG_FILE, CUSTOM_TYPES_FILE, FRONTEND_DIR, ICON_FILE, LLM_FILE
from pdf_extract import extract_text, looks_like_scan
from pdf_redact import redact_pdf, redact_pdf_spec
from rules import DEFAULT_TYPE_COLOR, TYPE_LABELS, default_color_for

app = FastAPI(title="MedAiCR", version="1.2.0")


def all_type_labels() -> dict:
    """Types integres + types personnalises (appris via IA)."""
    return {**TYPE_LABELS, **custom_types.labels()}


def is_valid_type(cr_type: str, allow_auto: bool = False) -> bool:
    return cr_type in all_type_labels() or (allow_auto and cr_type == "auto")


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def resolved_type_colors() -> dict:
    """Couleur (lisere Workflow) pour CHAQUE type connu : valeur enregistree dans
    config.json, sinon defaut integre, sinon couleur de repli."""
    saved = _read_config().get("type_colors", {})
    if not isinstance(saved, dict):
        saved = {}
    out = {}
    for tid in all_type_labels():
        col = saved.get(tid)
        if not (col and _HEX_COLOR.match(str(col))):
            col = default_color_for(tid)
        out[tid] = col
    return out


@app.get("/")
def index() -> FileResponse:
    # no-store : le navigateur recharge toujours la derniere version de l'interface
    # (evite d'afficher une page en cache apres une mise a jour).
    return FileResponse(
        FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-store"}
    )


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(ICON_FILE)


# Sert les libs front locales (PDF.js) — 100% hors-ligne.
app.mount("/vendor", StaticFiles(directory=FRONTEND_DIR / "vendor"), name="vendor")


@app.get("/api/types")
def list_types() -> dict:
    """Types de CR disponibles pour le menu deroulant."""
    return {"types": [{"id": k, "label": v} for k, v in all_type_labels().items()]}


_AIDE = (
    "Surveillance de dossiers : depose un PDF dans 'directory', une version "
    "'ANOM_<nom>.pdf' est creee a cote. cr_type : echo_cardiaque, polygraphie, "
    "holter, ou 'auto'. Edite via l'onglet Configuration de l'interface web."
)


def _read_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"poll_interval_seconds": 10, "watch": []}
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"config.json illisible : {exc}") from exc


@app.get("/api/config")
def get_config() -> dict:
    """Configuration de surveillance + existence des repertoires."""
    cfg = _read_config()
    watch = []
    for w in cfg.get("watch", []):
        d = str(w.get("directory", ""))
        watch.append(
            {
                "directory": d,
                "cr_type": w.get("cr_type", "auto"),
                "enabled": bool(w.get("enabled", True)),
                "recursive": bool(w.get("recursive", True)),
                "exists": bool(d) and Path(d).is_dir(),
            }
        )
    return {
        "poll_interval_seconds": int(cfg.get("poll_interval_seconds", 10)),
        "watch": watch,
        "cr_types": [{"id": "auto", "label": "Détection automatique"}]
        + [{"id": k, "label": v} for k, v in all_type_labels().items()],
        "type_colors": resolved_type_colors(),
    }


@app.post("/api/config")
def save_config(payload: dict = Body(...)) -> dict:
    """Valide puis ecrit config.json. Cree les repertoires manquants."""
    try:
        interval = int(payload.get("poll_interval_seconds", 10))
    except (TypeError, ValueError):
        raise HTTPException(400, "poll_interval_seconds doit etre un entier.")
    interval = max(2, min(3600, interval))

    raw_watch = payload.get("watch", [])
    if not isinstance(raw_watch, list):
        raise HTTPException(400, "'watch' doit etre une liste.")

    clean_watch: list[dict] = []
    warnings: list[str] = []
    for i, w in enumerate(raw_watch, 1):
        directory = str(w.get("directory", "")).strip()
        cr_type = str(w.get("cr_type", "auto"))
        enabled = bool(w.get("enabled", True))
        recursive = bool(w.get("recursive", True))
        if not directory:
            continue  # ligne vide ignoree
        if not is_valid_type(cr_type, allow_auto=True):
            raise HTTPException(400, f"Type inconnu '{cr_type}' (ligne {i}).")
        # Cree le repertoire si absent (outil local mono-utilisateur).
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"Répertoire non créable : {directory} ({exc})")
        clean_watch.append(
            {"directory": directory, "cr_type": cr_type,
             "enabled": enabled, "recursive": recursive}
        )

    out = _read_config()  # on repart de l'existant pour PRESERVER type_colors
    out.update({"_aide": _AIDE, "poll_interval_seconds": interval, "watch": clean_watch})
    CONFIG_FILE.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "warnings": warnings, "saved": len(clean_watch)}


@app.post("/api/config/colors")
def save_type_colors(payload: dict = Body(...)) -> dict:
    """Enregistre la couleur (lisere Workflow) par type d'examen, sans toucher au
    reste de la config (dossiers surveilles, intervalle)."""
    colors = payload.get("type_colors")
    if not isinstance(colors, dict):
        raise HTTPException(400, "'type_colors' doit etre un objet {type: couleur}.")
    cfg = _read_config()
    saved = cfg.get("type_colors")
    if not isinstance(saved, dict):
        saved = {}
    valid = all_type_labels()
    for tid, col in colors.items():
        if tid in valid and _HEX_COLOR.match(str(col)):
            saved[tid] = str(col)
    cfg["type_colors"] = saved
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "type_colors": resolved_type_colors()}


@app.post("/api/pick-folder")
def pick_folder(payload: dict = Body(default={})) -> dict:
    """Ouvre une boite de dialogue NATIVE (cote serveur = poste local) pour choisir
    un dossier, et renvoie son chemin. L'app etant locale (127.0.0.1), la fenetre
    s'affiche sur l'ecran de l'utilisateur. Renvoie {"path": ""} si annule."""
    initial = str((payload or {}).get("initial", "")).strip()
    result: dict = {}

    def _ask() -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)  # devant le navigateur
            chosen = filedialog.askdirectory(
                title="Choisir le dossier a surveiller",
                initialdir=initial or None,
            )
            root.destroy()
            result["path"] = chosen or ""
        except Exception as exc:  # tkinter absent / pas d'affichage
            result["error"] = str(exc)

    # Tkinter n'est pas thread-safe : on l'isole dans un thread dedie, cree/utilise/
    # detruit au meme endroit, et on attend sa fin.
    t = threading.Thread(target=_ask)
    t.start()
    t.join()

    if "error" in result:
        raise HTTPException(
            500,
            "Selecteur de dossier indisponible sur ce poste — collez le chemin "
            f"manuellement. ({result['error']})",
        )
    path = result.get("path", "")
    if path:
        path = str(Path(path))  # normalise en separateurs Windows
    return {"path": path}


# --------------------------------------------------------------------------
# Sauvegarde / restauration de TOUTE la configuration en un seul fichier :
# dossiers surveilles (config.json), reglages IA + prompts systeme + cle API
# (llm.json), types d'examens personnalises (custom_types.json). Pratique apres
# une reinstallation (les donnees vivent dans %LOCALAPPDATA%\MedAiCR).
# --------------------------------------------------------------------------

_BACKUP_FILES = {
    "config": CONFIG_FILE,
    "llm": LLM_FILE,
    "custom_types": CUSTOM_TYPES_FILE,
}
_BACKUP_APP = "MedAiCR"


def _read_json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@app.get("/api/config/export")
def export_config() -> Response:
    """Renvoie un fichier JSON unique regroupant toute la configuration."""
    bundle = {
        "app": _BACKUP_APP,
        "format": 1,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        **{key: _read_json_file(path) for key, path in _BACKUP_FILES.items()},
    }
    # PDF fictifs de reference par type (pour re-editer les zones apres restauration)
    samples = {}
    for tid in custom_types.load():
        data = custom_types.read_sample(tid)
        if data:
            samples[tid] = base64.b64encode(data).decode("ascii")
    if samples:
        bundle["samples"] = samples
    data = json.dumps(bundle, ensure_ascii=False, indent=2)
    fname = "MedAiCR_config_" + datetime.datetime.now().strftime("%Y%m%d_%H%M") + ".json"
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/config/import")
def import_config(payload: dict = Body(...)) -> dict:
    """Restaure la configuration depuis un fichier de sauvegarde MedAiCR.

    Chaque section presente (dict) est reecrite ; les autres sont laissees telles
    quelles. Les fichiers sont relus a chaud (scanner, IA, types) -> pas besoin
    de redemarrer."""
    if not isinstance(payload, dict) or payload.get("app") != _BACKUP_APP:
        raise HTTPException(400, "Fichier de sauvegarde MedAiCR invalide.")
    restored: list[str] = []
    for key, path in _BACKUP_FILES.items():
        section = payload.get(key)
        if isinstance(section, dict):
            path.write_text(
                json.dumps(section, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            restored.append(key)
    # PDF fictifs de reference par type
    samples = payload.get("samples")
    if isinstance(samples, dict):
        n = 0
        for tid, b64 in samples.items():
            try:
                custom_types.save_sample(tid, base64.b64decode(b64))
                n += 1
            except Exception:  # noqa: BLE001
                pass
        if n:
            restored.append(f"samples({n})")
    if not restored:
        raise HTTPException(400, "Aucune section de configuration valide dans le fichier.")
    return {"ok": True, "restored": restored}


# --------------------------------------------------------------------------
# Generation de CR via LLM (DeepSeek). Le texte ANONYMISE est envoye a l'API.
# --------------------------------------------------------------------------

@app.get("/api/llm-config")
def get_llm_config() -> dict:
    cfg = llm.read_config()
    providers = {}
    for pid, meta in llm.PROVIDERS.items():
        p = cfg["providers"][pid]
        providers[pid] = {
            "label": meta["label"],
            "format": meta["format"],
            "models": meta["models"],
            "model": p["model"],
            "base_url": p["base_url"],
            "api_key_set": bool(p.get("api_key")),
        }
    return {
        "provider": cfg["provider"],
        "providers": providers,
        "system_prompts": cfg["system_prompts"],
        "reco_enabled": cfg["reco_enabled"],
        "types": [{"id": k, "label": v} for k, v in all_type_labels().items()],
    }


@app.post("/api/llm-config")
def save_llm_config(payload: dict = Body(...)) -> dict:
    cfg = llm.write_config(payload)
    return {"ok": True, "provider": cfg["provider"]}


@app.post("/api/llm-test")
def test_llm(payload: dict = Body(...)) -> dict:
    """Teste la connexion au fournisseur (cle/modele eventuellement non encore enregistres)."""
    try:
        result = llm.test_connection(
            payload.get("provider"), payload.get("api_key"), payload.get("model")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, **result}


# --------------------------------------------------------------------------
# Types personnalises appris via IA (a partir d'un CR FICTIF).
# --------------------------------------------------------------------------

@app.post("/api/learn-type")
async def learn_type(
    type_name: str = Form(...),
    provider: str | None = Form(None),
    model: str | None = Form(None),
    api_key: str | None = Form(None),
    file: UploadFile = ...,
) -> dict:
    """Analyse un CR fictif via IA -> propose des regles + apercu + verification."""
    if not (type_name or "").strip():
        raise HTTPException(400, "Donne un nom au type de document.")
    if file is None or not file.filename:
        raise HTTPException(400, "Fournis un PDF fictif.")

    pdf_bytes = await file.read()
    try:
        raw = extract_text(pdf_bytes)
    except Exception as exc:
        raise HTTPException(400, f"Lecture du PDF impossible : {exc}") from exc
    if looks_like_scan(raw):
        raise HTTPException(422, "Ce PDF semble etre un scan (pas de couche texte).")

    try:
        spec = llm.detect_fields(raw, provider, model, api_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    # La cle a fonctionne -> on la memorise pour ce fournisseur (saisie une fois).
    if api_key:
        pid = provider or llm.read_config()["provider"]
        llm.write_config({"providers": {pid: {"api_key": api_key}}})

    rules = {k: spec[k] for k in ("name_labels", "name_before", "id_labels", "dob_labels")}
    anon, masked = anonymize_with_spec(raw, rules)

    # Verification : les valeurs fictives detectees subsistent-elles ?
    low = anon.lower()
    leftovers = [
        v.strip() for v in spec.get("detected", {}).values()
        if v and v.strip() and v.strip().lower() in low
    ]

    # PDF avec les zones detectees SURLIGNEES EN ROUGE (apercu visuel).
    try:
        red_pdf, _ = redact_pdf_spec(pdf_bytes, rules, highlight=True)
        pdf_b64 = base64.b64encode(red_pdf).decode("ascii")
    except Exception:
        pdf_b64 = ""

    # Zones INITIALES (rectangles des champs detectes par l'IA) : pre-remplissent
    # le pinceau pour que l'utilisateur ajuste visuellement.
    try:
        init_zones = zones.zones_from_spans(
            pdf_bytes, identifier_spans_for_spec(raw, rules))
    except Exception:  # noqa: BLE001
        init_zones = []

    return {
        "label": type_name.strip(),
        "spec": rules,
        "detected": spec.get("detected", {}),
        "model": spec.get("model"),
        "preview": anon,
        "summary": summarize(masked),
        "leftovers": leftovers,
        "pdf_base64": pdf_b64,
        "zones": init_zones,
    }


@app.post("/api/preview-zones")
async def preview_zones(file: UploadFile = ..., spec: str = Form("{}")) -> dict:
    """Apercu LIVE pendant que l'utilisateur peint : applique libelles IA ET zones
    dessinees, renvoie le PDF surligne (rouge) + le recap des elements masques."""
    if file is None or not file.filename:
        raise HTTPException(400, "Aucun fichier PDF fourni.")
    pdf_bytes = await file.read()
    try:
        s = json.loads(spec) if spec else {}
    except json.JSONDecodeError:
        s = {}
    if not isinstance(s, dict):
        s = {}
    try:
        raw = extract_text(pdf_bytes)
    except Exception as exc:
        raise HTTPException(400, f"Lecture du PDF impossible : {exc}") from exc

    zvals = zones.extract_values(pdf_bytes, s.get("zones"))
    anon, masked = anonymize_with_spec(raw, s, extra_identifiers=zvals)
    try:
        red_pdf, _ = redact_pdf_spec(pdf_bytes, s, highlight=True)
        pdf_b64 = base64.b64encode(red_pdf).decode("ascii")
    except Exception:  # noqa: BLE001 — l'apercu PDF est optionnel
        pdf_b64 = ""
    return {"preview": anon, "summary": summarize(masked), "pdf_base64": pdf_b64}


@app.get("/api/custom-types")
def list_custom_types() -> dict:
    return {"types": [
        {"id": k, "has_sample": custom_types.has_sample(k), **v}
        for k, v in custom_types.load().items()
    ]}


@app.post("/api/custom-types")
async def save_custom_type(
    label: str = Form(...),
    spec: str = Form("{}"),
    type_id: str | None = Form(None),
    file: UploadFile | None = None,
) -> dict:
    """Cree/met a jour un type. Si un PDF (fictif) est fourni, il est STOCKE comme
    echantillon de reference -> re-edition des zones sans re-upload."""
    label = (label or "").strip()
    if not label:
        raise HTTPException(400, "Nom du type manquant.")
    try:
        s = json.loads(spec) if spec else {}
    except json.JSONDecodeError:
        s = {}
    tid = custom_types.upsert(label, s if isinstance(s, dict) else {}, type_id or None)
    if file is not None and file.filename:
        try:
            custom_types.save_sample(tid, await file.read())
        except Exception:  # noqa: BLE001 — l'echantillon est optionnel
            pass
    return {"ok": True, "type_id": tid, "has_sample": custom_types.has_sample(tid)}


@app.get("/api/custom-types/{type_id}/sample")
def get_custom_type_sample(type_id: str) -> Response:
    """Renvoie le PDF fictif stocke pour ce type (pour re-editer les zones)."""
    data = custom_types.read_sample(type_id)
    if data is None:
        raise HTTPException(404, "Aucun PDF echantillon pour ce type.")
    return Response(content=data, media_type="application/pdf")


@app.delete("/api/custom-types/{type_id}")
def delete_custom_type(type_id: str) -> dict:
    custom_types.delete(type_id)
    return {"ok": True}


@app.post("/api/generate")
def generate_endpoint(payload: dict = Body(...)) -> dict:
    """Genere un CR a partir d'un texte (deja anonymise) + le system prompt."""
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Aucun texte a traiter (anonymisez d'abord un CR).")
    try:
        report_md, reco_md = llm.generate_full(
            text, payload.get("cr_type"), payload.get("provider"),
            payload.get("model"), payload.get("observations"),
            with_reco=llm.reco_enabled(payload.get("cr_type")),
        )
    except ValueError as exc:  # config manquante
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:  # erreur reseau / API
        raise HTTPException(502, str(exc)) from exc
    # report = brut (apercu) ; report_md/reco_md = avec gras (**...**) pour la liseuse.
    return {"report": llm.to_plain(report_md), "report_md": report_md,
            "reco": llm.to_plain(reco_md), "reco_md": reco_md}


# --------------------------------------------------------------------------
# Workflow : liste de travail des examens (dossiers surveilles) -> interpretation.
# --------------------------------------------------------------------------

@app.get("/api/workflow/exams")
def workflow_exams() -> dict:
    """Examens des dossiers actives, du plus recent au plus ancien (cache instantane)."""
    return {"exams": workflow.list_exams(), "scanning": workflow.scanning()}


@app.get("/api/workflow/exam/{exam_id}")
def workflow_exam(exam_id: str) -> dict:
    """Detail d'un examen : texte anonymise + PDF source/anonymise."""
    try:
        return workflow.get_exam(exam_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/workflow/generate")
def workflow_generate(payload: dict = Body(...)) -> dict:
    """Genere le CR d'un examen (texte anonymise + interpretation du medecin)."""
    exam_id = (payload.get("id") or "").strip()
    if not exam_id:
        raise HTTPException(400, "Identifiant d'examen manquant.")
    try:
        return workflow.generate_cr(exam_id, payload.get("observations"))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:  # config IA manquante / examen non anonymisable
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:  # erreur reseau / API
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/anonymize")
async def anonymize_endpoint(
    cr_type: str = Form(...),
    file: UploadFile | None = None,
    text: str | None = Form(None),
) -> JSONResponse:
    """Anonymise un PDF uploade OU un texte colle.

    Renvoie le texte anonymise + le recap des elements masques.
    """
    if not is_valid_type(cr_type):
        raise HTTPException(400, f"Type de CR inconnu : {cr_type}")

    # 1. Recuperer le texte source (PDF prioritaire, sinon texte colle)
    extra: list = []
    if file is not None and file.filename:
        pdf_bytes = await file.read()
        try:
            raw = extract_text(pdf_bytes)
        except Exception as exc:  # PDF illisible / corrompu
            raise HTTPException(400, f"Lecture du PDF impossible : {exc}") from exc
        if looks_like_scan(raw):
            raise HTTPException(
                422,
                "Ce PDF semble etre un scan (pas de couche texte). "
                "OCR non disponible pour l'instant.",
            )
        extra = zones.values_for_type(pdf_bytes, cr_type)  # valeurs sous les zones
    elif text:
        raw = text
    else:
        raise HTTPException(400, "Fournir un fichier PDF ou un texte a anonymiser.")

    # 2. Anonymiser
    anonymized, masked = anonymize(raw, cr_type, extra_identifiers=extra)

    return JSONResponse(_payload(cr_type, anonymized, masked))


@app.post("/api/anonymize-pdf")
async def anonymize_pdf_endpoint(
    cr_type: str = Form(...),
    file: UploadFile = ...,
) -> JSONResponse:
    """Anonymise un PDF en CONSERVANT sa mise en page.

    Renvoie le PDF redige (base64) + le texte anonymise + le recap.
    """
    if not is_valid_type(cr_type):
        raise HTTPException(400, f"Type de CR inconnu : {cr_type}")
    if file is None or not file.filename:
        raise HTTPException(400, "Aucun fichier PDF fourni.")

    pdf_bytes = await file.read()
    try:
        raw = extract_text(pdf_bytes)
    except Exception as exc:
        raise HTTPException(400, f"Lecture du PDF impossible : {exc}") from exc
    if looks_like_scan(raw):
        raise HTTPException(
            422,
            "Ce PDF semble etre un scan (pas de couche texte). "
            "OCR non disponible pour l'instant.",
        )

    # Texte anonymise (pour l'apercu + recap) et PDF redige (pour le telechargement).
    extra = zones.values_for_type(pdf_bytes, cr_type)  # valeurs sous les zones
    anonymized, masked = anonymize(raw, cr_type, extra_identifiers=extra)
    try:
        pdf_out, _ = redact_pdf(pdf_bytes, cr_type)
    except Exception as exc:
        raise HTTPException(500, f"Redaction du PDF impossible : {exc}") from exc

    payload = _payload(cr_type, anonymized, masked)
    payload["pdf_base64"] = base64.b64encode(pdf_out).decode("ascii")
    payload["pdf_filename"] = _redacted_name(file.filename)
    return JSONResponse(payload)


def _payload(cr_type: str, anonymized: str, masked: list) -> dict:
    return {
        "type": cr_type,
        "type_label": all_type_labels().get(cr_type, cr_type),
        "anonymized_text": anonymized,
        "masked": [
            {"category": m.category, "original": m.original, "placeholder": m.placeholder}
            for m in masked
        ],
        "summary": summarize(masked),
    }


def _redacted_name(filename: str) -> str:
    stem = Path(filename).stem
    return f"{stem}_anonymise.pdf"
