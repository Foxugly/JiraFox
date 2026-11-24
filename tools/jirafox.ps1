<#
.SYNOPSIS
    Pilote JiraFox en local : start, stop, restart, status, pull, migrate, open.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\jirafox.ps1 start
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("start", "stop", "restart", "status", "pull", "migrate", "open")]
    [string]$Action
)

. "$PSScriptRoot\jirafox-lib.ps1"

switch ($Action) {
    "start"   { Start-JiraFox }
    "stop"    { Stop-JiraFox }
    "restart" { Stop-JiraFox; Start-JiraFox }
    "status"  { Write-JF (Get-JFStatus) }
    "pull"    { Invoke-JFPull }
    "migrate" { [void](Invoke-JFMigrate) }
    "open"    { Open-JiraFox }
}
