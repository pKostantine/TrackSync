<#
    TrackSync installer.

    Deliberately dependency-free: this is PowerShell that ships with every
    supported version of Windows, so a friend can unzip and run it without
    installing anything first. It does what a "real" installer does --
    copies the program somewhere sensible, makes Start Menu and Desktop
    shortcuts, and registers an uninstaller in Add or Remove Programs -- and
    nothing it does needs administrator rights, because it installs per-user.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Silent,
    [string]$InstallDir
)

$AppName    = 'TrackSync'
$Publisher  = 'TrackSync'
$Version    = '1.1.0'
$RegKey     = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
$ErrorActionPreference = 'Stop'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
}
$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$Desktop   = [Environment]::GetFolderPath('Desktop')
$LnkStart  = Join-Path $StartMenu "$AppName.lnk"
$LnkDesk   = Join-Path $Desktop   "$AppName.lnk"

function Write-Step($msg) { Write-Host "  $msg" }

function New-Shortcut($path, $target, $icon, $desc) {
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($path)
    $s.TargetPath       = $target
    $s.WorkingDirectory = Split-Path $target
    $s.IconLocation     = $icon
    $s.Description      = $desc
    $s.Save()
}

function Remove-TrackSync {
    Write-Host ''
    Write-Host " Removing $AppName..." -ForegroundColor Cyan
    foreach ($l in @($LnkStart, $LnkDesk)) {
        if (Test-Path $l) { Remove-Item $l -Force; Write-Step "removed $l" }
    }
    if (Test-Path $RegKey) { Remove-Item $RegKey -Recurse -Force }
    if (Test-Path $InstallDir) {
        # the uninstaller may be running from inside the folder it is
        # deleting, so hand the last step to a detached cmd
        $bat = Join-Path $env:TEMP "remove_$AppName.cmd"
        @"
@echo off
ping 127.0.0.1 -n 3 >nul
rmdir /s /q "$InstallDir"
del "%~f0"
"@ | Set-Content -Encoding ASCII $bat
        Start-Process cmd.exe "/c `"$bat`"" -WindowStyle Hidden
        Write-Step "removing $InstallDir"
    }
    Write-Host ''
    Write-Host " $AppName has been removed." -ForegroundColor Green
    if (-not $Silent) { Write-Host ''; Read-Host ' Press Enter to close' }
    exit 0
}

if ($Uninstall) { Remove-TrackSync }

# ---- install -------------------------------------------------------------
$here    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$payload = Join-Path $here 'app'
if (-not (Test-Path (Join-Path $payload "$AppName.exe"))) {
    Write-Host ''
    Write-Host " Could not find app\$AppName.exe next to this script." -ForegroundColor Red
    Write-Host " Unzip the whole folder before running the installer -" -ForegroundColor Red
    Write-Host " running it from inside the .zip will not work." -ForegroundColor Red
    Write-Host ''
    Read-Host ' Press Enter to close'
    exit 1
}

Write-Host ''
Write-Host " ============================================"
Write-Host "   $AppName $Version"
Write-Host " ============================================"
Write-Host ''
Write-Host " Installing to $InstallDir"
Write-Host ''

if (Test-Path $InstallDir) {
    Write-Step 'removing the previous version'
    Get-ChildItem $InstallDir -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
} else {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}

Write-Step 'copying files'
Copy-Item (Join-Path $payload '*') $InstallDir -Recurse -Force

$exe = Join-Path $InstallDir "$AppName.exe"
$ico = Join-Path $InstallDir '_internal\assets\TrackSync.ico'
if (-not (Test-Path $ico)) { $ico = $exe }

Write-Step 'creating shortcuts'
New-Shortcut $LnkStart $exe $ico 'Time-align two recordings of the same event'
New-Shortcut $LnkDesk  $exe $ico 'Time-align two recordings of the same event'

Write-Step 'registering with Add or Remove Programs'
$uninstallCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\Uninstall-TrackSync.ps1`" -Uninstall"
Copy-Item (Join-Path $here 'Install-TrackSync.ps1') `
          (Join-Path $InstallDir 'Uninstall-TrackSync.ps1') -Force
New-Item -Path $RegKey -Force | Out-Null
$size = [int]((Get-ChildItem $InstallDir -Recurse -File |
                Measure-Object Length -Sum).Sum / 1KB)
Set-ItemProperty $RegKey DisplayName     $AppName
Set-ItemProperty $RegKey DisplayVersion  $Version
Set-ItemProperty $RegKey Publisher       $Publisher
Set-ItemProperty $RegKey DisplayIcon     $ico
Set-ItemProperty $RegKey InstallLocation $InstallDir
Set-ItemProperty $RegKey UninstallString $uninstallCmd
Set-ItemProperty $RegKey NoModify        1 -Type DWord
Set-ItemProperty $RegKey NoRepair        1 -Type DWord
Set-ItemProperty $RegKey EstimatedSize   $size -Type DWord

Write-Host ''
Write-Host " Verifying the install..." -ForegroundColor Cyan
Write-Host ''
# TrackSync-check.exe, not TrackSync.exe: the windowed build is a GUI
# subsystem process, so it has no stdout, PowerShell does not wait for it,
# and $LASTEXITCODE is whatever the previous command left behind.
$check = Join-Path $InstallDir 'TrackSync-check.exe'
if (Test-Path $check) {
    $proc = Start-Process -FilePath $check -ArgumentList '--selftest' `
                          -NoNewWindow -Wait -PassThru
    $code = $proc.ExitCode
} else {
    Write-Host ' (no verification tool in this build - skipping)'
    $code = 0
}
if ($code -ne 0) {
    Write-Host ''
    Write-Host " The program was installed but failed its self-test." -ForegroundColor Red
    Write-Host " See the messages above." -ForegroundColor Red
    if (-not $Silent) { Read-Host ' Press Enter to close' }
    exit 1
}

Write-Host ''
Write-Host " ============================================" -ForegroundColor Green
Write-Host "   Installed." -ForegroundColor Green
Write-Host ''
Write-Host "   Look for $AppName in the Start Menu, or the"
Write-Host "   shortcut on your Desktop."
Write-Host ''
Write-Host "   To remove it later: Settings > Apps, or run"
Write-Host "   Uninstall-TrackSync.ps1 in the install folder."
Write-Host " ============================================" -ForegroundColor Green
Write-Host ''
if (-not $Silent) { Read-Host ' Press Enter to close' }
