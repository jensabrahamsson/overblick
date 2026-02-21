"""
GitHub monitoring plugin — watches issues on public repos.

Evaluates events via heuristic scoring, optionally builds code context,
and generates identity-voiced responses via the SafeLLMPipeline.
Notifications go through TelegramNotifier.

Tick cycle:
1. Poll configured repos for new issues/comments (since last check)
2. Filter already-seen events (DB dedup)
3. Score events via DecisionEngine
4. For RESPOND events: build code context, generate response, post comment
5. For NOTIFY events: send Telegram notification
6. Record all events in DB
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.plugins.github.client import GitHubAPIClient, GitHubAPIError
from overblick.plugins.github.code_context import CodeContextBuilder
from overblick.plugins.github.database import GitHubDB
from overblick.plugins.github.decision_engine import GitHubDecisionEngine
from overblick.plugins.github.models import (
    CommentRecord,
    EventAction,
    EventRecord,
    EventType,
    GitHubEvent,
    PluginState,
)
from overblick.plugins.github.response_gen import ResponseGenerator

logger = logging.getLogger(__name__)


class GitHubPlugin(PluginBase):
    """
    GitHub issue monitoring plugin.

    Watches configured public repos for issues and comments,
    scores events, and responds when appropriate using identity-voiced
    LLM responses with code context.
    """

    name = "github"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._db: Optional[GitHubDB] = None
        self._client: Optional[GitHubAPIClient] = None
        self._decision_engine: Optional[GitHubDecisionEngine] = None
        self._code_context: Optional[CodeContextBuilder] = None
        self._response_gen: Optional[ResponseGenerator] = None
        self._state = PluginState()
        self._system_prompt: str = ""
        self._repos: list[str] = []
        self._default_branch: str = "main"
        self._check_interval: int = 300
        self._max_responses_per_tick: int = 2
        self._dry_run: bool = False
        self._bot_username: str = ""
        self._last_check_iso: str = ""

    async def setup(self) -> None:
        """Initialize the GitHub plugin: database, client, engines."""
        identity = self.ctx.identity
        if not identity:
            raise RuntimeError("GitHubPlugin requires an identity")

        # Load config from identity
        raw_config = identity.raw_config
        gh_config = raw_config.get("github", {})

        self._repos = gh_config.get("repos", [])
        if not self._repos:
            raise RuntimeError("GitHubPlugin: no repos configured")

        self._bot_username = gh_config.get("bot_username", "")
        self._default_branch = gh_config.get("default_branch", "main")
        self._max_responses_per_tick = gh_config.get("max_responses_per_tick", 2)
        self._dry_run = gh_config.get("dry_run", False)

        # Thresholds
        respond_threshold = gh_config.get("respond_threshold", 50)
        notify_threshold = gh_config.get("notify_threshold", 25)
        max_issue_age_hours = gh_config.get("max_issue_age_hours", 168)

        # Triggers
        triggers = gh_config.get("triggers", {})
        respond_labels = triggers.get("respond_to_labels", ["question", "help wanted"])

        # Code context config
        cc_config = gh_config.get("code_context", {})

        # Check interval from schedule
        self._check_interval = identity.schedule.feed_poll_minutes * 60

        # Load GitHub token from secrets
        token = self.ctx.get_secret("github_token") or ""
        if not token:
            logger.warning("GitHubPlugin: no github_token secret — running in read-only mode")

        # Initialize database
        db_path = self.ctx.data_dir / "github.db"
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        self._db = GitHubDB(backend)
        await self._db.setup()

        # Initialize API client
        self._client = GitHubAPIClient(token=token)

        # Extract interest keywords from identity
        interest_keywords = list(identity.interest_keywords) if identity.interest_keywords else []

        # Initialize decision engine
        self._decision_engine = GitHubDecisionEngine(
            bot_username=self._bot_username,
            respond_threshold=respond_threshold,
            notify_threshold=notify_threshold,
            interest_keywords=interest_keywords,
            respond_labels=respond_labels,
            priority_repos=self._repos,
            max_issue_age_hours=max_issue_age_hours,
        )

        # Initialize code context builder
        self._code_context = CodeContextBuilder(
            client=self._client,
            db=self._db,
            llm_pipeline=self.ctx.llm_pipeline,
            max_files=cc_config.get("max_files_per_question", 8),
            max_file_size=cc_config.get("max_file_size_bytes", 50000),
            tree_refresh_minutes=gh_config.get("tree_refresh_minutes", 60),
            include_patterns=cc_config.get("include_patterns"),
            exclude_patterns=cc_config.get("exclude_patterns"),
        )

        # Build system prompt
        self._system_prompt = self._build_system_prompt()

        # Initialize response generator
        self._response_gen = ResponseGenerator(
            llm_pipeline=self.ctx.llm_pipeline,
            code_context_builder=self._code_context,
            system_prompt=self._system_prompt,
        )

        # Load stats from DB
        stats = await self._db.get_stats()
        self._state.events_processed = stats.get("events_processed", 0)
        self._state.comments_posted = stats.get("comments_posted", 0)
        self._state.repos_monitored = len(self._repos)

        if self._dry_run:
            logger.info("GitHubPlugin running in DRY RUN mode — no comments will be posted")

        logger.info(
            "GitHubPlugin setup for '%s' (repos: %s, dry_run: %s)",
            self.ctx.identity_name,
            ", ".join(self._repos),
            self._dry_run,
        )

    async def tick(self) -> None:
        """
        Main tick: poll repos and process events.

        Guards: interval, quiet hours, LLM pipeline availability.
        """
        now = time.time()

        if self._state.last_check and (now - self._state.last_check < self._check_interval):
            return

        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        if not self.ctx.llm_pipeline:
            logger.debug("GitHub: no LLM pipeline available")
            return

        self._state.last_check = now
        logger.info("GitHub: tick started for %d repo(s)", len(self._repos))

        responses_this_tick = 0

        for repo in self._repos:
            try:
                events = await self._poll_events(repo)
                for event in events:
                    if responses_this_tick >= self._max_responses_per_tick:
                        logger.info("GitHub: max responses per tick reached (%d)", self._max_responses_per_tick)
                        break

                    action_taken = await self._process_event(event)
                    if action_taken == "responded":
                        responses_this_tick += 1

            except GitHubAPIError as e:
                logger.error("GitHub: error polling %s: %s", repo, e)
                self._state.current_health = "degraded"
            except Exception as e:
                logger.error("GitHub: unexpected error for %s: %s", repo, e, exc_info=True)
                self._state.current_health = "degraded"

        # Update rate limit info
        if self._client:
            self._state.rate_limit_remaining = self._client.rate_limit_remaining

    async def _poll_events(self, repo: str) -> list[GitHubEvent]:
        """Fetch new issues and comments for a repo since last check."""
        events: list[GitHubEvent] = []

        try:
            issues = await self._client.list_issues(
                repo, since=self._last_check_iso, state="open",
            )
        except Exception as e:
            logger.warning("GitHub: failed to list issues for %s: %s", repo, e)
            return events

        for issue in issues:
            # Skip pull requests (GitHub API returns PRs as issues)
            is_pr = "pull_request" in issue

            issue_number = issue.get("number", 0)
            event_id = f"{repo}/issues/{issue_number}"

            # Check if already seen
            if await self._db.has_event(event_id):
                continue

            labels = [l.get("name", "") for l in issue.get("labels", [])]
            author = issue.get("user", {}).get("login", "")

            events.append(GitHubEvent(
                event_id=event_id,
                event_type=EventType.ISSUE_OPENED,
                repo=repo,
                issue_number=issue_number,
                issue_title=issue.get("title", ""),
                body=issue.get("body", "") or "",
                author=author,
                labels=labels,
                created_at=issue.get("created_at", ""),
                is_pull_request=is_pr,
            ))

            # Also check for new comments on this issue
            try:
                comments = await self._client.list_issue_comments(
                    repo, issue_number, since=self._last_check_iso,
                )
                for comment in comments:
                    comment_id = comment.get("id", 0)
                    comment_event_id = f"{repo}/comments/{comment_id}"

                    if await self._db.has_event(comment_event_id):
                        continue

                    comment_author = comment.get("user", {}).get("login", "")
                    comment_body = comment.get("body", "") or ""

                    # Check for @mentions in comments
                    events.append(GitHubEvent(
                        event_id=comment_event_id,
                        event_type=EventType.ISSUE_COMMENT,
                        repo=repo,
                        issue_number=issue_number,
                        issue_title=issue.get("title", ""),
                        body=comment_body,
                        author=comment_author,
                        labels=labels,
                        created_at=comment.get("created_at", ""),
                        is_pull_request=is_pr,
                    ))
            except Exception as e:
                logger.debug("GitHub: failed to fetch comments for %s#%d: %s", repo, issue_number, e)

        # Update last check timestamp
        self._last_check_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return events

    async def _process_event(self, event: GitHubEvent) -> str:
        """
        Process a single GitHub event through the decision pipeline.

        Returns:
            Description of action taken
        """
        already_responded = await self._db.has_responded_to_issue(
            event.repo, event.issue_number,
        )

        decision = self._decision_engine.evaluate(event, already_responded=already_responded)

        # Record the event regardless of action
        await self._db.record_event(EventRecord(
            event_id=event.event_id,
            event_type=event.event_type.value,
            repo=event.repo,
            issue_number=event.issue_number,
            author=event.author,
            score=decision.score,
            action_taken=decision.action.value,
        ))
        self._state.events_processed += 1

        if decision.action == EventAction.RESPOND:
            return await self._handle_respond(event)
        elif decision.action == EventAction.NOTIFY:
            return await self._handle_notify(event, decision)
        else:
            logger.debug(
                "GitHub: skipping %s#%d (score=%d)",
                event.repo, event.issue_number, decision.score,
            )
            return "skipped"

    async def _handle_respond(self, event: GitHubEvent) -> str:
        """Generate and post a response to a GitHub issue."""
        if self._dry_run:
            logger.info(
                "GitHub DRY RUN: would respond to %s#%d — %s",
                event.repo, event.issue_number, event.issue_title,
            )
            await self._notify_principal(
                f"[DRY RUN] Would respond to {event.repo}#{event.issue_number}: {event.issue_title}"
            )
            return "dry_run"

        # Fetch existing comments for context
        existing_comments = []
        try:
            existing_comments = await self._client.list_issue_comments(
                event.repo, event.issue_number,
            )
        except Exception as e:
            logger.debug("GitHub: failed to fetch comments for context: %s", e)

        # Generate response
        response_text = await self._response_gen.generate(
            event=event,
            existing_comments=existing_comments,
            branch=self._default_branch,
        )

        if not response_text:
            logger.warning("GitHub: response generation failed for %s#%d", event.repo, event.issue_number)
            return "generation_failed"

        # Post comment
        try:
            result = await self._client.create_comment(
                event.repo, event.issue_number, response_text,
            )
            comment_id = result.get("id", 0)

            # Record in DB
            content_hash = hashlib.sha256(response_text.encode()).hexdigest()[:16]
            await self._db.record_comment(CommentRecord(
                github_comment_id=comment_id,
                repo=event.repo,
                issue_number=event.issue_number,
                content_hash=content_hash,
            ))
            self._state.comments_posted += 1

            # Notify principal
            await self._notify_principal(
                f"Responded to {event.repo}#{event.issue_number}: {event.issue_title}"
            )

            # Emit event
            if self.ctx.event_bus:
                self.ctx.event_bus.emit("github.issue_responded", {
                    "repo": event.repo,
                    "issue_number": event.issue_number,
                    "comment_id": comment_id,
                })

            # Audit
            if self.ctx.audit_log:
                self.ctx.audit_log.log("github_comment_posted", {
                    "repo": event.repo,
                    "issue_number": event.issue_number,
                    "comment_id": comment_id,
                    "content_hash": content_hash,
                })

            logger.info("GitHub: posted comment on %s#%d", event.repo, event.issue_number)
            return "responded"

        except GitHubAPIError as e:
            logger.error("GitHub: failed to post comment on %s#%d: %s", event.repo, event.issue_number, e)
            return "post_failed"

    async def _handle_notify(self, event: GitHubEvent, decision: Any) -> str:
        """Send a Telegram notification about a GitHub event."""
        notification = (
            f"*GitHub: {event.repo}#{event.issue_number}*\n"
            f"_{event.issue_title}_\n\n"
            f"By @{event.author} | Score: {decision.score}\n"
            f"{event.body[:300]}"
        )

        success = await self._notify_principal(notification)
        if success:
            self._state.notifications_sent += 1
            return "notified"
        return "notification_failed"

    async def _notify_principal(self, message: str) -> bool:
        """Send a notification via TelegramNotifier capability."""
        notifier = self.ctx.get_capability("telegram_notifier")
        if not notifier:
            logger.debug("GitHub: telegram_notifier capability not available")
            return False

        try:
            await notifier.send_notification(message)
            return True
        except Exception as e:
            logger.warning("GitHub: notification failed: %s", e)
            return False

    def _build_system_prompt(self) -> str:
        """Build system prompt from identity personality."""
        try:
            identity = self.ctx.load_identity(self.ctx.identity_name)
            return self.ctx.build_system_prompt(identity, platform="GitHub")
        except FileNotFoundError:
            return (
                "You are a helpful GitHub assistant. Provide technically accurate, "
                "concise responses to issues and questions. Reference code where relevant."
            )

    def get_status(self) -> dict:
        """Expose status for dashboard."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "events_processed": self._state.events_processed,
            "comments_posted": self._state.comments_posted,
            "notifications_sent": self._state.notifications_sent,
            "repos_monitored": self._state.repos_monitored,
            "rate_limit_remaining": self._state.rate_limit_remaining,
            "dry_run": self._dry_run,
            "health": self._state.current_health,
        }

    async def teardown(self) -> None:
        """Cleanup database and HTTP session."""
        if self._client:
            await self._client.close()
        if self._db:
            await self._db.close()
        logger.info("GitHubPlugin teardown complete")
