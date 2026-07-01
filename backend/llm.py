"""Generation de CR via plusieurs fournisseurs d'IA, au choix.

Le texte envoye est le texte ANONYMISE (anonymisation locale en amont).
Reglages stockes localement dans llm.json (data_dir) : fournisseur selectionne,
une cle API + un modele par fournisseur, et un system prompt PAR TYPE d'examen.

Deux formats d'API sont geres :
  - "openai"    : POST {base}/chat/completions, auth "Bearer", reponse
                  choices[0].message.content. (OpenAI, DeepSeek, et compatibles.)
  - "anthropic" : POST {base}/v1/messages, header x-api-key + anthropic-version,
                  'system' separe, 'max_tokens' requis, reponse content[].text.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from appconfig import LLM_FILE


def _strip_markdown(text: str) -> str:
    """Retire le formatage markdown (gras **, italique __, titres #) du CR."""
    if not text:
        return text
    text = text.replace("**", "").replace("__", "")
    # Titres markdown en debut de ligne : "### Titre" -> "Titre"
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    return text


def _clean_keep_bold(text: str) -> str:
    """Nettoie le markdown SAUF le gras (**...**), conserve pour le courrier Word."""
    if not text:
        return text
    text = text.replace("__", "")
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    return text


def to_plain(text: str) -> str:
    """Texte brut, sans aucun markdown (pour l'apercu et le fichier .txt)."""
    return _strip_markdown(text)

# Catalogue des fournisseurs : format d'API, endpoint, modeles suggeres.
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "format": "openai",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "format": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"],
        "default_model": "gpt-4o",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "format": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "default_model": "claude-opus-4-8",
    },
}

DEFAULT_PROMPTS = {"echo_cardiaque": "", "polygraphie": "", "holter": ""}
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 8000


def read_config() -> dict:
    try:
        data = json.loads(LLM_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    providers = {}
    saved = data.get("providers") if isinstance(data.get("providers"), dict) else {}
    for pid, meta in PROVIDERS.items():
        p = saved.get(pid, {}) if isinstance(saved.get(pid), dict) else {}
        providers[pid] = {
            "api_key": str(p.get("api_key", "")),
            "model": str(p.get("model", meta["default_model"])),
            "base_url": str(p.get("base_url", meta["base_url"])),
        }

    prompts = dict(DEFAULT_PROMPTS)
    if isinstance(data.get("system_prompts"), dict):
        prompts.update({k: str(v) for k, v in data["system_prompts"].items()})

    # Recommandations IA activees par TYPE d'examen (opt-in, off par defaut).
    reco = {}
    if isinstance(data.get("reco_enabled"), dict):
        reco = {str(k): bool(v) for k, v in data["reco_enabled"].items()}

    provider = data.get("provider", "deepseek")
    if provider not in PROVIDERS:
        provider = "deepseek"

    return {"provider": provider, "providers": providers,
            "system_prompts": prompts, "reco_enabled": reco}


def reco_enabled(cr_type: str | None) -> bool:
    """Les recommandations IA sont-elles activees pour ce type d'examen ?"""
    return bool(read_config().get("reco_enabled", {}).get(cr_type or "", False))


def write_config(updates: dict) -> dict:
    """Met a jour les reglages. La cle API d'un fournisseur n'est ecrasee que si
    une nouvelle valeur non vide est fournie."""
    cfg = read_config()

    if updates.get("provider") in PROVIDERS:
        cfg["provider"] = updates["provider"]

    upd_providers = updates.get("providers")
    if isinstance(upd_providers, dict):
        for pid, pu in upd_providers.items():
            if pid not in PROVIDERS or not isinstance(pu, dict):
                continue
            tgt = cfg["providers"][pid]
            if pu.get("model"):
                tgt["model"] = str(pu["model"]).strip()
            if pu.get("base_url"):
                tgt["base_url"] = str(pu["base_url"]).strip()
            if pu.get("api_key"):
                tgt["api_key"] = str(pu["api_key"]).strip()

    if isinstance(updates.get("system_prompts"), dict):
        prompts = dict(cfg["system_prompts"])
        prompts.update({str(k): str(v) for k, v in updates["system_prompts"].items()})
        cfg["system_prompts"] = prompts

    if isinstance(updates.get("reco_enabled"), dict):
        reco = dict(cfg.get("reco_enabled", {}))
        reco.update({str(k): bool(v) for k, v in updates["reco_enabled"].items()})
        cfg["reco_enabled"] = reco

    LLM_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _http_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Erreur API ({exc.code}) : {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connexion impossible a l'API : {exc.reason}") from exc


def _call(pcfg: dict, meta: dict, model: str, system: str, user: str,
          max_tokens: int, timeout: int) -> str:
    """Construit et envoie la requete selon le format du fournisseur."""
    base = pcfg["base_url"].rstrip("/")
    if meta["format"] == "anthropic":
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            payload["system"] = system
        headers = {
            "content-type": "application/json",
            "x-api-key": pcfg["api_key"],
            "anthropic-version": ANTHROPIC_VERSION,
        }
        data = _http_json(base + "/v1/messages", headers, payload, timeout)
        try:
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"Reponse inattendue : {str(data)[:300]}") from exc

    # Format OpenAI-compatible (OpenAI, DeepSeek...)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {pcfg['api_key']}",
    }
    data = _http_json(base + "/chat/completions", headers, payload, timeout)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Reponse inattendue : {str(data)[:300]}") from exc


def _resolve(provider: str | None, api_key: str | None, model: str | None):
    """Resout (pid, meta, pcfg, model) en appliquant d'eventuelles surcharges."""
    cfg = read_config()
    pid = provider or cfg["provider"]
    if pid not in PROVIDERS:
        raise ValueError(f"Fournisseur inconnu : {pid}")
    meta = PROVIDERS[pid]
    pcfg = dict(cfg["providers"][pid])
    if api_key:
        pcfg["api_key"] = api_key.strip()
    if not pcfg.get("api_key"):
        raise ValueError(
            f"Cle API manquante pour {meta['label']}. "
            "Renseignez-la dans l'onglet Configuration > Generation de CR."
        )
    return cfg, pid, meta, pcfg, (model or pcfg["model"])


RECO_MARKER = "===RECOMMANDATIONS==="
_RECO_INSTRUCTION = (
    "\n\nEnsuite, APRES le compte rendu, insere une ligne contenant EXACTEMENT :\n"
    + RECO_MARKER + "\n"
    "puis une section « Recommandations de prise en charge » ADAPTEE AU TYPE "
    "D'EXAMEN et aux resultats ci-dessus, fondee sur les DERNIERES recommandations "
    "applicables (societes savantes / ESC quand pertinent). N'applique QUE des "
    "recommandations reellement pertinentes pour cet examen : par exemple, pour une "
    "echocardiographie -> valvulopathies (ESC/EACTS 2021), cardiomyopathies/CMH "
    "(ESC 2023), insuffisance cardiaque ; pour un ECG -> troubles du rythme/"
    "conduction correspondants ; etc. N'INVENTE PAS de recommandations hors sujet. "
    "Sois concis et actionnable (seuils/grades, surveillance, indications). Mets les "
    "elements importants en gras (**...**). Si aucune recommandation specifique ne "
    "s'applique, ecris simplement qu'un suivi standard suffit."
)


def generate_full(text: str, cr_type: str | None = None, provider: str | None = None,
                  model: str | None = None, observations: str | None = None,
                  with_reco: bool = False, timeout: int = 180) -> tuple[str, str]:
    """Genere le CR (system prompt du TYPE) et, si with_reco, une section de
    RECOMMANDATIONS de prise en charge dans le MEME appel. Renvoie (cr_md, reco_md)
    avec le gras markdown conserve. reco_md = "" si non demandee/absente.

    observations : constatations libres du medecin a integrer imperativement."""
    cfg, _pid, meta, pcfg, mdl = _resolve(provider, None, model)
    system_prompt = cfg["system_prompts"].get(cr_type or "", "")
    user = text
    if observations and observations.strip():
        user = (
            text
            + "\n\n--- CONSTATATIONS DU MEDECIN (a integrer imperativement au "
            "compte rendu) ---\n" + observations.strip()
        )
    if with_reco:
        user += _RECO_INSTRUCTION
    raw = _clean_keep_bold(_call(pcfg, meta, mdl, system_prompt, user, MAX_TOKENS, timeout))
    if with_reco and RECO_MARKER in raw:
        cr_md, _sep, reco_md = raw.partition(RECO_MARKER)
        return cr_md.strip(), reco_md.strip()
    return raw.strip(), ""


def generate(text: str, cr_type: str | None = None, provider: str | None = None,
             model: str | None = None, observations: str | None = None,
             timeout: int = 180) -> str:
    """Genere un CR (sans recommandations). Conserve le gras (**...**)."""
    cr_md, _ = generate_full(text, cr_type, provider, model, observations,
                             with_reco=False, timeout=timeout)
    return cr_md


def test_connection(provider: str | None = None, api_key: str | None = None,
                    model: str | None = None, timeout: int = 30) -> dict:
    """Verifie la connexion : petit appel reel, renvoie le modele + la reponse."""
    _cfg, _pid, meta, pcfg, mdl = _resolve(provider, api_key, model)
    reply = _call(pcfg, meta, mdl, "", "Reponds uniquement par: OK", 16, timeout)
    return {"model": mdl, "reply": (reply or "").strip()[:200]}


_DETECT_SYSTEM = (
    "Tu analyses un compte rendu medical FICTIF pour configurer son "
    "anonymisation. Tu reperes les LIBELLES (intitules de champ) qui precedent "
    "les informations identifiantes du patient. Reponds STRICTEMENT en JSON, "
    "sans aucun texte autour ni balises de code."
)


def _parse_json(raw: str) -> dict:
    s = (raw or "").strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        s = m.group(0)
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Reponse IA non exploitable (JSON attendu) : {raw[:300]}") from exc


def detect_fields(text: str, provider: str | None = None, model: str | None = None,
                  api_key: str | None = None, timeout: int = 60) -> dict:
    """Demande a l'IA les libelles des champs identifiants d'un CR fictif."""
    _cfg, _pid, meta, pcfg, mdl = _resolve(provider, api_key, model)
    user = (
        "Compte rendu FICTIF a analyser :\n\n" + text[:6000] + "\n\n"
        "Renvoie uniquement ce JSON :\n"
        '{"name_labels": [], "name_before": [], "id_labels": [], "dob_labels": [], '
        '"detected": {"name": "", "id": "", "dob": ""}}\n'
        "- name_labels : libelles qui precedent le NOM/prenom du patient "
        "(sans les deux-points). Mets [] si le nom n'a pas de libelle.\n"
        "- name_before : SI le nom n'a pas de libelle (ex. il apparait seul en "
        "en-tete), donne le texte qui SUIT immediatement le nom sur la meme ligne "
        "(ex. 'Date etude'). Sinon [].\n"
        "- id_labels : libelles du NUMERO patient / dossier / serie.\n"
        "- dob_labels : libelles de la DATE DE NAISSANCE.\n"
        "- detected : les valeurs fictives exactes que tu as reperees "
        "(nom complet, numero, date de naissance)."
    )
    data = _parse_json(_call(pcfg, meta, mdl, _DETECT_SYSTEM, user, 1500, timeout))

    def _lst(x):
        return [str(v).strip() for v in x if str(v).strip()] if isinstance(x, list) else []

    det = data.get("detected") if isinstance(data.get("detected"), dict) else {}
    return {
        "name_labels": _lst(data.get("name_labels")),
        "name_before": _lst(data.get("name_before")),
        "id_labels": _lst(data.get("id_labels")),
        "dob_labels": _lst(data.get("dob_labels")),
        "detected": {k: str(det.get(k, "")).strip() for k in ("name", "id", "dob")},
        "model": mdl,
    }
