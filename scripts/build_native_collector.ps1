param(
    [string]$CargoBin = "cargo.exe",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$SkillRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $SkillRoot "bin"
}

$Manifest = Join-Path $SkillRoot "native-collector\Cargo.toml"
$VsDevCmd = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
if (Test-Path -LiteralPath $VsDevCmd) {
    $BuildCommand = 'call "' + $VsDevCmd + '" -arch=x64 && "' + $CargoBin + '" build --locked --release --bins --manifest-path "' + $Manifest + '"'
    & cmd.exe /d /c $BuildCommand
} else {
    & $CargoBin build --locked --release --bins --manifest-path $Manifest
}
if ($LASTEXITCODE -ne 0) {
    throw "Cargo build failed with exit code $LASTEXITCODE"
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
foreach ($Name in @("memory-wuxian-collector.exe", "memory-wuxian-envelope.exe")) {
    $Built = Join-Path $SkillRoot "native-collector\target\release\$Name"
    $Output = Join-Path $OutputDirectory $Name
    Copy-Item -Force -LiteralPath $Built -Destination $Output
    Write-Output (Resolve-Path -LiteralPath $Output)
}
