# Receipt schema

Required JSON fields:
task_id, mode, attempt, base_commit, head_commit, platforms, status,
issue_ids_in, issue_dispositions_out, artifacts, commands, tests,
files_changed, commit_sha, known_limitations, blockers.

Each command records platform, command, cwd, exit_code, stdout_artifact, stderr_artifact.
Each test records name, phase, platform, expected, actual, result.

Implementation also requires:
red_proof_artifact, green_windows_artifact when relevant,
green_wsl_artifact when relevant, git_diff_stat_artifact, commit_sha.

Verifier PASS is invalid if it:
- only trusts implementer summaries
- skips required platform rerun
- substitutes in-memory MCP for a real-stdio requirement
- accepts Windows success that requires a host fcntl shim
- accepts Linux correctness tests run only under /mnt/c
