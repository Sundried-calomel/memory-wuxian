param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$resolved = [IO.Path]::GetFullPath($Path)
if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
    [ordered]@{ exists = $false; path = $resolved } | ConvertTo-Json
    exit 0
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($resolved)
[ordered]@{
    exists = $true
    path = $resolved
    target = $shortcut.TargetPath
    working_directory = $shortcut.WorkingDirectory
    icon = $shortcut.IconLocation
    arguments = $shortcut.Arguments
    target_exists = Test-Path -LiteralPath $shortcut.TargetPath -PathType Leaf
} | ConvertTo-Json
