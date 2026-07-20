param([Parameter(Mandatory = $true)][string]$SkillRoot)

$ErrorActionPreference = "Stop"
$archiveRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "MemoryWuxianArchive"
$sessionsRoot = Join-Path $env:USERPROFILE ".codex\sessions"

$bootstrapText = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SkillRoot "scripts\bootstrap_windows.ps1") -InstallMissing
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian runtime bootstrap failed." }
$bootstrapOutput = $bootstrapText | Out-String
$jsonStart = $bootstrapOutput.IndexOf("{")
if ($jsonStart -lt 0) { throw "MemoryWuxian bootstrap did not return a status document." }
$bootstrap = $bootstrapOutput.Substring($jsonStart) | ConvertFrom-Json
if (-not $bootstrap.ready) { throw "MemoryWuxian runtime requirements are incomplete." }

$python = $bootstrap.checks.python.path
$codexCli = $bootstrap.checks.codex_cli.path
New-Item -ItemType Directory -Force -Path $archiveRoot, $sessionsRoot | Out-Null
& $python (Join-Path $SkillRoot "scripts\memory_cli.py") --root $archiveRoot --config (Join-Path $SkillRoot "config.yaml") init | Out-Null
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian archive initialization failed." }

& $python (Join-Path $SkillRoot "scripts\install_codex_autosync_windows.py") `
  --archive-root $archiveRoot `
  --skill-root $SkillRoot `
  --sessions-root $sessionsRoot `
  --python-executable $python `
  --codex-cli $codexCli `
  --load
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian background collector activation failed." }
