# HorizonHaptics - Windows Firewall setup
# Adds an inbound UDP rule on port 5300 so FH6 telemetry is not blocked.
# Right-click -> Run with PowerShell (UAC prompt will appear if not already admin).

$RuleName = "HorizonHaptics UDP 5300"

# Self-elevate via UAC if not already running as administrator.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Host "Requesting administrator privileges..."
    Start-Process PowerShell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy ByPass -File `"$PSCommandPath`""
    exit
}

if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Write-Host "Firewall rule '$RuleName' already exists - nothing to do."
} else {
    New-NetFirewallRule `
        -DisplayName $RuleName `
        -Direction Inbound `
        -Protocol UDP `
        -LocalPort 5300 `
        -Action Allow `
        -Profile Any | Out-Null
    Write-Host "Firewall rule '$RuleName' added."
}

Write-Host ""
Read-Host "Press Enter to close"
