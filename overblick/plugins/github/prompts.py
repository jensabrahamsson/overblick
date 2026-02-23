"""
GitHub-specific prompt templates.

Domain-specific prompts that remain in the plugin:
1. File selector — lightweight LLM call to pick relevant files from tree
2. Code question — build a response using code context
3. Issue response — general issue response without code context
4. Dependabot review — evaluate a Dependabot PR for auto-merge safety
5. Issue classification — classify and triage an issue

Generic planning and reflection prompts have moved to core/agentic/prompts.py.
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
# Agentic prompts (GitHub-specific)
# ---------------------------------------------------------------------------

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
