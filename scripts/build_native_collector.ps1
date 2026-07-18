param(
    [string]$CargoBin = "cargo.exe",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$SkillRoot = Split-Path -Parent $PSScriptRoot
if (-not $Output) {
    $Output = Join-Path $SkillRoot "bin\memory-wuxian-collector.exe"
}

$Manifest = Join-Path $SkillRoot "native-collector\Cargo.toml"
$VsDevCmd = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
if (Test-Path -LiteralPath $VsDevCmd) {
    $BuildCommand = 'call "' + $VsDevCmd + '" -arch=x64 && "' + $CargoBin + '" build --locked --release --manifest-path "' + $Manifest + '"'
    & cmd.exe /d /c $BuildCommand
} else {
    & $CargoBin build --locked --release --manifest-path $Manifest
}
if ($LASTEXITCODE -ne 0) {
    throw "Cargo build failed with exit code $LASTEXITCODE"
}

$Built = Join-Path $SkillRoot "native-collector\target\release\memory-wuxian-collector.exe"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
Copy-Item -Force -LiteralPath $Built -Destination $Output
Write-Output (Resolve-Path -LiteralPath $Output)
