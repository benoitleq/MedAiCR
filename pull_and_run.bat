@echo off
REM Met a jour le projet depuis GitHub puis lance l'app en local.
REM Pratique sur la machine cible : double-clic = derniere version + serveur.
cd /d "%~dp0"

echo [1/3] Mise a jour depuis GitHub (git pull)...
where git >nul 2>nul
if errorlevel 1 (
  echo   ! Git introuvable : etape ignoree (installe Git pour la mise a jour auto).
) else (
  git pull --ff-only
  if errorlevel 1 echo   ! git pull a echoue ^(modifs locales / reseau / auth^) : on lance la version actuelle.
)

if not exist ".venv\Scripts\python.exe" (
  echo [2/3] Creation de l'environnement Python ^(1re fois^)...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
) else (
  echo [2/3] Environnement Python deja present.
)

echo [3/3] Demarrage du serveur sur http://127.0.0.1:8000
echo (Ctrl+C pour arreter)
.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
pause
