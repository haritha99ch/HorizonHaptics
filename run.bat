@echo off
cd /d "%~dp0"

:: --- Windows Firewall ---
:: Check if the inbound UDP rule already exists (no admin needed to read rules).
netsh advfirewall firewall show rule name="HorizonHaptics UDP 5300" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo HorizonHaptics needs inbound UDP port 5300 to receive FH6 telemetry.
    echo If Windows shows a network access security alert, click "Allow access".
    echo.
    echo To add the rule silently without a dialog, right-click and run as Administrator:
    echo   packaging\windows\add-firewall-rule.ps1
    echo.
)

:: --- uv ---
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo uv not found -- installing...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
)

uv run main.py
