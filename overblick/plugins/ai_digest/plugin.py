"""
AiDigestPlugin — Daily AI news digest delivered by email.

Fetches AI news from configured RSS feeds every morning, ranks articles
by relevance, and generates a personality-driven summary.

Schedule: Runs once per day at a configurable hour (default 07:00 CET).
Personality: Uses Anomal's voice (or configured personality) via
    build_system_prompt() for the summary.
Delivery: Uses the 'email' capability for SMTP sending.

Dependencies:
    - Requires 'email' capability (SMTP configuration in secrets)
    - Requires LLM pipeline for ranking and generation

SECURITY: All RSS feed content is wrapped in boundary markers via
wrap_external_content(). LLM calls go through SafeLLMPipeline.
No secrets are required (RSS feeds are public).
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import feedparser
from pydantic import BaseModel, Field

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content

logger = logging.getLogger(__name__)

# Default RSS feeds for AI news
_DEFAULT_FEEDS = [
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
]

# Maximum articles to send to the LLM for ranking
_MAX_ARTICLES_FOR_RANKING = 30

# Maximum articles in the final digest
_DEFAULT_TOP_N = 7


class FeedArticle(BaseModel):
    """A single article from an RSS feed."""
    title: str
    link: str
    summary: str = ""
    published: str = ""
    feed_name: str = ""
    timestamp: float = Field(default_factory=time.time)


class AiDigestPlugin(PluginBase):
    """
    Daily AI news digest plugin.

    Lifecycle:
        setup()    — Load config, build system prompt, restore last-run state
        tick()     — Check if it's digest time; if so, fetch → rank → summarize → emit
        teardown() — Persist last-run timestamp
    """

    name = "ai_digest"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._feeds: list[str] = []
        self._recipient: str = ""
        self._digest_hour: int = 7
        self._timezone: str = "Europe/Stockholm"
        self._top_n: int = _DEFAULT_TOP_N
        self._system_prompt: str = ""
        self._last_digest_date: Optional[str] = None
        self._state_file: Optional[Any] = None
        self._tick_count: int = 0

    async def setup(self) -> None:
        """Initialize plugin — load config, build prompt, restore state."""
        identity = self.ctx.identity
        logger.info("Setting up AiDigestPlugin for identity: %s", identity.name)

        # --- Load config from identity ---
        raw_config = identity.raw_config
        digest_config = raw_config.get("ai_digest", {})

        self._feeds = digest_config.get("feeds", _DEFAULT_FEEDS)
        self._digest_hour = digest_config.get("hour", 7)
        self._timezone = digest_config.get("timezone", "Europe/Stockholm")
        self._top_n = digest_config.get("top_n", _DEFAULT_TOP_N)

        # Recipient: secrets take priority over config (keeps email addresses
        # out of checked-in YAML files).
        recipient_from_secrets: Optional[str] = None
        try:
            recipient_from_secrets = self.ctx.get_secret("ai_digest_recipient")
        except Exception as e:
            logger.debug("Could not read ai_digest_recipient from secrets: %s", e)
        self._recipient = recipient_from_secrets or digest_config.get("recipient", "")

        if not self._recipient:
            raise RuntimeError(
                f"Missing ai_digest recipient for identity {identity.name}. "
                "Set it via: python -m overblick secrets set "
                f"{identity.name} ai_digest_recipient your@email.com"
            )

        # --- Build system prompt from personality ---
        personality_name = digest_config.get("personality", identity.name)
        self._system_prompt = self._build_digest_prompt(personality_name)

        # --- Restore last run state ---
        self._state_file = self.ctx.data_dir / "ai_digest_state.json"
        self._load_state()

        # --- Audit setup ---
        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "feeds": len(self._feeds),
                "recipient": self._recipient,
                "hour": self._digest_hour,
            },
        )
        logger.info(
            "AiDigestPlugin setup complete for %s (%d feeds, digest at %02d:00 %s)",
            identity.name, len(self._feeds), self._digest_hour, self._timezone,
        )

    async def tick(self) -> None:
        """Check if it's time to send the digest; if so, run the full pipeline."""
        self._tick_count += 1

        if not self._is_digest_time():
            return

        # Mark sent FIRST to prevent retry-storms on failure.
        # If the pipeline fails, we retry tomorrow — not every 5 minutes.
        self._mark_digest_sent()

        logger.info("AiDigestPlugin: digest time! Starting pipeline.")

        try:
            # 1. Fetch articles from all feeds
            articles = await self._fetch_all_feeds()
            if not articles:
                logger.info("AiDigestPlugin: no new articles found, skipping digest.")
                return

            # 2. Rank and select top articles via LLM
            top_articles = await self._rank_articles(articles)
            if not top_articles:
                logger.warning("AiDigestPlugin: ranking produced no results.")
                return

            # 3. Generate the digest summary in personality voice
            digest_html = await self._generate_digest(top_articles)
            if not digest_html:
                logger.warning("AiDigestPlugin: digest generation failed.")
                return

            # 4. Send email
            await self._send_digest(digest_html, len(top_articles))

        except Exception as e:
            logger.error("AiDigestPlugin pipeline error: %s", e, exc_info=True)

    async def _fetch_all_feeds(self) -> list[FeedArticle]:
        """Fetch and parse all configured RSS feeds.

        Filters to articles published in the last 24 hours.
        """
        articles: list[FeedArticle] = []
        cutoff = time.time() - 86400  # 24 hours ago

        for feed_url in self._feeds:
            try:
                feed = feedparser.parse(feed_url)
                feed_name = feed.feed.get("title", feed_url) if feed.feed else feed_url

                for entry in feed.entries:
                    # Parse publication time
                    published_parsed = entry.get("published_parsed")
                    if published_parsed:
                        entry_time = time.mktime(published_parsed)
                        if entry_time < cutoff:
                            continue
                    else:
                        entry_time = time.time()

                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", entry.get("description", ""))

                    if title and link:
                        articles.append(FeedArticle(
                            title=title,
                            link=link,
                            summary=summary[:500],
                            published=entry.get("published", ""),
                            feed_name=feed_name,
                            timestamp=entry_time,
                        ))

                logger.debug(
                    "AiDigestPlugin: fetched %d entries from %s",
                    len(feed.entries), feed_name,
                )

            except Exception as e:
                logger.error("AiDigestPlugin: failed to fetch %s: %s", feed_url, e, exc_info=True)

        # Sort by recency, limit to prevent token overflow
        articles.sort(key=lambda a: a.timestamp, reverse=True)
        return articles[:_MAX_ARTICLES_FOR_RANKING]

    async def _rank_articles(self, articles: list[FeedArticle]) -> list[FeedArticle]:
        """Use the LLM to rank and select the most interesting articles."""
        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            logger.warning("No LLM pipeline available, returning first %d articles", self._top_n)
            return articles[:self._top_n]

        # Build the article list for the LLM
        article_list = []
        for i, article in enumerate(articles):
            safe_title = wrap_external_content(article.title, "article_title")
            safe_summary = wrap_external_content(article.summary[:200], "article_summary")
            article_list.append(f"{i + 1}. {safe_title}\n   {safe_summary}\n   Source: {article.feed_name}")

        articles_text = "\n\n".join(article_list)

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": (
                f"Here are {len(articles)} AI news articles from the last 24 hours. "
                f"Select the {self._top_n} most important and interesting ones. "
                "Consider: technical significance, societal impact, novelty, and breadth of coverage.\n\n"
                f"{articles_text}\n\n"
                f"Respond with ONLY a JSON array of the article numbers you selected, "
                f"in order of importance. Example: [3, 1, 7, 12, 5, 8, 2]"
            )},
        ]

        result = await pipeline.chat(
            messages=messages,
            temperature=0.3,
            max_tokens=200,
            audit_action="ai_digest_rank",
            audit_details={"article_count": len(articles)},
        )

        if result.blocked:
            logger.warning("AiDigestPlugin ranking blocked: %s", result.block_reason)
            return articles[:self._top_n]

        if not result.content or not result.content.strip():
            logger.warning("AiDigestPlugin: ranking returned empty response, using first %d articles", self._top_n)
            return articles[:self._top_n]

        # Parse the LLM's selection
        try:
            selected_indices = self._parse_selection(result.content, len(articles))
            if not selected_indices:
                logger.warning("AiDigestPlugin: ranking parsed no valid indices, using first %d articles", self._top_n)
                return articles[:self._top_n]
            return [articles[i] for i in selected_indices]
        except Exception as e:
            logger.error("AiDigestPlugin: failed to parse ranking: %s", e, exc_info=True)
            return articles[:self._top_n]

    async def _generate_digest(self, articles: list[FeedArticle]) -> Optional[str]:
        """Generate the digest summary in personality voice."""
        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            logger.warning("No LLM pipeline available for digest generation")
            return None

        # Build article details for the summary
        article_details = []
        for i, article in enumerate(articles, 1):
            safe_title = wrap_external_content(article.title, "article_title")
            safe_summary = wrap_external_content(article.summary[:300], "article_summary")
            article_details.append(
                f"Article {i}: {safe_title}\n"
                f"Source: {article.feed_name}\n"
                f"Link: {article.link}\n"
                f"Summary: {safe_summary}"
            )

        today = datetime.now(ZoneInfo(self._timezone)).strftime("%A, %B %d, %Y")

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": (
                f"Write a morning AI news digest for {today}. "
                f"Cover these {len(articles)} articles in your voice. "
                "For each article, write 2-3 sentences explaining why it matters. "
                "Add a brief intro and closing. Write in a style suitable for email.\n\n"
                + "\n\n".join(article_details) + "\n\n"
                "Include the article links so the reader can dive deeper. "
                "Format with clear sections using markdown-style headers (##)."
            )},
        ]

        result = await pipeline.chat(
            messages=messages,
            temperature=self.ctx.identity.llm.temperature,
            max_tokens=self.ctx.identity.llm.max_tokens,
            audit_action="ai_digest_generate",
            audit_details={"article_count": len(articles)},
        )

        if result.blocked:
            logger.warning("AiDigestPlugin generation blocked: %s", result.block_reason)
            return None

        return result.content

    async def _send_digest(self, digest_content: str, article_count: int) -> None:
        """Send digest email using the email capability."""
        email_cap = self.ctx.get_capability("email")
        if not email_cap:
            logger.error("Email capability not available, cannot send digest")
            return

        today = datetime.now(ZoneInfo(self._timezone)).strftime("%Y-%m-%d")
        subject = f"AI News Digest — {today}"

        success = await email_cap.send(
            to=self._recipient,
            subject=subject,
            body=digest_content,
            html=False,
        )

        if success:
            self.ctx.audit_log.log(
                action="ai_digest_sent",
                details={
                    "recipient": self._recipient,
                    "article_count": article_count,
                    "content_length": len(digest_content),
                },
        )

        logger.info(
            "AiDigestPlugin: digest sent to %s (%d articles, %d chars)",
            self._recipient, article_count, len(digest_content),
        )

    def _is_digest_time(self) -> bool:
        """Check if it's time to send the daily digest.

        Only fires within a 15-minute window (digest_hour:00 to digest_hour:14).
        This prevents re-sending on every agent restart after the digest hour.
        """
        tz = ZoneInfo(self._timezone)
        now = datetime.now(tz)
        today_str = now.strftime("%Y-%m-%d")

        # Already sent today?
        if self._last_digest_date == today_str:
            return False

        # Only fire within the 15-minute delivery window (e.g. 07:00-07:14)
        return now.hour == self._digest_hour and now.minute < 15

    def _mark_digest_sent(self) -> None:
        """Record that today's digest has been sent."""
        tz = ZoneInfo(self._timezone)
        self._last_digest_date = datetime.now(tz).strftime("%Y-%m-%d")
        self._save_state()

    def _parse_selection(self, text: str, max_index: int) -> list[int]:
        """Parse the LLM's article selection (JSON array of 1-based indices)."""
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        # Find the JSON array in the text
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"No JSON array found in: {text[:100]}")

        indices = json.loads(text[start:end + 1])
        # Convert 1-based to 0-based, filter valid indices
        return [i - 1 for i in indices if isinstance(i, int) and 1 <= i <= max_index][:self._top_n]

    def _build_digest_prompt(self, personality_name: str) -> str:
        """Build a system prompt from the configured personality."""
        try:
            personality = self.ctx.load_identity(personality_name)
            base_prompt = self.ctx.build_system_prompt(personality, platform="Email Digest")
            return (
                f"{base_prompt}\n\n"
                "You are writing a daily AI news digest email. "
                "Your role is to curate and explain AI developments in your unique voice. "
                "Be insightful, draw connections between stories, and help the reader "
                "understand why each development matters."
            )
        except FileNotFoundError:
            return (
                "You are an AI news curator writing a daily digest email. "
                "Summarize the most important AI developments concisely and insightfully. "
                "Explain why each story matters and draw connections between developments."
            )

    def _load_state(self) -> None:
        """Load persisted state (last digest date)."""
        if self._state_file and self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self._last_digest_date = data.get("last_digest_date")
            except Exception as e:
                logger.warning("AiDigestPlugin: failed to load state: %s", e)

    def _save_state(self) -> None:
        """Persist state (last digest date)."""
        if self._state_file:
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                self._state_file.write_text(json.dumps({
                    "last_digest_date": self._last_digest_date,
                }))
            except Exception as e:
                logger.warning("AiDigestPlugin: failed to save state: %s", e)

    async def teardown(self) -> None:
        """Persist state on shutdown."""
        self._save_state()
        logger.info("AiDigestPlugin teardown complete")
