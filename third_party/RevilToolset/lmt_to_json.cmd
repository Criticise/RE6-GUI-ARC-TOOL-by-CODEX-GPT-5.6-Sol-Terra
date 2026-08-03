echo off
cd /d %~dp0
revil_toolset lmt_to_json %*
if %errorlevel% NEQ 0 pause
