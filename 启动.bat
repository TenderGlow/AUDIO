@echo off
title CloudSync
cd /d "%~dp0"
rem 打包过就直接用 exe（静默、无黑框）；没打包就退回源码运行
if exist "%~dp0dist\CloudSync.exe" (
  start "" "%~dp0dist\CloudSync.exe"
) else (
  start "" pythonw.exe cloudSyncG-main.py
)
exit