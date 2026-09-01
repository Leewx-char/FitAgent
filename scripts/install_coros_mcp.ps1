<#
.SYNOPSIS
在与 FitAgent 隔离的虚拟环境中安装社区 Coros MCP 服务。

.DESCRIPTION
coros-mcp 依赖 FastMCP，后者可能要求与 FastAPI 应用锁定版本不同的 Starlette。
将其安装在 .tools/coros-mcp-venv 中，既保持 API 服务的依赖图稳定，也提供本地
stdio MCP 可执行程序。

源代码版本被固定，以便面试演示可复现。只有验证 app/services/coros_client.py 的
工具契约和真实同步后，才应升级该版本。
#>

[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
# coros-mcp 会输出 Unicode 状态符号。强制 UTF-8，避免 Windows PowerShell 的旧版 GBK
# 控制台编码导致 `auth-status` 或交互式登录崩溃。
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
