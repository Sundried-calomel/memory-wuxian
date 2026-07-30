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
$MinimumPython = [version]"3.14"
$MaximumPython = [version]"3.15"

function Find-Python {
    $candidates = @()
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) {
        $candidates += (Resolve-Path $PythonPath).Path
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $runtimeRoot = Join-Path $env:USERPROFILE ".cache\codex-runtimes"
    if (Test-Path -LiteralPath $runtimeRoot) {
        $candidates += Get-ChildItem -LiteralPath $runtimeRoot -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\dependencies\\python\\python\.exe$' } |
            Select-Object -ExpandProperty FullName
    }
    $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            $versionText = & $candidate -c "import platform; print(platform.python_version())"
            $version = [version]$versionText
            if ($version -ge $MinimumPython -and $version -lt $MaximumPython) {
                return (Resolve-Path $candidate).Path
            }
        } catch {}
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        & $winget.Source install --id Python.Python.3.14 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "winget Python installation failed: $LASTEXITCODE" }
        return
    }
    $version = "3.14.0"
    $installer = Join-Path $env:TEMP "python-$version-amd64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$version/python-$version-amd64.exe" -OutFile $installer
    $process = Start-Process -FilePath $installer -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0"
    ) -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Python installer failed: $($process.ExitCode)" }
}

function Get-PyYamlStatus([string]$Executable) {
    $probe = @'
import json
import re
try:
    import yaml
    from importlib.metadata import version
    installed = version('PyYAML')
    importable = getattr(yaml, '__version__', None) == installed
    stable_six = re.fullmatch(r'6\.\d+(?:\.\d+)?', installed) is not None
    print(json.dumps({'ready': importable and stable_six, 'version': installed}))
except Exception:
    print(json.dumps({'ready': False, 'version': None}))
'@
    return (& $Executable -c $probe | ConvertFrom-Json)
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
$dashboardWindowReady = $false
$yamlReady = $false
$yamlVersion = $null
if ($python) {
    $yamlInfo = Get-PyYamlStatus $python
    $yamlReady = [bool]$yamlInfo.ready
    $yamlVersion = $yamlInfo.version
    if (-not $yamlReady -and $InstallMissing) {
        & $python -m pip install --disable-pip-version-check "PyYAML>=6.0,<7"
        if ($LASTEXITCODE -ne 0) { throw "core YAML dependency installation failed: $LASTEXITCODE" }
        $yamlInfo = Get-PyYamlStatus $python
        $yamlReady = [bool]$yamlInfo.ready
        $yamlVersion = $yamlInfo.version
    }
    $dashboardWindowReady = (& $python -c "import importlib.util; print('1' if importlib.util.find_spec('webview') else '0')") -eq "1"
    if (-not $dashboardWindowReady -and $InstallMissing) {
        & $python -m pip install "pywebview>=6.2,<7" "psutil>=7,<8"
        if ($LASTEXITCODE -ne 0) { throw "dashboard dependency installation failed: $LASTEXITCODE" }
        $dashboardWindowReady = (& $python -c "import importlib.util; print('1' if importlib.util.find_spec('webview') else '0')") -eq "1"
    }
}
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
    dashboard_window = [ordered]@{
        ready = $dashboardWindowReady
        runtime = "Microsoft Edge WebView2"
        python_package = "pywebview"
    }
    yaml = [ordered]@{
        ready = $yamlReady
        python_package = "PyYAML"
        version = $yamlVersion
        version_range = ">=6.0,<7"
    }
}
$ready = $checks.python.ready -and $checks.codex_cli.ready -and $checks.collector.ready -and $checks.sessions.ready -and $checks.yaml.ready
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
