echo off
cd /d %~dp0
revil_toolset obb_extract %*
if %errorlevel% NEQ 0 pause
