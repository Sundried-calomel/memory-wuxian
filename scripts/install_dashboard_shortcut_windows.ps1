param(
    [Parameter(Mandatory = $true)][string]$SkillRoot,
    [Parameter(Mandatory = $true)][string]$ArchiveRoot,
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [string]$Desktop = "",
    [string]$ShortcutName = "",
    [string]$DiagnosticPath = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

function Limit-DiagnosticText([string]$Value) {
    if ($null -eq $Value) { return $null }
    if ($Value.Length -le 2048) { return $Value }
    return $Value.Substring(0, 2048)
}

function Write-ShortcutDiagnostic($Checks) {
    if (-not $DiagnosticPath) { return }
    $parent = Split-Path -Parent $DiagnosticPath
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $document = [ordered]@{
        schema_version = 1
        error_code = "dashboard-shortcut-activation-mismatch"
        safe_message = "Dashboard shortcut activation verification failed."
        source = [ordered]@{
            file = "install_dashboard_shortcut_windows.ps1"
            line = $null
            function = "installed-shortcut-verification"
        }
        checks = $Checks
    }
    $json = $document | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($DiagnosticPath, "$json`n", [Text.UTF8Encoding]::new($false))
}
if (-not $ShortcutName) {
    $ShortcutName = (
        "Memory" +
        [char]0x65E0 + [char]0x9650 +
        [char]0x72B6 + [char]0x6001 + [char]0x53F0 +
        ".lnk"
    )
}
if ([IO.Path]::GetFileName($ShortcutName) -ne $ShortcutName -or [IO.Path]::GetExtension($ShortcutName) -ne ".lnk") {
    throw "ShortcutName must be a .lnk leaf name."
}
$shortcutDescription = (
    "Memory" +
    [char]0x65E0 + [char]0x9650 +
    [char]0x672C + [char]0x5730 +
    [char]0x72B6 + [char]0x6001 + [char]0x53F0
)
if (-not $Desktop) { $Desktop = [Environment]::GetFolderPath("Desktop") }
if (-not $Desktop) { throw "Windows desktop directory was not found." }

$shortcutPath = Join-Path $Desktop $ShortcutName
if ($Uninstall) {
    [IO.File]::Delete($shortcutPath)
    [ordered]@{
        status = "removed"
        shortcut = $shortcutPath
    } | ConvertTo-Json
    exit 0
}

$python = [IO.Path]::GetFullPath($PythonExecutable)
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }

$skill = [IO.Path]::GetFullPath($SkillRoot)
$archive = [IO.Path]::GetFullPath($ArchiveRoot)
$launcher = Join-Path $skill "bin\memory-wuxian-dashboard-launcher.exe"
$icon = Join-Path $skill "assets\memory-wuxian.ico"
foreach ($required in @($pythonw, $launcher, $icon)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Dashboard shortcut dependency does not exist: $required"
    }
}

$skillsRoot = Split-Path -Parent $skill
$codexHome = Split-Path -Parent $skillsRoot
if ((Split-Path -Leaf $codexHome) -ne ".codex") {
    throw "SkillRoot must be installed under .codex\skills."
}
$launcherConfig = Join-Path $codexHome "memory-wuxian-dashboard-launcher.json"
$launcherConfigTemporary = "$launcherConfig.tmp"
$launcherSettings = [ordered]@{
    schema_version = 1
    python_executable = $pythonw
    archive_root = $archive
} | ConvertTo-Json
[IO.File]::WriteAllText($launcherConfigTemporary, $launcherSettings + "`n", [Text.UTF8Encoding]::new($false))
$launcherConfigBackup = "$launcherConfig.bak"
if (Test-Path -LiteralPath $launcherConfig) {
    [IO.File]::Replace($launcherConfigTemporary, $launcherConfig, $launcherConfigBackup)
    [IO.File]::Delete($launcherConfigBackup)
} else {
    [IO.File]::Move($launcherConfigTemporary, $launcherConfig)
}

New-Item -ItemType Directory -Force -Path $Desktop | Out-Null
$temporaryPath = Join-Path $Desktop ("." + [IO.Path]::GetRandomFileName() + ".lnk")
$backupPath = Join-Path $Desktop ("." + [IO.Path]::GetRandomFileName() + ".bak")
$discardPath = Join-Path $Desktop ("." + [IO.Path]::GetRandomFileName() + ".discard")
$replacedExisting = $false
try {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($temporaryPath)
    $shortcut.TargetPath = $launcher
    $shortcut.Arguments = ""
    $shortcut.WorkingDirectory = $skill
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = $shortcutDescription
    $shortcut.WindowStyle = 1
    $shortcut.Save()

    $temporaryShortcut = $shell.CreateShortcut($temporaryPath)
    if (
        $temporaryShortcut.TargetPath -ne $launcher -or
        $temporaryShortcut.WorkingDirectory -ne $skill -or
        $temporaryShortcut.IconLocation -ne "$icon,0"
    ) {
        throw "Dashboard shortcut did not preserve the requested activation paths."
    }
    if (Test-Path -LiteralPath $shortcutPath) {
        [IO.File]::Replace($temporaryPath, $shortcutPath, $backupPath)
        $replacedExisting = $true
    } else {
        [IO.File]::Move($temporaryPath, $shortcutPath)
    }

    $installedShortcut = $shell.CreateShortcut($shortcutPath)
    $targetExists = [bool](Test-Path -LiteralPath $installedShortcut.TargetPath -PathType Leaf)
    $checks = @(
        [ordered]@{ id = "target"; passed = ($installedShortcut.TargetPath -eq $launcher); expected = (Limit-DiagnosticText $launcher); observed = (Limit-DiagnosticText $installedShortcut.TargetPath) }
        [ordered]@{ id = "working_directory"; passed = ($installedShortcut.WorkingDirectory -eq $skill); expected = (Limit-DiagnosticText $skill); observed = (Limit-DiagnosticText $installedShortcut.WorkingDirectory) }
        [ordered]@{ id = "icon"; passed = ($installedShortcut.IconLocation -eq "$icon,0"); expected = (Limit-DiagnosticText "$icon,0"); observed = (Limit-DiagnosticText $installedShortcut.IconLocation) }
        [ordered]@{ id = "target_exists"; passed = $targetExists; expected = $true; observed = $targetExists }
    )
    if (@($checks | Where-Object { -not $_.passed }).Count -gt 0) {
        Write-ShortcutDiagnostic $checks
        if ($replacedExisting -and (Test-Path -LiteralPath $backupPath)) {
            [IO.File]::Replace($backupPath, $shortcutPath, $discardPath)
        } else {
            [IO.File]::Delete($shortcutPath)
        }
        throw "Dashboard shortcut activation verification failed."
    }
} finally {
    [IO.File]::Delete($temporaryPath)
    [IO.File]::Delete($backupPath)
    [IO.File]::Delete($discardPath)
}

[ordered]@{
    status = "installed"
    shortcut = $shortcutPath
    target = $launcher
    arguments = ""
    launcher_config = $launcherConfig
    archive_root = $archive
} | ConvertTo-Json
