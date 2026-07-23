param([Parameter(Mandatory = $true)][string]$SkillRoot)

$systemSchtasks = Join-Path $env:SystemRoot "System32\schtasks.exe"
& $systemSchtasks /End /TN MemoryWuxianCloudSync 2>$null
& $systemSchtasks /Delete /TN MemoryWuxianCloudSync /F 2>$null

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if ($python -and (Test-Path (Join-Path $SkillRoot "scripts\install_auto_update.py"))) {
  & $python.Source (Join-Path $SkillRoot "scripts\install_auto_update.py") --skill-root $SkillRoot --uninstall
}

$archiveRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "MemoryWuxianArchive"
$python = Get-Command python.exe -ErrorAction SilentlyContinue
if ($python) {
  & $python.Source (Join-Path $SkillRoot "scripts\install_codex_autosync_windows.py") --archive-root $archiveRoot --uninstall
} else {
  schtasks.exe /End /TN MemoryWuxianCodexSync 2>$null
  schtasks.exe /Delete /TN MemoryWuxianCodexSync /F 2>$null
  reg.exe DELETE "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /V MemoryWuxianCodexSync /F 2>$null
}
