@echo off
setlocal
chcp 65001 >nul
where pwsh.exe >nul 2>&1
if "%ERRORLEVEL%"=="0" (
    pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
)
set "exitCode=%ERRORLEVEL%"
if not "%exitCode%"=="0" pause
exit /b %exitCode%
