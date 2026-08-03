echo off
cd /d %~dp0
revil_toolset spac_conv %*
if %errorlevel% NEQ 0 pause
