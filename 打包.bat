@echo off
title 打包 CloudSync.exe
cd /d "%~dp0"

echo [1/3] 安装/更新 PyInstaller ...
python -m pip install --disable-pip-version-check -q pyinstaller
if errorlevel 1 goto :fail

echo [2/3] 清理旧的构建产物 ...
if exist build rd /s /q build
if exist dist rd /s /q dist

echo [3/3] 开始打包（首次约 1-3 分钟）...
python -m PyInstaller --noconfirm --clean pack\CloudSync.spec
if errorlevel 1 goto :fail

echo.
echo 打包完成：%CD%\dist\CloudSync.exe
echo 双击即可静默启动，不会有黑框、不进任务栏、不进托盘。
echo.
pause
exit /b 0

:fail
echo.
echo [失败] 打包未完成，请看上面的报错。
pause
exit /b 1