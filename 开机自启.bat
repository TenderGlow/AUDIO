@echo off
title cloudSyncG
cd /d "%~dp0"
rem 开机自启走的是"静默启动"路径：双击 exe 即可，不需要任何安装步骤
if exist "%~dp0dist\CloudSync.exe" (
  start "" "%~dp0dist\CloudSync.exe"
) else (
  start "" pythonw.exe cloudSyncG-main.py
)
exit