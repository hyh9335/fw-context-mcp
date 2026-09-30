# SINGLE ENTRY — INSTALL + START PROJECT-LOCAL AUTONOMOUS CONTROL LOOP

You are in the CURRENT fw-context repository inside an already-running DeepSeek Harness host.

The human explicitly authorizes you to install and start a PROJECT-LOCAL autonomous engineering controller for THIS repository only.

This is a single-entry bootstrap. Do not ask the human to restart DSH. Do not ask the human to launch a second DSH process.

# 0. NON-NEGOTIABLE ISOLATION

Everything specific to this controller MUST stay inside the current repository.

You MUST NOT:

- edit `$DSH_HOME/AGENTS.md`;
- edit `$DSH_HOME/cordis.patch.yml`;
- edit any DSH profile `cordis.patch.yml`;
- install any project-specific DSH plugin into the Host;
- modify shared presets;
- expose new fw-context-specific tools globally;
- restart the DSH Host;
- require a second DSH process.

Use only generic capabilities already available in this DSH session.

The allowed persistent project-local locations are:

- `<repo>/AGENTS.local.md`
- `<repo>/.dsh-control/**`

If the current directory is not the intended fw-context Git repository:
STOP and report the workspace mismatch.

# 1. DETECT CURRENT DSH CAPABILITIES

Inspect the tools actually available to this session.

Detect:

A. Goal capability:
- get_goal
- create_goal
- update_goal

B. Delegation capability, in preference order:
- continuable spawn subagent + send_message/list_agents
- one-shot spawn
- one-shot fork
- workflow agent()

C. shell/file-edit capability.

Do not assume exact DSH version.

Record detected mode in:

`.dsh-control/state/DSH_CAPABILITIES.json`

## Delegation strategy

### MODE A — continuable available
Use:
- current top-level session = Controller
- one continuable Lead
- Guardian = FRESH review agent by default; optionally continuable only if independence is preserved
- Verifier = ALWAYS fresh one-shot

### MODE B — only one-shot delegation available
Use:
- current top-level session = Controller
- fresh Lead worker for each bounded work package
- fresh Guardian at every control gate
- fresh Verifier at every acceptance attempt

All continuity lives in `.dsh-control/state/` and repository commits.

MODE B is fully valid and may actually reduce long-context drift.

Do not modify DSH global configuration merely to obtain continuable children.

If NO subagent/delegation capability exists at all:
use the current session as Lead/Controller only long enough to create the project-local state,
then report a blocker because independent verification cannot be provided.
Do NOT install global plugins.

# 2. DISCOVER PROJECT ROOT

Resolve the canonical Git root.

Write:

`.dsh-control/state/PROJECT_ROOT.json`

containing:
- canonical project root
- bootstrap timestamp
- bootstrap HEAD
- current branch

All controller scripts must refuse to operate outside this root.

# 3. INSTALL PROJECT-LOCAL CONTROL TREE

Create:

`.dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md`
`.dsh-control/contracts/STAGE_GATE.json`
`.dsh-control/prompts/CONTROLLER.md`
`.dsh-control/prompts/LEAD.md`
`.dsh-control/prompts/GUARDIAN.md`
`.dsh-control/prompts/VERIFIER.md`
`.dsh-control/gates/`
`.dsh-control/state/`
`.dsh-control/evidence/`

The current immutable milestone is:

`M3B_NATIVE_V2_READ_ENGINE_COMPLETE`

# 4. INSTALL/UPDATE PROJECT-LOCAL AGENTS.local.md

Use the DSH project-instruction mechanism.

Create or append a clearly delimited block to repository-root:

`AGENTS.local.md`

Do NOT overwrite unrelated existing instructions.

Use markers:

`<!-- FWCTX_AUTONOMOUS_CONTROLLER_BEGIN -->`
`<!-- FWCTX_AUTONOMOUS_CONTROLLER_END -->`

The block must state:

- this repository is governed by `.dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md`;
- PROJECT_STATUS and ledgers are caches, not truth;
- current production code + executable hard gates outrank narration;
- no agent may weaken the immutable milestone;
- no agent may declare the project Goal complete unless the project-local final hard gate exits 0;
- same-view legacy fallback is safe compatibility but is NOT v2-native read;
- any agent asked to review completion must reconstruct the denominator from current code;
- production code changes outside the target Goal require normal engineering justification;
- this block applies only within this Git project.

Do not put large role prompts directly into AGENTS.local.md.
Keep it short and point to `.dsh-control/`.

# 5. IMMUTABLE M3B CONTRACT

Write a human-owned immutable contract with these semantics:

M3B means:

Every CORE_NORMAL_USE_SEMANTIC production tool computes its semantic answer from v2 World-scoped semantic facts/primitives.

Routing through v2 is insufficient.

This path:

`QueryView -> v2 routing -> V2UnsupportedOperation -> legacy semantic execution`

counts as LEGACY SEMANTIC EXECUTION.

Required machine counters:

- CORE_NORMAL_USE_SEMANTIC_TOOLS
- CORE_V2_ROUTED
- CORE_V2_NATIVE_READ
- CORE_LEGACY_SEMANTIC_EXECUTION
- CORE_UNSAFE_FALLBACK
- UNRESOLVED_NORMAL_USE_V2_BUGS

M3B numeric gate:

- CORE > 0
- CORE_V2_ROUTED == CORE
- CORE_V2_NATIVE_READ == CORE
- CORE_LEGACY_SEMANTIC_EXECUTION == 0
- CORE_UNSAFE_FALLBACK == 0
- UNRESOLVED_NORMAL_USE_V2_BUGS == 0

Additional gates:

- cross-family pinned session PASS
- multi-build ambiguity PASS
- historical no-drift PASS
- real production request-path PASS
- regression PASS
- fresh verifier accepted current HEAD
- fresh guardian accepted current HEAD
- durable checkpoint exists for accepted HEAD

Agents may change PLAN.
Agents may NOT change this contract without human escalation.

# 6. BUILD A CODE-DERIVED AUTHORITY SCANNER

Create a repository-local scanner.

Recommended command:

`python .dsh-control/gates/scan_authority_state.py`

It MUST derive its counters from CURRENT production code / executable probes.

Do NOT source counters from:
- PROJECT_STATUS.md
- AUTHORITY_LEDGER.md
- Lead reports.

Use a combination of:
- production handler registry enumeration;
- AST/static inspection;
- imports/introspection;
- runtime instrumentation;
- request-path probes.

It must classify each core production semantic tool as at least:

- V2_NATIVE
- V2_ROUTED_LEGACY_EXEC
- LEGACY_SOLE
- SPECIALIZED_OPTIONAL
- OPERATIONAL_NON_SEMANTIC
- UNKNOWN

Produce machine JSON under:

`.dsh-control/evidence/authority_state.json`

Include current Git HEAD and dirty status.

# 7. BUILD THE STAGE HARD GATE

Create:

`python .dsh-control/gates/run_stage_gate.py`

The gate must:

1. verify project root;
2. regenerate authority_state.json;
3. run mandatory focused tests;
4. read the latest verifier acceptance artifact;
5. read latest guardian acceptance artifact;
6. require both artifacts to name EXACTLY the current tested HEAD;
7. reject stale acceptance from older HEAD;
8. check regression result;
9. evaluate immutable formula;
10. exit non-zero on ANY failed clause.

No warning-pass.

Output exact failures.

# 8. HARD-GATE SELF TESTS

Before actual engineering work, prove the controller cannot fool itself.

At minimum:

TEST A:
simulate/current baseline has CORE=29, native=4, legacy_exec=25.
Expected gate FAIL.

TEST B:
write PROJECT_STATUS claiming native=29.
Scanner must still derive actual code truth and gate FAIL.

TEST C:
guardian artifact says ACCEPT but machine gate fails.
Gate FAIL.

TEST D:
verifier artifact says PASS but machine gate fails.
Gate FAIL.

TEST E:
acceptance artifacts are from an older HEAD.
Gate FAIL.

TEST F:
all real clauses pass on same HEAD.
Gate PASS.

Store self-test evidence.

Do NOT return to the human after self-tests.
Proceed directly.

# 9. ROLE PROMPTS

Install concise role prompts.

## Controller
Owns the control loop, not most coding.
Only Controller may decide to request completion of the parent project Goal.

## Lead
Owns implementation/test remediation.
Cannot change immutable contract.

## Guardian
Must be independent.
Must check ORIGINAL immutable Goal against CURRENT_STAGE.
Must detect scope shrink.
Should normally be FRESH for major reviews.

## Verifier
Always fresh for acceptance.
Must attempt to disprove completion.
Must independently derive denominator.
Never inherit Lead reasoning/history intentionally.

# 10. STATE MODEL

Maintain:

`.dsh-control/state/CURRENT_STAGE.md`
`.dsh-control/state/PROJECT_PLAN.md`
`.dsh-control/state/PROJECT_STATUS.md`
`.dsh-control/state/AUTHORITY_LEDGER.md`
`.dsh-control/state/DECISION_LOG.md`

These are useful caches.

They are NOT Hard Gate truth.

At every controller round compare them against scanner output and correct drift.

# 11. CREATE THE DSH PERSISTED GOAL IN THIS CURRENT SESSION

If Goal tools are available:

Check get_goal first.

If an unrelated active Goal already exists in THIS session:
do not overwrite it silently.
If this session was clearly created for this fw-context task, reconcile it.
Otherwise create/use a dedicated fw-context session in the same DSH workspace if the UI/runtime permits without restarting Host.

Goal text:

"Complete fw-context M3B Native v2 Read Engine according to
.dsh-control/contracts/IMMUTABLE_GOAL_CONTRACT.md.
Run autonomous Controller -> Lead -> Fresh Guardian -> Fresh Verifier loops.
Do not complete this Goal unless the repository-local executable hard gate passes
against the current durable checkpoint."

Use a high bounded round budget, approximately 128–256 when configurable.

If Goal tools are unavailable but automatic continuation can be represented by another already-loaded DSH primitive:
use the existing mechanism without changing Host config.

Do not install a global Goal plugin.

# 12. START THE CONTROL LOOP IMMEDIATELY

Do not stop after installation.

Controller must now:

1. run scanner;
2. reconcile current status;
3. request a FRESH Guardian pre-stage review;
4. select the highest-leverage failing hard-gate clause;
5. assign a bounded work package to Lead;
6. Lead implements/fixes/tests;
7. rerun scanner/gates;
8. repeat until candidate acceptance.

# 13. CONTROLLER ITERATION

Normal loop:

CURRENT CODE
-> SCANNER
-> FRESH GUARDIAN REVIEW
-> LEAD WORK
-> TEST
-> SCANNER
-> HARD GATE
-> if machine clauses still fail: continue Lead work
-> when engineering clauses appear green: FRESH VERIFIER
-> verifier FAIL: remediate, then NEW verifier
-> verifier PASS: FRESH Guardian completion review
-> checkpoint commit
-> rerun final gate against checkpoint
-> only then complete Goal.

Do not ask human for routine failures.

# 14. FRESH GUARDIAN STRATEGY

Unlike earlier designs, default Guardian should be fresh at major decision gates.

Reason:
a long-lived Guardian can drift together with the Lead.

Each Guardian invocation reads:
- immutable contract;
- code-derived scanner result;
- git diff/current HEAD;
- current stage/plan/status.

It outputs:

- CONTINUE
- CONTINUE_WITH_CORRECTION
- REPLAN
- REJECT_COMPLETION
- ESCALATE_TO_USER
- ACCEPT_COMPLETION

ACCEPT_COMPLETION is forbidden if machine gate is not already engineering-green.

# 15. LEAD CONTINUITY

If continuable subagent exists:
keep one durable Lead and steer it with new gate failures.

If not:
spawn a fresh Lead each bounded work package and give it:
- immutable contract;
- current scanner evidence;
- current stage;
- relevant diffs/tests.

The project files provide continuity.

# 16. VERIFIER

Verifier is ALWAYS fresh.

It independently checks actual handler paths.

For M3B it must explicitly identify:
- actual CORE denominator;
- v2-native reads;
- V2UnsupportedOperation -> legacy paths;
- hidden legacy DB helper calls;
- unsafe fallback;
- cross-tool session pinning;
- historical/multi-build semantics.

PASS is invalid if it did not inspect execution authority, not just routing.

# 17. NO SEPARATE DSH PROCESS

This controller must continue inside the current DSH Host.

Do not create:
- dedicated DSH profile;
- dedicated DSH server;
- project-only Cordis patch;
- custom Host plugin.

Isolation is achieved by:
- workspace/session cwd;
- project-local AGENTS.local.md;
- project-local files;
- per-session Goal;
- subagents created under this project session.

# 18. OTHER PROJECTS MUST REMAIN UNAFFECTED

Before continuing actual engineering work verify:

- DSH_HOME files unchanged;
- profile files unchanged;
- no Host plugin installed;
- no global tool changed;
- project controller files exist only under this repository.

Record this in bootstrap evidence.

# 19. REPORTING

Do not return to human after bootstrap.

Do not return after:
- one work package;
- verifier failure;
- guardian correction;
- gate failure;
- replan.

Continue autonomously.

Return only when:

A. the current immutable M3B hard gate passes on a durable checkpoint and the persisted Goal is completed;

or

B. a genuine immutable-contract/product decision requires human authority.

# 20. START NOW

Perform installation, self-tests, Goal creation, role creation, and actual engineering loop in this same session/Host now.
