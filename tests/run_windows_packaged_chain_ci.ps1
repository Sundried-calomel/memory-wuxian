param(
  [Parameter(Mandatory = $true)][string]$CandidateDir,
  [Parameter(Mandatory = $true)][string]$SourceRoot,
  [Parameter(Mandatory = $true)][string]$OutputRoot
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version Latest

if ($env:GITHUB_ACTIONS -ne "true" -or $env:RUNNER_ENVIRONMENT -ne "github-hosted") {
  throw "Packaged-chain rehearsal is restricted to a GitHub-hosted ephemeral runner."
}

$candidateDir = [IO.Path]::GetFullPath($CandidateDir)
$sourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$outputRoot = [IO.Path]::GetFullPath($OutputRoot)
$workRoot = Join-Path $env:RUNNER_TEMP "memory-wuxian-s09"
$skillRoot = Join-Path $env:USERPROFILE ".codex\skills\memory-wuxian"
$transactionRoot = Join-Path $env:LOCALAPPDATA "MemoryWuxian\installer-transaction"
$runtimeParent = Join-Path $env:LOCALAPPDATA "MemoryWuxian\runtime"
$pointer = Join-Path $env:USERPROFILE ".codex\memory-wuxian-active-root.txt"
$shortcut = Join-Path $env:USERPROFILE "Desktop\Memory无限状态台.lnk"
$taskNames = @("MemoryWuxianCodexSync", "MemoryWuxianMaintenance", "MemoryWuxianAutoUpdate")
$runValues = @("MemoryWuxianCodexSync", "MemoryWuxianAutoUpdate")
$receiptPath = Join-Path $outputRoot "packaged-chain-receipt.json"
$receipt = [ordered]@{
  schema_version = 1
  lane = "packaged-production-chain"
  runner = [ordered]@{
    github_actions = $env:GITHUB_ACTIONS
    runner_environment = $env:RUNNER_ENVIRONMENT
    image = $env:ImageOS
  }
  status = "failed"
  installer = $null
  clean_install = $null
  repeat_install = $null
  namespaced_rollback = $null
  uninstall = $null
  error = $null
}

function Write-CanonicalJson([string]$Path, $Value) {
  $json = $Value | ConvertTo-Json -Depth 20 -Compress
  [IO.File]::WriteAllText($Path, "$json`n", [Text.UTF8Encoding]::new($false))
}

function Get-FileEvidence([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return [ordered]@{ path = $Path; exists = $false; sha256 = $null }
  }
  return [ordered]@{
    path = $Path
    exists = $true
    sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}

function Get-TaskEvidence([string]$Name) {
  $query = & "$env:SystemRoot\System32\schtasks.exe" /Query /TN $Name /XML 2>$null
  return [ordered]@{ exists = ($LASTEXITCODE -eq 0); xml_sha256 = if ($LASTEXITCODE -eq 0) {
    $bytes = [Text.Encoding]::Unicode.GetBytes(($query -join "`n"))
    [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
  } else { $null } }
}

function Get-RunValueEvidence([string]$Name) {
  & "$env:SystemRoot\System32\reg.exe" QUERY "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /V $Name *> $null
  return [ordered]@{ exists = ($LASTEXITCODE -eq 0) }
}

function Get-ProductEvidence {
  $tasks = [ordered]@{}
  foreach ($name in $taskNames) { $tasks[$name] = Get-TaskEvidence $name }
  $values = [ordered]@{}
  foreach ($name in $runValues) { $values[$name] = Get-RunValueEvidence $name }
  $request = Join-Path $transactionRoot "request.json"
  $journal = Join-Path $transactionRoot "journal.json"
  return [ordered]@{
    skill = Get-FileEvidence (Join-Path $skillRoot "SKILL.md")
    request = Get-FileEvidence $request
    journal = Get-FileEvidence $journal
    request_document = if (Test-Path -LiteralPath $request) { Get-Content -LiteralPath $request -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    journal_document = if (Test-Path -LiteralPath $journal) { Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
    tasks = $tasks
    run_values = $values
    shortcut = Get-FileEvidence $shortcut
    archive_pointer = Get-FileEvidence $pointer
  }
}

function Invoke-Setup([string]$Installer, [string]$LogPath) {
  $process = Start-Process -FilePath $Installer -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SOURCEENTRYPOINT=inno", "/LOG=$LogPath"
  ) -Wait -PassThru
  return $process.ExitCode
}

function Copy-TransactionEvidence([string]$Name) {
  $destination = Join-Path $outputRoot $Name
  New-Item -ItemType Directory -Path $destination -Force | Out-Null
  if (Test-Path -LiteralPath $transactionRoot) {
    Copy-Item -Path (Join-Path $transactionRoot "*") -Destination $destination -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Remove-EphemeralProductResources {
  foreach ($name in $taskNames) {
    & "$env:SystemRoot\System32\schtasks.exe" /End /TN $name *> $null
    & "$env:SystemRoot\System32\schtasks.exe" /Delete /TN $name /F *> $null
  }
  foreach ($name in $runValues) {
    & "$env:SystemRoot\System32\reg.exe" DELETE "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /V $name /F *> $null
  }
  Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $skillRoot -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $runtimeParent -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $transactionRoot -Recurse -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
Remove-EphemeralProductResources
New-Item -ItemType Directory -Path (Join-Path $env:USERPROFILE ".codex\sessions") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $env:USERPROFILE ".codex\.sandbox-bin") -Force | Out-Null
Copy-Item -LiteralPath "$env:SystemRoot\System32\where.exe" -Destination (Join-Path $env:USERPROFILE ".codex\.sandbox-bin\codex.exe") -Force

try {
  $provenancePath = Join-Path $candidateDir "candidate-provenance.json"
  $provenance = Get-Content -LiteralPath $provenancePath -Raw -Encoding UTF8 | ConvertFrom-Json
  $installer = Join-Path $candidateDir $provenance.installer_name
  $actualHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actualHash -ne $provenance.installer_sha256) { throw "Candidate installer hash drift." }
  if ($provenance.source_commit -ne $env:GITHUB_SHA) { throw "Candidate source commit drift." }
  $receipt.installer = [ordered]@{ name = $provenance.installer_name; sha256 = $actualHash; source_commit = $provenance.source_commit }

  $cleanLog = Join-Path $outputRoot "clean-install.log"
  $cleanExit = Invoke-Setup $installer $cleanLog
  $cleanEvidence = Get-ProductEvidence
  Copy-TransactionEvidence "clean-transaction"
  $receipt.clean_install = [ordered]@{ exit_code = $cleanExit; evidence = $cleanEvidence }
  if ($cleanExit -ne 0 -or -not $cleanEvidence.skill.exists -or $cleanEvidence.request_document.source_entrypoint -ne "inno" -or $cleanEvidence.journal_document.phase -ne "committed") {
    throw "Clean packaged-chain installation did not commit through the Inno entrypoint."
  }

  $repeatLog = Join-Path $outputRoot "repeat-install.log"
  $repeatExit = Invoke-Setup $installer $repeatLog
  $repeatEvidence = Get-ProductEvidence
  Copy-TransactionEvidence "repeat-transaction"
  $receipt.repeat_install = [ordered]@{ exit_code = $repeatExit; evidence = $repeatEvidence }
  if ($repeatExit -ne 0 -or -not $repeatEvidence.skill.exists -or $repeatEvidence.request_document.source_entrypoint -ne "inno" -or $repeatEvidence.journal_document.phase -ne "committed") {
    throw "Repeat packaged-chain installation did not commit idempotently."
  }

  $directCandidate = Join-Path $workRoot "direct-candidate"
  New-Item -ItemType Directory -Path $directCandidate -Force | Out-Null
  $trackedFiles = & git -C $sourceRoot ls-files
  if ($LASTEXITCODE -ne 0) { throw "Unable to enumerate the tracked candidate projection." }
  foreach ($relative in $trackedFiles) {
    $source = Join-Path $sourceRoot $relative
    $destination = Join-Path $directCandidate $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
  }
  Copy-Item -LiteralPath (Join-Path $sourceRoot "bin") -Destination $directCandidate -Recurse -Force
  New-Item -ItemType Directory -Path (Join-Path $directCandidate "runtime") -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $sourceRoot "runtime\windows") -Destination (Join-Path $directCandidate "runtime") -Recurse -Force
  Copy-Item -LiteralPath (Join-Path $sourceRoot "config.yaml") -Destination (Join-Path $directCandidate "config.defaults.yaml") -Force
  $v215 = Join-Path $workRoot "v215"
  & git -C $sourceRoot worktree add --detach $v215 v2.15.0
  if ($LASTEXITCODE -ne 0) { throw "Unable to materialize the v2.15.0 fixture." }
  $runtimeRoot = [IO.Path]::GetFullPath((Join-Path $sourceRoot "runtime\windows"))
  $runtimePython = Join-Path $runtimeRoot "python\python.exe"
  $directReceipt = Join-Path $outputRoot "namespaced-rollback-receipt.json"
  & $runtimePython (Join-Path $sourceRoot "scripts\run_windows_installer_rehearsal.py") `
    --candidate-root $directCandidate `
    --runtime-bundle-root $runtimeRoot `
    --python-executable $runtimePython `
    --codex-cli (Join-Path $env:USERPROFILE ".codex\.sandbox-bin\codex.exe") `
    --v215-source $v215 `
    --work-root (Join-Path $workRoot "namespaced") `
    --output $directReceipt
  if ($LASTEXITCODE -ne 0) { throw "Namespaced rollback rehearsal failed." }
  $direct = Get-Content -LiteralPath $directReceipt -Raw -Encoding UTF8 | ConvertFrom-Json
  $receipt.namespaced_rollback = [ordered]@{
    lane = "namespaced-direct-controller-rollback"
    packaged_chain_claim = $false
    status = $direct.status
    rollback_exact = $direct.rollback_exact
    production_resources_unchanged = $direct.production_resources_unchanged
    receipt_sha256 = (Get-FileHash -LiteralPath $directReceipt -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  if ($direct.status -ne "passed" -or -not $direct.rollback_exact -or -not $direct.production_resources_unchanged) {
    throw "Namespaced rollback evidence is incomplete."
  }

  $uninstaller = Get-ChildItem -LiteralPath $skillRoot -Filter "unins*.exe" | Select-Object -First 1
  if (-not $uninstaller) { throw "Packaged installation did not register an uninstaller." }
  $uninstallProcess = Start-Process -FilePath $uninstaller.FullName -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru
  $uninstallEvidence = Get-ProductEvidence
  $receipt.uninstall = [ordered]@{ exit_code = $uninstallProcess.ExitCode; evidence = $uninstallEvidence }
  $tasksRemain = @($taskNames | Where-Object { $uninstallEvidence.tasks[$_].exists }).Count -gt 0
  $valuesRemain = @($runValues | Where-Object { $uninstallEvidence.run_values[$_].exists }).Count -gt 0
  if ($uninstallProcess.ExitCode -ne 0 -or $tasksRemain -or $valuesRemain -or $uninstallEvidence.shortcut.exists) {
    throw "Packaged uninstaller did not remove all owned runtime entrypoints."
  }

  $receipt.status = "passed"
} catch {
  $receipt.error = $_.Exception.Message
  throw
} finally {
  Write-CanonicalJson $receiptPath $receipt
  if (Test-Path -LiteralPath $workRoot) {
    & git -C $sourceRoot worktree remove --force (Join-Path $workRoot "v215") *> $null
  }
  Remove-EphemeralProductResources
}
