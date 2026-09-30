"""Built-in dispatch bridges: callee QN → dispatch entry point QN.

WHY dispatch bridges: libclang's call graph is static — it sees
``EventQueue::call_every(callback)`` but has no way to know that
``callback`` will be invoked by ``EventQueue::dispatch_forever`` during
the event loop.  These bridges inject synthetic edges that connect
dispatch-registration APIs to their event-loop entry points, making
the static call graph correctly reflect the runtime control flow.

When a function calls a dispatch-registration method
(e.g. ``EventQueue::call_every``), a synthetic ``ref_kind='dispatch'`` edge
is created from the dispatch entry point (e.g. ``EventQueue::dispatch_forever``)
to the callback target.  This lets the call graph traverse through event loops
and thread starts.

Used by ``symbols.py`` (detection phase — creates ``PendingDispatch`` records)
and ``_postprocess.py`` (resolution phase — resolves them into ``refs`` rows).

At resolution time, user-defined entries from ``[call_graph.dispatch_bridges]``
in ``.fw-context/config.toml`` are merged on top.  Entries whose entry point
symbol does not exist in the index are silently skipped — the map can safely
list all supported platforms in one place without causing errors for
non-matching projects.

Type-erased ISR functions
--------------------------

Some ISR registration APIs type-erase the handler argument
(e.g. ``NVIC_SetVector(IRQn, (uint32_t)handler)``), making the function
pointer invisible to libclang.  ``_TYPE_ERASED_ISR_FUNCTIONS`` maps such
function names to the (0-based) argument index that carries the handler.
The source-line fallback in ``symbols.py`` uses this to extract the handler
name from the raw source line.

WHY source-line fallback: type-erased arguments (``(uint32_t)handler``) are
invisible to libclang's AST — the function pointer assignment is completely
hidden.  Without the source-line fallback, ISR registrations in CMSIS, Nordic
SDK, and STM32 HAL would produce no ``fn_pointer_assignment`` records,
breaking the indirect-call resolution chain entirely.

Registry v2 (T08 / ADR-010)
---------------------------

The flat ``api → entry`` map above became a rule table (``BridgeRule``) with
``handler_arg``/``object_arg`` indices, so two registration shapes are
modeled instead of one:

* **handler-arg triggers** (mbed ``EventQueue::call_every(cb)``,
  STM32 ``HAL_*_RegisterCallback``) — the callback travels in the argument
  list and the pending record carries it directly;
* **object-mediated triggers** (Zephyr ``k_work_submit(&w)``) — the argument
  is a *work object*, not a callback.  A REGISTER rule
  (``k_work_init(&w, handler)``, ``K_WORK_DEFINE`` init lists, FreeRTOS
  ``xTimerCreate(…, cb)``) links the object to its handler; the trigger
  resolves the handler through that object linkage in post-processing.

Every resolved dispatch edge is annotated with per-edge provenance in
``refs.source`` (``'bridge:<api_qn>'``) and object linkage facts are
persisted as ``fp_assignments`` rows with ``method='register'``.  Entries
whose entry point symbol does not exist in the index are no longer dropped
silently: post-processing records a skip reason (``dispatch_skip_report`` in
manifest.json + an index note in the log).

**Queue semantics are explicitly out of scope (ADR-010)**: EVENT_SEND /
EVENT_RECEIVE edges, message queues, and work-queue ordering are NOT modeled
— a queue producer stays unconnected to its consumer, and that absence is
documented here rather than improvised.  ``xTaskCreate*`` was removed from
the registry entirely: mapping the scheduler's task entry to
``prvTaskExitError`` produced a false dispatch edge (the task body is
invoked by the scheduler, not by the exit stub — the caller's indirect
fn-pointer argument edge already records the spawn).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BridgeRule:
    """One dispatch-bridge rule (registry v2, ADR-010).

    Attributes:
        api_qn: Qualified name of the registration/trigger API.  Detection
            (symbols.py) looks calls up by this exact name.
        operation: ``'trigger'`` — the call arms the dispatch (event loop,
            timer service, work queue); ``'register'`` — the call binds a
            handler to an object and produces no edge by itself.
        entry_qn: Qualified name of the dispatch entry point the registered
            handler eventually runs under (``None`` for pure REGISTER rules).
        handler_arg: 0-based index of the function-pointer handler argument,
            or ``None`` when the signature is template-erased (mbed) and any
            argument may carry the callback.
        object_arg: 0-based index of the handle/object argument whose
            linkage resolves the handler, or ``None`` when the call carries
            the handler directly.
        object_return: True when the object comes from the return value
            (``timer = xTimerCreate(…, cb)``) instead of an argument.
        certainty: ``'exact'`` for C signatures the rule pins down,
            ``'heuristic'`` for template-erased or inferred linkage.
        family: Registry family (mbed/zephyr/freertos/espidf/stm32) —
            documentation only, every family ships its own F08/F09 case.
    """
    api_qn: str
    operation: str
    entry_qn: str | None
    handler_arg: int | None = None
    object_arg: int | None = None
    object_return: bool = False
    certainty: str = "heuristic"
    family: str = ""


# Registry v2 (ADR-010): rules replace the flat map.  EXACT entry-point names
# are inherited from the v1 registry (research T08 §1.2).
_DISPATCH_BRIDGE_RULES: tuple[BridgeRule, ...] = (
    # ── mbed-os ─────────────────────────────────────────────────────
    BridgeRule("events::EventQueue::call", "trigger",
               "events::EventQueue::dispatch_forever", family="mbed"),
    BridgeRule("events::EventQueue::call_every", "trigger",
               "events::EventQueue::dispatch_forever", family="mbed"),
    BridgeRule("events::EventQueue::call_in", "trigger",
               "events::EventQueue::dispatch_forever", family="mbed"),
    BridgeRule("rtos::Thread::start", "trigger",
               "rtos::Thread::_thread_start", handler_arg=0, family="mbed"),
    BridgeRule("mbed::Timeout::attach", "trigger",
               "mbed::Timeout::_timeout_handler", handler_arg=0, family="mbed"),
    BridgeRule("mbed::Ticker::attach", "trigger",
               "mbed::Ticker::_ticker_handler", handler_arg=0, family="mbed"),
    # ── Zephyr ──────────────────────────────────────────────────────
    BridgeRule("k_work_submit", "trigger", "z_work_q_main",
               object_arg=0, family="zephyr"),
    BridgeRule("k_work_schedule", "trigger", "z_work_q_main",
               object_arg=1, family="zephyr"),
    BridgeRule("k_work_submit_to_queue", "trigger", "z_work_q_main",
               object_arg=1, family="zephyr"),
    BridgeRule("k_work_init", "register", None,
               object_arg=0, handler_arg=1, certainty="exact",
               family="zephyr"),
    # ── FreeRTOS ────────────────────────────────────────────────────
    BridgeRule("xTimerStart", "trigger", "prvTimerCallback",
               object_arg=0, family="freertos"),
    BridgeRule("xTimerStartFromISR", "trigger", "prvTimerCallback",
               object_arg=0, family="freertos"),
    BridgeRule("xTimerCreate", "register", None,
               object_return=True, handler_arg=4, certainty="exact",
               family="freertos"),
    # ── ESP-IDF ─────────────────────────────────────────────────────
    BridgeRule("esp_intr_alloc", "trigger", "<esp_intr_dispatch>",
               handler_arg=2, family="espidf"),
    # (intr_matrix_set dropped: its signature carries no handler argument at
    # all, so its v1 entry could never fire — dead registry weight.)
    # ── STM32 HAL ───────────────────────────────────────────────────
    BridgeRule("HAL_UART_RegisterCallback", "trigger", "HAL_UART_IRQHandler",
               handler_arg=2, certainty="exact", family="stm32"),
    BridgeRule("HAL_SPI_RegisterCallback", "trigger", "HAL_SPI_IRQHandler",
               handler_arg=2, certainty="exact", family="stm32"),
    BridgeRule("HAL_TIM_RegisterCallback", "trigger", "HAL_TIM_IRQHandler",
               handler_arg=2, certainty="exact", family="stm32"),
    BridgeRule("HAL_I2C_RegisterCallback", "trigger", "HAL_I2C_EV_IRQHandler",
               handler_arg=2, certainty="exact", family="stm32"),
    # (xTaskCreate / xTaskCreateStatic removed, see the module docstring:
    #  prvTaskExitError is a false dispatch entry for a scheduler spawn.)
)

_RULES_BY_API: dict[str, BridgeRule] = {r.api_qn: r for r in _DISPATCH_BRIDGE_RULES}


def lookup_bridge_rule(api_qn: str) -> BridgeRule | None:
    """Exact-name lookup of the bridge rule for *api_qn* (detection stage)."""
    return _RULES_BY_API.get(api_qn)


def merge_user_bridges(
    user_map: dict[str, str],
) -> dict[str, BridgeRule]:
    """Merge ``[call_graph.dispatch_bridges]`` additions over the builtins.

    A user entry for a known API *upgrades* the builtin rule's entry point
    (a REGISTER rule gains one and produces its edge from the registration
    site); an entry for an unknown API becomes a plain trigger rule with
    handler-arg semantics, matching v1's flat-map behavior.  Detection is
    builtin-only: user additions participate at resolution time (project
    config is not read during per-TU parsing).
    """
    rules = dict(_RULES_BY_API)
    for api, entry in (user_map or {}).items():
        builtin = rules.get(api)
        if builtin is not None:
            rules[api] = BridgeRule(
                api_qn=builtin.api_qn, operation=builtin.operation,
                entry_qn=entry, handler_arg=builtin.handler_arg,
                object_arg=builtin.object_arg,
                object_return=builtin.object_return,
                certainty=builtin.certainty, family=builtin.family)
        else:
            rules[api] = BridgeRule(api_qn=api, operation="trigger",
                                    entry_qn=entry, handler_arg=None)
    return rules


# v1 compatibility: flat map dispatch-registration API QN → dispatch entry
# point QN (trigger rules with a resolvable entry).  Kept because
# symbols.py's raw-text fallback and any external tooling key on it.
_DISPATCH_ENTRY_POINTS: dict[str, str] = {
    r.api_qn: r.entry_qn for r in _DISPATCH_BRIDGE_RULES
    if r.entry_qn is not None
}

# Type-erased ISR registration functions: function name → handler arg index (0‑based).
# These APIs cast the handler to an integer type so libclang cannot see the
# function‑pointer assignment.  The source‑line fallback uses this map to
# extract the handler name from the raw source line and create an fp_assignment.
_TYPE_ERASED_ISR_FUNCTIONS: dict[str, int] = {
    # ── ARM CMSIS / Nordic / STM32 ──────────────────────────────────
    "NVIC_SetVector":      1,   # NVIC_SetVector(IRQn, (uint32_t)handler)
    "__NVIC_SetVector":    1,
}

# Unqualified dispatch method names — used for fast pre-filtering in
# source-line fallback (``symbols.py:_run_source_line_fallback``).
_DISPATCH_METHOD_NAMES: frozenset[str] = frozenset({
    qn.rsplit("::", 1)[-1] for qn in _DISPATCH_ENTRY_POINTS
})
