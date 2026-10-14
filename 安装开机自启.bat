@echo off
rem 旧的"开机自启"安装脚本：现在统一走 registry 启动项（更干净、可一键禁用）。
rem 直接转交给「安装常驻.bat」，两套机制不要并存，免得互相覆盖。
call "%~dp0安装常驻.bat"
exit /b %ERRORLEVEL%