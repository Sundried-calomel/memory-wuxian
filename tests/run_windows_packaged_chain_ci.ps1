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

function Get-OptionalProperty($Value, [string]$Name) {
  if ($null -eq $Value) { return $null }
  $property = $Value.PSObject.Properties[$Name]
  if ($null -eq $property) { return $null }
  return $property.Value
}

function Get-SafePackageProjection($Value) {
  if ($null -eq $Value) { return $null }
  return [ordered]@{ version = (Get-OptionalProperty $Value "version"); sha256 = (Get-OptionalProperty $Value "sha256") }
}

function Get-SafeRequestProjection([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
  return [ordered]@{
    schema_version = (Get-OptionalProperty $value "schema_version")
    source_entrypoint = (Get-OptionalProperty $value "source_entrypoint")
    operation = (Get-OptionalProperty $value "operation")
    package = Get-SafePackageProjection (Get-OptionalProperty $value "package")
  }
}

function Get-SafeFailureProjection($Value) {
  if ($null -eq $Value) { return $null }
  $source = Get-OptionalProperty $Value "source"
  $rollback = Get-OptionalProperty $Value "rollback"
  $checks = @()
  foreach ($check in @(Get-OptionalProperty $Value "checks")) {
    $checks += [ordered]@{
      id = (Get-OptionalProperty $check "id")
      passed = (Get-OptionalProperty $check "passed")
      expected = (Get-OptionalProperty $check "expected")
      observed = (Get-OptionalProperty $check "observed")
    }
  }
  return [ordered]@{
    schema_version = (Get-OptionalProperty $Value "schema_version")
    recorded_at = (Get-OptionalProperty $Value "recorded_at")
    phase = (Get-OptionalProperty $Value "phase")
    operation = (Get-OptionalProperty $Value "operation")
    component = (Get-OptionalProperty $Value "component")
    resource_id = (Get-OptionalProperty $Value "resource_id")
    error_code = (Get-OptionalProperty $Value "error_code")
    exception_type = (Get-OptionalProperty $Value "exception_type")
    safe_message = (Get-OptionalProperty $Value "safe_message")
    source = if ($null -eq $source) { $null } else { [ordered]@{ file = (Get-OptionalProperty $source "file"); line = (Get-OptionalProperty $source "line"); function = (Get-OptionalProperty $source "function") } }
    package = Get-SafePackageProjection (Get-OptionalProperty $Value "package")
    checks = $checks
    rollback = if ($null -eq $rollback) { $null } else { [ordered]@{ phase = (Get-OptionalProperty $rollback "phase"); status = (Get-OptionalProperty $rollback "status"); error_count = (Get-OptionalProperty $rollback "error_count") } }
  }
}

function Get-SafeJournalProjection([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
  $mutations = @()
  foreach ($mutation in @(Get-OptionalProperty $value "mutations")) {
    $mutations += [ordered]@{
      name = (Get-OptionalProperty $mutation "name")
      resource_id = (Get-OptionalProperty $mutation "resource_id")
      status = (Get-OptionalProperty $mutation "status")
      apply_recorded = ($null -ne (Get-OptionalProperty $mutation "apply_evidence"))
      verify_recorded = ($null -ne (Get-OptionalProperty $mutation "verify_evidence"))
      commit_recorded = ($null -ne (Get-OptionalProperty $mutation "commit_evidence"))
      rollback_recorded = ($null -ne (Get-OptionalProperty $mutation "rollback_evidence"))
      rollback_verify_recorded = ($null -ne (Get-OptionalProperty $mutation "rollback_verify_evidence"))
    }
  }
  return [ordered]@{
    schema_version = (Get-OptionalProperty $value "schema_version")
    transaction_id = (Get-OptionalProperty $value "transaction_id")
    phase = (Get-OptionalProperty $value "phase")
    source_entrypoint = (Get-OptionalProperty $value "source_entrypoint")
    operation = (Get-OptionalProperty $value "operation")
    package = Get-SafePackageProjection (Get-OptionalProperty $value "package")
    manifest_sha256 = (Get-OptionalProperty $value "manifest_sha256")
    failure = Get-SafeFailureProjection (Get-OptionalProperty $value "failure")
    mutations = $mutations
  }
}

function Get-SafeBrokerProjection([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
  return [ordered]@{
    controller_sha256 = (Get-OptionalProperty $value "controller_sha256")
    manifest_sha256 = (Get-OptionalProperty $value "manifest_sha256")
    operation = (Get-OptionalProperty $value "operation")
    target_sid = (Get-OptionalProperty $value "target_sid")
    transaction_id = (Get-OptionalProperty $value "transaction_id")
    nonce_present = ($null -ne (Get-OptionalProperty $value "nonce"))
  }
}

function Assert-NoProhibitedEvidenceField($Value, [string]$Location = "$", [int]$Depth = 0) {
  $prohibited = @("transaction_token", "secret", "nonce", "password", "credential", "authorization", "raw_user_content", "conversation_content", "archive_content", "environment_dump", "unbounded_stdout", "unbounded_stderr", "traceback")
  if ($Depth -gt 32) { throw "Evidence nesting exceeds the closed depth limit at ${Location}." }
  if ($null -eq $Value -or $Value -is [string] -or $Value.GetType().IsPrimitive -or $Value -is [DateTime] -or $Value -is [DateTimeOffset]) { return }
  if ($Value -is [Collections.IDictionary]) {
    foreach ($key in $Value.Keys) {
      if ($prohibited -contains [string]$key) { throw "Prohibited evidence field at ${Location}.${key}." }
      Assert-NoProhibitedEvidenceField $Value[$key] "${Location}.${key}" ($Depth + 1)
    }
    return
  }
  if ($Value -is [Collections.IEnumerable]) {
    $index = 0
    foreach ($item in $Value) { Assert-NoProhibitedEvidenceField $item "${Location}[$index]" ($Depth + 1); $index += 1 }
    return
  }
  if ($Value -is [PSCustomObject]) {
    foreach ($property in $Value.PSObject.Properties) {
      if ($prohibited -contains $property.Name) { throw "Prohibited evidence field at ${Location}.$($property.Name)." }
      Assert-NoProhibitedEvidenceField $property.Value "${Location}.$($property.Name)" ($Depth + 1)
    }
    return
  }
  throw "Unsupported evidence value type at ${Location}: $($Value.GetType().FullName)."
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
    request_document = Get-SafeRequestProjection $request
    journal_document = Get-SafeJournalProjection $journal
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
    $request = Get-SafeRequestProjection (Join-Path $transactionRoot "request.json")
    $journal = Get-SafeJournalProjection (Join-Path $transactionRoot "journal.json")
    $broker = Get-SafeBrokerProjection (Join-Path $transactionRoot "broker-request.json")
    if ($null -ne $request) { Write-CanonicalJson (Join-Path $destination "request-evidence.json") $request }
    if ($null -ne $journal) { Write-CanonicalJson (Join-Path $destination "journal-evidence.json") $journal }
    if ($null -ne $broker) { Write-CanonicalJson (Join-Path $destination "broker-evidence.json") $broker }
    Assert-NoProhibitedEvidenceField ([ordered]@{ request = $request; journal = $journal; broker = $broker })
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
  $trackedFiles = & git -c core.quotePath=false -C $sourceRoot ls-files
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
  $receipt.error = [ordered]@{ code = "packaged-chain-rehearsal-failed"; exception_type = $_.Exception.GetType().Name }
  throw
} finally {
  Assert-NoProhibitedEvidenceField $receipt
  Write-CanonicalJson $receiptPath $receipt
  if (Test-Path -LiteralPath $workRoot) {
    & git -C $sourceRoot worktree remove --force (Join-Path $workRoot "v215") *> $null
  }
  Remove-EphemeralProductResources
}
