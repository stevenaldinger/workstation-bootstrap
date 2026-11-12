# Settings for scripts/check.sh powershell. start.ps1 runs in Windows PowerShell 5.1, so the
# syntax rule rejects anything newer on top of the default rules.
@{
    IncludeDefaultRules = $true
    Rules               = @{
        PSUseCompatibleSyntax = @{
            Enable         = $true
            TargetVersions = @('5.1')
        }
    }
}
