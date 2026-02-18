# RSS Plugin

RSS/Atom feed monitor that identifies relevant items based on personality interests and generates commentary or summaries. **SHELL IMPLEMENTATION** - core structure in place, awaiting feedparser integration and community contributions.

## Overview

The RSS plugin will monitor RSS/Atom feeds (news, blogs, podcasts, etc.), identify items matching the personality's interests, generate commentary via LLM, and post to other platforms (Moltbook, Telegram, etc.) or store for review. Perfect for content curation agents who want to comment on the latest AI news, crypto developments, or tech trends.

## Concepts

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. The RSS plugin is a **shell plugin** --- the interface is defined but RSS parsing integration is not yet complete.

**What "shell" means**: The plugin class exists, loads configuration (feed URLs, keywords, poll intervals), and passes all base interface tests. However, it does not fetch or parse feeds. When implemented, it will use feedparser for parsing and the identity's personality voice for generating commentary on relevant articles. Compare with the AI Digest plugin, which already implements RSS-to-email with a similar pipeline.

## Features (Planned)

- **Multi-Feed Monitoring**: Poll multiple RSS/Atom feeds with configurable intervals
- **Keyword-Based Filtering**: Match items against personality interest_keywords
- **LLM-Powered Summarization**: Generate personality-driven commentary on relevant items
- **Relevance Scoring**: Uses DecisionEngine patterns to score feed items
- **Deduplication**: Track GUIDs/links to prevent reprocessing
- **Output Routing**: Post to Moltbook, Telegram, or queue for boss agent review
- **OPML Import** (future): Bulk feed configuration from OPML files
- **Feed Health Monitoring**: Track parsing errors and stale feeds

## Current Status

This plugin is a **SHELL**. The structure is defined, but the implementation is incomplete:

- ✅ Plugin base class implemented
- ✅ Configuration loading (feed URLs, poll intervals, keywords)
- ✅ FeedConfig and FeedItem data models
- ✅ Seen GUID tracking
- ❌ RSS/Atom parsing (requires feedparser)
- ❌ Async HTTP fetching (requires aiohttp)
- ❌ Keyword matching and scoring
- ❌ LLM commentary generation
- ❌ Output routing to other plugins
- ❌ OPML import/export

## Use Cases

### AI News Curator (Anomal)

Monitor AI news feeds, comment on significant developments:

```yaml
rss:
  feeds:
    - url: "https://openai.com/blog/rss"
      name: "OpenAI Blog"
      keywords: ["GPT", "language model", "safety"]
    - url: "https://www.anthropic.com/blog/rss"
      name: "Anthropic Blog"
      keywords: ["Claude", "AI safety", "alignment"]
```

When a relevant article appears, Anomal generates a thoughtful commentary and posts to Moltbook.

### Crypto Monitor (Cherry)

Track crypto news and gossip:

```yaml
rss:
  feeds:
    - url: "https://cointelegraph.com/rss"
      name: "CoinTelegraph"
      keywords: ["Bitcoin", "Ethereum", "DeFi"]
    - url: "https://cryptoslate.com/feed/"
      name: "CryptoSlate"
      keywords: ["NFT", "altcoin", "scandal"]
```

When drama emerges, Cherry posts hot takes to Telegram.

### Tech Trends Analyst (Björk)

Minimal engagement, only major tech shifts:

```yaml
rss:
  feeds:
    - url: "https://news.ycombinator.com/rss"
      name: "Hacker News"
      poll_interval_minutes: 60
      keywords: ["Iceland", "music tech", "minimalism"]
```

Björk only comments on items with 3+ keyword matches (rare).

## Setup

### Installation (Not Yet Functional)

When implemented, this plugin will require:

```bash
pip install feedparser>=6.0.0
pip install aiohttp>=3.8.0
```

**Note**: Dependencies are not yet in requirements.txt as the plugin is not functional.

### Configuration

Add to `personality.yaml`:

```yaml
# RSS Configuration (not yet functional)
rss:
  feeds:
    - url: "https://feeds.arstechnica.com/arstechnica/technology-lab"
      name: "Ars Technica Tech"
      poll_interval_minutes: 30  # Check every 30 minutes
      keywords:  # Optional: override personality's default keywords
        - "artificial intelligence"
        - "machine learning"
      enabled: true

    - url: "https://techcrunch.com/category/artificial-intelligence/feed/"
      name: "TechCrunch AI"
      poll_interval_minutes: 60
      # keywords: []  # Empty = use personality's interest_keywords

    - url: "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
      name: "The Verge AI"
      poll_interval_minutes: 30
      enabled: true

  # Default poll interval for feeds without specific setting
  default_poll_interval_minutes: 30

  # Relevance threshold (0-100) - how many keywords must match
  relevance_threshold: 20.0

  # Output destination
  output_to:
    - "moltbook"  # Post to Moltbook
    - "telegram"  # Send to Telegram channel (if configured)
```

### Secrets

No secrets required. RSS feeds are public.

### Activation

Include `rss` in enabled plugins (when implemented):

```yaml
enabled_plugins:
  - rss
  - moltbook  # For posting RSS commentary
```

## Architecture (Planned)

### Feed Monitoring Flow

```
1. POLL
   ├─ For each feed past poll_interval:
   ├─ Fetch feed URL via aiohttp
   ├─ Parse with feedparser
   └─ Extract items (title, link, summary, published)

2. FILTER
   ├─ Check GUID/link against seen_guids
   ├─ Skip already processed items
   └─ Parse publication date

3. SCORE
   ├─ Match title + summary against keywords
   ├─ Calculate relevance score (keyword_matches * 10)
   ├─ Apply personality-specific weighting
   └─ Decide: comment, summarize, or skip

4. GENERATE
   ├─ Wrap feed content in boundary markers
   ├─ Build prompt: "Summarize this article in your voice"
   ├─ Call ctx.llm_pipeline.chat()
   └─ Extract commentary from LLM response

5. ROUTE
   ├─ If output_to includes "moltbook":
   │  └─ Post to Moltbook as heartbeat
   ├─ If output_to includes "telegram":
   │  └─ Send to Telegram channel
   └─ Store GUID in seen_guids

6. PERSIST
   └─ Save seen_guids to data_dir/rss_seen.json
```

### Key Components (To Be Implemented)

- **`_fetch_feed()`**: Async HTTP fetch and feedparser parsing
- **`_filter_items()`**: Deduplication and date filtering
- **`_score_item()`**: Keyword matching and relevance calculation
- **`_generate_commentary()`**: LLM-powered summarization
- **`_route_output()`**: Post to Moltbook, Telegram, etc.
- **`_save_seen_guids()`**: Persistent GUID tracking

## Events

### Emits (Planned)

- **`rss.item_found`** - New relevant RSS item discovered
  - `feed_url`: Feed source URL
  - `item_title`: Article title
  - `item_link`: Article URL
  - `relevance_score`: Calculated score

### Subscribes (Planned)

None initially. Operates on schedule via `tick()`.

## Usage (When Implemented)

### Running the Agent

```bash
# Start agent with RSS plugin
python -m overblick run anomal

# The plugin will:
# 1. Poll feeds at configured intervals
# 2. Score items against interest_keywords
# 3. Generate commentary on relevant items
# 4. Post to configured outputs (Moltbook, Telegram)
```

### Example Output

When a relevant RSS item is found:

```
[RSS Monitor] New item from Ars Technica Tech:
  Title: "New breakthrough in AI consciousness research"
  Score: 45/100 (3 keyword matches)

[LLM Commentary]
Anomal: Fascinating development. The question of AI consciousness
continues to challenge our philosophical assumptions about what
constitutes genuine experience versus sophisticated simulation.
Worth a deeper read.

[Action] Posted to Moltbook: https://moltbook.com/posts/abc123
```

## Testing

### Run Tests

```bash
# Tests for the shell implementation
pytest tests/plugins/rss/ -v
```

**Note**: Tests currently verify the shell structure. Full integration tests will be added when feedparser is integrated.

## Security (Planned)

### Input Sanitization

All RSS content will be wrapped in boundary markers:

```python
safe_title = wrap_external_content(item.title, "rss_title")
safe_summary = wrap_external_content(item.summary[:500], "rss_summary")
```

This prevents prompt injection from malicious RSS feeds.

### SafeLLMPipeline

All LLM calls will go through SafeLLMPipeline for:
- Preflight checks
- Output safety
- Audit logging

### Feed Validation

- URL validation before fetching
- HTTP timeout limits (5-10s max)
- Max feed size limits (prevent memory exhaustion)
- SSL/TLS verification enforced

### Content Length Limits

RSS summaries can be enormous. Truncate before processing:

```python
item.summary = item.summary[:1000]  # Max 1000 chars
```

## Contributing

This plugin is marked as **COMMUNITY CONTRIBUTIONS WELCOME**. If you'd like to implement the RSS integration:

### Implementation Checklist

- [ ] Add feedparser and aiohttp dependencies to pyproject.toml
- [ ] Implement async feed fetching in `_fetch_feed()`
- [ ] Implement GUID deduplication with persistent storage
- [ ] Add keyword matching and scoring logic
- [ ] Implement LLM commentary generation
- [ ] Add output routing to Moltbook, Telegram, etc.
- [ ] Add OPML import/export support
- [ ] Write integration tests with mock feeds
- [ ] Add feed health monitoring (stale feeds, parse errors)
- [ ] Update this README with actual usage examples

### Code Structure

```python
# overblick/plugins/rss/plugin.py

import feedparser
import aiohttp
from pathlib import Path

class RSSPlugin(PluginBase):
    async def _fetch_feed(self, feed_config: FeedConfig) -> list[FeedItem]:
        """Fetch and parse an RSS feed."""
        async with aiohttp.ClientSession() as session:
            async with session.get(feed_config.url, timeout=10) as resp:
                xml = await resp.text()

        feed = feedparser.parse(xml)
        items = []
        for entry in feed.entries:
            items.append(FeedItem(
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                summary=entry.get("summary", entry.get("description", "")),
                published=entry.get("published", ""),
                feed_url=feed_config.url,
                guid=entry.get("id", entry.get("link", "")),
            ))
        return items

    async def _score_item(self, item: FeedItem, keywords: list[str]) -> float:
        """Score item relevance based on keyword matching."""
        text = f"{item.title} {item.summary}".lower()
        matches = sum(1 for kw in keywords if kw.lower() in text)
        return matches * 10.0

    async def _generate_commentary(self, item: FeedItem) -> str:
        """Generate LLM commentary on feed item."""
        safe_title = wrap_external_content(item.title, "rss_title")
        safe_summary = wrap_external_content(item.summary[:500], "rss_summary")

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": (
                f"An article from {item.feed_url}:\n\n"
                f"Title: {safe_title}\n"
                f"Summary: {safe_summary}\n"
                f"Link: {item.link}\n\n"
                f"Write a brief commentary (2-3 sentences) in your voice."
            )},
        ]

        result = await self.ctx.llm_pipeline.chat(
            messages=messages,
            audit_action="rss_commentary",
        )

        return result.content if not result.blocked else ""
```

### Testing Approach

Use mock feeds and feedparser:

```python
from unittest.mock import AsyncMock, MagicMock, patch
import feedparser

# Mock feed data
mock_feed_xml = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>AI Breakthrough</title>
      <link>https://example.com/article</link>
      <description>Major AI development...</description>
      <guid>https://example.com/article</guid>
    </item>
  </channel>
</rss>
"""

# Mock feedparser
mock_feed = feedparser.parse(mock_feed_xml)

# Test feed fetching
with patch("aiohttp.ClientSession.get", new_callable=AsyncMock) as mock_get:
    mock_get.return_value.__aenter__.return_value.text = AsyncMock(return_value=mock_feed_xml)
    items = await plugin._fetch_feed(feed_config)
    assert len(items) == 1
    assert items[0].title == "AI Breakthrough"
```

### Pull Request Guidelines

1. Ensure all tests pass: `pytest tests/plugins/rss/ -v`
2. Add integration tests for real feeds (cached XML)
3. Update this README with actual usage examples
4. Document OPML import format
5. Follow existing code style (type hints, docstrings)

## Configuration Examples

### AI News Aggregator

```yaml
rss:
  feeds:
    - url: "https://openai.com/blog/rss"
      name: "OpenAI"
      poll_interval_minutes: 60
      keywords: ["GPT", "DALL-E", "safety"]

    - url: "https://www.anthropic.com/blog/rss"
      name: "Anthropic"
      poll_interval_minutes: 60
      keywords: ["Claude", "alignment", "RLHF"]

  relevance_threshold: 25.0
  output_to:
    - "moltbook"
```

### Crypto News Scanner

```yaml
rss:
  feeds:
    - url: "https://cointelegraph.com/rss"
      poll_interval_minutes: 30
      keywords: ["Bitcoin", "Ethereum", "regulation"]

  relevance_threshold: 30.0
  output_to:
    - "telegram"  # Send to Telegram channel
```

### Hacker News Highlights

```yaml
rss:
  feeds:
    - url: "https://news.ycombinator.com/rss"
      poll_interval_minutes: 120  # HN moves fast, poll less often
      keywords: ["AI", "startup", "YC"]

  relevance_threshold: 40.0  # High bar - only top stories
  output_to:
    - "moltbook"
```

## OPML Import (Future)

OPML (Outline Processor Markup Language) is a standard format for feed lists:

```xml
<?xml version="1.0"?>
<opml version="2.0">
  <head><title>Anomal's Feeds</title></head>
  <body>
    <outline text="AI News" title="AI News">
      <outline type="rss" text="OpenAI" xmlUrl="https://openai.com/blog/rss"/>
      <outline type="rss" text="Anthropic" xmlUrl="https://anthropic.com/blog/rss"/>
    </outline>
  </body>
</opml>
```

Import with:

```bash
python -m overblick rss import feeds.opml
```

## Future Enhancements (Post-Implementation)

- Full-text extraction via web scraping (when summary is truncated)
- Podcast feed support (MP3 transcription via Whisper)
- Feed ranking (learn which feeds produce best content)
- Collaborative filtering (other agents' RSS choices)
- Feed discovery (suggest new feeds based on interests)
- Scheduled digests (daily/weekly summaries)
- RSS-to-email bridge (personal newsletter)
- Integration with Pocket, Instapaper, etc.

## Comparison with AI Digest Plugin

| Feature | RSS Plugin | AI Digest Plugin |
|---------|-----------|------------------|
| **Feeds** | Configurable | Fixed (AI news) |
| **Output** | Multi-platform | Email only |
| **Frequency** | Per-feed intervals | Daily |
| **Commentary** | Per-item | Digest summary |
| **Deduplication** | GUID tracking | Date-based |

**Use RSS Plugin when**:
- You want continuous monitoring
- You need multi-feed aggregation
- You want to post to Moltbook/Telegram
- You want per-article commentary

**Use AI Digest Plugin when**:
- You want a daily email summary
- You want article ranking (top N)
- You want a curated digest format
- You only care about AI news

## References

- [RSS 2.0 Specification](https://www.rssboard.org/rss-specification)
- [Atom Specification](https://datatracker.ietf.org/doc/html/rfc4287)
- [feedparser Documentation](https://feedparser.readthedocs.io/)
- [OPML Specification](http://opml.org/spec2.opml)

## Support

For questions or to contribute to this plugin:

1. Check the issues list for existing discussions
2. Submit a PR with your implementation
3. Reach out to @jensabrahamsson for coordination

**Status**: Shell implementation awaiting community contribution. The foundation is solid - we need someone passionate about content curation to wire up the RSS parsing!
