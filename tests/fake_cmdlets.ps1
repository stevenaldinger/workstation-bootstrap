# Stand-ins for the cmdlets start.ps1 calls that reach the network or change the machine.
# tests/test_start_ps1.py puts them ahead of start.ps1 in a wrapper script. Each declares only
# the parameters the real cmdlet has, so a call with one it lacks fails the run.
param([string]$PersonalAccount = '')
$FakeDir = $env:FAKE_DIR

function Add-Call {
    param([string]$Name, [string]$Detail)
    $row = @{ name = $Name; detail = $Detail } | ConvertTo-Json -Compress
    [IO.File]::AppendAllText((Join-Path $FakeDir 'ps-calls.jsonl'), $row + "`n")
}

function Get-FakeConfig {
    Get-Content -Raw -LiteralPath (Join-Path $FakeDir 'host.json') | ConvertFrom-Json
}

function Install-FakeWinget {
    Copy-Item -LiteralPath (Join-Path $FakeDir 'winget-stash\winget.exe') -Destination (Join-Path $env:FAKE_BIN 'winget.exe')
}

function Invoke-WebRequest {
    param(
        [switch]$UseBasicParsing,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [Parameter(Mandatory = $true)][int]$TimeoutSec,
        [Parameter(Mandatory = $true)][int]$MaximumRedirection,
        [switch]$PassThru
    )
    Add-Call 'Invoke-WebRequest' $Uri
    $map = Get-Content -Raw -LiteralPath (Join-Path $FakeDir 'downloads.json') | ConvertFrom-Json
    $entry = $map.PSObject.Properties[$Uri]
    if ($null -eq $entry) { throw 'The remote server returned an error: (404) Not Found.' }
    Copy-Item -LiteralPath $entry.Value.source -Destination $OutFile -Force
    if (-not $PassThru) { return }
    $final = $entry.Value.final
    if (-not $final) { $final = $Uri }
    [pscustomobject]@{ BaseResponse = [pscustomobject]@{ ResponseUri = [Uri]$final } }
}

function Start-Sleep {
    param([Parameter(Mandatory = $true)][int]$Seconds)
    Add-Call 'Start-Sleep' ([string]$Seconds)
}

function Add-AppxPackage {
    param([switch]$RegisterByFamilyName, [Parameter(Mandatory = $true)][string]$MainPackage)
    Add-Call 'Add-AppxPackage' $MainPackage
    if ((Get-FakeConfig).appx -ne 'registers') { throw 'Deployment failed with HRESULT: 0x80073CF9' }
    Install-FakeWinget
}

function Install-Module {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$RequiredVersion,
        [string]$Repository,
        [string]$Scope,
        [switch]$Force
    )
    Add-Call 'Install-Module' "$Name $RequiredVersion"
}

function Import-Module {
    param([Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][string]$RequiredVersion)
    Add-Call 'Import-Module' "$Name $RequiredVersion"
}

function Repair-WinGetPackageManager {
    param([Parameter(Mandatory = $true)][string]$Version, [switch]$Force)
    Add-Call 'Repair-WinGetPackageManager' $Version
    $outcome = (Get-FakeConfig).module_repair
    if ($outcome -eq 'fails') { throw 'The package manager could not be repaired.' }
    if ($outcome -eq 'works') { Install-FakeWinget }
}
