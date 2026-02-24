# Dev Agent Plugin ("Smed")

Autonomous bug-fixing agent for the Overblick framework. Finds bugs,
writes fixes, runs tests, and creates pull requests — all without
human intervention. Built on the core `AgenticPluginBase` framework.

> **Status:** Functional in dry-run mode. Not yet battle-tested in
> production (live mode). Enable with `dry_run: false` after validation.

## What It Does

- **Discovers** bugs from three sources: GitHub issues, log file scanning,
  and IPC messages from other agents
- **Analyzes** root causes using `opencode` with Devstral 2 123B (local
  LLM via LM Studio — zero API cost)
- **Fixes** bugs by writing code changes in an isolated git workspace
- **Tests** every fix with pytest before committing — no untested code
  leaves the forge
- **Creates PRs** via `gh` CLI — never commits to main directly
- **Learns** from outcomes (successes and failures) to improve over time

## Architecture

```
DevAgentPlugin (AgenticPluginBase)
  |
  |-- create_observer() ----------> BugObserver
  |                                    |-> IPC queue (bug_report, log_alert)
  |                                    |-> LogWatcher (scans identity logs)
  |                                    \-> DevAgentDB (active bug tracking)
  |
  |-- get_action_handlers() -------> build_dev_agent_handlers()
  |                                    |-> AnalyzeBugHandler
  |                                    |-> FixBugHandler
  |                                    |-> RunTestsHandler
  |                                    |-> CreatePRHandler
  |                                    |-> NotifyOwnerHandler
  |                                    |-> CleanWorkspaceHandler
  |                                    \-> SkipHandler
  |
  \-- get_planning_prompt_config() -> PlanningPromptConfig
```

### The Agentic Loop

Each tick runs the five-phase cycle provided by `core/agentic/`:

1. **OBSERVE** — `BugObserver` drains the IPC queue, scans log files for
   new ERROR/CRITICAL patterns, and queries the database for active bugs.
   Returns a `DevAgentObservation` snapshot.

2. **THINK** — Assembles context: current goals, recent action history,
   accumulated learnings. Everything formatted as text for the LLM.

3. **PLAN** — LLM planner receives the full world state and produces an
   `ActionPlan` — an ordered list of `PlannedAction` with priorities and
   reasoning. Actions are validated against the 7 registered types.

4. **ACT** — `ActionExecutor` dispatches each planned action to the
   matching handler. Enforces `max_actions_per_tick` (default 3). Each
   handler returns an `ActionOutcome` (success/failure + result/error).

5. **REFLECT** — LLM reviews the tick's outcomes and extracts
   `AgentLearning` records (category, insight, confidence). These are
   stored in the database and fed back into future planning context.

### Bug Discovery

Bugs arrive from three sources:

1. **IPC messages** — The GitHub agent sends `bug_report` messages when
   it finds issues labeled `"bug"`. The supervisor sends `log_alert`
   messages for critical errors. Messages are queued and processed on the
   next tick.

2. **Log scanning** — `LogWatcher` reads log files from configured
   identities (e.g. anomal, cherry, blixt, stal) looking for
   `ERROR`/`CRITICAL` lines and Python tracebacks. It tracks byte offsets
   per file to avoid re-processing old entries. Handles log rotation
   gracefully (resets offset if file shrinks).

3. **Database** — Bugs that were discovered in previous ticks but remain
   unresolved are included in every observation. This ensures bugs aren't
   forgotten between ticks.

All bugs are deduplicated by `(source, source_ref)` before insertion.

### Bug Lifecycle

```
NEW ──────> ANALYZING ──> NEW (with analysis stored)
  |                         |
  +─────────────────────────+──> FIXING ──> TESTING
                                              |
                                    ┌─────────┴──────────┐
                                    |                     |
                              Tests pass            Tests fail
                                    |                     |
                              FIXING (committed)    attempts < max?
                                    |                  |         |
                                 CREATE_PR           yes         no
                                    |                 |          |
                                  FIXED             NEW       FAILED
```

Each bug allows up to `max_fix_attempts` (default 3). After that, the
bug is marked FAILED and the owner is notified.

### Fix Pipeline (FixBugHandler)

The core fix pipeline runs as a single handler:

```
1. workspace.ensure_cloned()      — Clone repo if needed
2. workspace.create_branch()      — "fix/42-api-500" from main
3. opencode_runner.fix_bug()      — Devstral 2 writes the fix
4. test_runner.run_tests()        — pytest validates
5a. Tests pass → workspace.commit_and_push()
5b. Tests fail → log failure, increment attempt counter, retry next tick
```

All five steps must succeed in sequence. A failure at any step is
recorded as a `FixAttempt` in the database for learning.

## Action Types

| Action | Handler | What it does |
|--------|---------|-------------|
| `analyze_bug` | AnalyzeBugHandler | Read-only analysis via opencode (no code changes) |
| `fix_bug` | FixBugHandler | Full pipeline: sync → branch → fix → test → commit |
| `run_tests` | RunTestsHandler | Run pytest in workspace, return pass/fail + stats |
| `create_pr` | CreatePRHandler | Create GitHub PR via `gh` CLI |
| `notify_owner` | NotifyOwnerHandler | Send Telegram notification to repo owner |
| `clean_workspace` | CleanWorkspaceHandler | Delete old fix/ branches (local + remote) |
| `skip` | SkipHandler | Explicitly do nothing (logs reasoning for learning) |

## Default Goals

| Priority | Goal | Description |
|----------|------|-------------|
| 90 | `fix_bugs` | Fix bugs from GitHub issues and log files. Branch, fix, test, PR. |
| 80 | `fix_log_errors` | Monitor identity logs for ERROR/CRITICAL. Create bug reports. |
| 70 | `maintain_test_health` | Ensure test suite passes after every fix. Zero regressions. |
| 40 | `keep_workspace_clean` | Delete merged branches, sync main regularly. |

## Configuration

In the identity's `personality.yaml` under `operational.dev_agent`:

```yaml
operational:
  plugins: ["dev_agent"]
  dev_agent:
    repo_url: "https://github.com/user/repo.git"
    workspace_dir: "workspace/repo"      # Relative to data/<identity>/
    default_branch: "main"
    dry_run: true                        # Safe by default — no writes
    max_fix_attempts: 3                  # Attempts per bug before FAILED
    max_actions_per_tick: 3              # Action budget per cycle
    tick_interval_minutes: 30            # How often to check

    opencode:
      model: "lmstudio/devstral-2-123b-iq5"
      timeout_seconds: 600               # 10 min per opencode invocation

    log_watcher:
      enabled: true
      scan_identities:                   # Which identities' logs to scan
        - anomal
        - cherry
        - blixt
        - stal

    github:
      monitor_issues: true               # (Future) direct GitHub API polling
      issue_labels: ["bug"]
      repos: ["user/repo"]
```

### Secrets

No secrets are required for dry-run mode. For live mode:

- `gh` CLI must be authenticated (`gh auth login`) for PR creation
- The git workspace needs push access to the repo (SSH key or HTTPS token)
- Optional: `telegram_bot_token` + `telegram_chat_id` for owner notifications

### Going Live

1. Validate in dry-run mode — check logs for `DRY RUN:` messages
2. Verify the workspace clone at `data/smed/workspace/overblick/`
3. Ensure `gh auth status` shows authenticated
4. Set `dry_run: false` in `personality.yaml`
5. Monitor the first few ticks closely via logs

## IPC Integration

Smed receives messages from other agents via the supervisor's IPC system:

| Type | Direction | Payload fields | Purpose |
|------|-----------|---------------|---------|
| `bug_report` | GitHub agent → Smed | `title`, `ref`, `description`, `error_text`, `priority` | New bug issue detected |
| `log_alert` | Supervisor → Smed | `message`, `ref`, `identity`, `traceback`, `priority` | Error pattern in logs |

Messages are queued in a bounded deque (max 100) and drained on each
`observe()` call. Duplicates are detected by `source_ref` and skipped.

## Files

| File | Purpose |
|------|---------|
| `plugin.py` | Main plugin — config loading, tick guards, IPC handlers, goal definitions |
| `models.py` | Data models: BugReport, FixAttempt, OpencodeResult, TestRunResult, ActionType |
| `database.py` | DevAgentDB — bug/attempt tracking, log scan state, AgenticDB composition |
| `observation.py` | BugObserver — IPC queue + log scanning + DB query → DevAgentObservation |
| `action_handlers.py` | 7 action handlers + `build_dev_agent_handlers()` factory |
| `workspace.py` | WorkspaceManager — git clone, branch, commit, push (async subprocess) |
| `opencode_runner.py` | OpencodeRunner — invokes `opencode run --format json` |
| `test_runner.py` | TestRunner — runs pytest, parses summary output |
| `pr_creator.py` | PRCreator — runs `gh pr create`, builds PR description |
| `log_watcher.py` | LogWatcher — scans log files for ERROR patterns and tracebacks |
| `prompts.py` | PlanningPromptConfig for the dev-agent domain |

## Database Schema

Three plugin-specific tables (+ 4 shared agentic tables):

**bugs** — Tracked bug reports with full lifecycle state:
- `source` + `source_ref` (unique key for deduplication)
- `status` (new → analyzing → fixing → testing → pr_created → fixed/failed)
- `fix_attempts` / `max_attempts`
- `branch_name`, `pr_url`, `analysis`

**fix_attempts** — History of every fix attempt:
- `bug_id`, `attempt_number`
- `files_changed`, `tests_passed`, `opencode_output`
- `committed`, `branch_name`, `duration_seconds`

**log_scan_state** — Byte offset tracking per log file:
- `file_path` (primary key)
- `last_offset` (resume scanning from here)

## Safety

- **Dry run by default** — all write operations are logged but not
  executed until `dry_run: false` is explicitly set
- **Branch protection** — `commit_and_push()` asserts `current_branch
  != "main"` — hardcoded safety check, not configurable
- **Max attempts** — 3 fix attempts per bug before marking FAILED
  (configurable via `max_fix_attempts`)
- **Test gate** — tests must pass before any commit is allowed
- **opencode timeout** — 10 minutes per invocation (configurable)
- **Workspace isolation** — operates in a separate git clone at
  `data/smed/workspace/`, never reads or writes outside it
- **Quiet hours** — respects the framework's quiet hours configuration
- **Audit trail** — all actions logged to the agentic database
- **Subprocess safety** — all external commands (`git`, `opencode`,
  `pytest`, `gh`) use `asyncio.create_subprocess_exec` (no shell) —
  safe against command injection

## Running

```bash
# Start with dry-run (default)
python -m overblick run smed

# Run plugin tests (123 tests)
./venv/bin/python3 -m pytest tests/plugins/dev_agent/ -v

# Run full suite (verify no regressions)
./venv/bin/python3 -m pytest tests/ -v -m "not llm and not e2e"
```

## Dependencies

| Tool | Required for | Install |
|------|-------------|---------|
| `opencode` | Bug analysis and code fixing | [opencode.ai](https://opencode.ai) |
| `gh` | PR creation | `brew install gh` |
| LM Studio | Local LLM (Devstral 2 123B) | [lmstudio.ai](https://lmstudio.ai) |
| `git` | Workspace management | Pre-installed on macOS |
| `pytest` | Test validation | Included in dev dependencies |
