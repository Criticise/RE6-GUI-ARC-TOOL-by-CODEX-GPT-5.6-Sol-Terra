echo off
cd /d %~dp0
revil_toolset bin_to_xml %*
if %errorlevel% NEQ 0 pause
