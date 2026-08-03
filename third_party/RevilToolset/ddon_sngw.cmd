echo off
cd /d %~dp0
revil_toolset ddon_sngw %*
if %errorlevel% NEQ 0 pause
