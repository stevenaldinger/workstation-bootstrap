#Requires -Version 5.1
<#
.SYNOPSIS
Workstation bootstrap, stage 1, for Windows.

.DESCRIPTION
Prepares a fresh Windows 11 computer to run start.py: checks the computer, installs a
checksum-pinned mise, installs the pinned Python, uv, and GitHub CLI through it, downloads the
starter files named in start.sha256, verifies them, and hands off to start.py.

Usage:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File start.ps1 -PersonalAccount <email or sign-in address> [start.py options]

What it does:
  1. Checks the computer is x64 Windows 11 23H2 or later with virtualization on, and validates
     the arguments, before it changes anything.
  2. Takes the named lock Local\workstation-setup, so two runs never overlap.
  3. Downloads mise, checks it against $MiseSha256, and installs it.
  4. Makes sure winget works.
  5. Has mise install the pinned Python, uv, and gh (mise verifies those downloads).
  6. Downloads start.sha256, checks it against $ManifestSha256, then downloads and checks every
     file it lists.
  7. Runs start.py with the pinned Python, uv, and gh on PATH.

Design decisions:
  - Windows PowerShell 5.1 is the only shell a fresh Windows 11 is guaranteed to have, so
    nothing here may need a newer one.
  - Only the mise archive and the manifest are pinned by checksum in this file. The manifest
    pins every starter file, and mise checks what it downloads (see the README's trust
    boundary section).
  - Every step decides from the machine's state, so running the same command again finishes a
    run that stopped anywhere, including one that waited for a restart.
  - The script copies itself to %LOCALAPPDATA%\workstation-bootstrap, where the sign-in resume
    entry that start.py registers runs it from.
  - WS_ALLOW_SERVER_EDITION=1 relaxes only the client-edition test, and WS_SKIP_SUPPORT_CHECK=1
    skips the support test entirely. Both exist for the clean-machine proof, which runs on
    hosted runners that are not Windows 11 clients.

.PARAMETER PersonalAccount
Email or sign-in address of the personal 1Password account.

Every other argument is passed to start.py, which passes the ones it does not know on to
`ws bootstrap`. Name -PersonalAccount first: this script has no advanced-function binding,
because that would take `--verbose` as PowerShell's own -Verbose and drop it.
#>
param([string]$PersonalAccount = '')

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$InformationPreference = 'Continue'

# MiseSha256 is the Windows x64 archive's line in the release's SHASUMS256.txt.
$MiseVersion = '2026.11.7'
$MiseSha256 = '20065c429f051c55857a62929060cf8659b8e7848c1cae4d6d1c0c9b0727758b'
$PythonVersion = '3.15.2'
$UvVersion = '0.13.7'
$GhVersion = '2.103.3'
$WinGetVersion = '1.29.380'
$MinimumBuild = 22631
# The starter files are listed, with their checksums, in start.sha256 at the commit that last
# changed them. Only that manifest's own checksum is pinned here.
$FilesCommit = '4dfed37797e9f4bddd4184e8dfacf325c7aa3455'
$ManifestSha256 = '99ed1cd052ec2d8f6624b239ce109322f38cdaaf4ced46cecfc8482dcc6ffe32'
$RawUrl = "https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/$FilesCommit"
$WorkDir = Join-Path $env:LOCALAPPDATA 'workstation-bootstrap'
$BinDir = Join-Path $env:USERPROFILE '.local\bin'
$MiseExe = Join-Path $BinDir 'mise.exe'

# =============================================================================
# Output
# =============================================================================

function Exit-Starter {
    param([string]$Message, [int]$Code = 1)
    [Console]::Error.WriteLine("start.ps1: $Message")
    exit $Code
}

# =============================================================================
# Checks
# =============================================================================

# Reads the arguments before anything is installed, so a typo costs nothing.
function Assert-Argument {
    param([string]$Account)
    if ([string]::IsNullOrWhiteSpace($Account)) {
        Exit-Starter '-PersonalAccount is required: the email or sign-in address of the personal 1Password account' 2
    }
    if ($Account -match '^-|\s|"') {
        Exit-Starter "-PersonalAccount needs an email or sign-in address, got: $Account" 2
    }
}

function Assert-SupportedHost {
    if ($env:WS_SKIP_SUPPORT_CHECK -eq '1') { return }
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $system = Get-CimInstance -ClassName Win32_ComputerSystem
    $processors = @(Get-CimInstance -ClassName Win32_Processor)
    if ([int]$os.ProductType -ne 1 -and $env:WS_ALLOW_SERVER_EDITION -ne '1') {
        Exit-Starter 'unsupported edition of Windows; a Windows 11 client edition (Home, Pro, Enterprise, or Education) is required' 2
    }
    if ([int]$os.BuildNumber -lt $MinimumBuild) {
        Exit-Starter "unsupported Windows build $($os.BuildNumber); Windows 11 23H2 (build $MinimumBuild) or later is required. Update Windows, then run the same command again." 2
    }
    # Win32_Processor.Architecture 9 is x64; 12 is Arm64.
    if (@($processors | Where-Object { [int]$_.Architecture -ne 9 }).Count -gt 0) {
        Exit-Starter 'unsupported processor architecture; an x64 (AMD64) computer is required' 2
    }
    $firmwareOn = @($processors | Where-Object { $_.VirtualizationFirmwareEnabled -eq $true }).Count -gt 0
    if (-not ($system.HypervisorPresent -eq $true -or $firmwareOn)) {
        Exit-Starter 'virtualization is turned off; turn on virtualization (Intel VT-x or AMD-V) in the firmware settings, then run the same command again' 2
    }
}

# A second run, such as the sign-in resume starting while a hand-typed run is still going,
# stops here. The lock lasts as long as this process.
function Enter-RunLock {
    $script:RunLock = New-Object -TypeName System.Threading.Mutex -ArgumentList $false, 'Local\workstation-setup'
    $acquired = $false
    try {
        $acquired = $script:RunLock.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        Exit-Starter 'another run of the workstation setup is in progress; wait for it to finish' 3
    }
}

# =============================================================================
# Steps
# =============================================================================

# TLS 1.2 at least, and 1.3 where this .NET Framework knows it.
function Enable-SecureProtocol {
    $protocols = [Net.SecurityProtocolType]::Tls12
    try {
        $protocols = $protocols -bor [Net.SecurityProtocolType]::Tls13
    }
    catch {
        Write-Information 'TLS 1.3 is not available here; using TLS 1.2.'
    }
    [Net.ServicePointManager]::SecurityProtocol = $protocols
}

function Save-Download {
    param([string]$Uri, [string]$Path)
    if (-not $Uri.StartsWith('https://')) {
        Exit-Starter "refusing a download that is not https: $Uri" 1
    }
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Path -TimeoutSec 300 -MaximumRedirection 5 -PassThru
            if ($response.BaseResponse.ResponseUri.Scheme -ne 'https') {
                Remove-Item -LiteralPath $Path -Force
                throw "redirected away from https: $Uri"
            }
            return
        }
        catch {
            if ($attempt -eq 3) { Exit-Starter "download failed: $Uri" 1 }
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
}

function Test-FileHash {
    param([string]$Path, [string]$Expected)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -ieq $Expected
}

function Assert-FileHash {
    param([string]$Path, [string]$Expected, [string]$Message)
    if (-not (Test-FileHash -Path $Path -Expected $Expected)) {
        Remove-Item -LiteralPath $Path -Force
        Exit-Starter $Message 1
    }
}

# Keeps a copy where the sign-in resume entry runs it from.
function Save-Self {
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
    $copy = Join-Path $WorkDir 'start.ps1'
    if ($PSCommandPath -and $PSCommandPath -ne $copy) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $copy -Force
    }
}

# Replaces a file only when it differs from the archive entry, so a run that stopped half way
# through an extraction is finished by the next one.
function Copy-ArchiveEntry {
    param($Archive, [string]$EntryName, [string]$Destination)
    $entry = $Archive.GetEntry($EntryName)
    if ($null -eq $entry) { Exit-Starter "the mise archive has no $EntryName" 1 }
    $stream = $entry.Open()
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        $expected = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '')
    }
    finally {
        $stream.Dispose()
    }
    if (-not (Test-FileHash -Path $Destination -Expected $expected)) {
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $Destination, $true)
    }
}

function Install-Mise {
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $zip = Join-Path $WorkDir 'mise.zip'
    if (-not (Test-FileHash -Path $zip -Expected $MiseSha256)) {
        $uri = "https://github.com/jdx/mise/releases/download/v$MiseVersion/mise-v$MiseVersion-windows-x64.zip"
        Save-Download -Uri $uri -Path $zip
        Assert-FileHash -Path $zip -Expected $MiseSha256 -Message "checksum mismatch: $uri"
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        foreach ($name in 'mise.exe', 'mise-shim.exe') {
            Copy-ArchiveEntry -Archive $archive -EntryName "mise/bin/$name" -Destination (Join-Path $BinDir $name)
        }
    }
    finally {
        $archive.Dispose()
    }
    $env:PATH = "$BinDir;$env:PATH"
}

function Test-Winget {
    if ($null -eq (Get-Command -Name winget -CommandType Application -ErrorAction SilentlyContinue)) { return $false }
    & winget --version | Out-Null
    return $LASTEXITCODE -eq 0
}

# A client edition ships winget; it only needs registering when the first sign-in has not
# finished. A computer without it, such as a server image, gets the pinned client instead.
function Repair-Winget {
    if (Test-Winget) { return }
    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe'
    }
    catch {
        Write-Information 'Registering the App Installer did not make winget available.'
    }
    if (Test-Winget) { return }
    Install-Module -Name Microsoft.WinGet.Client -RequiredVersion $WinGetVersion -Repository PSGallery -Scope CurrentUser -Force
    Import-Module -Name Microsoft.WinGet.Client -RequiredVersion $WinGetVersion
    Repair-WinGetPackageManager -Version $WinGetVersion -Force | Out-Null
    if (-not (Test-Winget)) { Exit-Starter 'winget is not available and could not be repaired' 1 }
}

function Invoke-Native {
    param([string]$Path, [string[]]$Arguments)
    & $Path @Arguments
    if ($LASTEXITCODE -ne 0) { Exit-Starter "$Path failed with exit status $LASTEXITCODE" $LASTEXITCODE }
}

function Install-Runtime {
    # MISE_YES answers mise's trust prompts for this install only, not for start.py.
    $env:MISE_YES = '1'
    try {
        Invoke-Native -Path $MiseExe -Arguments @('install', "python@$PythonVersion", "uv@$UvVersion", "gh@$GhVersion")
    }
    finally {
        Remove-Item -Path Env:\MISE_YES -ErrorAction SilentlyContinue
    }
}

function Save-StarterFile {
    $manifest = Join-Path $WorkDir 'start.sha256'
    Save-Download -Uri "$RawUrl/start.sha256" -Path $manifest
    Assert-FileHash -Path $manifest -Expected $ManifestSha256 -Message "checksum mismatch: $RawUrl/start.sha256"
    $entries = @()
    foreach ($line in Get-Content -LiteralPath $manifest) {
        if ($line -notmatch '^([0-9a-f]{64})\s+([A-Za-z0-9_.-]+)$') {
            Exit-Starter 'the starter manifest has a line this script cannot read' 1
        }
        $entries += , @($Matches[1], $Matches[2])
        Save-Download -Uri "$RawUrl/$($Matches[2])" -Path (Join-Path $WorkDir $Matches[2])
    }
    foreach ($entry in $entries) {
        if (-not (Test-FileHash -Path (Join-Path $WorkDir $entry[1]) -Expected $entry[0])) {
            Exit-Starter 'checksum mismatch in the starter files' 1
        }
    }
}

function Invoke-Walkthrough {
    param([string]$Account, [string[]]$Extra)
    $env:PYTHONUTF8 = '1'
    $arguments = @(
        'exec', "python@$PythonVersion", "uv@$UvVersion", "gh@$GhVersion", '--',
        'python', (Join-Path $WorkDir 'start.py'),
        '--stage1', (Join-Path $WorkDir 'stage1-windows.toml'),
        '--personal-account', $Account
    ) + @($Extra)
    & $MiseExe @arguments
    $status = $LASTEXITCODE
    $script:RunLock.Dispose()
    exit $status
}

# =============================================================================
# Main
# =============================================================================

function Main {
    param([string]$Account, [string[]]$Extra)
    Assert-Argument -Account $Account
    Assert-SupportedHost
    Enter-RunLock
    Enable-SecureProtocol
    Save-Self
    Install-Mise
    Repair-Winget
    Install-Runtime
    Save-StarterFile
    Invoke-Walkthrough -Account $Account -Extra $Extra
}

Main -Account $PersonalAccount -Extra $args
