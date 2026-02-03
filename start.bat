@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Error: Virtual environment not found
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
.venv\Scripts\python.exe -m kirara_ai %*
call deactivate

echo.
echo Press any key to close...
pause >nul
