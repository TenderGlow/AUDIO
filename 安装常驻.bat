@echo off
title 安装 CloudSync 常驻（开机自启）
setlocal

rem exe 位置：优先用打包产物，其次用项目根目录下的那份
set "EXE=%~dp0dist\CloudSync.exe"
if not exist "%EXE%" set "EXE=%~dp0CloudSync.exe"
if not exist "%EXE%" (
  echo [失败] 找不到 CloudSync.exe，请先运行「打包.bat」。
  pause & exit /b 1
)

rem 写当前用户的启动项（HKCU，不需要管理员权限，也不会碰系统目录）
set "KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
reg add "%KEY%" /v "CloudSync" /t REG_SZ /d "\"%EXE%\"" /f >nul
if errorlevel 1 (
  echo [失败] 写入启动项失败。
  pause & exit /b 1
)

echo [完成] 已安装开机自启：%EXE%
echo.
echo   * 开机登录后会静默启动，悬浮窗直接出现，不弹任何界面。
echo   * 任务管理器的「启动」选项卡里能看到它，也能在那里一键禁用。
echo   * 想彻底关掉：点悬浮窗的「退出」按钮，或运行「卸载常驻.bat」。
echo.
echo 正在启动一次...
start "" "%EXE%"
echo.
pause
exit /b 0