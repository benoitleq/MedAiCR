"""Application FastAPI : interface web locale d'anonymisation de CR.

Tout le traitement (extraction PDF + anonymisation) se fait en local, dans ce
processus. Aucune donnee n'est envoyee vers un service externe.

Lancer :  uvicorn main:app  (depuis le dossier backend)
ou bien le script run.bat a la racine du projet.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from fastapi import Body, FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import custom_types
import llm
import worklist as workflow  # nom de fichier "worklist" : evite le hook PyInstaller du paquet PyPI "workflow"
from anonymizer import anonymize, anonymize_with_spec, summarize
from appconfig import CONFIG_FILE, FRONTEND_DIR, ICON_FILE
from pdf_extract import extract_text, looks_like_scan
from pdf_redact import redact_pdf, redact_pdf_spec
from rules import TYPE_LABELS

app = FastAPI(title="Anonymiseur CR", version="0.1.0")


def all_type_labels() -> dict:
    """Types integres + types personnalises (appris via IA)."""
    return {**TYPE_LABELS, **custom_types.labels()}


def is_valid_type(cr_type: str, allow_auto: bool = False) -> bool:
    return cr_type in all_type_labels() or (allow_auto and cr_type == "auto")


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

    out = {"_aide": _AIDE, "poll_interval_seconds": interval, "watch": clean_watch}
    CONFIG_FILE.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "warnings": warnings, "saved": len(clean_watch)}


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

    return {
        "label": type_name.strip(),
        "spec": rules,
        "detected": spec.get("detected", {}),
        "model": spec.get("model"),
        "preview": anon,
        "summary": summarize(masked),
        "leftovers": leftovers,
        "pdf_base64": pdf_b64,
    }


@app.get("/api/custom-types")
def list_custom_types() -> dict:
    return {"types": [{"id": k, **v} for k, v in custom_types.load().items()]}


@app.post("/api/custom-types")
def save_custom_type(payload: dict = Body(...)) -> dict:
    label = (payload.get("label") or "").strip()
    spec = payload.get("spec") or {}
    if not label:
        raise HTTPException(400, "Nom du type manquant.")
    type_id = custom_types.upsert(label, spec, payload.get("type_id"))
    return {"ok": True, "type_id": type_id}


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
        report = llm.generate(
            text, payload.get("cr_type"), payload.get("provider"),
            payload.get("model"), payload.get("observations"),
        )
    except ValueError as exc:  # config manquante
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:  # erreur reseau / API
        raise HTTPException(502, str(exc)) from exc
    return {"report": report}


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
    elif text:
        raw = text
    else:
        raise HTTPException(400, "Fournir un fichier PDF ou un texte a anonymiser.")

    # 2. Anonymiser
    anonymized, masked = anonymize(raw, cr_type)

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
    anonymized, masked = anonymize(raw, cr_type)
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
