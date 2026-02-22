"""
ObservationCollector — gathers world state for the agentic loop.

Collects snapshots of PRs, issues, CI status, and Telegram commands
into a RepoObservation that represents the complete world state
at a point in time.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from overblick.plugins.github.client import GitHubAPIClient, GitHubAPIError
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.models import (
    CIStatus,
    IssueSnapshot,
    PRSnapshot,
    RepoObservation,
    VersionBumpType,
)

logger = logging.getLogger(__name__)

# Dependabot author names
_DEPENDABOT_AUTHORS = frozenset({"dependabot[bot]", "dependabot"})

# Regex to extract version bump from Dependabot PR titles
# Examples: "Bump lodash from 4.17.20 to 4.17.21"
#           "Update pytest requirement from ~=7.0 to ~=8.0"
_VERSION_RE = re.compile(
    r"from\s+(\d+)\.(\d+)\.?(\d*)\s+to\s+(\d+)\.(\d+)\.?(\d*)",
    re.IGNORECASE,
)


def _parse_version_bump(title: str) -> VersionBumpType:
    """Parse semantic version bump type from a Dependabot PR title."""
    match = _VERSION_RE.search(title)
    if not match:
        return VersionBumpType.UNKNOWN

    old_major, old_minor = int(match.group(1)), int(match.group(2))
    new_major, new_minor = int(match.group(4)), int(match.group(5))

    if new_major != old_major:
        return VersionBumpType.MAJOR
    if new_minor != old_minor:
        return VersionBumpType.MINOR
    return VersionBumpType.PATCH


def _age_hours(iso_timestamp: str) -> float:
    """Calculate age in hours from an ISO 8601 timestamp."""
    try:
        ts = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600.0
    except (ValueError, TypeError):
        return 0.0


class ObservationCollector:
    """
    Collects world-state snapshots from GitHub API.

    Produces a RepoObservation with categorized PRs and issues,
    CI status, and derived classifications (stale PRs, unanswered issues).
    """

    def __init__(
        self,
        client: GitHubAPIClient,
        db: GitHubDB,
        bot_username: str = "",
        stale_pr_hours: float = 48.0,
        unanswered_issue_hours: float = 24.0,
    ):
        self._client = client
        self._db = db
        self._bot_username = bot_username.lower()
        self.stale_pr_hours = stale_pr_hours
        self.unanswered_issue_hours = unanswered_issue_hours

    async def observe(self, repo: str) -> RepoObservation:
        """
        Collect a complete world-state snapshot for a repository.

        Fetches open PRs and issues, checks CI status, and categorizes
        everything for the planner.
        """
        observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Fetch PRs and issues in parallel-ish (sequential for now)
        open_prs = await self._collect_prs(repo)
        open_issues = await self._collect_issues(repo)

        # Categorize
        dependabot_prs = [pr for pr in open_prs if pr.is_dependabot]
        failing_ci = [pr for pr in open_prs if pr.ci_status == CIStatus.FAILURE]
        stale_prs = [
            pr for pr in open_prs
            if pr.age_hours > self.stale_pr_hours and not pr.draft
        ]
        unanswered_issues = [
            issue for issue in open_issues
            if issue.age_hours > self.unanswered_issue_hours
            and not issue.has_our_response
            and issue.comments_count == 0
        ]

        # Get repo summary from cache
        repo_summary_data = await self._db.get_repo_summary(repo)
        repo_summary = repo_summary_data.get("summary", "") if repo_summary_data else ""

        # Get file count from tree cache
        tree_paths = await self._db.get_tree_paths(repo)

        observation = RepoObservation(
            repo=repo,
            observed_at=observed_at,
            open_prs=open_prs,
            open_issues=open_issues,
            dependabot_prs=dependabot_prs,
            failing_ci=failing_ci,
            stale_prs=stale_prs,
            unanswered_issues=unanswered_issues,
            repo_summary=repo_summary,
            file_count=len(tree_paths),
        )

        logger.info(
            "GitHub observation for %s: %d PRs, %d issues, %d dependabot, "
            "%d failing CI, %d stale, %d unanswered",
            repo, len(open_prs), len(open_issues), len(dependabot_prs),
            len(failing_ci), len(stale_prs), len(unanswered_issues),
        )

        return observation

    async def _collect_prs(self, repo: str) -> list[PRSnapshot]:
        """Fetch and snapshot all open PRs."""
        prs: list[PRSnapshot] = []
        try:
            raw_prs = await self._client.list_pulls(repo, state="open")
        except GitHubAPIError as e:
            logger.warning("GitHub: failed to list PRs for %s: %s", repo, e)
            return prs

        for raw in raw_prs:
            pr = await self._snapshot_pr(repo, raw)
            prs.append(pr)

            # Track in database
            await self._db.upsert_pr_tracking(
                repo, pr.number,
                title=pr.title,
                author=pr.author,
                is_dependabot=pr.is_dependabot,
                version_bump=pr.version_bump.value,
                ci_status=pr.ci_status.value,
            )

        return prs

    async def _snapshot_pr(self, repo: str, raw: dict) -> PRSnapshot:
        """Build a PRSnapshot from raw GitHub API data."""
        number = raw.get("number", 0)
        author = raw.get("user", {}).get("login", "")
        title = raw.get("title", "")
        head_sha = raw.get("head", {}).get("sha", "")
        is_dependabot = author.lower() in _DEPENDABOT_AUTHORS
        created_at = raw.get("created_at", "")
        updated_at = raw.get("updated_at", "")

        # Parse version bump for Dependabot PRs
        version_bump = _parse_version_bump(title) if is_dependabot else VersionBumpType.UNKNOWN

        # Determine mergeable status
        mergeable = raw.get("mergeable", False)
        if mergeable is None:
            # GitHub hasn't computed mergeability yet
            mergeable = False

        # Get CI status
        ci_status = CIStatus.UNKNOWN
        ci_details: list[dict[str, str]] = []
        if head_sha:
            ci_status, ci_details = await self._get_ci_status(repo, head_sha)

        # Get review state
        review_state = await self._get_review_state(repo, number)

        labels = [l.get("name", "") for l in raw.get("labels", [])]

        return PRSnapshot(
            number=number,
            title=title,
            author=author,
            state=raw.get("state", "open"),
            draft=raw.get("draft", False),
            mergeable=mergeable,
            merged=raw.get("merged", False),
            labels=labels,
            created_at=created_at,
            updated_at=updated_at,
            head_sha=head_sha,
            base_branch=raw.get("base", {}).get("ref", "main"),
            ci_status=ci_status,
            ci_details=ci_details,
            is_dependabot=is_dependabot,
            version_bump=version_bump,
            review_state=review_state,
            comments_count=raw.get("comments", 0),
            changed_files=raw.get("changed_files", 0),
            additions=raw.get("additions", 0),
            deletions=raw.get("deletions", 0),
            age_hours=_age_hours(created_at),
        )

    async def _get_ci_status(
        self, repo: str, ref: str,
    ) -> tuple[CIStatus, list[dict[str, str]]]:
        """Get aggregated CI status for a git reference."""
        details: list[dict[str, str]] = []
        try:
            # Try check runs first (GitHub Actions)
            check_data = await self._client.get_check_runs(repo, ref)
            check_runs = check_data.get("check_runs", [])

            if check_runs:
                all_success = True
                any_failure = False
                any_pending = False

                for run in check_runs:
                    name = run.get("name", "")
                    status = run.get("status", "")
                    conclusion = run.get("conclusion", "")
                    details.append({"name": name, "status": status, "conclusion": conclusion})

                    if status != "completed":
                        any_pending = True
                        all_success = False
                    elif conclusion not in ("success", "skipped", "neutral"):
                        any_failure = True
                        all_success = False

                if any_failure:
                    return CIStatus.FAILURE, details
                if any_pending:
                    return CIStatus.PENDING, details
                if all_success:
                    return CIStatus.SUCCESS, details

            # Also check commit status (legacy status API)
            status_data = await self._client.get_combined_status(repo, ref)
            state = status_data.get("state", "")
            if state == "success":
                return CIStatus.SUCCESS, details
            if state == "failure":
                return CIStatus.FAILURE, details
            if state == "pending":
                return CIStatus.PENDING, details

        except GitHubAPIError as e:
            logger.debug("GitHub: failed to get CI status for %s@%s: %s", repo, ref[:8], e)

        return CIStatus.UNKNOWN, details

    async def _get_review_state(self, repo: str, pr_number: int) -> str:
        """Get the latest review state for a PR."""
        try:
            reviews = await self._client.list_pull_reviews(repo, pr_number)
            if not reviews:
                return "pending"

            # Find the latest non-comment review
            for review in reversed(reviews):
                state = review.get("state", "").lower()
                if state in ("approved", "changes_requested"):
                    return state

            return "pending"
        except GitHubAPIError:
            return ""

    async def _collect_issues(self, repo: str) -> list[IssueSnapshot]:
        """Fetch and snapshot all open issues (excluding PRs)."""
        issues: list[IssueSnapshot] = []
        try:
            raw_issues = await self._client.list_issues(repo, state="open", per_page=50)
        except GitHubAPIError as e:
            logger.warning("GitHub: failed to list issues for %s: %s", repo, e)
            return issues

        for raw in raw_issues:
            # Skip pull requests (GitHub API returns PRs as issues)
            if "pull_request" in raw:
                continue

            number = raw.get("number", 0)
            has_response = await self._db.has_responded_to_issue(repo, number)

            issues.append(IssueSnapshot(
                number=number,
                title=raw.get("title", ""),
                author=raw.get("user", {}).get("login", ""),
                state=raw.get("state", "open"),
                labels=[l.get("name", "") for l in raw.get("labels", [])],
                body=(raw.get("body", "") or "")[:2000],
                created_at=raw.get("created_at", ""),
                updated_at=raw.get("updated_at", ""),
                comments_count=raw.get("comments", 0),
                age_hours=_age_hours(raw.get("created_at", "")),
                has_our_response=has_response,
            ))

        return issues

    def format_for_planner(self, observation: RepoObservation) -> str:
        """Format observation as human-readable text for the LLM planner."""
        parts = [f"Repository: {observation.repo}"]
        parts.append(f"Observed at: {observation.observed_at}")
        parts.append(f"Files tracked: {observation.file_count}")

        if observation.repo_summary:
            parts.append(f"\nRepo summary: {observation.repo_summary[:500]}")

        # PRs
        if observation.open_prs:
            parts.append(f"\n--- Open PRs ({len(observation.open_prs)}) ---")
            for pr in observation.open_prs:
                flags = []
                if pr.is_dependabot:
                    flags.append(f"dependabot:{pr.version_bump.value}")
                if pr.draft:
                    flags.append("draft")
                flags.append(f"ci:{pr.ci_status.value}")
                if pr.mergeable:
                    flags.append("mergeable")
                if pr.review_state:
                    flags.append(f"review:{pr.review_state}")

                flag_str = " | ".join(flags)
                parts.append(
                    f"  PR #{pr.number}: {pr.title} "
                    f"(by {pr.author}, {pr.age_hours:.0f}h old) [{flag_str}]"
                )
        else:
            parts.append("\nNo open PRs.")

        # Dependabot PRs (highlighted)
        if observation.dependabot_prs:
            parts.append(f"\n--- Dependabot PRs ({len(observation.dependabot_prs)}) ---")
            for pr in observation.dependabot_prs:
                parts.append(
                    f"  PR #{pr.number}: {pr.title} "
                    f"[{pr.version_bump.value} bump, ci:{pr.ci_status.value}, "
                    f"mergeable:{pr.mergeable}]"
                )

        # Failing CI
        if observation.failing_ci:
            parts.append(f"\n--- FAILING CI ({len(observation.failing_ci)}) ---")
            for pr in observation.failing_ci:
                details_str = ", ".join(
                    f"{d['name']}:{d.get('conclusion', 'unknown')}"
                    for d in pr.ci_details[:5]
                )
                parts.append(f"  PR #{pr.number}: {pr.title} [{details_str}]")

        # Issues
        if observation.open_issues:
            parts.append(f"\n--- Open Issues ({len(observation.open_issues)}) ---")
            for issue in observation.open_issues[:10]:  # Cap at 10 for prompt size
                label_str = ", ".join(issue.labels) if issue.labels else "none"
                status = "responded" if issue.has_our_response else "unanswered"
                parts.append(
                    f"  Issue #{issue.number}: {issue.title} "
                    f"(by {issue.author}, {issue.age_hours:.0f}h old, "
                    f"comments: {issue.comments_count}, labels: {label_str}) [{status}]"
                )
        else:
            parts.append("\nNo open issues.")

        # Stale PRs
        if observation.stale_prs:
            parts.append(
                f"\n--- Stale PRs (>{self.stale_pr_hours:.0f}h unreviewed) ---"
            )
            for pr in observation.stale_prs:
                parts.append(f"  PR #{pr.number}: {pr.title} ({pr.age_hours:.0f}h old)")

        return "\n".join(parts)
