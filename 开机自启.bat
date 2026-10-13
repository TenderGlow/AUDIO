@echo off
chcp 65001 >nul
title cloudSyncG
cd /d "%~dp0"
start "" pythonw.exe cloudSyncG-main.py
exit