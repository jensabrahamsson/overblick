# GitHub Agent Plugin

Agentic repository caretaker that keeps GitHub repos healthy through
autonomous observation, planning, and action. Built on the core
`AgenticPluginBase` framework — the plugin provides domain-specific
logic while the core handles the OBSERVE/THINK/PLAN/ACT/REFLECT loop.

> **Status:** Work in progress. Functional in dry-run mode. Not yet
> battle-tested in production (live mode).

## What It Does

- **Auto-merges** safe Dependabot PRs (patch/minor bumps with passing CI)
- **Responds** to issues labeled `question`, `help wanted`, or `bug`
  with code-aware, identity-voiced answers
- **Notifies** the repo owner (via Telegram) about failing CI, stale PRs,
  and major version bumps
- **Learns** from outcomes to improve decisions over time

## Architecture

```
GitHubAgentPlugin (AgenticPluginBase)
  |
  |-- create_observer() ---------> _MultiRepoObserver
  |                                   \-> ObservationCollector
  |                                         \-> GitHubAPIClient
  |
  |-- get_action_handlers() -----> build_github_handlers()
  |                                   |-> MergePRHandler
  |                                   |-> ApprovePRHandler
  |                                   |-> ReviewPRHandler
  |                                   |-> RespondIssueHandler
  |                                   |-> NotifyOwnerHandler
  |                                   |-> CommentPRHandler
  |                                   |-> RefreshContextHandler
  |                                   \-> SkipHandler
  |
  \-- get_planning_prompt_config() -> PlanningPromptConfig
```

### The Agentic Loop

Each tick runs the five-phase cycle provided by `core/agentic/`:

1. **OBSERVE** — `ObservationCollector` polls GitHub API for each configured
   repo: open PRs, open issues, CI status, review state, staleness.
   Returns a `RepoObservation` snapshot.

2. **THINK** — Assembles context: current goals, recent action history,
   accumulated learnings. Everything formatted as text for the LLM.

3. **PLAN** — LLM planner receives the full world state and produces an
   `ActionPlan` — an ordered list of `PlannedAction` with priorities and
   reasoning. Actions are validated against the plugin's registered types.

4. **ACT** — `ActionExecutor` dispatches each planned action to the
   matching handler. Enforces `max_actions_per_tick`. Each handler
   returns an `ActionOutcome` (success/failure + result/error).

5. **REFLECT** — LLM reviews the tick's outcomes and extracts
   `AgentLearning` records (category, insight, confidence). These are
   stored in the database and fed back into future planning context.

## Action Types

| Action | Handler | What it does |
|--------|---------|-------------|
| `merge_pr` | MergePRHandler | Merges Dependabot PRs (patch/minor, CI passing, mergeable) |
| `approve_pr` | ApprovePRHandler | Creates an APPROVE review |
| `review_pr` | ReviewPRHandler | Leaves a COMMENT review with reasoning |
| `respond_issue` | RespondIssueHandler | Generates + posts a code-aware response |
| `notify_owner` | NotifyOwnerHandler | Sends Telegram notification to repo owner |
| `comment_pr` | CommentPRHandler | Posts a general comment on a PR |
| `refresh_context` | RefreshContextHandler | No-op (context refreshed during observation) |
| `skip` | SkipHandler | Explicitly does nothing (logs reasoning) |

## Default Goals

| Priority | Goal | Description |
|----------|------|-------------|
| 90 | `communicate_with_owner` | Notify about failing CI, stale PRs, important issues |
| 80 | `merge_safe_dependabot` | Auto-merge patch/minor Dependabot PRs with passing CI |
| 70 | `respond_issues_24h` | Respond to labeled issues within 24 hours |
| 60 | `no_stale_prs` | No PRs unreviewed for more than 48 hours |
| 40 | `maintain_codebase_understanding` | Keep file tree cache fresh |

## Configuration

In the identity's `personality.yaml`:

```yaml
github:
  repos:
    - "owner/repo"
  dry_run: true                    # Safety first — no writes until explicitly enabled
  bot_username: "overblick-bot"    # For detecting own comments
  default_branch: "main"
  tick_interval_minutes: 10        # How often to check
  max_actions_per_tick: 5          # Action budget per cycle

  dependabot:
    auto_merge_patch: true
    auto_merge_minor: true
    auto_merge_major: false        # Major bumps always require owner approval
    require_ci_pass: true

  issues:
    respond_to_labels:
      - question
      - help wanted
      - bug
    max_response_age_hours: 168    # 7 days

  code_context:
    max_files_per_question: 12
    max_file_size_bytes: 100000    # 100 KB per file
    max_context_chars: 100000      # Total context budget
    tree_refresh_minutes: 30
```

### Secrets

The GitHub token is stored as an encrypted per-identity secret:

```yaml
# config/secrets/<identity>.yaml (Fernet-encrypted)
github_token: "github_pat_..."
```

Accessed via `ctx.get_secret("github_token")` during setup. The plugin
degrades to read-only mode if no token is available.

### Required Token Permissions (Fine-Grained PAT)

For a single repository:

| Permission | Access | Why |
|------------|--------|-----|
| Issues | Read & Write | Respond to issues, read labels |
| Pull requests | Read & Write | Merge PRs, post reviews, comments |
| Contents | Read | Fetch file tree + code for context |
| Commit statuses | Read | Check CI status |
| Actions | Read | Check GitHub Actions workflow runs |

## Files

| File | Purpose |
|------|---------|
| `plugin.py` | Main plugin — config loading, tick guards, goal definitions |
| `models.py` | Data models: PRSnapshot, IssueSnapshot, RepoObservation, ActionType |
| `observation.py` | ObservationCollector — polls GitHub API, builds world state |
| `action_executor.py` | 8 action handlers + `build_github_handlers()` factory |
| `client.py` | GitHubAPIClient — REST API wrapper with rate limit tracking |
| `dependabot_handler.py` | Dependabot merge/review logic |
| `issue_responder.py` | Issue response generation + posting |
| `response_gen.py` | ResponseGenerator — LLM-driven response with code context |
| `code_context.py` | CodeContextBuilder — file tree caching + context assembly |
| `database.py` | GitHubDB — GitHub-specific tables + AgenticDB wrapper |
| `prompts.py` | GitHub-specific prompt templates |

## Safety

- **Dry run by default** — all write operations are logged but not executed
  until `dry_run: false` is explicitly set
- **Dependabot-only merges** — the agent will never merge non-Dependabot PRs
  without an explicit owner command
- **Major bump guardrail** — major version bumps always notify the owner
  instead of merging
- **CI requirement** — merges require all checks to pass (configurable)
- **Rate limit awareness** — tracks GitHub API rate limits in plugin state
- **Quiet hours** — respects the framework's quiet hours configuration
- **Audit trail** — all actions logged to the agentic database

## Roadmap

The following enhancements are planned for future releases:

- **Owner command channel** — route Telegram commands to the agent for manual overrides
- **PR diff analysis** — deeper code review via LLM-powered diff summaries
- **Webhook-based observation** — replace polling with GitHub webhook events for real-time reactivity
- **Dashboard integration** — dedicated dashboard tab showing agent status, goals, and action history
- **Multi-identity support** — allow different identities to manage different repositories
