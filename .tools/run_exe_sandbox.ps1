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

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-AsAdmin {
    $argsText = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $argsText -Verb RunAs -Wait -PassThru
    exit $process.ExitCode
}

function Start-ProcessSafe {
    param(
        [string] $FilePath,
        [string[]] $Arguments = @(),
        [int] $TimeoutSeconds = 5
    )

    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru -WindowStyle Hidden -ErrorAction Stop
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            return $false
        }
        return $process.ExitCode -eq 0
    } catch {
        return $false
    }
}

function Disable-SandboxApplicationControl {
    $ciPolicyArgs = @(
        "add",
        "HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy",
        "/v",
        "VerifiedAndReputablePolicyState",
        "/t",
        "REG_DWORD",
        "/d",
        "0",
        "/f"
    )
    Start-ProcessSafe -FilePath "reg.exe" -Arguments $ciPolicyArgs -TimeoutSeconds 5 | Out-Null

    $machineSmartScreenArgs = @(
        "add",
        "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer",
        "/v",
        "SmartScreenEnabled",
        "/t",
        "REG_SZ",
        "/d",
        "Off",
        "/f"
    )
    Start-ProcessSafe -FilePath "reg.exe" -Arguments $machineSmartScreenArgs -TimeoutSeconds 5 | Out-Null

    $appHostArgs = @(
        "add",
        "HKCU\Software\Microsoft\Windows\CurrentVersion\AppHost",
        "/v",
        "EnableWebContentEvaluation",
        "/t",
        "REG_DWORD",
        "/d",
        "0",
        "/f"
    )
    Start-ProcessSafe -FilePath "reg.exe" -Arguments $appHostArgs -TimeoutSeconds 5 | Out-Null

    $userSmartScreenArgs = @(
        "add",
        "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer",
        "/v",
        "SmartScreenEnabled",
        "/t",
        "REG_SZ",
        "/d",
        "Off",
        "/f"
    )
    Start-ProcessSafe -FilePath "reg.exe" -Arguments $userSmartScreenArgs -TimeoutSeconds 5 | Out-Null

    $ciTool = Join-Path $env:WINDIR "System32\CiTool.exe"
    if (Test-Path -LiteralPath $ciTool) {
        Start-ProcessSafe -FilePath $ciTool -Arguments @("-r") -TimeoutSeconds 3 | Out-Null
    }
}

if (-not (Test-Admin)) {
    Restart-AsAdmin
}

Disable-SandboxApplicationControl

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
  <Networking>Enable</Networking>
  <ProtectedClient>Disable</ProtectedClient>
  <ClipboardRedirection>Enable</ClipboardRedirection>
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
