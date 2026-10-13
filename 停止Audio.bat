@echo off
chcp 65001 >nul
title 停止 Audio
cd /d "%~dp0"
echo 正在停止 Audio 悬浮窗...
python audio\shutdown.py stop
echo.
pause
exit