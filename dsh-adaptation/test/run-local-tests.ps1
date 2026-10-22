# run-local-tests.ps1 - verify fw-context-mcp <-> DeepSeek Harness (dsh) adapter
#
# Three independent checks:
#   1. Server-side MCP handshake + tool inventory (python, works everywhere)
#   2. dsh patch composition in an ISOLATED DSH_HOME (does not touch ~/.dsh)
#   3. Official dsh-mcp-client bridge config acceptance (node)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File dsh-adaptation/test/run-local-tests.ps1

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$scratch = Join-Path $repo 'scratch'
$null = New-Item -ItemType Directory -Path $scratch -Force
$patch = Join-Path $repo 'dsh-adaptation\cordis.fw-context.patch.yml'

$Script:pass = 0
$Script:fail = 0

function Test-Step([string]$name, [scriptblock]$body) {
    Write-Host "`n=== $name ==="
    try {
        & $body
        Write-Host "  -> PASS" -ForegroundColor Green
        $Script:pass++
    } catch {
        Write-Host "  -> FAIL: $($_.Exception.Message)" -ForegroundColor Red
        $Script:fail++
    }
}

Test-Step '1. fw-context-mcp MCP handshake + tools/list (python)' {
    $out = & python (Join-Path $PSScriptRoot 'mcp_probe.py') 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "probe exited $LASTEXITCODE`n$out" }
    if ($out -notmatch 'TOOL_COUNT\s+3[0-9]') { throw "expected ~38 tools, got:`n$out" }
    Write-Host ($out.Trim())
}

Test-Step '2. dsh accepts the patch (isolated DSH_HOME, --dump-config)' {
    $dshHome = Join-Path $scratch 'dsh-home'
    $prof = Join-Path $dshHome 'profiles\headless'
    $null = New-Item -ItemType Directory -Path $prof -Force
    Copy-Item (Join-Path $HOME '.dsh\profiles\headless\package.json') (Join-Path $prof 'package.json') -Force
    Copy-Item (Join-Path $HOME '.dsh\profiles\headless\cordis.yml') (Join-Path $prof 'cordis.yml') -Force
    $oldDshHome = $env:DSH_HOME
    $env:DSH_HOME = $dshHome
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        $dump = (& dsh --profile headless --dump-config --patch $patch 2>&1 | Out-String)
        $exit = $LASTEXITCODE
    } finally {
        $env:DSH_HOME = $oldDshHome
    }
    if ($exit -ne 0) { throw "dsh exited $exit`n$dump" }
    if ($dump -notmatch 'mcp-fw-context' -or $dump -notmatch 'dsh-mcp-client') { throw "mcp-fw-context row missing from dump" }
    Write-Host 'dsh composed the mcp-fw-context row (exit 0).'
}

Test-Step '3. bridge Config acceptance (node dsh-mcp-client)' {
    $out = (& node (Join-Path $PSScriptRoot 'dsh-mcp-integration.mjs') 2>&1 | Out-String)
    $exit = $LASTEXITCODE
    if ($exit -ne 0 -and $exit -ne '') { throw "node exited $exit`n$out" }
    if ($out -notmatch 'bridge Config accepted the patch config') { throw "config was not accepted: $out" }
    Write-Host ($out.Trim())
}

Write-Host "`n=================="
Write-Host "PASS: $Script:pass  FAIL: $Script:fail"
Write-Host "=================="
if ($Script:fail -gt 0) { exit 1 }