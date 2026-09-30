@echo off
rem FWCTX validation WSL bridge (user-approved scheduled-task workaround).
rem Runs the current job script inside Ubuntu-22.04, output captured here.
wsl.exe -d Ubuntu-22.04 -e bash /mnt/d/github/fw-context-mcp/.fw-validation/wsl_job.sh > D:\github\fw-context-mcp\.fw-validation\wsl_job.out 2>&1
