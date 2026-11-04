@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -c "import ecta_gs, PySide6, sqlalchemy, alembic" >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install -e ".[dev]"
    if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m ecta_gs
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. See README.md for installation instructions.
pause
exit /b 1
