"""
Prompt templates for the GitHub agent plugin.

Prompt types:
1. File selector — lightweight LLM call to pick relevant files from tree
2. Code question — build a response using code context
3. Issue response — general issue response without code context
4. Agent planning — strategic planning given world state + goals
5. Dependabot review — evaluate a Dependabot PR for auto-merge safety
6. Issue classification — classify and triage an issue
7. Reflection — extract learnings from tick outcomes

All external content is expected to be pre-wrapped via wrap_external_content().
"""


# ---------------------------------------------------------------------------
# Legacy prompts (used by ResponseGenerator, CodeContextBuilder)
# ---------------------------------------------------------------------------

def file_selector_prompt(
    file_tree: str,
    question: str,
    max_files: int = 8,
) -> list[dict[str, str]]:
    """
    Build prompt for LLM to select relevant files from a repo tree.

    The LLM should return a JSON array of file paths.
    """
    system = (
        "You are a code navigation assistant. Given a repository file tree and a question, "
        "select the most relevant source files that would help answer the question.\n\n"
        "Rules:\n"
        f"- Select at most {max_files} files\n"
        "- Prefer source code files over config/lock files\n"
        "- Include test files only if the question is about testing\n"
        "- Focus on files most likely to contain the answer\n\n"
        "Respond with ONLY a JSON array of file paths, nothing else.\n"
        'Example: ["src/main.py", "src/utils.py", "README.md"]'
    )

    user = (
        f"Question: {question}\n\n"
        f"Repository file tree:\n{file_tree}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def code_question_prompt(
    system_prompt: str,
    question: str,
    code_context: str,
    existing_comments: str = "",
    issue_body: str = "",
) -> list[dict[str, str]]:
    """
    Build prompt for answering a code question with file context.

    Used when the decision engine determines a code-related response is needed.
    """
    context_section = ""
    if existing_comments:
        context_section = f"\nExisting discussion:\n{existing_comments}\n"

    issue_section = ""
    if issue_body:
        issue_section = f"\nOriginal issue:\n{issue_body}\n"

    user = (
        f"{issue_section}"
        f"\nQuestion/comment to respond to:\n{question}\n"
        f"{context_section}"
        f"\nRelevant source code:\n{code_context}\n\n"
        "Provide a helpful, technically accurate response. Reference specific "
        "files and line numbers where relevant. Be concise but thorough."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


def issue_response_prompt(
    system_prompt: str,
    issue_title: str,
    issue_body: str,
    existing_comments: str = "",
) -> list[dict[str, str]]:
    """
    Build prompt for responding to a general issue (no code context needed).

    Used for non-code questions, bug reports with clear descriptions, etc.
    """
    comments_section = ""
    if existing_comments:
        comments_section = f"\nExisting discussion:\n{existing_comments}\n"

    user = (
        f"Issue: {issue_title}\n\n"
        f"{issue_body}\n"
        f"{comments_section}\n"
        "Provide a helpful response. Be concise, professional, and "
        "technically accurate."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Agentic prompts
# ---------------------------------------------------------------------------

def planning_prompt(
    system_prompt: str,
    observations: str,
    goals: str,
    recent_actions: str,
    learnings: str,
    owner_commands: str = "",
    max_actions: int = 5,
) -> list[dict[str, str]]:
    """
    Build prompt for the agent planning phase.

    The LLM receives the complete world state, active goals, and recent
    history, then produces a prioritized action plan as JSON.
    """
    system = (
        f"{system_prompt}\n\n"
        "=== AGENT ROLE ===\n"
        "You are a GitHub repository caretaker. Your job is to keep the repo healthy.\n"
        "You observe the current state of the repository, consider your goals, and plan actions.\n\n"
        "Available action types:\n"
        "- merge_pr: Merge a pull request (only safe Dependabot PRs with passing CI)\n"
        "- approve_pr: Approve a pull request\n"
        "- review_pr: Leave a review comment on a PR\n"
        "- respond_issue: Respond to a GitHub issue\n"
        "- notify_owner: Send a notification to the repo owner\n"
        "- comment_pr: Leave a comment on a PR\n"
        "- refresh_context: Refresh repository understanding\n"
        "- skip: Do nothing (explain why in reasoning)\n\n"
        "SAFETY RULES:\n"
        "- ONLY merge Dependabot PRs that are patch/minor bumps with ALL CI checks passing\n"
        "- NEVER merge major version bumps — notify the owner instead\n"
        "- NEVER merge non-Dependabot PRs without explicit owner command\n"
        "- When unsure, NOTIFY the owner rather than acting\n"
        "- Owner commands (from Telegram) always take highest priority\n\n"
        f"Plan at most {max_actions} actions, ordered by priority (highest first).\n\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "reasoning": "Your overall analysis of the current state",\n'
        '  "actions": [\n'
        "    {\n"
        '      "action_type": "merge_pr|approve_pr|review_pr|respond_issue|notify_owner|comment_pr|refresh_context|skip",\n'
        '      "target": "PR #42 or issue #7",\n'
        '      "target_number": 42,\n'
        '      "repo": "owner/repo",\n'
        '      "priority": 90,\n'
        '      "reasoning": "Why this action is needed"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    parts = []
    if owner_commands:
        parts.append(f"=== OWNER COMMANDS (HIGHEST PRIORITY) ===\n{owner_commands}")

    parts.append(f"=== CURRENT STATE ===\n{observations}")
    parts.append(f"=== ACTIVE GOALS ===\n{goals}")

    if recent_actions:
        parts.append(f"=== RECENT ACTIONS (for context) ===\n{recent_actions}")
    if learnings:
        parts.append(f"=== LEARNINGS ===\n{learnings}")

    parts.append("Plan your actions now. Remember: safety first, owner commands take priority.")

    user = "\n\n".join(parts)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def dependabot_review_prompt(
    system_prompt: str,
    pr_title: str,
    pr_diff: str,
    version_bump: str,
    ci_status: str,
    repo_summary: str = "",
) -> list[dict[str, str]]:
    """
    Build prompt for reviewing a Dependabot PR before auto-merge.

    The LLM analyzes the dependency change and decides if it's safe.
    """
    system = (
        f"{system_prompt}\n\n"
        "You are reviewing a Dependabot dependency update PR.\n"
        "Analyze the change and decide if it's safe to auto-merge.\n\n"
        "Consider:\n"
        "- Is this a patch/minor/major version bump?\n"
        "- Does the diff look reasonable for a dependency update?\n"
        "- Are there any suspicious changes beyond dependency files?\n"
        "- Is the CI passing?\n\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "safe_to_merge": true/false,\n'
        '  "confidence": 0.0-1.0,\n'
        '  "reasoning": "Brief explanation",\n'
        '  "concerns": ["list of any concerns"]\n'
        "}"
    )

    user = (
        f"PR: {pr_title}\n"
        f"Version bump: {version_bump}\n"
        f"CI status: {ci_status}\n\n"
    )
    if repo_summary:
        user += f"Repository context:\n{repo_summary}\n\n"
    user += f"Diff:\n{pr_diff[:8000]}\n"  # Cap diff size

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def issue_classification_prompt(
    issue_title: str,
    issue_body: str,
    labels: str,
) -> list[dict[str, str]]:
    """
    Build prompt for classifying an issue to determine response strategy.
    """
    system = (
        "Classify this GitHub issue. Respond with ONLY a JSON object:\n"
        "{\n"
        '  "category": "question|bug_report|feature_request|discussion|support",\n'
        '  "needs_code_context": true/false,\n'
        '  "urgency": "high|medium|low",\n'
        '  "summary": "One-sentence summary"\n'
        "}"
    )

    user = (
        f"Title: {issue_title}\n"
        f"Labels: {labels}\n\n"
        f"Body:\n{issue_body[:3000]}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def reflection_prompt(
    system_prompt: str,
    tick_summary: str,
    action_outcomes: str,
) -> list[dict[str, str]]:
    """
    Build prompt for the agent's reflection phase after executing actions.

    The LLM extracts learnings from the tick's outcomes.
    """
    system = (
        f"{system_prompt}\n\n"
        "Reflect on this tick cycle. Extract any useful learnings.\n\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "learnings": [\n'
        "    {\n"
        '      "category": "dependabot|issues|ci|general",\n'
        '      "insight": "What you learned",\n'
        '      "confidence": 0.0-1.0\n'
        "    }\n"
        "  ],\n"
        '  "tick_summary": "Brief summary of what happened"\n'
        "}"
    )

    user = (
        f"=== TICK SUMMARY ===\n{tick_summary}\n\n"
        f"=== ACTION OUTCOMES ===\n{action_outcomes}"
    )

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
