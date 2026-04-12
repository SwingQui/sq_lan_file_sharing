@echo off
echo ========================================
echo   SQ LAN File Share - Build Script
echo ========================================
echo.

REM Kill running instance
taskkill /f /im SQLanFileShare.exe >nul 2>&1

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.8+
    pause
    exit /b 1
)

REM Install dependencies
echo [1/3] Installing dependencies...
pip install -r requirements.txt -q

REM Install PyInstaller
echo [2/3] Checking PyInstaller...
pip install pyinstaller -q

REM Build
echo [3/3] Building...
pyinstaller build.spec --clean

echo.
if exist "dist\SQLanFileShare.exe" (
    echo ========================================
    echo   Build Success!
    echo   Output: dist\SQLanFileShare.exe
    echo ========================================
) else (
    echo [ERROR] Build failed, check errors above
)

pause
