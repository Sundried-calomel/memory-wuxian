param(
  [Parameter(Mandatory = $true)][string]$SkillRoot,
  [Parameter(Mandatory = $true)][string]$CandidateRoot,
  [ValidateSet("inno", "manual", "auto-update")][string]$SourceEntrypoint = "manual"
)

$ErrorActionPreference = "Stop"
function Test-MemoryWuxianSkillRoot([string]$Candidate) {
  if (-not $Candidate) { return $false }
  $resolved = [IO.Path]::GetFullPath($Candidate)
  if (-not (Test-Path -LiteralPath $resolved -PathType Container)) { return $false }
  if ((Split-Path -Leaf $resolved) -ne "memory-wuxian") { return $false }
  $skills = Split-Path -Parent $resolved
  if ((Split-Path -Leaf $skills) -ne "skills") { return $false }
  $codex = Split-Path -Parent $skills
  if ((Split-Path -Leaf $codex) -ne ".codex") { return $false }
  return (Test-Path -LiteralPath (Join-Path $resolved "SKILL.md") -PathType Leaf)
}
function Test-MemoryWuxianCandidate([string]$Candidate) {
  if (-not $Candidate) { return $false }
  $resolved = [IO.Path]::GetFullPath($Candidate)
  foreach ($required in @("SKILL.md", "config.yaml", "bin\memory-wuxian-collector.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolved $required) -PathType Leaf)) { return $false }
  }
  return $true
}

# The package-provided path is authoritative when it is a complete installed
# Skill root. Sandboxed launchers can have a service SID whose ProfileList entry
# is not the interactive user's profile.
if (-not (Test-MemoryWuxianCandidate $CandidateRoot)) {
  throw "MemoryWuxian candidate Skill root is incomplete."
}
if ((Test-Path -LiteralPath $SkillRoot) -and -not (Test-MemoryWuxianSkillRoot $SkillRoot)) {
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $profileKey = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$currentSid"
  $profileImagePath = (Get-ItemProperty -LiteralPath $profileKey -Name ProfileImagePath).ProfileImagePath
  $realUserProfile = [Environment]::ExpandEnvironmentVariables($profileImagePath)
  $expectedSkillRoot = Join-Path $realUserProfile ".codex\skills\memory-wuxian"
  if (-not (Test-MemoryWuxianSkillRoot $expectedSkillRoot)) {
    throw "MemoryWuxian installed Skill root was not found."
  }
  $SkillRoot = $expectedSkillRoot
}
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
if (-not (Test-Path -LiteralPath $sessionsRoot -PathType Container)) {
  throw "Codex sessions root was not found."
}
$packageRuntimeRoot = Join-Path $CandidateRoot "runtime\windows"
$packagePython = Join-Path $packageRuntimeRoot "python\python.exe"
if (-not (Test-Path -LiteralPath $packagePython -PathType Leaf)) {
  throw "MemoryWuxian package does not contain its isolated Python runtime."
}
$runtimeParent = Join-Path $env:LOCALAPPDATA "MemoryWuxian\runtime"
$runtimeText = & $packagePython (Join-Path $CandidateRoot "scripts\install_windows_runtime.py") `
  activate `
  --bundle-root $packageRuntimeRoot `
  --target-parent $runtimeParent
if ($LASTEXITCODE -ne 0) { throw "MemoryWuxian isolated runtime activation failed." }
$runtimeOutput = $runtimeText | Out-String
$runtimeJsonStart = $runtimeOutput.IndexOf("{")
if ($runtimeJsonStart -lt 0) { throw "MemoryWuxian runtime activation returned no status document." }
$runtime = $runtimeOutput.Substring($runtimeJsonStart) | ConvertFrom-Json
if ($runtime.status -ne "ready") { throw "MemoryWuxian isolated runtime is not ready." }
$python = $runtime.python_executable
$runtimeBundleRoot = $runtime.runtime_root
$runtimeBundleId = $runtime.bundle_id

$bundledCodex = Join-Path $installedUserProfile ".codex\.sandbox-bin\codex.exe"
$codexCommand = Get-Command codex.exe -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $bundledCodex -PathType Leaf) {
  $codexCli = $bundledCodex
} elseif ($codexCommand) {
  $codexCli = $codexCommand.Source
} else {
  throw "Codex CLI executable was not found."
}
$transactionRoot = Join-Path $env:LOCALAPPDATA "MemoryWuxian\installer-transaction"
New-Item -ItemType Directory -Force -Path $transactionRoot | Out-Null
$manifestPath = Join-Path $transactionRoot "request.json"
$brokerRequestPath = Join-Path $transactionRoot "broker-request.json"
$nonceRoot = Join-Path $transactionRoot "nonces"
$controllerPath = Join-Path $CandidateRoot "scripts\install_windows_transaction.py"
$brokerPath = Join-Path $CandidateRoot "scripts\windows_installer_broker.py"
& $python (Join-Path $CandidateRoot "scripts\install_windows_transaction.py") `
  --prepare-only `
  --source-entrypoint $SourceEntrypoint `
  --candidate-root $CandidateRoot `
  --skill-root $SkillRoot `
  --archive-root $archiveRoot `
  --archive-pointer $activeRootPointer `
  --sessions-root $sessionsRoot `
  --python-executable $python `
  --runtime-bundle-root $runtimeBundleRoot `
  --runtime-bundle-id $runtimeBundleId `
  --codex-cli $codexCli `
  --manifest-output $manifestPath
$prepareExit = $LASTEXITCODE
if ($prepareExit -ne 0) {
  [Console]::Error.WriteLine("MemoryWuxian manifest preparation failed with exit code $prepareExit.")
  exit $prepareExit
}
& $python $brokerPath `
  --launch-manifest $manifestPath `
  --controller $controllerPath `
  --request-output $brokerRequestPath `
  --nonce-root $nonceRoot
$transactionExit = $LASTEXITCODE
if ($transactionExit -ne 0) {
  [Console]::Error.WriteLine("MemoryWuxian unified installer transaction failed with exit code $transactionExit.")
}
exit $transactionExit
