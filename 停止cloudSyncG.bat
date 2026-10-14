@echo off
title 停止 CloudSync
cd /d "%~dp0"
rem 这一条命令同时会停掉悬浮窗进程和监控进程（监控不会被"再拉起来"）
if exist "%~dp0dist\CloudSync.exe" (
  "%~dp0dist\CloudSync.exe" --stop
) else (
  python -m cloudSyncG.shutdown stop
)
echo.
pause
exit