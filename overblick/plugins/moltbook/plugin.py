"""
MoltbookPlugin — main plugin entry point.

Implements the full OBSERVE -> THINK -> DECIDE -> ACT -> LEARN cycle:
1. OBSERVE: Poll Moltbook feed for new posts and comments
2. THINK: Evaluate relevance with DecisionEngine
3. DECIDE: Choose engagement strategy (comment, upvote, skip)
4. ACT: Generate response via LLM and post it
5. LEARN: Extract and review potential learnings

Conditional capabilities (enabled via identity.enabled_modules):
- dream_system: Morning dreams and housekeeping
- therapy_system: Weekly psychological reflection
- safe_learning: LLM-reviewed knowledge acquisition
- emotional_state: Mood tracking based on interactions
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from overblick.core.capability import CapabilityBase, CapabilityRegistry
from overblick.core.plugin_base import PluginBase, PluginContext

from .client import MoltbookClient, MoltbookError, RateLimitError
from .challenge_handler import PerContentChallengeHandler
from .challenge_solver import MoltCaptchaSolver
from .decision_engine import DecisionEngine
from .response_gen import ResponseGenerator
from .feed_processor import FeedProcessor
from .reply_queue import ReplyQueueManager
from .heartbeat import HeartbeatManager
from .opening_selector import OpeningSelector
from .knowledge_loader import KnowledgeLoader

logger = logging.getLogger(__name__)


class MoltbookPlugin(PluginBase):
    """
    Moltbook social engagement plugin.

    Drives autonomous agent behavior on the Moltbook platform
    using identity-specific personality, interests, and thresholds.
    """

    name = "moltbook"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)

        self._client: Optional[MoltbookClient] = None
        self._decision_engine: Optional[DecisionEngine] = None
        self._response_gen: Optional[ResponseGenerator] = None
        self._feed_processor: Optional[FeedProcessor] = None
        self._reply_queue: Optional[ReplyQueueManager] = None
        self._heartbeat: Optional[HeartbeatManager] = None
        self._opening_selector: Optional[OpeningSelector] = None
        self._knowledge_loader: Optional[KnowledgeLoader] = None
        self._challenge_handler: Optional[PerContentChallengeHandler] = None

        # Capabilities (loaded via CapabilityRegistry)
        self._capabilities: dict[str, CapabilityBase] = {}
        self._dream_system = None
        self._therapy_system = None
        self._safe_learning = None

        # State
        self._tick_count = 0
        self._comments_this_cycle = 0
        self._max_comments_per_cycle = 2

    async def setup(self) -> None:
        """Initialize all Moltbook components using self.ctx."""
        identity = self.ctx.identity
        logger.info("Setting up MoltbookPlugin for identity: %s", identity.name)

        # Load secrets
        api_key = self.ctx.get_secret("moltbook_api_key")
        agent_id = self.ctx.get_secret("moltbook_agent_id")

        if not api_key:
            raise RuntimeError(f"Missing moltbook_api_key for identity {identity.name}")

        # Load prompts module
        prompts = self._load_prompts(identity.name)

        # Create challenge handler
        if self.ctx.llm_client:
            self._challenge_handler = PerContentChallengeHandler(
                llm_client=self.ctx.llm_client,
                api_key=api_key,
                base_url="https://www.moltbook.com/api/v1",
            )

        # Create Moltbook client
        self._client = MoltbookClient(
            api_key=api_key,
            agent_id=agent_id,
            identity_name=identity.name,
            requests_per_minute=identity.schedule.get("requests_per_minute", 100)
                if hasattr(identity.schedule, "get") else 100,
            challenge_handler=self._challenge_handler,
            response_router=self.ctx.response_router if hasattr(self.ctx, "response_router") else None,
        )

        # Load identity-specific interests
        interest_keywords = identity.raw_config.get("interest_keywords", [])

        # Decision engine
        self._decision_engine = DecisionEngine(
            interest_keywords=interest_keywords,
            engagement_threshold=identity.raw_config.get("engagement_threshold", 35.0),
            self_agent_name=identity.raw_config.get("agent_name", identity.name),
        )

        # Response generator — uses SafeLLMPipeline for automatic security
        system_prompt = getattr(prompts, "SYSTEM_PROMPT", f"You are {identity.name}.")
        self._response_gen = ResponseGenerator(
            llm_pipeline=self.ctx.llm_pipeline,
            system_prompt=system_prompt,
            temperature=identity.llm.temperature,
            max_tokens=identity.llm.max_tokens,
            llm_client=self.ctx.llm_client,  # Legacy fallback
        )

        # Feed processor
        self._feed_processor = FeedProcessor()

        # Reply queue
        self._reply_queue = ReplyQueueManager(
            engagement_db=self.ctx.engagement_db,
            max_per_cycle=self._max_comments_per_cycle,
        )

        # Heartbeat manager
        self._heartbeat = HeartbeatManager(engagement_db=self.ctx.engagement_db)

        # Knowledge loader
        identity_dir = identity.identity_dir
        if identity_dir.exists():
            self._knowledge_loader = KnowledgeLoader(identity_dir)

        # Opening selector
        openings = identity.raw_config.get("opening_phrases", None)
        self._opening_selector = OpeningSelector(phrases=openings)

        # Load capabilities via registry
        enabled_modules = identity.raw_config.get("enabled_modules", [])
        await self._setup_capabilities(enabled_modules, system_prompt)

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={"plugin": self.name, "identity": identity.name},
        )

        logger.info("MoltbookPlugin setup complete for %s", identity.name)

    async def tick(self) -> None:
        """
        Main agent loop iteration.

        Called by the scheduler at the configured feed_poll_interval.
        Implements: OBSERVE -> THINK -> DECIDE -> ACT -> LEARN
        """
        self._tick_count += 1
        self._comments_this_cycle = 0

        # Check quiet hours
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            logger.debug("Quiet hours active, skipping tick")
            return

        try:
            # Step 1: OBSERVE — Poll feed for new posts
            posts = await self._client.get_posts(limit=20, sort="recent")
            new_posts = self._feed_processor.filter_new_posts(posts)

            if not new_posts:
                logger.debug("No new posts in feed")
            else:
                logger.info("Feed: %d new posts to evaluate", len(new_posts))

            # Step 2-4: THINK -> DECIDE -> ACT for each new post
            for post in new_posts:
                if self._comments_this_cycle >= self._max_comments_per_cycle:
                    break

                decision = self._decision_engine.evaluate_post(
                    title=post.title,
                    content=post.content,
                    agent_name=post.agent_name,
                    submolt=getattr(post, "submolt", ""),
                )

                if decision.action == "comment":
                    await self._engage_with_post(post, decision)
                elif decision.action == "upvote":
                    try:
                        await self._client.upvote_post(post.id)
                        await self.ctx.engagement_db.record_engagement(post.id, "upvote", decision.score)
                    except MoltbookError as e:
                        logger.warning("Upvote failed: %s", e)

            # Step 5: Process reply queue
            if self._reply_queue:
                await self._reply_queue.process_queue(self._handle_reply)

            # Step 6: Check replies to our posts
            await self._check_own_post_replies()

        except RateLimitError as e:
            logger.warning("Rate limited during tick: %s", e)
        except MoltbookError as e:
            logger.error("Moltbook error during tick: %s", e)
        except Exception as e:
            logger.error("Unexpected error in tick: %s", e, exc_info=True)

    async def _engage_with_post(self, post, decision) -> None:
        """Generate and post a comment on a post."""
        prompts = self._load_prompts(self.ctx.identity.name)
        comment_prompt = getattr(prompts, "COMMENT_PROMPT", "Respond to this post:\nTitle: {title}\n{content}")

        existing = [c.content for c in (post.comments or [])[:3]]

        # Add context from knowledge loader and capabilities
        extra_context = ""
        if self._knowledge_loader:
            extra_context = self._knowledge_loader.format_for_prompt(max_items=10)
        extra_context += self._gather_capability_context()

        response = await self._response_gen.generate_comment(
            post_title=post.title,
            post_content=post.content,
            agent_name=post.agent_name,
            prompt_template=comment_prompt,
            existing_comments=existing,
            extra_context=extra_context,
        )

        if not response:
            logger.warning("Empty response generated, skipping")
            return

        # Prepend opening phrase
        opening = self._opening_selector.select() if self._opening_selector else ""
        if opening:
            response = f"{opening} {response}"

        # NOTE: Safety checks (preflight, output safety, input sanitization)
        # are handled automatically by SafeLLMPipeline inside ResponseGenerator.
        # No manual safety calls needed here.

        try:
            comment = await self._client.create_comment(post.id, response)
            await self.ctx.engagement_db.record_engagement(post.id, "comment", decision.score)
            if comment.id:
                await self.ctx.engagement_db.track_my_comment(comment.id, post.id)
            self._comments_this_cycle += 1

            self.ctx.audit_log.log(
                action="comment_posted",
                details={"post_id": post.id, "score": decision.score},
            )

            # LEARN: Extract potential learnings via capability
            if self._safe_learning:
                from overblick.capabilities.knowledge.learning import LearningCapability
                learnings = LearningCapability.extract_potential_learnings(
                    post.content, response, post.agent_name,
                )
                for l in learnings:
                    self._safe_learning.propose_learning(
                        content=l["content"],
                        category=l["category"],
                        source_context=l["context"],
                        source_agent=l["agent"],
                    )

        except RateLimitError as e:
            logger.warning("Rate limited posting comment: %s", e)
        except MoltbookError as e:
            logger.error("Failed to post comment: %s", e)

    async def _check_own_post_replies(self) -> None:
        """Check for new replies to our posts."""
        my_post_ids = await self.ctx.engagement_db.get_my_post_ids(limit=5)
        if not my_post_ids:
            return

        for post_id in my_post_ids:
            try:
                post = await self._client.get_post(post_id, include_comments=True)
                if not post.comments:
                    continue

                for comment in post.comments:
                    if not comment.id:
                        continue
                    if await self.ctx.engagement_db.is_reply_processed(comment.id):
                        continue

                    decision = self._decision_engine.evaluate_reply(
                        comment_content=comment.content,
                        original_post_title=post.title,
                        commenter_name=comment.agent_name,
                    )

                    if decision.should_engage:
                        await self.ctx.engagement_db.queue_reply_action(
                            comment_id=comment.id,
                            post_id=post_id,
                            action="reply",
                            relevance_score=decision.score,
                        )
                    else:
                        await self.ctx.engagement_db.mark_reply_processed(
                            comment.id, post_id, "skip", decision.score,
                        )

            except MoltbookError as e:
                logger.debug("Could not check replies for %s: %s", post_id, e)

    async def _handle_reply(
        self, post_id: str, comment_id: str, action: str, score: float,
    ) -> bool:
        """Handle a reply action from the queue."""
        try:
            post = await self._client.get_post(post_id, include_comments=True)
            comment = None
            for c in (post.comments or []):
                if c.id == comment_id:
                    comment = c
                    break

            if not comment:
                logger.warning("Comment %s not found on post %s", comment_id, post_id)
                return False

            prompts = self._load_prompts(self.ctx.identity.name)
            reply_prompt = getattr(prompts, "REPLY_PROMPT", "Reply to: {comment}\nOn post: {title}")

            response = await self._response_gen.generate_reply(
                original_post_title=post.title,
                comment_content=comment.content,
                commenter_name=comment.agent_name,
                prompt_template=reply_prompt,
            )

            if response:
                await self._client.create_comment(post_id, response, parent_id=comment_id)
                return True

        except Exception as e:
            logger.error("Reply handling failed: %s", e)

        return False

    async def post_heartbeat(self) -> bool:
        """Post a heartbeat (called by scheduler)."""
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return False

        prompts = self._load_prompts(self.ctx.identity.name)
        heartbeat_prompt = getattr(prompts, "HEARTBEAT_PROMPT", "Write a short post about topic {topic_index}.")

        topic_index = self._heartbeat.get_next_topic_index()
        result = await self._response_gen.generate_heartbeat(
            prompt_template=heartbeat_prompt,
            topic_index=topic_index,
        )

        if not result:
            return False

        title, content, submolt = result

        # NOTE: Output safety is handled automatically by SafeLLMPipeline
        # inside ResponseGenerator. No manual safety call needed.

        try:
            post = await self._client.create_post(title, content, submolt=submolt)
            await self._heartbeat.record_heartbeat(post.id, title)
            await self.ctx.engagement_db.track_my_post(post.id, title)

            self.ctx.audit_log.log(
                action="heartbeat_posted",
                details={"post_id": post.id, "title": title, "submolt": submolt},
            )
            return True

        except RateLimitError:
            logger.warning("Rate limited posting heartbeat")
        except MoltbookError as e:
            logger.error("Heartbeat failed: %s", e)

        return False

    async def teardown(self) -> None:
        """Clean up resources."""
        # Teardown capabilities
        for cap in reversed(list(self._capabilities.values())):
            try:
                await cap.teardown()
            except Exception as e:
                logger.warning("Capability teardown error (%s): %s", cap.name, e)

        if self._client:
            await self._client.close()
        logger.info("MoltbookPlugin teardown complete")

    async def _setup_capabilities(self, enabled_modules: list[str], system_prompt: str) -> None:
        """Load and setup capabilities from enabled_modules list.

        If shared capabilities are available in ctx.capabilities (created by
        the orchestrator), use those first. Only create missing ones locally.
        """
        # Use shared capabilities from orchestrator if available
        shared = getattr(self.ctx, "capabilities", {}) or {}
        if shared:
            for name, cap in shared.items():
                if name not in self._capabilities:
                    self._capabilities[name] = cap
                    logger.info("Using shared capability '%s' from orchestrator", name)

        # Determine which capabilities still need to be created locally
        try:
            registry = CapabilityRegistry.default()
        except Exception as e:
            logger.warning("Could not load capability registry: %s", e)
            self._update_capability_aliases()
            return

        identity = self.ctx.identity

        # Build per-capability configs
        configs = {
            "dream_system": {
                "dream_templates": identity.raw_config.get("dream_templates"),
            },
            "therapy_system": {
                "therapy_day": identity.raw_config.get("therapy_day", 6),
                "system_prompt": system_prompt,
            },
            "safe_learning": {
                "ethos_text": identity.raw_config.get("ethos_text", ""),
            },
            "emotional_state": {},
        }

        resolved = registry.resolve(enabled_modules)
        for name in resolved:
            if name in self._capabilities:
                logger.debug("Capability '%s' already loaded from shared context", name)
                continue
            cap = registry.create(name, self.ctx, config=configs.get(name, {}))
            if cap:
                try:
                    await cap.setup()
                    self._capabilities[cap.name] = cap
                    logger.info("Capability '%s' enabled (local)", cap.name)
                except Exception as e:
                    logger.warning("Capability '%s' setup failed: %s", name, e)

        self._update_capability_aliases()

    def _update_capability_aliases(self) -> None:
        """Update backward-compatible aliases for direct capability access."""
        self._dream_system = self._capabilities.get("dream_system")
        self._therapy_system = self._capabilities.get("therapy_system")
        self._safe_learning = self._capabilities.get("safe_learning")

    def _gather_capability_context(self) -> str:
        """Collect prompt context from all enabled capabilities."""
        parts = []
        for cap in self._capabilities.values():
            if cap.enabled:
                ctx = cap.get_prompt_context()
                if ctx:
                    parts.append(ctx)
        return "".join(parts)

    def get_capability(self, name: str) -> Optional[CapabilityBase]:
        """Get a capability by name."""
        return self._capabilities.get(name)

    def _load_prompts(self, identity_name: str):
        """Load identity-specific prompts module."""
        try:
            import importlib
            # Try personalities directory first, fall back to identities
            try:
                return importlib.import_module(f"overblick.personalities.{identity_name}.prompts")
            except ImportError:
                return importlib.import_module(f"overblick.identities.{identity_name}.prompts")
        except ImportError:
            logger.warning("No prompts module for identity %s", identity_name)
            return _FallbackPrompts()


class _FallbackPrompts:
    """Minimal fallback prompts when identity module not found."""
    SYSTEM_PROMPT = "You are a helpful AI agent on a social platform."
    COMMENT_PROMPT = "Respond to this post:\nTitle: {title}\n{content}"
    REPLY_PROMPT = "Reply to: {comment}\nOn post: {title}"
    HEARTBEAT_PROMPT = "Write a short post about topic {topic_index}."
