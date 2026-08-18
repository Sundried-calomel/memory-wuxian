param([Parameter(Mandatory = $true)][string]$SkillRoot)

$resolvedSkillRoot = [IO.Path]::GetFullPath($SkillRoot)
$skillsRoot = Split-Path -Parent $resolvedSkillRoot
$codexHome = Split-Path -Parent $skillsRoot
$installedUserProfile = Split-Path -Parent $codexHome
if ((Split-Path -Leaf $codexHome) -ne ".codex") {
  $installedUserProfile = $env:USERPROFILE
  $codexHome = Join-Path $installedUserProfile ".codex"
}
$archiveRoot = Join-Path $installedUserProfile "Documents\MemoryWuxianArchive"
$activeRootPointer = Join-Path $codexHome "memory-wuxian-active-root.txt"
if (Test-Path -LiteralPath $activeRootPointer) {
  $preservedArchiveRoot = (Get-Content -LiteralPath $activeRootPointer -Raw -Encoding UTF8).Trim()
  if ($preservedArchiveRoot) { $archiveRoot = [IO.Path]::GetFullPath($preservedArchiveRoot) }
}

$systemSchtasks = Join-Path $env:SystemRoot "System32\schtasks.exe"
& $systemSchtasks /End /TN MemoryWuxianCloudSync 2>$null
& $systemSchtasks /Delete /TN MemoryWuxianCloudSync /F 2>$null

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if ($python -and (Test-Path (Join-Path $SkillRoot "scripts\install_auto_update.py"))) {
  & $python.Source (Join-Path $SkillRoot "scripts\install_auto_update.py") --skill-root $SkillRoot --uninstall
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if ($python) {
  & $python.Source (Join-Path $SkillRoot "scripts\install_codex_autosync_windows.py") --archive-root $archiveRoot --uninstall
} else {
  schtasks.exe /End /TN MemoryWuxianCodexSync 2>$null
  schtasks.exe /Delete /TN MemoryWuxianCodexSync /F 2>$null
  # Cleanup only: current installers never create this legacy startup owner.
  reg.exe DELETE "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /V MemoryWuxianCodexSync /F 2>$null
}

$shortcutInstaller = Join-Path $SkillRoot "scripts\install_dashboard_shortcut_windows.ps1"
if (Test-Path -LiteralPath $shortcutInstaller) {
  $shortcutPython = if ($python) { $python.Source } else { Join-Path $env:USERPROFILE "python.exe" }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $shortcutInstaller `
    -SkillRoot $SkillRoot `
    -ArchiveRoot $archiveRoot `
    -PythonExecutable $shortcutPython `
    -Uninstall
}
[IO.File]::Delete((Join-Path $codexHome "memory-wuxian-dashboard-launcher.json"))
