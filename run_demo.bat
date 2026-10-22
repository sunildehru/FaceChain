@echo off
setlocal
cd /d "%~dp0"

echo ========================================================
echo         FaceChain Biometric Verification Pipeline
echo ========================================================
echo Working Directory: %CD%
echo.

REM Ensure temp directories exist
if not exist "temp\scraped" (
    mkdir "temp\scraped"
)

REM Check if venv exists
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found in 'venv' folder.
    echo Please create the virtual environment and install requirements:
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo [1/3] Activating virtual environment...
call .\venv\Scripts\activate.bat

echo [2/3] Verifying smart contract compilation...
python src\chain\compile.py
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Contract compilation step had an issue. Checking for existing compiled artifact...
    if not exist "src\chain\contracts\RegistryCompiled.json" (
        echo [ERROR] RegistryCompiled.json not found and compilation failed.
        pause
        exit /b 1
    )
)

echo.
echo [3/3] Launching FaceChain Server on http://127.0.0.1:8000 ...
echo Press Ctrl+C in this window to stop the server.
echo.
python -m uvicorn src.api:app --reload --host 127.0.0.1 --port 8000

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Server exited with code %ERRORLEVEL%.
    pause
)
