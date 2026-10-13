@echo off
rem 把「开机自启.bat」复制到当前用户的"启动"文件夹，实现开机自动运行。
rem 再次运行本脚本会先移除旧的快捷方式，避免重复安装。
chcp 65001 >nul
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SRC=%~dp0开机自启.bat"
if not exist "%STARTUP%" mkdir "%STARTUP%"
if exist "%STARTUP%\开机自启.bat" del "%STARTUP%\开机自启.bat"
copy /y "%SRC%" "%STARTUP%\开机自启.bat" >nul
echo 已安装开机自启：%STARTUP%\开机自启.bat
echo 如需卸载，直接删除上面这个文件即可。
pause