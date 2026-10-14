@echo off
title 卸载 CloudSync 常驻
setlocal

rem 1) 先让程序自己收工（悬浮窗退出 + 监控停机）。
rem    必须走这一步：直接去任务管理器杀进程的话，监控会立刻把悬浮窗再拉起来，
rem    那是保活设计，不是故障。
set "EXE=%~dp0dist\CloudSync.exe"
if not exist "%EXE%" set "EXE=%~dp0CloudSync.exe"
if exist "%EXE%" (
  "%EXE%" --stop
  timeout /t 3 /nobreak >nul
)

rem 2) 兜底：还有残留就强杀（两个角色 + 代理，都是同一个 exe）
taskkill /F /IM CloudSync.exe >nul 2>&1

rem 3) 删掉开机自启项
set "KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
reg delete "%KEY%" /v "CloudSync" /f >nul 2>&1
if exist "%KEY%" (
  reg query "%KEY%" /v "CloudSync" >nul 2>&1
  if errorlevel 1 (
    echo [完成] 已移除开机自启。
  ) else (
    echo [失败] 未能移除启动项，请检查权限。
  )
) else (
  echo [完成] 已移除开机自启。
)

rem 4) 清掉运行期留下的状态文件（pid、退出标记、日志）
if exist "%APPDATA%\cloudSyncG" (
  rd /s /q "%APPDATA%\cloudSyncG" >nul 2>&1
  echo [完成] 已清理运行期状态（%APPDATA%\cloudSyncG）。
)

echo.
echo 想彻底删干净：把 CloudSync.exe 所在的那个文件/文件夹删掉即可。
echo.
pause
exit /b 0