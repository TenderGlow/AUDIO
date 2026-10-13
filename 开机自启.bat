@echo off
chcp 65001 >nul
title Audio
cd /d "%~dp0"
start "" pythonw.exe main.py
exit