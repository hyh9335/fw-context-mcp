# C7 — Planted source-deterministic init/submit pairing break (solver-visible)

## Goal item
G-4/G-6 (dead config guard → required submit path unavailable). Corpus
minimum role: the ONE bounded planted E2E case added by wave-1.1 (R4).

## Fixture
`fixture/sensor_module.c` defines `sensor_read` (live under the pinned
build). `sensor_submit` and the module's ONLY `work_submit` call are enclosed
by `#if defined(CONFIG_SENSOR_IRQ_PATH)`. No Kconfig source defines
`CONFIG_SENSOR_IRQ_PATH` (the real symbol is `CONFIG_SENSOR_IRQ_MODE`), so
the guarded branch is dead under every configuration.

## Pinned build identity
`-DCONFIG_SENSOR_ENABLE=1` (CMakeLists `target_compile_definitions`).
`CONFIG_SENSOR_IRQ_PATH` is never defined.

## Two-part separation
- part A (provable): source-level mechanism — guarded submit path compiled
  out; `work_submit` has no live caller; `sensor_submit` has no compiled
  definition.
- part B (required-but-unavailable): runtime occurrence (whether the work
  item would ever be queued/executed) — never assertable from source.
