@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 py -3.11 -m venv .venv
    if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -c "import ecta_gs, PySide6, sqlalchemy, alembic, pymupdf, docx, openpyxl, ezdxf, charset_normalizer, tomllib; from importlib.metadata import version; assert version('ecta-gs') == tomllib.load(open('pyproject.toml','rb'))['project']['version']" >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python.exe -m pip install -e "."
    if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m ecta_gs
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Python 3.12 or 3.11 is required. See README.md.
pause
exit /b 1
