echo off
cd /d %~dp0
revil_toolset make_arc %*
if %errorlevel% NEQ 0 pause
