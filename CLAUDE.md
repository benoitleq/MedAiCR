# CLAUDE.md — contexte projet pour Claude Code

> ⚠️ **Outil à but PÉDAGOGIQUE uniquement — ce n'est PAS un dispositif médical (DM).**
> Non certifié, non marqué CE, à ne pas utiliser sur des patients réels.

## Ce que fait l'app

Application web **locale** (FastAPI + interface HTML unique) qui :
1. **anonymise** des comptes rendus (CR) médicaux PDF — 100 % en local
   (extraction texte + masquage par règles + rédaction PyMuPDF) ;
2. aide **optionnellement** à **rédiger le CR** via une IA, à partir du **texte
   anonymisé** uniquement (DeepSeek / OpenAI / Anthropic).

Tout tourne sur `127.0.0.1:8000`. L'anonymisation ne sort jamais du poste ; seule
la génération de CR envoie le texte **anonymisé** au fournisseur choisi.

## Lancer / construire

- **Dev** : double-clic `run.bat` (crée le venv au 1er lancement) → http://127.0.0.1:8000
  - ou : `.venv/Scripts/python -m uvicorn main:app --app-dir backend --port 8000`
- **Build exe + installeur** : `build_installer.bat` (nécessite le venv +
  [Inno Setup 6](https://jrsoftware.org/isinfo.php)). Sorties dans `dist/` et `installer/`.
- Python 3.11+ (3.13 utilisé). Dépendances : `requirements.txt`.

> Ne pas reconstruire l'exe/l'installeur sans que l'utilisateur le demande.

## Architecture

```
app_launcher.py   point d'entrée de l'app empaquetée (serveur + watcher + navigateur)
watcher.py        surveillance de dossiers (anonymisation PDF auto, en tâche de fond)
backend/
  main.py         API FastAPI + service du front + endpoints /api/workflow/*
  appconfig.py    chemins portables : dev = racine projet ; .exe (frozen) = %LOCALAPPDATA%\AnonymiseurCR
  pdf_extract.py  extraction texte (pdfplumber)
  extractors.py   extraction des identifiants par type de CR (+ types personnalisés)
  rules.py        règles regex génériques (dates, âge, sexe, contacts…)
  anonymizer.py   moteur texte : extraction → remplacement global → règles
  pdf_redact.py   rédaction du PDF d'origine (PyMuPDF), mise en page conservée
  custom_types.py types de documents appris via IA (à partir d'un CR FICTIF)
  worklist.py     onglet Workflow : scan des dossiers EN TÂCHE DE FOND + anonymisation worker
  llm.py          génération de CR multi-fournisseurs (DeepSeek/OpenAI/Anthropic)
frontend/
  index.html      interface (onglets Workflow / Manuel / Gestion de document / Configuration)
  vendor/pdfjs/   PDF.js (liseuse intégrée, hors-ligne) — committé
```

## Points importants / pièges

- **`worklist.py` ne doit PAS s'appeler `workflow.py`** : PyInstaller a un hook
  tiers `hook-workflow.py` qui fait échouer la build. `main.py` fait
  `import worklist as workflow`.
- **Workflow = scan en tâche de fond** : `worklist._scanner()` (thread démarré à
  l'import) parcourt les dossiers surveillés et met à jour un cache ; l'endpoint
  `/api/workflow/exams` renvoie l'instantané **instantanément** (ne jamais
  remettre le `os.walk` dans la requête — ça bloquait l'UI sur partage réseau).
  Un worker anonymise en fond, du plus récent au plus ancien (badge ✓).
- **Chemins portables** via `appconfig.py` : en dev, `config.json` / `llm.json` /
  `custom_types.json` / `watcher.log` sont à la **racine du projet** ; en .exe ils
  sont dans `%LOCALAPPDATA%\AnonymiseurCR`.
- **Multi-fournisseurs LLM** (`llm.py`) : format "openai" (Bearer,
  `/chat/completions`) vs "anthropic" (`x-api-key`, `anthropic-version`,
  `/v1/messages`, `max_tokens` requis). Un system prompt **par type d'examen**.
- **Types personnalisés** : on fournit un CR **FICTIF**, l'IA repère les champs ;
  les règles tournent ensuite 100 % en local.
- **Vérification** : utiliser des valeurs fictives distinctives (ZZNOMTEST,
  0000000001, 01/01/1900) pour confirmer le masquage.

## Secrets — JAMAIS commiter (déjà dans `.gitignore`)

`llm.json` (clés API), `config.json` (dossiers surveillés / infra),
`custom_types.json`, `watcher_state.json`, `*.log`, et `build/ dist/ installer/ .venv/`.
Sur une nouvelle machine, ces fichiers sont recréés (config par défaut) ou à
reconfigurer dans l'onglet Configuration (clé API + dossiers surveillés).

## Conventions

- Code et commentaires **en français** (sans accents dans les commentaires de
  certains modules historiques — rester cohérent avec le fichier édité).
- Toujours **vérifier l'anonymisation** (récap des éléments masqués + aperçu PDF)
  avant tout envoi à une IA.
