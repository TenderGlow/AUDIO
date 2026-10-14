@echo off
title 取消开机自启
echo 正在移除开机自启项，并停掉正在运行的 CloudSync ...
call "%~dp0卸载常驻.bat"
exit /b %ERRORLEVEL%