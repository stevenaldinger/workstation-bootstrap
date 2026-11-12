# A stand-in for Get-CimInstance that answers the three classes start.ps1 reads from host.json.
function Get-CimInstance {
    param([Parameter(Mandatory = $true)][string]$ClassName)
    $config = Get-FakeConfig
    switch ($ClassName) {
        'Win32_OperatingSystem' { [pscustomobject]@{ ProductType = [uint32]$config.product_type; BuildNumber = [string]$config.build } }
        'Win32_ComputerSystem' { [pscustomobject]@{ HypervisorPresent = [bool]$config.hypervisor } }
        'Win32_Processor' {
            foreach ($p in $config.processors) {
                [pscustomobject]@{ Architecture = [uint16]$p.arch; VirtualizationFirmwareEnabled = [bool]$p.firmware }
            }
        }
        default { throw "unmodelled CIM class $ClassName" }
    }
}
