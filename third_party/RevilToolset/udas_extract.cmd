echo off
cd /d %~dp0
revil_toolset udas_extract %*
if %errorlevel% NEQ 0 pause
