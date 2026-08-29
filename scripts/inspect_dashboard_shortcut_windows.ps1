param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

function Get-Sha256([string]$File) {
    $stream = [IO.File]::OpenRead($File)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
        } finally {
            $sha256.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

$resolved = [IO.Path]::GetFullPath($Path)
if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
    [ordered]@{ exists = $false; path = $resolved } | ConvertTo-Json
    return
}

$inspectionPath = Join-Path (Split-Path -Parent $resolved) (
    ".memory-wuxian-inspect-" + [Guid]::NewGuid().ToString("N") + ".lnk"
)
try {
    [IO.File]::Copy($resolved, $inspectionPath, $false)
    $sourceSha256 = Get-Sha256 $resolved
    $projectionSha256 = Get-Sha256 $inspectionPath
    if ($projectionSha256 -ne $sourceSha256) {
        throw "Shortcut inspection projection hash mismatch."
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($inspectionPath)
    $target = [string]$shortcut.TargetPath
    $workingDirectory = [string]$shortcut.WorkingDirectory
    $icon = [string]$shortcut.IconLocation
    $arguments = [string]$shortcut.Arguments
    $targetExists = [bool]($target -and (Test-Path -LiteralPath $target -PathType Leaf))
    [ordered]@{
        exists = $true
        path = $resolved
        inspection_mode = "hash-equal-ascii-projection"
        source_sha256 = $sourceSha256
        projection_sha256 = $projectionSha256
        target = $target
        working_directory = $workingDirectory
        icon = $icon
        arguments = $arguments
        target_exists = $targetExists
    } | ConvertTo-Json
} finally {
    [IO.File]::Delete($inspectionPath)
}
