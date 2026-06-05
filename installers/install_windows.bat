@echo off
:: installers/install_windows.bat
:: ─────────────────────────────────────────────────────────────────────────────
:: Windows Installer for Lobster Graph (Paper Processor)
:: Requires Winget (Windows Package Manager)
:: ─────────────────────────────────────────────────────────────────────────────
setlocal EnableDelayedExpansion

echo =======================================================
echo  🦞 Lobster Graph — Windows Setup
echo =======================================================
echo.

:: 1. Check Winget
echo [~] Checking Windows Package Manager (winget)...
where winget >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [X] Winget not found. Please install App Installer from the Microsoft Store.
    exit /b 1
)
echo [OK] Winget is available.
echo.

:: 2. System Dependencies
echo [~] Installing System Dependencies (Python, Graphviz)...
winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
winget install Graphviz.Graphviz --silent --accept-package-agreements --accept-source-agreements
echo [OK] System packages installed.
echo.

:: 3. Virtual Environment
echo [~] Configuring Python Environment...
if not exist ".venv" (
    python -m venv .venv
    echo [OK] Virtual environment created.
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

if exist "..\requirements.txt" (
    pip install -r ..\requirements.txt
) else if exist "requirements.txt" (
    pip install -r requirements.txt
) else (
    pip install pymupdf requests neo4j
)
echo [OK] Python dependencies installed.
echo.

:: 4. Ollama Installation Check
echo [~] Checking Ollama...
where ollama >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [X] Ollama not found. Installing via Winget...
    winget install Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
    echo [OK] Ollama installed.
) else (
    echo [OK] Ollama is already installed.
)
echo.

:: 5. Neo4j Instructions
echo [!] Lobster Graph visualizer requires a Neo4j graph database.
echo [!] Recommended deployment is via Docker Desktop:
echo     docker run -d --name paper-processor-neo4j -v "%%cd%%\neo4j_viz\data:/data" -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password123 neo4j:latest

echo.
echo =======================================================
echo  🎉 Setup Complete!
echo =======================================================
echo To start the application, run:
echo   .venv\Scripts\activate.bat
echo   python paper_processor.py C:\path\to\pdfs
pause
