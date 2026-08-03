echo off
cd /d %~dp0
revil_toolset xml_to_bin %*
if %errorlevel% NEQ 0 pause
