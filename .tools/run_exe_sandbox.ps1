param(
    [string] $ExePath = "",
    [switch] $Build,
    [switch] $NoLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path
}

function Assert-UnderRoot {
    param(
        [Parameter(Mandatory = $true)] [string] $Path,
        [Parameter(Mandatory = $true)] [string] $Root
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not $resolvedRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $resolvedRoot += [System.IO.Path]::DirectorySeparatorChar
    }
    if (-not $resolvedPath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch a path outside the sandbox output root: $resolvedPath"
    }
}

function Write-TextFile {
    param(
        [Parameter(Mandatory = $true)] [string] $Path,
        [Parameter(Mandatory = $true)] [string] $Value
    )
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Value, $utf8NoBom)
}

$repoRoot = Resolve-RepoRoot
if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $repoRoot ".build\windows\TensaLauncher.exe"
}

if ($Build -or -not (Test-Path -LiteralPath $ExePath)) {
    Push-Location $repoRoot
    try {
        & python ".tools\build.py" --target windows
        if ($LASTEXITCODE -ne 0) {
            throw "Windows executable build failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "TensaLauncher executable was not found: $ExePath"
}

$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path
$sandboxRoot = Join-Path $repoRoot ".build\sandbox-exe"
$payloadDir = Join-Path $sandboxRoot "TensaLauncher"
$wsbPath = Join-Path $sandboxRoot "TensaLauncher.wsb"

New-Item -ItemType Directory -Force -Path $sandboxRoot | Out-Null
Assert-UnderRoot -Path $payloadDir -Root $sandboxRoot
if (Test-Path -LiteralPath $payloadDir) {
    Remove-Item -LiteralPath $payloadDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null

Copy-Item -LiteralPath $resolvedExe -Destination (Join-Path $payloadDir "TensaLauncher.exe") -Force

$runnerScript = @'
$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "TensaLauncher.exe"
$workDir = Join-Path $env:USERPROFILE "Desktop\TensaLauncherRun"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
$target = Join-Path $workDir "TensaLauncher.exe"
Copy-Item -LiteralPath $source -Destination $target -Force
Unblock-File -LiteralPath $target -ErrorAction SilentlyContinue
$env:TENSALAUNCHER_CLEAR_LOG_ON_START = "1"
Start-Process -FilePath $target -WorkingDirectory $workDir
'@
Write-TextFile -Path (Join-Path $payloadDir "Run-TensaLauncher.ps1") -Value $runnerScript

$runnerCmd = @'
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Run-TensaLauncher.ps1"
'@
Write-TextFile -Path (Join-Path $payloadDir "Run.cmd") -Value $runnerCmd

$escapedPayloadDir = [System.Security.SecurityElement]::Escape($payloadDir)
$wsb = @"
<Configuration>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$escapedPayloadDir</HostFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\Desktop\TensaLauncher\Run-TensaLauncher.ps1"</Command>
  </LogonCommand>
</Configuration>
"@
Write-TextFile -Path $wsbPath -Value $wsb

Write-Host "Prepared Windows Sandbox payload:"
Write-Host "  $payloadDir"
Write-Host "Sandbox file:"
Write-Host "  $wsbPath"

if (-not $NoLaunch) {
    Start-Process -FilePath $wsbPath
}
