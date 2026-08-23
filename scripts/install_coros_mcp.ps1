<#
.SYNOPSIS
Install the community Coros MCP server in a virtual environment isolated from FitAgent.

.DESCRIPTION
coros-mcp depends on FastMCP, which can require a Starlette version different from the
FastAPI application's fixed dependency. Keeping it in .tools/coros-mcp-venv preserves
the API server's dependency graph while still providing a local stdio MCP executable.

The source revision is intentionally pinned for reproducible interview demos. Upgrade it
only after validating the tool contracts in app/services/coros_client.py and real sync.
#>

[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
# coros-mcp writes Unicode status symbols. Force UTF-8 so Windows PowerShell's legacy GBK
# console encoding cannot make `auth-status` or an interactive login crash.
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".tools\coros-mcp-venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$Source = "git+https://github.com/cygnusb/coros-mcp.git@71d594cec58372077b30d583847bb2adfc181d76"

if (-not (Test-Path $VenvPython)) {
    & $Python -m venv $VenvPath
}

& $VenvPython -m pip install $Source
& $VenvPython -m pip check
& (Join-Path $VenvPath "Scripts\coros-mcp.exe") --help

Write-Host "Installed Coros MCP in $VenvPath"
Write-Host "Next: & (Join-Path $VenvPath 'Scripts\coros-mcp.exe') auth-web"
