echo off
cd /d %~dp0
revil_toolset fpk_extract %*
if %errorlevel% NEQ 0 pause
