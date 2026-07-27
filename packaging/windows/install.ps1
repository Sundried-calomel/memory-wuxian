param([Parameter(Mandatory = $true)][string]$SkillRoot)

$ErrorActionPreference = "Stop"
$resolvedSkillRoot = [IO.Path]::GetFullPath($SkillRoot)
$skillsRoot = Split-Path -Parent $resolvedSkillRoot
$codexHome = Split-Path -Parent $skillsRoot
$installedUserProfile = Split-Path -Parent $codexHome
if ((Split-Path -Leaf $codexHome) -ne ".codex") {
  $installedUserProfile = $env:USERPROFILE
  $codexHome = Join-Path $installedUserProfile ".codex"
}
$defaultArchiveRoot = Join-Path $installedUserProfile "Documents\MemoryWuxianArchive"
$activeRootPointer = Join-Path $codexHome "memory-wuxian-active-root.txt"
$archiveRoot = $defaultArchiveRoot
if (Test-Path -LiteralPath $activeRootPointer) {
  $preservedArchiveRoot = (Get-Content -LiteralPath $activeRootPointer -Raw -Encoding UTF8).Trim()
  if ($preservedArchiveRoot) { $archiveRoot = [IO.Path]::GetFullPath($preservedArchiveRoot) }
}
$sessionsRoot = Join-Path $codexHome "sessions"

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

& $python (Join-Path $SkillRoot "scripts\memory_cli.py") `
  --root $archiveRoot `
  --config (Join-Path $SkillRoot "config.yaml") `
  init-node `
  --display-name $env:COMPUTERNAME | Out-Null
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian node initialization failed." }

& $python (Join-Path $SkillRoot "scripts\install_codex_autosync_windows.py") `
  --archive-root $archiveRoot `
  --skill-root $SkillRoot `
  --sessions-root $sessionsRoot `
  --python-executable $python `
  --codex-cli $codexCli `
  --load
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian background collector activation failed." }

& $python (Join-Path $SkillRoot "scripts\install_auto_update.py") `
  --skill-root $SkillRoot `
  --python-executable $python
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian automatic update activation failed." }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SkillRoot "scripts\install_dashboard_shortcut_windows.ps1") `
  -SkillRoot $SkillRoot `
  -ArchiveRoot $archiveRoot `
  -PythonExecutable $python
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian dashboard shortcut installation failed." }
