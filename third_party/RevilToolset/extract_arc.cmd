echo off
cd /d %~dp0
revil_toolset extract_arc %*
if %errorlevel% NEQ 0 pause
