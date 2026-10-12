# M9 BASELINE_FREEZE Report

**Phase**: M9 BASELINE_FREEZE (re-establish M8 baselines on current HEAD + freeze new end-to-end thresholds)
**HEAD**: `99d8932f95cd6dcc0835f02d92edbec9e97e1ad4` (M7-FINAL-ECONOMIC-ADDENDUM)
**Frozen at**: 2026-10-12T07:11:27Z (UTC)
**Tooling**: libclang 18.1.1 (python wheel) — matches M8 exactly
**Python**: `C:\Users\h00969597\AppData\Local\Programs\Python\Python311\python.exe`
  (the bundled venv at `D:\deepseek harness\resources\app.asar\dsh\.venv\Scripts\python.exe` is **absent** in this checkout; system Python 3.11 with libclang 18.1.1 used instead — same libclang version as M8, so AST output is comparable)
**Fixture**: FreeRTOS-Kernel @ `8be86d4a24fd4091f8f4192018423ab590f408db` (unchanged from M8)

---

## 1. Re-run results vs M8 (drift analysis)

The three M8 bench scripts were re-run on HEAD `99d8932`. **Every structural/safety measurement is bit-identical to M8; only absolute timings drifted within machine noise.**

### 1.1 `baseline_parse.py` — per-TU parse cost

| Metric | M8 | M9-baseline | Drift | Verdict |
|---|---|---|---|---|
| Total parses | 30 | 30 | 0 | ✓ |
| Mean parse time (s/TU) | 0.0146 | 0.0130 | -0.0016 | timing-only noise |
| Total frontend cost (s) | 0.66 | 0.654 | -0.006 | timing-only noise |
| AST cursor count `list.c` | 1821/1823/1827 | 1821/1823/1827 | 0 | ✓ bit-identical |
| AST cursor count `queue.c` | 4080/4262/4370/4383/4495 | 4080/4262/4370/4383/4495 | 0 | ✓ bit-identical |

**Drift explanation**: the ~11% drop in mean parse time is machine/load noise on a 15ms-scale measurement (timer resolution + OS scheduling). The AST cursor counts — the structural ground truth — are byte-identical, confirming the same libclang 18.1.1 produces the same ASTs.

### 1.2 `extended_benchmark.py` — AST fingerprints + certificate safety

**AST fingerprints — all 15 (source × build) fingerprints byte-identical to M8:**

| Source | Build | M8 fingerprint | M9-baseline fingerprint | Match |
|---|---|---|---|---|
| `list.c` | BASE/NO_PREEMPT/FULL_FEATURE | `fe96c27d77155545` | `fe96c27d77155545` | ✓ |
| `list.c` | NO_MUTEXES | `89a1b65d1be4ff07` | `89a1b65d1be4ff07` | ✓ |
| `list.c` | STATIC_ONLY | `4ad9f0047ab5dd90` | `4ad9f0047ab5dd90` | ✓ |
| `queue.c` | BASE | `5294708edf59a5b3` | `5294708edf59a5b3` | ✓ |
| `queue.c` | NO_PREEMPT | `f3dc68ab6d6faf39` | `f3dc68ab6d6faf39` | ✓ |
| `queue.c` | **NO_MUTEXES (correct)** | `ba2e9f57a49c97a2` | `ba2e9f57a49c97a2` | ✓ |
| `queue.c` | STATIC_ONLY | `dd6ec373bc215051` | `dd6ec373bc215051` | ✓ |
| `queue.c` | FULL_FEATURE | `3e3530addb919313` | `3e3530addb919313` | ✓ |
| `tasks.c` | BASE/FULL_FEATURE | `93b243de8cb40f8f` | `93b243de8cb40f8f` | ✓ |
| `tasks.c` | NO_PREEMPT | `5ce7e7680f3a18c0` | `5ce7e7680f3a18c0` | ✓ |
| `tasks.c` | NO_MUTEXES | `a67a28aaef69fd24` | `a67a28aaef69fd24` | ✓ |
| `tasks.c` | STATIC_ONLY | `d9159bba6ad096f7` | `d9159bba6ad096f7` | ✓ |

The M8 report's PCH cross-build corruption case (`2613bea3…` stale vs `ba2e9f57…` correct) is re-confirmed: `ba2e9f57a49c97a2` is the correct `queue.c`/NO_MUTEXES L2 fingerprint on this HEAD.

**Duplication ratio**: 1.25× (12 unique ASTs / 15 parses) — matches M8.

**Certificate safety (L1 → L2 predictor) — 0 false-positives across all 30 build-pairs:**

| Source | Pairs | L1 false-positives (M8) | L1 false-positives (M9) | Match |
|---|---|---|---|---|
| `list.c` | 10 | 0 | 0 | ✓ |
| `queue.c` | 10 | 0 | 0 | ✓ |
| `tasks.c` | 10 | 0 | 0 | ✓ |
| **Total** | **30** | **0** | **0** | ✓ |

L0 (source-only) false-positives remain non-zero (list.c=7, queue.c=10, tasks.c=9), re-confirming that a source-only key is unsafe and the L1 key (source + config + header closure) is required.

### 1.3 `certificate_savings.py` — workload-dependent savings

| Scenario | M8 | M9-baseline | Match |
|---|---|---|---|
| A: global config — parses skipped | 0/25 | 0/25 | ✓ |
| A: global config — savings % | 0.0% | 0.0% | ✓ |
| B: per-file config — parses skipped | 16/25 | 16/25 | ✓ |
| B: per-file config — savings % | 64.0% | 64.0% | ✓ |

### 1.4 Drift verdict

**M8 baselines are re-confirmed on HEAD `99d8932`.** All structural (fingerprints, cursor counts, false-positive rates, savings ratios) and safety (L1 predictor perfection) measurements are bit-identical. Absolute timing drifted ~11% on mean parse (0.0146→0.0130s) — within machine noise on a 15ms measurement, not a regression. The M8 feasibility verdict (LIMITED — safe certificate exists but captures 0% on global-config / 64% on per-file-config) still holds.

---

## 2. New M9 `baseline_freeze.json` numbers

End-to-end frontend wall-clock baselines (parse + extract symbols via `walk_preorder`, **NO M6 materialization**), measured by the new `bench/m9_baseline_freeze.py`:

| Workload | Total parses | Total symbols | Total wall (s) | Theoretical cert skips | Cert savings % |
|---|---|---|---|---|---|
| **W1: Global config (NEGATIVE control)** | 25 | 46,969 | **0.969** | 0/25 | 0.0% |
| **W2: Per-file config (POSITIVE control)** | 25 | 48,075 | **1.015** | 16/25 | 64.0% |

The theoretical skip counts (0 and 16) reproduce M8 exactly — the L1 key construction in the new script is byte-compatible with M8's `certificate_savings.py`. The measured wall-clock is the **no-reuse cost** (current architecture); the M9 prototype must beat W2 by ≥30% after cert overhead and must not regress W1 by >5%.

Per-TU parse time (combined, 50 parses): **0.01632 s/TU** (mean), consistent with M8's 0.0146 within noise.

Full detail (per-build wall-clock, per-TU parse/extract stats) in `evidence/baseline_freeze.json`.

---

## 3. Frozen thresholds

File: `.dsh-control/m9_frontend_reuse/state/baseline_freeze_thresholds.json`

```json
{
  "schema": "m9_baseline_freeze_thresholds/v1",
  "head": "99d8932f95cd6dcc0835f02d92edbec9e97e1ad4",
  "frozen_at": "2026-10-12T07:11:27.663165+00:00",
  "thresholds": {
    "positive_control_target_savings_pct_min": 30,
    "negative_control_max_overhead_pct": 5,
    "certificate_cost_per_tu_ms_max": 1.0,
    "frontend_false_reuse_max": 0,
    "certificate_false_accept_max": 0,
    "gt_parity_failures_max": 0
  },
  "baseline_measurements": {
    "baseline_mean_parse_time_per_tu_s": 0.01632,
    "baseline_positive_control_wall_clock_s": 1.015,
    "baseline_negative_control_wall_clock_s": 0.969
  }
}
```

**HARD thresholds** (zero tolerance — a single violation blocks M9 acceptance):
- `frontend_false_reuse_max = 0`
- `certificate_false_accept_max = 0`
- `gt_parity_failures_max = 0`

**Non-HARD thresholds** (bounded slack):
- `positive_control_target_savings_pct_min = 30` (M8 measured 64% theoretical; M9 must deliver ≥30% real after cert overhead)
- `negative_control_max_overhead_pct = 5` (global-config must not regress >5%)
- `certificate_cost_per_tu_ms_max = 1.0` (cert computation must stay cheap)

---

## 4. Scripts that needed fixing and how

**No M8 script needed fixing.** All three M8 bench scripts (`baseline_parse.py`, `extended_benchmark.py`, `certificate_savings.py`) ran cleanly on the first invocation.

- The `Path(__file__).resolve().parents[3]` depth is correct for this checkout's layout (`bench/foo.py` → `parents[3]` = repo root).
- The bundled Python venv at `D:\deepseek harness\resources\app.asar\dsh\.venv\Scripts\python.exe` referenced in the task brief is **absent** in this checkout. The system Python 3.11 at `C:\Users\h00969597\AppData\Local\Programs\Python\Python311\python.exe` has libclang 18.1.1 installed (exact version match to M8), so AST output is directly comparable. This was verified before running: `import clang.cindex` succeeds and a test parse of `list.c` returns 494 cursors (the no-config-include count).

**One new script created**: `bench/m9_baseline_freeze.py` (under `.dsh-control/m9_frontend_reuse/bench/`). It mirrors the M8 `clang.cindex` direct approach (`idx.parse` + `walk_preorder`), reuses the byte-identical `BASE_CONFIG` template so L1/L2 keys are M8-compatible, and adds an `_extract_symbols` step that walks the AST and collects symbol records (kind/spelling/usr/file/line) — the frontend work upstream of M6, with **no DB writes / no M6 materialization**. A convoluted `if False else` expression in the initial summary block was cleaned up before the final run.

---

## 5. Evidence files written

### Re-run (overwrote M8 evidence in place — M8 numbers re-confirmed, now stamped on HEAD `99d8932`):
- `.dsh-control/m8_frontend_reuse/evidence/baseline.json` (44,101 bytes)
- `.dsh-control/m8_frontend_reuse/evidence/extended_benchmark.json` (14,047 bytes)
- `.dsh-control/m8_frontend_reuse/evidence/certificate_savings.json` (741 bytes)

### New M9 evidence:
- `.dsh-control/m9_frontend_reuse/evidence/baseline_freeze.json` (4,543 bytes) — end-to-end wall-clock baselines for both workloads, `head` + `frozen_at` fields set
- `.dsh-control/m9_frontend_reuse/state/baseline_freeze_thresholds.json` — frozen acceptance thresholds + baseline measurements + M8 re-confirmation record

### New M9 bench script:
- `.dsh-control/m9_frontend_reuse/bench/m9_baseline_freeze.py` — the measurement script (mirrors M8 `clang.cindex` approach; runnable via `cd .dsh-control\m9_frontend_reuse\bench; python m9_baseline_freeze.py`)

---

## 6. Conclusion

M8 baselines are frozen on HEAD `99d8932`. The M9 prototype now has a frozen reference point: it must deliver ≥30% real wall-clock savings on the per-file-config positive control (W2, baseline 1.015s), must not regress the global-config negative control (W1, baseline 0.969s) by >5%, and must hold zero false-frontend-reuse / zero false-cert-accept / zero GT-parity-failure (all HARD). The certificate L1→L2 safety guarantee (0 false-positives in 30 build-pairs) and the 64% theoretical ceiling on the positive control are re-confirmed bit-identical to M8.
