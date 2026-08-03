echo off
cd /d %~dp0
revil_toolset re_tex_to_dds %*
if %errorlevel% NEQ 0 pause
