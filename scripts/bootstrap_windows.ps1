param(
    [switch]$InstallMissing,
    [string]$PythonPath = "",
    [string]$CodexCliPath = "",
    [string]$CollectorPath = "",
    [string]$SessionsRoot = "",
    [string]$AgentsPath = ""
)

$ErrorActionPreference = "Stop"
$SkillRoot = Split-Path -Parent $PSScriptRoot
$MinimumPython = [version]"3.9"

function Find-Python {
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) { return (Resolve-Path $PythonPath).Path }
    $candidates = @()
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $runtimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes"
    if (Test-Path -LiteralPath $runtimeRoot) {
        $candidates += Get-ChildItem -LiteralPath $runtimeRoot -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\dependencies\\python\\python\.exe$' } |
            Select-Object -ExpandProperty FullName
    }
    $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            $versionText = & $candidate -c "import platform; print(platform.python_version())"
            if ([version]$versionText -ge $MinimumPython) { return (Resolve-Path $candidate).Path }
        } catch {}
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget Python installation failed: $LASTEXITCODE" }
        return
    }
    $version = "3.12.10"
    $installer = Join-Path $env:TEMP "python-$version-amd64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$version/python-$version-amd64.exe" -OutFile $installer
    $process = Start-Process -FilePath $installer -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0"
    ) -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Python installer failed: $($process.ExitCode)" }
}

$python = Find-Python
if (-not $python -and $InstallMissing) {
    Install-Python
    $python = Find-Python
}

if (-not $CodexCliPath) {
    $bundledCodex = Join-Path $env:USERPROFILE ".codex\.sandbox-bin\codex.exe"
    $codexCommand = Get-Command codex.exe -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $bundledCodex) {
        $CodexCliPath = $bundledCodex
    } elseif ($codexCommand) {
        $CodexCliPath = $codexCommand.Source
    }
}
if (-not $CollectorPath) {
    $CollectorPath = Join-Path $SkillRoot "bin\memory-wuxian-collector.exe"
}

if (-not $SessionsRoot) { $SessionsRoot = Join-Path $env:USERPROFILE ".codex\sessions" }
$pythonVersion = if ($python) { & $python -c "import platform; print(platform.python_version())" } else { $null }
$checks = [ordered]@{
    platform = "windows"
    python = [ordered]@{
        ready = [bool]$python
        path = $python
        version = $pythonVersion
        minimum_version = $MinimumPython.ToString()
    }
    codex_cli = [ordered]@{
        ready = [bool]($CodexCliPath -and (Test-Path -LiteralPath $CodexCliPath))
        path = if ($CodexCliPath) { $CodexCliPath } else { $null }
    }
    collector = [ordered]@{
        ready = Test-Path -LiteralPath $CollectorPath
        path = $CollectorPath
    }
    sessions = [ordered]@{
        ready = Test-Path -LiteralPath $SessionsRoot
        path = $SessionsRoot
    }
}
$ready = $checks.python.ready -and $checks.codex_cli.ready -and $checks.collector.ready -and $checks.sessions.ready
if ($AgentsPath) {
    if (-not $python) { throw "Python is required to install Memory无限 AGENTS.md rules." }
    & $python (Join-Path $PSScriptRoot "install_agent_rules.py") --agents-file $AgentsPath
    if ($LASTEXITCODE -ne 0) { throw "AGENTS.md rules installation failed: $LASTEXITCODE" }
}
$result = [ordered]@{
    status = if ($ready) { "ready" } else { "missing-runtime" }
    ready = $ready
    checks = $checks
    build_tools_required = $false
    agents_rules = if ($AgentsPath) { (Resolve-Path -LiteralPath $AgentsPath).Path } else { $null }
}
$result | ConvertTo-Json -Depth 5
if (-not $ready) { exit 2 }
