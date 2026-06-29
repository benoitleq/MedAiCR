@echo off
REM Reconstruit l'executable autonome (.exe) puis l'installeur Windows.
REM Necessite : le venv du projet + Inno Setup 6 (pour l'installeur).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
)

echo [1/2] Construction de l'executable (PyInstaller)...
.venv\Scripts\python.exe -m PyInstaller AnonymiseurCR.spec --clean --noconfirm
if errorlevel 1 ( echo Echec PyInstaller & pause & exit /b 1 )

echo [2/2] Construction de l'installeur (Inno Setup)...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup introuvable. L'executable autonome est dispo dans dist\.
  echo Installez Inno Setup 6 pour generer l'installeur, ou distribuez dist\MedAiCR.exe tel quel.
  pause & exit /b 0
)
"%ISCC%" installer.iss
echo.
echo Termine :
echo   - Application autonome : dist\MedAiCR.exe
echo   - Installeur           : installer\MedAiCR_Setup_1.0.0.exe
pause
