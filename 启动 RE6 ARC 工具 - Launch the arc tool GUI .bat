@echo off
setlocal DisableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "PS1_PATH=%~dpn0.ps1"
set "POWERSHELL_EXE="
set "INSTALLER_BAT="

if not exist "%PS1_PATH%" (
    set "PS1_PATH="
    for %%I in ("%SCRIPT_DIR%*.ps1") do (
        if exist "%%~fI" (
        if not defined PS1_PATH set "PS1_PATH=%%~fI"
        )
    )
)

if not exist "%PS1_PATH%" (
    echo Missing launcher script:
    echo %PS1_PATH%
    echo.
    pause
    exit /b 1
)

call :resolve_powershell
if not defined POWERSHELL_EXE (
    echo PowerShell was not found on this machine.
    echo Install Windows PowerShell or PowerShell 7, then try again.
    echo.
    pause
    exit /b 1
)

"%POWERSHELL_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1_PATH%" %*
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="66" (
    call :find_installer
    if defined INSTALLER_BAT (
        echo.
        echo Python was not found, but a local installer helper is available:
        echo %INSTALLER_BAT%
        choice /C YN /N /M "Run the installer now? [Y/N] "
        if errorlevel 2 goto after_installer_prompt
        call "%INSTALLER_BAT%"
        if "%ERRORLEVEL%"=="0" (
            echo.
            echo Retrying RE6 ARC Tool...
            "%POWERSHELL_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1_PATH%" %*
            set "EXIT_CODE=%ERRORLEVEL%"
        )
    )
)

:after_installer_prompt

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Failed to launch RE6 ARC Tool.
    pause
)

exit /b %EXIT_CODE%

:resolve_powershell
for %%I in (
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
    "%ProgramFiles%\PowerShell\7\pwsh.exe"
) do (
    if not defined POWERSHELL_EXE if exist "%%~fI" set "POWERSHELL_EXE=%%~fI"
)

if not defined POWERSHELL_EXE (
    for %%I in (powershell.exe pwsh.exe) do (
        if not defined POWERSHELL_EXE (
            for /f "delims=" %%P in ('where.exe %%I 2^>nul') do (
                if not defined POWERSHELL_EXE set "POWERSHELL_EXE=%%P"
            )
        )
    )
)
goto :eof

:find_installer
if defined INSTALLER_BAT goto :eof

for %%I in (
    "%SCRIPT_DIR%Install_Codex_V4_Python_3_14.bat"
    "%SCRIPT_DIR%..\Install_Codex_V4_Python_3_14.bat"
) do (
    if not defined INSTALLER_BAT if exist "%%~fI" set "INSTALLER_BAT=%%~fI"
)

if not defined INSTALLER_BAT (
    for /d %%D in ("%SCRIPT_DIR%..\*") do (
        if not defined INSTALLER_BAT if exist "%%~fD\Install_Codex_V4_Python_3_14.bat" set "INSTALLER_BAT=%%~fD\Install_Codex_V4_Python_3_14.bat"
    )
)
goto :eof
