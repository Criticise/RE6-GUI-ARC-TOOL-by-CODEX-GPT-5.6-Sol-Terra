echo off
cd /d %~dp0
revil_toolset reasset_to_gltf %*
if %errorlevel% NEQ 0 pause
