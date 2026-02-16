#!/usr/bin/env python3
"""Test full AI Digest workflow: RSS fetch → LLM rank → LLM generate → Email send."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, '/Users/jens/kod/blick')

from overblick.core.event_bus import EventBus
from overblick.core.plugin_base import PluginContext
from overblick.core.security.secrets_manager import SecretsManager
from overblick.plugins.ai_digest.plugin import AiDigestPlugin
from overblick.core.llm.ollama_client import OllamaClient
from overblick.core.llm.pipeline import SafeLLMPipeline
from unittest.mock import MagicMock
import yaml

async def test_full_digest():
    """Run complete AI Digest workflow."""
    
    print("="*60)
    print("AI DIGEST FULL WORKFLOW TEST")
    print("="*60)
    print("\n⚠️  Warning: This may be slow if LLM is busy with prompt tuning")
    print("⚠️  Using reasoning=ON for deep thinking\n")
    
    # Load Anomal personality YAML directly to get raw_config
    personality_file = Path("/Users/jens/kod/blick/overblick/identities/anomal/personality.yaml")
    with open(personality_file) as f:
        personality_data = yaml.safe_load(f)
    
    # Create a minimal identity object with raw_config
    from overblick.identities import Personality, LLMSettings
    identity = Personality(
        name="anomal",
        display_name="Anomal",
        description="The intellectual humanist",
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=2000),
        raw_config=personality_data,  # Include full config with ai_digest settings
    )
    
    # Create event bus
    event_bus = EventBus()
    
    # Create secrets manager
    secrets_manager = SecretsManager(Path("config/secrets"))
    
    # Create real LLM client (Ollama) - use default base_url with /v1
    llm_client = OllamaClient()  # Defaults to http://localhost:11434/v1
    
    # Create LLM pipeline with all security checks
    pipeline = SafeLLMPipeline(
        llm_client=llm_client,
        preflight_checker=None,  # Skip for test
        output_safety=None,      # Skip for test
        audit_log=MagicMock(),
        rate_limiter=None,       # Skip for test
    )
    
    # Create plugin context
    ctx = PluginContext(
        identity_name="anomal",
        data_dir=Path("/tmp/overblick_test/data"),
        log_dir=Path("/tmp/overblick_test/logs"),
        llm_client=llm_client,
        llm_pipeline=pipeline,
        event_bus=event_bus,
        scheduler=MagicMock(),
        audit_log=MagicMock(),
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=identity,
        engagement_db=MagicMock(),
    )
    
    # Set secrets getter
    ctx._secrets_getter = lambda key: secrets_manager.get("anomal", key)
    
    # Create and register email capability
    print("\n[1/6] Setting up email capability...")
    from overblick.capabilities.communication.email import EmailCapability
    email_cap = EmailCapability(ctx)
    await email_cap.setup()
    ctx.capabilities["email"] = email_cap
    print("✅ Email capability ready")
    
    # Create AI Digest plugin
    print("\n[2/6] Setting up AI Digest plugin...")
    ai_digest = AiDigestPlugin(ctx)
    await ai_digest.setup()
    print(f"✅ AI Digest plugin ready (recipient: {ai_digest._recipient})")
    
    # Force digest generation (bypass time check)
    print("\n[3/6] Fetching RSS feeds...")
    articles = await ai_digest._fetch_all_feeds()
    print(f"✅ Fetched {len(articles)} articles from {len(ai_digest._feeds)} feeds")
    
    if len(articles) == 0:
        print("\n❌ No articles found. Check RSS feeds or network connection.")
        return
    
    # Show sample articles
    print("\nSample articles:")
    for i, article in enumerate(articles[:3], 1):
        print(f"  {i}. {article.title[:60]}... ({article.feed_name})")
    
    # Rank articles with LLM
    print(f"\n[4/6] Ranking {len(articles)} articles with LLM (this may take a while)...")
    print("⏳ LLM is thinking deeply with reasoning ON...")
    import time
    start = time.time()
    ranked = await ai_digest._rank_articles(articles)
    elapsed = time.time() - start
    print(f"✅ Ranked {len(ranked)} articles in {elapsed:.1f}s")
    
    if len(ranked) == 0:
        print("\n❌ Ranking failed or returned no results.")
        return
    
    print("\nTop ranked articles:")
    for i, article in enumerate(ranked[:5], 1):
        print(f"  {i}. {article.title[:60]}...")
    
    # Generate digest with LLM
    print(f"\n[5/6] Generating digest in Anomal's voice (this may take a while)...")
    print("⏳ LLM is writing with deep reasoning...")
    start = time.time()
    digest = await ai_digest._generate_digest(ranked)
    elapsed = time.time() - start
    
    if not digest:
        print("\n❌ Digest generation failed (blocked or error).")
        return
    
    print(f"✅ Digest generated in {elapsed:.1f}s!")
    print("\n" + "="*60)
    print("GENERATED DIGEST:")
    print("="*60)
    print(digest)
    print("="*60)
    
    # Send via email
    print("\n[6/6] Sending digest via email...")
    await ai_digest._send_digest(digest, len(ranked))
    
    # Give event bus time to process
    await asyncio.sleep(2)
    
    print("\n" + "="*60)
    print("✅ FULL WORKFLOW COMPLETE!")
    print("="*60)
    print("\nCheck your configured recipient for the AI digest email.")
    print("\nDigest contains:")
    print(f"  - {len(ranked)} ranked articles")
    print(f"  - Generated in Anomal's voice (with reasoning)")
    print(f"  - {len(digest)} characters")

if __name__ == "__main__":
    asyncio.run(test_full_digest())
