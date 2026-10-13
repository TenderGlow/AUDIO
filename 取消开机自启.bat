@echo off
chcp 65001 >nul
title 取消开机自启
echo 正在移除开机自启项...
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\开机自启.bat" 2>nul
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\开机自启.bat" (
  echo [失败] 未能删除自启项，请检查权限
) else (
  echo [完成] 已取消开机自启
)
echo.
pause
exit