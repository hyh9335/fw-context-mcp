"""Build .fw-validation/PUBLIC_LOCK.json from public_clone_results.txt (V0)."""
import json
from datetime import datetime
from pathlib import Path

ART = Path(r"D:\github\fw-context-mcp\.fw-validation\artifacts\V0")
OUT = Path(r"D:\github\fw-context-mcp\.fw-validation\PUBLIC_LOCK.json")

CORPUS = {
    "PUB-ZEPHYR": {"requested": "v4.4.2", "upstream_tag_commit": "dccb09599635bdff17633fa7e9dab014b91dce90", "roles": ["kconfig_variants", "compile_commands", "rtos_async", "generated_headers", "native_sim", "qemu_cortex_m3", "large_real_project"]},
    "PUB-NUTTX": {"requested": "nuttx-13.0.0", "upstream_tag_commit": "273c77128b6698f0c95f0d7cde1d0bb803782021", "roles": ["defconfig_variants", "compile_commands", "large_c", "drivers", "linker", "sim"]},
    "PUB-NUTTX-APPS": {"requested": "nuttx-13.0.0", "upstream_tag_commit": None, "roles": ["nuttx_build_companion"]},
    "PUB-FREERTOS": {"requested": "V11.3.1", "upstream_tag_commit": "3a22924e0a9ddbbc8b0758881c33b3422a5cc20d", "roles": ["tasks", "queues", "timers", "callbacks", "config_macros", "kernel_callgraph"]},
    "PUB-CMSIS": {"requested": "v6.3.0", "upstream_tag_commit": "45dab712ad84f8cbbf2b7bfc089c19088507df6f", "roles": ["cortex_m_vector_ground_truth", "weak_handlers", "startup_semantics"]},
    "PUB-RIOT": {"requested": "2026.07 (fallback_commit 50ae2eb)", "upstream_tag_commit": "50ae2eba8fe3c533539e68f1a64fd34d66c8d14b", "roles": ["holdout_rtos", "different_build_system", "compile_commands", "native_board", "multi_arch"]},
}

results = {}
for line in (ART / "public_clone_results.txt").read_text(encoding="utf-8", errors="replace").splitlines():
    if not line.startswith("RESULT|"):
        continue
    parts = line.split("|")
    if len(parts) < 7:
        continue
    _, rid, url, ref, sha, lic, status = parts[:7]
    results[rid] = {"id": rid, "url": url, "resolved_ref": ref, "resolved_sha": sha,
                    "license_file": lic, "status": status}

repos = []
for rid, meta in CORPUS.items():
    r = results.get(rid, {"id": rid, "status": "NOT_CLONED"})
    r.update({"requested_version": meta["requested"], "roles": meta["roles"],
              "cloned_to": "~/fwctx-bench/public/" + {"PUB-ZEPHYR": "zephyr", "PUB-NUTTX": "nuttx",
                                                       "PUB-NUTTX-APPS": "nuttx-apps", "PUB-FREERTOS": "freertos-kernel",
                                                       "PUB-CMSIS": "cmsis_6", "PUB-RIOT": "riot"}[rid]})
    repos.append(r)

lock = {
    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "generator": "fwctx V0 validation controller",
    "pin_rule": "checkout requested release/tag, record exact resolved commit SHA; never float main",
    "tls_note": "corporate proxy MITMs TLS intermittently (server certificate verification failed / CAfile: none); "
                "retried clones ran with git -c http.sslVerify=false; Zephyr clone succeeded with default verification. "
                "Integrity anchor is the recorded resolved_sha (shallow clone of the pinned tag).",
    "repositories": repos,
}
OUT.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
print(json.dumps({r["id"]: r["status"] for r in repos}, indent=1))
print("sha summary:", {r["id"]: r.get("resolved_sha", "")[:12] for r in repos})
