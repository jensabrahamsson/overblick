"""
KontrastPlugin — Multi-Perspective Content Engine.

When a news topic emerges, ALL available identities write their take
simultaneously. Published side-by-side as a "Kontrast" piece on the
dashboard. Same event, multiple worldviews.

Architecture: Scheduled + event-driven. RSS trigger (reuses AI Digest
feed infra) -> fan-out to identities via LLM pipeline -> collect ->
assemble -> publish to dashboard.

Security: All external RSS content is wrapped in boundary markers via
wrap_external_content(). LLM calls go through SafeLLMPipeline.
"""

import hashlib
import json
import logging
import time
from typing import Any, Optional

import feedparser
from pydantic import BaseModel

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content
from overblick.identities import list_identities

from .models import KontrastPiece, PerspectiveEntry

logger = logging.getLogger(__name__)

# Defaults
_DEFAULT_FEEDS = [
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
]
_DEFAULT_INTERVAL_HOURS = 24
_DEFAULT_MIN_ARTICLES = 3
_MAX_ARTICLES = 20
_MAX_PIECES_STORED = 50


class KontrastPlugin(PluginBase):
    """
    Multi-perspective content engine.

    Lifecycle:
        setup()    — Load config, discover identities, restore state
        tick()     — Check for new topics, generate perspectives
        teardown() — Persist state
    """

    name = "kontrast"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._feeds: list[str] = []
        self._interval_hours: int = _DEFAULT_INTERVAL_HOURS
        self._min_articles: int = _DEFAULT_MIN_ARTICLES
        self._identity_names: list[str] = []
        self._pieces: list[KontrastPiece] = []
        self._seen_topic_hashes: set[str] = set()
        self._last_run: float = 0.0
        self._state_file: Optional[Any] = None
        self._tick_count: int = 0

    async def setup(self) -> None:
        """Initialize plugin — load config, discover identities, restore state."""
        identity = self.ctx.identity
        logger.info("Setting up KontrastPlugin for identity: %s", identity.name)

        raw_config = identity.raw_config
        kontrast_config = raw_config.get("kontrast", {})

        self._feeds = kontrast_config.get("feeds", _DEFAULT_FEEDS)
        self._interval_hours = kontrast_config.get(
            "interval_hours", _DEFAULT_INTERVAL_HOURS
        )
        self._min_articles = kontrast_config.get("min_articles", _DEFAULT_MIN_ARTICLES)

        # Discover available identities (or use configured subset)
        configured_identities = kontrast_config.get("identities", [])
        if configured_identities:
            self._identity_names = configured_identities
        else:
            self._identity_names = list_identities()

        # State persistence
        self._state_file = self.ctx.data_dir / "kontrast_state.json"
        self._load_state()

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "feeds": len(self._feeds),
                "identities": len(self._identity_names),
                "interval_hours": self._interval_hours,
            },
        )
        logger.info(
            "KontrastPlugin setup complete (%d feeds, %d identities, %dh interval)",
            len(self._feeds),
            len(self._identity_names),
            self._interval_hours,
        )

    async def tick(self) -> None:
        """Check if it's time to generate a new Kontrast piece."""
        self._tick_count += 1

        if not self._is_run_time():
            return

        self._last_run = time.time()
        logger.info("KontrastPlugin: starting perspective generation")

        try:
            # 1. Fetch articles from RSS feeds
            articles = await self._fetch_feeds()
            if len(articles) < self._min_articles:
                logger.info(
                    "KontrastPlugin: only %d articles (need %d), skipping",
                    len(articles),
                    self._min_articles,
                )
                self._save_state()
                return

            # 2. Extract topic from articles via LLM
            topic, summary = await self._extract_topic(articles)
            if not topic:
                logger.warning("KontrastPlugin: failed to extract topic")
                self._save_state()
                return

            # 3. Check for duplicate topic
            topic_hash = hashlib.sha256(topic.lower().encode()).hexdigest()[:16]
            if topic_hash in self._seen_topic_hashes:
                logger.info("KontrastPlugin: topic already covered: %s", topic)
                self._save_state()
                return

            # 4. Generate perspectives from each identity
            perspectives = await self._generate_perspectives(topic, summary)

            # 5. Assemble and store the piece
            piece = KontrastPiece(
                topic=topic,
                topic_hash=topic_hash,
                source_summary=summary,
                perspectives=perspectives,
                article_count=len(articles),
            )
            self._pieces.append(piece)
            self._seen_topic_hashes.add(topic_hash)

            # Trim old pieces
            if len(self._pieces) > _MAX_PIECES_STORED:
                self._pieces = self._pieces[-_MAX_PIECES_STORED:]

            self._save_state()

            # 6. Emit event for other plugins (e.g. Telegram notification)
            if self.ctx.event_bus:
                await self.ctx.event_bus.emit(
                    "kontrast.new_piece",
                    {
                        "topic": topic,
                        "identity_count": len(perspectives),
                        "article_count": len(articles),
                    },
                )

            self.ctx.audit_log.log(
                action="kontrast_generated",
                details={
                    "topic": topic,
                    "perspectives": len(perspectives),
                    "articles": len(articles),
                },
            )
            logger.info(
                "KontrastPlugin: generated piece on '%s' with %d perspectives",
                topic,
                len(perspectives),
            )

        except Exception as e:
            logger.error("KontrastPlugin pipeline error: %s", e, exc_info=True)
            self._save_state()

    async def _fetch_feeds(self) -> list[dict[str, str]]:
        """Fetch recent articles from configured RSS feeds."""
        articles: list[dict[str, str]] = []
        cutoff = time.time() - 86400  # 24 hours

        for feed_url in self._feeds:
            try:
                feed = feedparser.parse(feed_url)
                feed_name = feed.feed.get("title", feed_url) if feed.feed else feed_url

                for entry in feed.entries:
                    published_parsed = entry.get("published_parsed")
                    if published_parsed:
                        entry_time = time.mktime(published_parsed)
                        if entry_time < cutoff:
                            continue

                    title = entry.get("title", "")
                    summary = entry.get("summary", entry.get("description", ""))[:500]

                    if title:
                        articles.append({
                            "title": title,
                            "summary": summary,
                            "feed": feed_name,
                        })
            except Exception as e:
                logger.error("KontrastPlugin: feed error %s: %s", feed_url, e)

        articles = articles[:_MAX_ARTICLES]
        return articles

    async def _extract_topic(
        self, articles: list[dict[str, str]]
    ) -> tuple[str, str]:
        """Use LLM to extract the dominant topic from articles."""
        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            # Fallback: use first article title
            return articles[0]["title"], articles[0].get("summary", "")

        article_text = "\n".join(
            f"- {wrap_external_content(a['title'], 'article_title')}: "
            f"{wrap_external_content(a.get('summary', '')[:200], 'article_summary')}"
            for a in articles
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a news analyst. Extract the single most significant "
                    "topic from the articles below. Respond with ONLY a JSON object: "
                    '{"topic": "short topic title", "summary": "2-3 sentence summary"}'
                ),
            },
            {
                "role": "user",
                "content": f"Articles:\n{article_text}",
            },
        ]

        result = await pipeline.chat(
            messages=messages,
            temperature=0.3,
            max_tokens=300,
            audit_action="kontrast_extract_topic",
            audit_details={"article_count": len(articles)},
        )

        if result.blocked or not result.content:
            return articles[0]["title"], articles[0].get("summary", "")

        try:
            text = result.content.strip()
            # Strip code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                return articles[0]["title"], articles[0].get("summary", "")

            parsed = json.loads(text[start : end + 1])
            return parsed.get("topic", ""), parsed.get("summary", "")
        except (json.JSONDecodeError, KeyError):
            return articles[0]["title"], articles[0].get("summary", "")

    async def _generate_perspectives(
        self, topic: str, summary: str
    ) -> list[PerspectiveEntry]:
        """Generate a perspective from each identity."""
        pipeline = self.ctx.llm_pipeline
        if not pipeline:
            return []

        perspectives: list[PerspectiveEntry] = []

        for identity_name in self._identity_names:
            try:
                identity = self.ctx.load_identity(identity_name)
                system_prompt = self.ctx.build_system_prompt(
                    identity, platform="Kontrast Panel"
                )

                safe_topic = wrap_external_content(topic, "kontrast_topic")
                safe_summary = wrap_external_content(summary, "kontrast_summary")

                messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"A major topic is emerging: {safe_topic}\n\n"
                            f"Context: {safe_summary}\n\n"
                            "Write your take on this in 150-300 words. "
                            "Be opinionated, stay in character, and bring "
                            "your unique perspective. What does this mean "
                            "through your lens?"
                        ),
                    },
                ]

                result = await pipeline.chat(
                    messages=messages,
                    temperature=identity.llm.temperature,
                    max_tokens=800,
                    audit_action="kontrast_perspective",
                    audit_details={
                        "identity": identity_name,
                        "topic": topic,
                    },
                )

                if not result.blocked and result.content:
                    perspectives.append(
                        PerspectiveEntry(
                            identity_name=identity_name,
                            display_name=identity.display_name,
                            content=result.content,
                        )
                    )
                else:
                    logger.warning(
                        "KontrastPlugin: %s perspective blocked: %s",
                        identity_name,
                        result.block_reason,
                    )

            except FileNotFoundError:
                logger.warning(
                    "KontrastPlugin: identity '%s' not found, skipping",
                    identity_name,
                )
            except Exception as e:
                logger.error(
                    "KontrastPlugin: error generating %s perspective: %s",
                    identity_name,
                    e,
                    exc_info=True,
                )

        return perspectives

    def get_pieces(self, limit: int = 10) -> list[KontrastPiece]:
        """Get recent Kontrast pieces (newest first)."""
        return list(reversed(self._pieces[-limit:]))

    def get_piece_by_hash(self, topic_hash: str) -> Optional[KontrastPiece]:
        """Get a specific piece by topic hash."""
        for piece in self._pieces:
            if piece.topic_hash == topic_hash:
                return piece
        return None

    def _is_run_time(self) -> bool:
        """Check if enough time has passed since last run."""
        if self._last_run == 0.0:
            return True
        elapsed = time.time() - self._last_run
        return elapsed >= self._interval_hours * 3600

    def _load_state(self) -> None:
        """Load persisted state."""
        if self._state_file and self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                self._last_run = data.get("last_run", 0.0)
                self._seen_topic_hashes = set(data.get("seen_topic_hashes", []))

                for piece_data in data.get("pieces", []):
                    self._pieces.append(KontrastPiece.model_validate(piece_data))
            except Exception as e:
                logger.warning("KontrastPlugin: failed to load state: %s", e)

    def _save_state(self) -> None:
        """Persist state."""
        if self._state_file:
            try:
                data = {
                    "last_run": self._last_run,
                    "seen_topic_hashes": list(self._seen_topic_hashes),
                    "pieces": [
                        p.model_dump() for p in self._pieces[-_MAX_PIECES_STORED:]
                    ],
                }
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                self._state_file.write_text(json.dumps(data, indent=2))
            except Exception as e:
                logger.warning("KontrastPlugin: failed to save state: %s", e)

    async def teardown(self) -> None:
        """Persist state on shutdown."""
        self._save_state()
        logger.info("KontrastPlugin teardown complete")
