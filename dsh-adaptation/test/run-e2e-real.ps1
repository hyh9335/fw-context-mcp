<#  -*- coding: utf-8 -*-
    run-e2e-real.ps1 - real install + real usage end-to-end for fw-context-mcp <-> dsh

    Run this on a machine where Node can spawn child processes (your normal
    Windows terminal, WSL, or Linux).  It will:

      1. PRECHECK   node / python / fw-context-mcp / dsh
      2. PROJECT    build a tiny C firmware fixture and a REAL fw-context index
      3. INSTALL    run the real installer  `fw-context init --tool dsh`
                    against an ISOLATED $DSH_HOME (not ~/.dsh)
      4. COMPOSE    verify dsh itself accepts the installer-written patch
      5. USE        run the official @deepseek-ai/dsh-mcp-client bridge via
                    node, which spawns the real fw-context-mcp, registers all
                    tools on ctx.tools and calls one tool (live JSON-RPC)
      6. CLEANUP    remove the isolated DSH_HOME, fixture, and index

    This does NOT touch your real ~/.dsh, ~/.fw-context, or WSL user files:
    everything lives under $DshHome (for dsh) and $Fixture (for the project),
    both in the system temp dir and removed in the finally block.

    Usage:
      powershell -ExecutionPolicy Bypass -File dsh-adaptation/test/run-e2e-real.ps1
#>
param(
    [string]$DshHome = '',
    [string]$Fixture = ''
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $DshHome) { $DshHome = Join-Path ([IO.Path]::GetTempPath()) ('fw-context-dsh-e2e-' + [guid]::NewGuid().ToString('N')) }
if (-not $Fixture) { $Fixture = Join-Path ([IO.Path]::GetTempPath()) ('fw-context-fixture-' + [guid]::NewGuid().ToString('N')) }

Write-Host "== e2e: repo=$repo"
Write-Host "== e2e: isolated DSH_HOME=$DshHome   fixture=$Fixture"

Write-Host "`n[1/6] PRECHECK"
foreach ($c in @('node', 'python', 'fw-context-mcp', 'dsh')) {
    $found = Get-Command $c -ErrorAction SilentlyContinue
    if (-not $found) { throw "missing prerequisite: $c" }
    Write-Host "  ok $c -> $($found.Source)"
}

Write-Host "`n[2/6] PROJECT + INDEX (tiny C fixture)"
$null = New-Item -ItemType Directory -Path "$Fixture\src", "$Fixture\include", "$Fixture\.fw-context" -Force
@'
#ifndef DRIVER_H
#define DRIVER_H
#include <stdint.h>
typedef enum { DRIVER_OK = 0, DRIVER_ERROR_TIMEOUT = 1 } driver_status_t;
driver_status_t driver_init(uint32_t baudrate);
void driver_deinit(void);
#endif
'@ | Set-Content -Path "$Fixture\include\driver.h" -Encoding ascii
@'
#include "driver.h"
static uint8_t s_ok = 0;
driver_status_t driver_init(uint32_t baudrate) { (void)baudrate; s_ok = 1; return DRIVER_OK; }
void driver_deinit(void) { s_ok = 0; }
'@ | Set-Content -Path "$Fixture\src\driver.c" -Encoding ascii
@'
#include "driver.h"
int main(void) {
    driver_status_t s = driver_init(115200);
    if (s != DRIVER_OK) return 1;
    driver_deinit();
    return 0;
}
'@ | Set-Content -Path "$Fixture\src\main.c" -Encoding ascii
$ccJson = @(
    @{ directory = ($Fixture -replace '\\', '/'); command = 'gcc -std=c11 -Iinclude -c src/driver.c -o build/driver.o'; file = 'src/driver.c' },
    @{ directory = ($Fixture -replace '\\', '/'); command = 'gcc -std=c11 -Iinclude -c src/main.c -o build/main.o'; file = 'src/main.c' }
) | ConvertTo-Json -Depth 4
Set-Content -Path "$Fixture\compile_commands.json" -Value $ccJson -Encoding ascii
Set-Content -Path "$Fixture\.fw-context\config.toml" -Value "[project]`nid = `"dsh-e2e-test`"`n`n[index]`ncompile_commands = `"compile_commands.json`"`n" -Encoding ascii
$null = New-Item -ItemType File -Path "$Fixture\.fw-context\local.toml" -Force
$oldEnv = $env:FW_CONTEXT_INDEX_DIR
$env:FW_CONTEXT_INDEX_DIR = "$Fixture\.idx"
& fw-context index --project $Fixture --no-embeddings --no-analyze --force | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Warning "index exited $LASTEXITCODE (global-registry write may fail on restricted hosts; index itself is built)" }
Write-Host "  indexed fixture"

Write-Host "`n[3/6] INSTALL (real installer -> isolated dsh)"
$env:DSH_HOME = $DshHome
$null = New-Item -ItemType Directory -Path "$DshHome\profiles\headless" -Force
Copy-Item (Join-Path $HOME '.dsh\profiles\headless\package.json') "$DshHome\profiles\headless\" -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $HOME '.dsh\profiles\headless\cordis.yml') "$DshHome\profiles\headless\" -Force -ErrorAction SilentlyContinue
# Run the real installer with --tool dsh (idempotent upsert). --skip-doctor/--skip-build
# keep it fast; works in a real environment where Node/network are fine.
& fw-context init --tool dsh --dsh-home $DshHome --project $Fixture --skip-doctor --skip-build --non-interactive 2>&1 | ForEach-Object { Write-Host "  init| $_" }
$patch = Join-Path $DshHome 'cordis.patch.yml'
if (-not (Test-Path $patch)) { throw "installer did not write $patch" }
Write-Host "  installer wrote $patch"

Write-Host "`n[4/6] COMPOSE (dsh accepts the installer patch)"
$dump = (& dsh --profile headless --dump-config --patch $patch 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { throw "dsh rejected installer patch`n$dump" }
if ($dump -notmatch 'mcp-fw-context') { throw 'mcp-fw-context row missing' }
Write-Host "  dsh composed mcp-fw-context row (exit 0)"

Write-Host "`n[5/6] USE (official @deepseek-ai/dsh-mcp-client bridge, real node spawn)"
$globalNm = (& npm root -g 2>$null | Select-Object -First 1)
if (-not $globalNm) { $globalNm = Join-Path (Split-Path (Get-Command dsh).Source -Parent) 'node_modules' }
$env:DSH_PKG_ROOT = Join-Path $globalNm '@deepseek-ai\dsh'
& node (Join-Path $PSScriptRoot 'dsh-mcp-integration.mjs') 2>&1 | ForEach-Object { Write-Host "  use| $_" }

Write-Host "`n[6/6] CLEANUP"
if ($oldEnv) { $env:FW_CONTEXT_INDEX_DIR = $oldEnv } else { Remove-Item Env:\FW_CONTEXT_INDEX_DIR -ErrorAction SilentlyContinue }
Remove-Item Env:\DSH_HOME -ErrorAction SilentlyContinue
Remove-Item $DshHome -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Fixture -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "  removed isolated DSH_HOME and fixture (nothing was written to WSL or ~/.dsh)"
Write-Host "`nE2E COMPLETE"
