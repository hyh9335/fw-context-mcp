# FINAL ECONOMIC CLOSURE ADDENDUM — IDENTITY BINDING (§3)

## Binding

| Field | Value |
|---|---|
| HEAD | `e828081` |
| Source remediation HEAD | `74d63c2` |
| Branch | `m3b-native-v2` |
| Working tree | clean source (only evidence/state JSON files dirty) |
| Clang | `clang version 22.1.8` (https://github.com/llvm/llvm-project ca7933e47d3a3451d81e72ac174dcb5aa28b59d1) |
| FreeRTOS corpus pin | `8be86d4` (Optional PRIVILEGED_DATA wrapper + Partner-Supported-Ports submodule update #1433) |
| list.c SHA256 | `DC7A0F35DB80859D107ADB5D3EDFAC157D721C65FA73DD19D6D9D5F6DB3CD693` |
| queue.c SHA256 | `C24692767944A1726B6386273DFD0D26DA9287701CAEDB82714045E2EEBCE9E9` |
| final_closure_remediation_gate.py SHA256 | `58C6B81410B600C48E14A6D38556C8B8D72B2C1B91BF782D01225E9F1D641C8D` |
| final_closure_remediation_gate.json SHA256 | `98962BA343AC714DB96B5CB7C3B03C8F1383A668F1620ED871A469C5B9F97410` |

## Production defaults (frozen)

| Env var | Default | Source | Effect |
|---|---|---|---|
| `FW_CONTEXT_M6_READ_AUTHORITY` | `"1"` | authority.py | M6 is read authority (RuntimeError if store missing) |
| `FW_CONTEXT_M6_WRITE_AUTHORITY` | `"1"` | _unit_processor.py | M6 direct materialization hook active per-TU |
| `FW_CONTEXT_M6_LEGACY_CONVERSION` | `"0"` | runner.py | Legacy-DB→M6 conversion is OPT-IN compat only |
| `FW_CONTEXT_LEGACY_COMPATIBILITY_MODE` | `False` | _unit_processor.py | Legacy index.db NOT created in normal mode |

## Verdict prior state (provisional)

`ORIGINAL_MULTI_VERSION_REUSE_GOAL_CLOSED` — TREATED AS PROVISIONAL per §2.
The previous final verdict is NOT inherited as assumption; this addendum re-derives the evidence.

## Machine

- Windows; DSH sandbox: danger-full-access.
- Clang target: `--target=x86_64-pc-windows-msvc`.
- Resource include: `C:\Program Files\LLVM\lib\clang\22\include`.
