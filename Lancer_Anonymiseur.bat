@echo off
REM Lanceur de MedAiCR : demarre le serveur local, la surveillance et le navigateur.
cd /d "%~dp0"

REM Cree l'environnement Python au premier lancement.
if not exist ".venv\Scripts\python.exe" (
  echo Premier lancement : installation en cours, merci de patienter...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)

REM Demarre la surveillance des dossiers (config.json) dans sa propre fenetre reduite.
start "Surveillance CR" /min .venv\Scripts\python.exe watcher.py

REM Ouvre le navigateur apres 3 secondes (en parallele du serveur).
start "" /min cmd /c "timeout /t 3 >nul & start """" http://127.0.0.1:8000/"

REM Demarre le serveur (laisser cette fenetre ouverte pendant l'utilisation).
echo.
echo === MedAiCR ===
echo - Interface web : http://127.0.0.1:8000/ (le navigateur va s'ouvrir)
echo - Surveillance des dossiers : active (fenetre "Surveillance CR" reduite)
echo.
echo Fermez cette fenetre pour arreter le serveur web.
echo (Fermez aussi la fenetre "Surveillance CR" pour arreter la surveillance.)
echo.
.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --log-level warning
