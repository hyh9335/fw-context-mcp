import json
import io

# findings.json
fp = r".fw-validation/findings.json"
d = json.load(io.open(fp, encoding="utf-8"))
findings = d["findings"]
new = {
    "id": "F-V2-002",
    "phase": "V2_PUBLIC_REAL",
    "case": "Z2_zephyr_variant_pair",
    "severity": "API_CONTRACT",
    "title": "lookup_symbol with exact=True silently relaxes to an unrelated-symbol fallback when the name is absent",
    "reproduction": {
        "fixture": "zephyr v4.4.2 native_sim hello_world (Z1 index)",
        "query": "lookup_symbol {name: 'z_impl_printk', exact: true}"
    },
    "expected": "empty result (documented contract: exact=True = exact name match; the relaxed fallback is documented for prefix/LIKE lookups only)",
    "observed": [
        "returns z_impl_k_sleep rows (sched.c + generated syscall headers) with _fallback: True",
        "DB truth (controller oracle): z_impl_printk ABSENT from the index; the relaxation picked a symbol sharing only the z_impl_ token prefix"
    ],
    "analysis": "the _fallback marker preserves honesty at the row level, but the empty-contract of exact=True is violated: "
                "an existence check by an automated caller (non-empty == exists) gets a false positive. "
                "Low-medium severity: marked, deterministic, but semantics differ from the documented contract.",
    "classification": "FAIL_PRODUCT",
    "first_observed": "V2_PUBLIC_REAL"
}
if not any(f.get("id") == "F-V2-002" for f in findings):
    findings.append(new)
    d["findings"] = findings
    json.dump(d, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("findings total:", len(findings))

# result JSONs
ART = r".fw-validation/artifacts/V2"
z1 = {"case_id": "Z1_zephyr_hello", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
      "scenario": "Z1 native_sim hello_world via plain cmake (host toolchain), compile-DB truth check",
      "fixture": "zephyr v4.4.2@dccb0959 samples/hello_world BOARD=native_sim ZEPHYR_TOOLCHAIN_VARIANT=host",
      "env_fixes": ["python 3.12.10 source-built with libssl (zephyr requires >=3.12)", "gcc-multilib (32-bit native)",
                    "west+jsonschema+pyyaml venv"],
      "build": {"tu_count": 75, "qemu": "n/a (native_sim)"},
      "index": {"db_size_gb": 1.7, "note": "zephyr per-TU generated-header closure makes the DB ~1.7GB for 75 TUs — scale observation"},
      "queries": {"main": "samples/hello_world/src/main.c:9 is_definition=true (correct)",
                  "config_hash": "24ffaf0390f6ca58…", "build_system_detected": "unknown (cosmetic: cmake build not classified)"},
      "status": "PASS"}
z2 = {"case_id": "Z2_zephyr_variant_pair", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
      "scenario": "Z2 same sample with CONFIG_ASSERT=y; variant separation across two index dirs",
      "build": {"tu_count": 76, "delta_vs_z1": "+1 TU (assert TU)"},
      "queries": {"config_hashes": {"z1": "24ffaf0390f6ca58…", "z2": "0c8d26ea73ed60c9…"},
                  "variant_separation": "different index dirs → different config_hash ✓; main resolves identically ✓"},
      "finding": "F-V2-002 (exact=True silent fallback on absent name)",
      "status": "PASS (with F-V2-002 observation)"}
open(rf"{ART}/Z1/Z1.result.json", "w").write(json.dumps(z1, indent=2) + "\n")
open(rf"{ART}/Z2/Z2.result.json", "w").write(json.dumps(z2, indent=2) + "\n")
print("Z1/Z2 result JSONs written")
