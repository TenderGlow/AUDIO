@echo off
chcp 65001 >nul
title 停止 cloudSyncG
cd /d "%~dp0"
echo 正在停止 cloudSyncG 悬浮窗...
python -m cloudSyncG.shutdown stop
echo.
pause
exit