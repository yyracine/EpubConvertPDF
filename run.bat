@echo off
setlocal
cd /d "%~dp0"
title Reliure - Convertisseur EPUB vers PDF

if not exist ".venv\Scripts\python.exe" (
    echo [Reliure] Creation de l'environnement Python...
    python -m venv .venv
    if errorlevel 1 goto :python_error
)

echo [Reliure] Verification des dependances...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet --requirement requirements.txt
if errorlevel 1 goto :install_error

echo [Reliure] Demarrage sur http://127.0.0.1:8765
".venv\Scripts\python.exe" app.py
goto :end

:python_error
echo.
echo Python est introuvable. Installez Python 3.10 ou plus recent depuis https://www.python.org/downloads/
pause
goto :end

:install_error
echo.
echo L'installation des dependances a echoue. Verifiez votre connexion reseau puis relancez.
pause

:end
endlocal
