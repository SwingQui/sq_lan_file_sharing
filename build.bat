@echo off
echo ========================================
echo   局域网文件共享工具 - 打包脚本
echo ========================================
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 安装依赖
echo [1/3] 安装依赖...
pip install -r requirements.txt -q

REM 安装PyInstaller
echo [2/3] 检查PyInstaller...
pip install pyinstaller -q

REM 打包
echo [3/3] 打包中...
pyinstaller build.spec --clean

echo.
if exist "dist\SQLanFileShare.exe" (
    echo ========================================
    echo   打包成功！
    echo   输出文件: dist\SQLanFileShare.exe
    echo ========================================
) else (
    echo [错误] 打包失败，请检查错误信息
)

pause
