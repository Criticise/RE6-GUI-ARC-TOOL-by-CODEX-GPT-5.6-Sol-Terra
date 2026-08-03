param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ForwardedArgs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$launcherDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appScript = Join-Path $launcherDir "codex_re6_arc_gui.py"
$launchLogPath = Join-Path $launcherDir "RE6_ARC_TOOL_LAUNCH.log"
$pythonMissingExitCode = 66
$launchArguments = @($appScript)
$consoleCliSwitches = @("-h", "--help", "--extract", "--repack", "--scan", "--roundtrip")
$consoleCliMode = $false
if ($null -ne $ForwardedArgs -and $ForwardedArgs.Count -gt 0) {
    $launchArguments += $ForwardedArgs
    foreach ($argument in $ForwardedArgs) {
        if ($consoleCliSwitches -icontains $argument) {
            $consoleCliMode = $true
            break
        }
    }
}
# GUI uses pythonw and detaches; CLI commands keep python.exe and console output.
$preferConsoleMode = $consoleCliMode

if (-not (Test-Path -LiteralPath $appScript)) {
    throw "Missing GUI script: $appScript"
}

function Test-UsablePythonLauncherPath([string]$filePath) {
    if ([string]::IsNullOrWhiteSpace($filePath)) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $filePath)) {
        return $false
    }
    try {
        $item = Get-Item -LiteralPath $filePath -ErrorAction Stop
        if ($item.PSIsContainer) {
            return $false
        }
        if ($filePath -like "*\Microsoft\WindowsApps\*" -and $item.Length -eq 0) {
            return $false
        }
    }
    catch {
        return $false
    }
    return $true
}

function New-PythonCandidate([string]$filePath, [string[]]$arguments, [string]$kind) {
    return [pscustomobject]@{
        FilePath = $filePath
        Arguments = @($arguments)
        Kind = $kind
    }
}

function Add-PythonCandidate([System.Collections.Generic.List[object]]$candidates, [System.Collections.Generic.HashSet[string]]$seen, [string]$filePath, [string[]]$arguments, [string]$kind) {
    if ([string]::IsNullOrWhiteSpace($filePath)) {
        return
    }
    $normalizedPath = $filePath.Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($normalizedPath)) {
        return
    }
    $key = $normalizedPath.ToLowerInvariant() + "|" + (($arguments -join "`0").ToLowerInvariant())
    if ($seen.Add($key)) {
        $candidates.Add((New-PythonCandidate -filePath $normalizedPath -arguments $arguments -kind $kind)) | Out-Null
    }
}

function Add-PythonFamilyCandidates([System.Collections.Generic.List[object]]$candidates, [System.Collections.Generic.HashSet[string]]$seen, [string]$basePath, [string]$kind) {
    if ([string]::IsNullOrWhiteSpace($basePath)) {
        return
    }

    $trimmedPath = $basePath.Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($trimmedPath)) {
        return
    }

    $isDirectory = $false
    try {
        if (Test-Path -LiteralPath $trimmedPath) {
            $item = Get-Item -LiteralPath $trimmedPath -ErrorAction Stop
            $isDirectory = $item.PSIsContainer
        }
    }
    catch {}

    if ($isDirectory -or -not ($trimmedPath -match "\.exe$")) {
        if ($preferConsoleMode) {
            $relativePaths = @(
                "python.exe",
                "pythonw.exe",
                "Scripts\python.exe",
                "Scripts\pythonw.exe",
                "bin\python.exe",
                "bin\pythonw.exe",
                "Python\python.exe",
                "Python\pythonw.exe",
                "runtime\python.exe",
                "runtime\pythonw.exe",
                "_python\python.exe",
                "_python\pythonw.exe",
                ".venv\Scripts\python.exe",
                ".venv\Scripts\pythonw.exe",
                "venv\Scripts\python.exe",
                "venv\Scripts\pythonw.exe"
            )
        }
        else {
            $relativePaths = @(
                "pythonw.exe",
                "python.exe",
                "Scripts\pythonw.exe",
                "Scripts\python.exe",
                "bin\pythonw.exe",
                "bin\python.exe",
                "Python\pythonw.exe",
                "Python\python.exe",
                "runtime\pythonw.exe",
                "runtime\python.exe",
                "_python\pythonw.exe",
                "_python\python.exe",
                ".venv\Scripts\pythonw.exe",
                ".venv\Scripts\python.exe",
                "venv\Scripts\pythonw.exe",
                "venv\Scripts\python.exe"
            )
        }
        foreach ($relativePath in $relativePaths) {
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath (Join-Path $trimmedPath $relativePath) -arguments $launchArguments -kind $kind
        }
        return
    }

    $dirPath = Split-Path -Parent $trimmedPath
    $leafName = Split-Path -Leaf $trimmedPath
    if ($leafName -ieq "python.exe") {
        if ($preferConsoleMode) {
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath $trimmedPath -arguments $launchArguments -kind $kind
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath (Join-Path $dirPath "pythonw.exe") -arguments $launchArguments -kind ($kind + " -> pythonw.exe")
        }
        else {
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath (Join-Path $dirPath "pythonw.exe") -arguments $launchArguments -kind ($kind + " -> pythonw.exe")
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath $trimmedPath -arguments $launchArguments -kind $kind
        }
        return
    }
    if ($leafName -ieq "pythonw.exe") {
        if ($preferConsoleMode) {
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath (Join-Path $dirPath "python.exe") -arguments $launchArguments -kind ($kind + " -> python.exe")
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath $trimmedPath -arguments $launchArguments -kind $kind
        }
        else {
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath $trimmedPath -arguments $launchArguments -kind $kind
            Add-PythonCandidate -candidates $candidates -seen $seen -filePath (Join-Path $dirPath "python.exe") -arguments $launchArguments -kind ($kind + " -> python.exe")
        }
        return
    }

    Add-PythonCandidate -candidates $candidates -seen $seen -filePath $trimmedPath -arguments $launchArguments -kind $kind
}

function Add-PortableDirectoryCandidates([System.Collections.Generic.List[object]]$candidates, [System.Collections.Generic.HashSet[string]]$seen) {
    foreach ($root in @(
        $launcherDir,
        (Join-Path $launcherDir "Python"),
        (Join-Path $launcherDir "runtime"),
        (Join-Path $launcherDir "_python"),
        (Join-Path $launcherDir ".venv"),
        (Join-Path $launcherDir "venv")
    )) {
        Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $root -kind ("portable: " + $root)
    }
}

function Add-CommonInstallCandidates([System.Collections.Generic.List[object]]$candidates, [System.Collections.Generic.HashSet[string]]$seen) {
    $versions = @("314", "313", "312", "311", "310")
    $roots = [System.Collections.Generic.List[string]]::new()

    if ($env:LOCALAPPDATA) {
        $roots.Add((Join-Path $env:LOCALAPPDATA "Python\bin")) | Out-Null
        foreach ($version in $versions) {
            $roots.Add((Join-Path $env:LOCALAPPDATA ("Programs\Python\Python" + $version))) | Out-Null
            $roots.Add((Join-Path $env:LOCALAPPDATA ("Python\pythoncore-3." + $version.Substring(1) + "-64"))) | Out-Null
        }
    }

    foreach ($envPath in @($env:ProgramFiles, $env:ProgramW6432)) {
        if ([string]::IsNullOrWhiteSpace($envPath)) {
            continue
        }
        foreach ($version in $versions) {
            $roots.Add((Join-Path $envPath ("Python" + $version))) | Out-Null
        }
    }

    foreach ($version in $versions) {
        $roots.Add(("C:\Python" + $version)) | Out-Null
    }

    foreach ($root in $roots) {
        Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $root -kind ("common: " + $root)
    }
}

function Add-RegistryCandidates([System.Collections.Generic.List[object]]$candidates, [System.Collections.Generic.HashSet[string]]$seen) {
    foreach ($registryBase in @(
        "HKCU:\Software\Python\PythonCore",
        "HKLM:\Software\Python\PythonCore",
        "HKLM:\Software\Wow6432Node\Python\PythonCore"
    )) {
        if (-not (Test-Path -LiteralPath $registryBase)) {
            continue
        }

        foreach ($versionKey in Get-ChildItem -LiteralPath $registryBase -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending) {
            $installKeyPath = $versionKey.PSPath + "\InstallPath"
            if (-not (Test-Path -LiteralPath $installKeyPath)) {
                continue
            }
            try {
                $installKey = Get-Item -LiteralPath $installKeyPath -ErrorAction Stop
                $defaultPath = $installKey.GetValue("")
                $executablePath = $installKey.GetValue("ExecutablePath")
                if (-not [string]::IsNullOrWhiteSpace($executablePath)) {
                    Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $executablePath -kind ("registry: " + $versionKey.PSChildName)
                }
                if (-not [string]::IsNullOrWhiteSpace($defaultPath)) {
                    Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $defaultPath -kind ("registry: " + $versionKey.PSChildName)
                }
            }
            catch {}
        }
    }
}

function Add-PyLauncherCandidates([System.Collections.Generic.List[object]]$candidates, [System.Collections.Generic.HashSet[string]]$seen) {
    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -eq $pyCommand) {
        return
    }

    foreach ($selector in @("-3.14", "-3.13", "-3.12", "-3.11", "-3.10", "-3")) {
        try {
            $resolvedPython = & py.exe $selector -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($resolvedPython)) {
                Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $resolvedPython -kind ("py.exe " + $selector)
                break
            }
        }
        catch {}
    }
}

function Add-PathCommandCandidates([System.Collections.Generic.List[object]]$candidates, [System.Collections.Generic.HashSet[string]]$seen) {
    foreach ($commandName in @("pythonw.exe", "python.exe")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $command.Source -kind ("PATH " + $commandName)
    }
}

function Get-NearbyInstallerBats {
    $found = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $roots = [System.Collections.Generic.List[string]]::new()
    $parentDir = Split-Path -Parent $launcherDir

    foreach ($root in @($launcherDir, $parentDir, (Split-Path -Parent $parentDir))) {
        if (-not [string]::IsNullOrWhiteSpace($root) -and (Test-Path -LiteralPath $root)) {
            $roots.Add($root) | Out-Null
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($parentDir) -and (Test-Path -LiteralPath $parentDir)) {
        foreach ($childDir in Get-ChildItem -LiteralPath $parentDir -Directory -ErrorAction SilentlyContinue) {
            $roots.Add($childDir.FullName) | Out-Null
        }
    }

    foreach ($root in $roots) {
        $candidate = Join-Path $root "Install_Codex_V4_Python_3_14.bat"
        if ($seen.Add($candidate) -and (Test-Path -LiteralPath $candidate)) {
            $found.Add($candidate) | Out-Null
        }
    }

    return $found
}

function Quote-ProcessArgument([string]$value) {
    if ($null -eq $value) {
        return '""'
    }
    return '"' + $value.Replace('"', '\"') + '"'
}

function Join-ProcessArgumentList([string[]]$arguments) {
    if ($null -eq $arguments -or $arguments.Count -eq 0) {
        return ""
    }
    return (($arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " ")
}

function Write-LaunchLogLine([string]$text) {
    $timestamp = [DateTime]::Now.ToString("yyyy-MM-dd HH:mm:ss.fff")
    Add-Content -LiteralPath $launchLogPath -Value ("[{0}] {1}" -f $timestamp, $text) -Encoding UTF8
}

function Resolve-PythonLauncher {
    $candidates = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

    if ($env:CODEX_V4_PYTHONW_EXE) {
        Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $env:CODEX_V4_PYTHONW_EXE -kind "env:CODEX_V4_PYTHONW_EXE"
    }

    if ($env:CODEX_V4_PYTHON_EXE) {
        Add-PythonFamilyCandidates -candidates $candidates -seen $seen -basePath $env:CODEX_V4_PYTHON_EXE -kind "env:CODEX_V4_PYTHON_EXE"
    }

    Add-PortableDirectoryCandidates -candidates $candidates -seen $seen
    Add-CommonInstallCandidates -candidates $candidates -seen $seen
    Add-RegistryCandidates -candidates $candidates -seen $seen
    Add-PyLauncherCandidates -candidates $candidates -seen $seen
    Add-PathCommandCandidates -candidates $candidates -seen $seen

    foreach ($candidate in $candidates) {
        if (-not (Test-UsablePythonLauncherPath $candidate.FilePath)) {
            continue
        }
        return $candidate
    }

    return $null
}

function Show-PythonMissingGuidance {
    Write-Host ""
    Write-Host "Python launcher not found for RE6 ARC Tool."
    Write-Host "Checked portable runtimes, common install folders, registry, py.exe, and PATH."
    Write-Host ""
    Write-Host "Fix options:"
    Write-Host "1. Install Python 3.10+ and rerun the BAT."
    Write-Host "2. Add the Python installation to PATH, then rerun the BAT."

    $installerBats = Get-NearbyInstallerBats
    if ($installerBats.Count -gt 0) {
        Write-Host "3. Local installer helper found:"
        foreach ($installerBat in $installerBats) {
            Write-Host ("   " + $installerBat)
        }
    }

    Write-Host ""
}

$pythonLauncher = Resolve-PythonLauncher
if ($null -eq $pythonLauncher) {
    Show-PythonMissingGuidance
    exit $pythonMissingExitCode
}

Write-Host "Launching RE6 ARC Tool..."
Write-Host "Python: $($pythonLauncher.FilePath)"
Write-Host "Mode:   $($pythonLauncher.Kind)"
Write-Host "Log:    $launchLogPath"

Push-Location $launcherDir
try {
    $pythonInvokeArguments = @("-X", "faulthandler", "-u") + @($pythonLauncher.Arguments)
    $previousFaulthandler = $env:PYTHONFAULTHANDLER
    $previousUnbuffered = $env:PYTHONUNBUFFERED
    $stdoutCapturePath = Join-Path $launcherDir "RE6_ARC_TOOL_STDOUT.tmp.log"
    $stderrCapturePath = Join-Path $launcherDir "RE6_ARC_TOOL_STDERR.tmp.log"
    $env:PYTHONFAULTHANDLER = "1"
    $env:PYTHONUNBUFFERED = "1"
    try {
        Write-LaunchLogLine "=== Launch Begin ==="
        Write-LaunchLogLine ("Python: " + $pythonLauncher.FilePath)
        Write-LaunchLogLine ("Resolver: " + $pythonLauncher.Kind)
        Write-LaunchLogLine ("Args: " + (Join-ProcessArgumentList $pythonInvokeArguments))
        Remove-Item -LiteralPath $stdoutCapturePath -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrCapturePath -ErrorAction SilentlyContinue
        $argumentLine = Join-ProcessArgumentList $pythonInvokeArguments
        $pythonLeafName = Split-Path -Leaf $pythonLauncher.FilePath
        $detachDirectWindowedLaunch = (-not $consoleCliMode) -and ($pythonLeafName -ieq "pythonw.exe")
        if ($detachDirectWindowedLaunch) {
            $process = Start-Process `
                -FilePath $pythonLauncher.FilePath `
                -ArgumentList $argumentLine `
                -WorkingDirectory $launcherDir `
                -PassThru
            Write-LaunchLogLine ("DetachedWindowedPID: " + $process.Id)
            Write-LaunchLogLine "ExitCode: 0 (windowed GUI detached)"
            Write-LaunchLogLine "=== Launch End ==="
            exit 0
        }
        $process = Start-Process `
            -FilePath $pythonLauncher.FilePath `
            -ArgumentList $argumentLine `
            -WorkingDirectory $launcherDir `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutCapturePath `
            -RedirectStandardError $stderrCapturePath
        if (Test-Path -LiteralPath $stdoutCapturePath) {
            $stdoutText = Get-Content -LiteralPath $stdoutCapturePath -Raw -ErrorAction SilentlyContinue
            if (-not [string]::IsNullOrWhiteSpace($stdoutText)) {
                Add-Content -LiteralPath $launchLogPath -Value $stdoutText -Encoding UTF8
                Write-Host $stdoutText
            }
        }
        if (Test-Path -LiteralPath $stderrCapturePath) {
            $stderrText = Get-Content -LiteralPath $stderrCapturePath -Raw -ErrorAction SilentlyContinue
            if (-not [string]::IsNullOrWhiteSpace($stderrText)) {
                Add-Content -LiteralPath $launchLogPath -Value $stderrText -Encoding UTF8
                [Console]::Error.WriteLine($stderrText)
            }
        }
        $exitCode = if ($null -ne $process) { [int]$process.ExitCode } else { 1 }
        Write-LaunchLogLine ("ExitCode: " + $exitCode)
        Write-LaunchLogLine "=== Launch End ==="
        exit $exitCode
    }
    catch {
        Write-LaunchLogLine ("Launcher exception: " + ($_ | Out-String).Trim())
        throw
    }
    finally {
        Remove-Item -LiteralPath $stdoutCapturePath -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stderrCapturePath -ErrorAction SilentlyContinue
        if ($null -eq $previousFaulthandler) {
            Remove-Item Env:PYTHONFAULTHANDLER -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONFAULTHANDLER = $previousFaulthandler
        }
        if ($null -eq $previousUnbuffered) {
            Remove-Item Env:PYTHONUNBUFFERED -ErrorAction SilentlyContinue
        }
        else {
            $env:PYTHONUNBUFFERED = $previousUnbuffered
        }
    }
}
finally {
    Pop-Location
}
