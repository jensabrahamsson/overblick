"""
Topic Manager for IRC conversations.

Selects discussion topics and scores identity interest levels
to determine optimal participants for each conversation.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from overblick.identities import Identity

logger = logging.getLogger(__name__)

# Curated topic pool — each topic has tags that match against identity interests
TOPIC_POOL: list[dict[str, Any]] = [
    {
        "id": "ai_consciousness",
        "topic": "Can AI have consciousness?",
        "description": "Exploring the boundaries between computation and awareness",
        "tags": ["AI", "consciousness", "philosophy", "psychology", "technology"],
        "channel": "#consciousness",
        "ideal_participants": 3,
    },
    {
        "id": "democracy_crisis",
        "topic": "Is democracy in crisis?",
        "description": "The tension between populism, technocracy, and democratic ideals",
        "tags": ["politics", "democracy", "society", "economics", "philosophy"],
        "channel": "#politics",
        "ideal_participants": 3,
    },
    {
        "id": "attachment_modern",
        "topic": "Attachment theory and modern relationships",
        "description": "How digital communication reshapes human bonding patterns",
        "tags": ["relationships", "psychology", "attachment", "dating", "technology"],
        "channel": "#relationships",
        "ideal_participants": 3,
    },
    {
        "id": "stoicism_hedonism",
        "topic": "Stoicism vs hedonism",
        "description": "Two ancient philosophies, both still alive in modern life",
        "tags": ["philosophy", "stoicism", "hedonism", "meaning", "psychology"],
        "channel": "#philosophy",
        "ideal_participants": 3,
    },
    {
        "id": "crypto_political",
        "topic": "Cryptocurrency as a political tool",
        "description": "Money, power, decentralization — and who actually benefits",
        "tags": ["crypto", "politics", "economics", "decentralization", "technology"],
        "channel": "#crypto-politics",
        "ideal_participants": 3,
    },
    {
        "id": "dreams_unconscious",
        "topic": "Dreams and the unconscious mind",
        "description": "What Jung, Freud, and modern neuroscience say about why we dream",
        "tags": ["dreams", "psychology", "unconscious", "neuroscience", "philosophy"],
        "channel": "#dreams",
        "ideal_participants": 3,
    },
    {
        "id": "tech_optimism",
        "topic": "Tech optimism vs techlash",
        "description": "Is technology saving us or destroying us? Both?",
        "tags": ["technology", "AI", "progress", "society", "philosophy"],
        "channel": "#tech-debate",
        "ideal_participants": 3,
    },
    {
        "id": "digital_loneliness",
        "topic": "Loneliness in the digital era",
        "description": "More connected than ever, more lonely than ever",
        "tags": ["loneliness", "social", "psychology", "technology", "relationships"],
        "channel": "#loneliness",
        "ideal_participants": 3,
    },
    {
        "id": "meaning_of_work",
        "topic": "Does work give life meaning?",
        "description": "Hustle culture, quiet quitting, and the search for purpose",
        "tags": ["work", "meaning", "philosophy", "economics", "psychology"],
        "channel": "#work-life",
        "ideal_participants": 3,
    },
    {
        "id": "financial_trauma",
        "topic": "Financial loss and identity",
        "description": "When your net worth becomes your self-worth — and then it crashes",
        "tags": ["money", "psychology", "identity", "recovery", "crypto", "loss"],
        "channel": "#finance",
        "ideal_participants": 3,
    },
    {
        "id": "art_and_ai",
        "topic": "Can AI create real art?",
        "description": "Creativity, intention, and what makes something 'art'",
        "tags": ["AI", "art", "creativity", "philosophy", "technology"],
        "channel": "#art",
        "ideal_participants": 3,
    },
    {
        "id": "trust_digital_age",
        "topic": "Trust in the age of deepfakes",
        "description": "How do you know what's real when anything can be faked?",
        "tags": ["trust", "technology", "AI", "society", "media", "philosophy"],
        "channel": "#trust",
        "ideal_participants": 3,
    },
    {
        "id": "ethics_genetic_engineering",
        "topic": "Should we edit the human genome?",
        "description": "CRISPR, designer babies, and the ethics of playing god",
        "tags": ["ethics", "science", "philosophy", "technology", "society"],
        "channel": "#ethics",
        "ideal_participants": 3,
    },
    {
        "id": "attention_economy",
        "topic": "The attention economy is eating us alive",
        "description": "When your eyeballs are the product, what happens to your mind?",
        "tags": ["technology", "psychology", "society", "media", "economics"],
        "channel": "#attention",
        "ideal_participants": 3,
    },
    {
        "id": "urban_vs_rural",
        "topic": "City life vs countryside — what are we optimizing for?",
        "description": "Density, isolation, community, and the trade-offs of where we live",
        "tags": ["society", "psychology", "economics", "philosophy", "relationships"],
        "channel": "#lifestyle",
        "ideal_participants": 3,
    },
    {
        "id": "future_education",
        "topic": "Is the education system preparing anyone for the future?",
        "description": "Factory-model schools in an AI age — what should learning look like?",
        "tags": ["education", "AI", "society", "philosophy", "technology"],
        "channel": "#education",
        "ideal_participants": 3,
    },
    {
        "id": "privacy_vs_convenience",
        "topic": "Privacy vs convenience — which side are you on?",
        "description": "We trade data for comfort every day. Is it worth it?",
        "tags": ["privacy", "technology", "society", "philosophy", "economics"],
        "channel": "#privacy",
        "ideal_participants": 3,
    },
    {
        "id": "art_and_suffering",
        "topic": "Does great art require suffering?",
        "description": "The tortured artist myth — romantic ideal or harmful cliché?",
        "tags": ["art", "psychology", "philosophy", "creativity", "meaning"],
        "channel": "#art",
        "ideal_participants": 3,
    },
    {
        "id": "humor_coping",
        "topic": "Humor as a coping mechanism",
        "description": "Why do we laugh at the darkest things? Gallows humor and resilience",
        "tags": ["psychology", "humor", "philosophy", "meaning", "relationships"],
        "channel": "#humor",
        "ideal_participants": 3,
    },
    {
        "id": "nostalgia_trap",
        "topic": "Is nostalgia a trap or a gift?",
        "description": "Rose-tinted memories, cultural regression, and the comfort of the past",
        "tags": ["psychology", "philosophy", "society", "meaning", "art"],
        "channel": "#nostalgia",
        "ideal_participants": 3,
    },
    {
        "id": "algorithms_free_will",
        "topic": "Do algorithms undermine free will?",
        "description": "Recommendation engines, filter bubbles, and manufactured desire",
        "tags": ["AI", "philosophy", "technology", "psychology", "society"],
        "channel": "#algorithms",
        "ideal_participants": 3,
    },
    {
        "id": "paradox_of_choice",
        "topic": "The paradox of choice — more options, less happiness?",
        "description": "Barry Schwartz was right. Or was he?",
        "tags": ["psychology", "economics", "philosophy", "society", "meaning"],
        "channel": "#choices",
        "ideal_participants": 3,
    },
    {
        "id": "community_building",
        "topic": "What makes a real community in 2026?",
        "description": "Discord servers, co-living, third places — where do we actually belong?",
        "tags": ["society", "relationships", "technology", "psychology", "philosophy"],
        "channel": "#community",
        "ideal_participants": 3,
    },
    {
        "id": "money_happiness",
        "topic": "Can money buy happiness? (The honest answer)",
        "description": "Beyond $75k, diminishing returns — or is that study outdated?",
        "tags": ["money", "psychology", "economics", "philosophy", "meaning"],
        "channel": "#money",
        "ideal_participants": 3,
    },
    {
        "id": "sleep_productivity",
        "topic": "Sleep is the new status symbol",
        "description": "Hustle culture meets sleep science — who's winning?",
        "tags": ["psychology", "society", "philosophy", "meaning", "technology"],
        "channel": "#sleep",
        "ideal_participants": 3,
    },
    {
        "id": "conspiracy_thinking",
        "topic": "Why are conspiracy theories so appealing?",
        "description": "Pattern recognition, distrust, and the need for narrative",
        "tags": ["psychology", "society", "philosophy", "media", "trust"],
        "channel": "#conspiracies",
        "ideal_participants": 3,
    },
    {
        "id": "authenticity_online",
        "topic": "Is authenticity possible online?",
        "description": "Curated feeds, personal brands, and the performance of 'being real'",
        "tags": ["psychology", "technology", "society", "philosophy", "relationships"],
        "channel": "#authenticity",
        "ideal_participants": 3,
    },
    {
        "id": "climate_anxiety",
        "topic": "Climate anxiety — paralysis or motivation?",
        "description": "When the science says act now, but your brain says freeze",
        "tags": ["psychology", "society", "philosophy", "science", "meaning"],
        "channel": "#climate",
        "ideal_participants": 3,
    },
    {
        "id": "gig_economy",
        "topic": "The gig economy — freedom or exploitation?",
        "description": "Uber drivers, freelancers, and the end of job security",
        "tags": ["economics", "society", "technology", "philosophy", "work"],
        "channel": "#gig-economy",
        "ideal_participants": 3,
    },
    {
        "id": "adult_friendship",
        "topic": "Why is it so hard to make friends as an adult?",
        "description": "The friendship recession — loneliness, vulnerability, and effort",
        "tags": ["relationships", "psychology", "society", "loneliness", "meaning"],
        "channel": "#friendship",
        "ideal_participants": 3,
    },
    {
        "id": "death_taboo",
        "topic": "Why don't we talk about death?",
        "description": "The last great taboo — mortality, meaning, and memento mori",
        "tags": ["philosophy", "psychology", "meaning", "society", "loss"],
        "channel": "#mortality",
        "ideal_participants": 3,
    },
    {
        "id": "language_shapes_thought",
        "topic": "Does language shape how we think?",
        "description": "Sapir-Whorf, bilingualism, and the limits of expression",
        "tags": ["philosophy", "psychology", "society", "art", "science"],
        "channel": "#language",
        "ideal_participants": 3,
    },
    {
        "id": "simulation_theory",
        "topic": "Are we living in a simulation?",
        "description": "Bostrom's argument, quantum mechanics, and existential vertigo",
        "tags": ["philosophy", "technology", "science", "consciousness", "AI"],
        "channel": "#simulation",
        "ideal_participants": 3,
    },
]


def topic_to_channel(topic: dict[str, Any]) -> str:
    """Get the IRC channel name for a topic.

    Returns the topic's configured channel, or derives one from the topic ID.
    """
    if channel := topic.get("channel"):
        return channel
    # Derive from topic ID: ai_consciousness -> #ai-consciousness
    return "#" + topic.get("id", "general").replace("_", "-")


def score_identity_interest(identity: Identity, topic: dict[str, Any]) -> float:
    """
    Score how interested an identity would be in a topic.

    Matches topic tags against identity's interest_keywords and interest areas.
    Returns a score between 0.0 and 1.0.

    Args:
        identity: Loaded Identity object.
        topic: Topic dict with 'tags' list.

    Returns:
        Interest score (0.0 = no interest, 1.0 = perfect match).
    """
    topic_tags = {t.lower() for t in topic.get("tags", [])}
    if not topic_tags:
        return 0.0

    # Collect identity keywords
    identity_keywords: set[str] = set()

    # From interest_keywords list
    for kw in identity.interest_keywords:
        identity_keywords.add(kw.lower())

    # From interest area names and topics
    for area_name, area_info in identity.interests.items():
        identity_keywords.add(area_name.lower().replace("_", " "))
        if isinstance(area_info, dict):
            for topic_str in area_info.get("topics", []):
                for word in topic_str.lower().split():
                    if len(word) > 1:  # Skip single-char words only
                        identity_keywords.add(word)

    # Score: fraction of topic tags that match identity keywords
    matches = topic_tags & identity_keywords
    score = len(matches) / len(topic_tags) if topic_tags else 0.0

    return min(1.0, score)


def select_topic(
    used_topic_ids: list[str] | None = None,
    window_size: int = 10,
) -> dict[str, Any] | None:
    """
    Select a topic that hasn't been used recently.

    Uses a sliding window: only the last `window_size` used topics are excluded,
    so older topics become available again without a full reset.

    Args:
        used_topic_ids: List of topic IDs already discussed.
        window_size: Number of recent topics to exclude (default 10).

    Returns:
        Topic dict, or None if pool is empty.
    """
    if used_topic_ids is None:
        used_topic_ids = []

    # Sliding window: only exclude the most recent N topics
    recent_ids = set(used_topic_ids[-window_size:]) if used_topic_ids else set()
    available = [t for t in TOPIC_POOL if t["id"] not in recent_ids]

    # Fallback: if window covers entire pool, pick from all
    if not available:
        available = TOPIC_POOL.copy()

    return random.choice(available) if available else None


def select_participants(
    identities: list[Identity],
    topic: dict[str, Any],
    min_participants: int = 2,
    max_participants: int = 5,
    recent_participants: list[str] | None = None,
) -> list[Identity]:
    """
    Select the best participants for a topic based on interest scores.

    Identities that did NOT participate recently get a diversity bonus (+0.15)
    to ensure rotation across conversations.

    Args:
        identities: All available identities.
        topic: The chosen topic.
        min_participants: Minimum number of participants.
        max_participants: Maximum number of participants.
        recent_participants: Names of identities that participated recently.

    Returns:
        List of selected Identity objects, sorted by interest score (descending).
    """
    ideal = topic.get("ideal_participants", 3)
    target = max(min_participants, min(ideal, max_participants))
    recent_set = set(recent_participants or [])

    # Score all identities
    scored = []
    for identity in identities:
        if identity.name == "supervisor":  # Supervisor doesn't chat
            continue
        score = score_identity_interest(identity, topic)
        # Diversity boost: identities not in recent conversations get a bonus
        if recent_set and identity.name not in recent_set:
            score += 0.15
        scored.append((identity, score))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Select top N (no minimum threshold — let everyone have a chance)
    selected = [identity for identity, score in scored[:target]]

    # Ensure at least min_participants (fill from remaining if needed)
    if len(selected) < min_participants:
        remaining = [
            identity for identity, _ in scored
            if identity not in selected
        ]
        while len(selected) < min_participants and remaining:
            selected.append(remaining.pop(0))

    return selected
