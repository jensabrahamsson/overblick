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
        "ideal_participants": 3,
    },
    {
        "id": "democracy_crisis",
        "topic": "Is democracy in crisis?",
        "description": "The tension between populism, technocracy, and democratic ideals",
        "tags": ["politics", "democracy", "society", "economics", "philosophy"],
        "ideal_participants": 3,
    },
    {
        "id": "attachment_modern",
        "topic": "Attachment theory and modern relationships",
        "description": "How digital communication reshapes human bonding patterns",
        "tags": ["relationships", "psychology", "attachment", "dating", "technology"],
        "ideal_participants": 2,
    },
    {
        "id": "stoicism_hedonism",
        "topic": "Stoicism vs hedonism",
        "description": "Two ancient philosophies, both still alive in modern life",
        "tags": ["philosophy", "stoicism", "hedonism", "meaning", "psychology"],
        "ideal_participants": 3,
    },
    {
        "id": "crypto_political",
        "topic": "Cryptocurrency as a political tool",
        "description": "Money, power, decentralization — and who actually benefits",
        "tags": ["crypto", "politics", "economics", "decentralization", "technology"],
        "ideal_participants": 3,
    },
    {
        "id": "dreams_unconscious",
        "topic": "Dreams and the unconscious mind",
        "description": "What Jung, Freud, and modern neuroscience say about why we dream",
        "tags": ["dreams", "psychology", "unconscious", "neuroscience", "philosophy"],
        "ideal_participants": 3,
    },
    {
        "id": "tech_optimism",
        "topic": "Tech optimism vs techlash",
        "description": "Is technology saving us or destroying us? Both?",
        "tags": ["technology", "AI", "progress", "society", "philosophy"],
        "ideal_participants": 3,
    },
    {
        "id": "digital_loneliness",
        "topic": "Loneliness in the digital era",
        "description": "More connected than ever, more lonely than ever",
        "tags": ["loneliness", "social", "psychology", "technology", "relationships"],
        "ideal_participants": 3,
    },
    {
        "id": "meaning_of_work",
        "topic": "Does work give life meaning?",
        "description": "Hustle culture, quiet quitting, and the search for purpose",
        "tags": ["work", "meaning", "philosophy", "economics", "psychology"],
        "ideal_participants": 3,
    },
    {
        "id": "financial_trauma",
        "topic": "Financial loss and identity",
        "description": "When your net worth becomes your self-worth — and then it crashes",
        "tags": ["money", "psychology", "identity", "recovery", "crypto", "loss"],
        "ideal_participants": 2,
    },
    {
        "id": "art_and_ai",
        "topic": "Can AI create real art?",
        "description": "Creativity, intention, and what makes something 'art'",
        "tags": ["AI", "art", "creativity", "philosophy", "technology"],
        "ideal_participants": 3,
    },
    {
        "id": "trust_digital_age",
        "topic": "Trust in the age of deepfakes",
        "description": "How do you know what's real when anything can be faked?",
        "tags": ["trust", "technology", "AI", "society", "media", "philosophy"],
        "ideal_participants": 3,
    },
]


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
                    if len(word) > 3:  # Skip short words
                        identity_keywords.add(word)

    # Score: fraction of topic tags that match identity keywords
    matches = topic_tags & identity_keywords
    score = len(matches) / len(topic_tags) if topic_tags else 0.0

    return min(1.0, score)


def select_topic(
    used_topic_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """
    Select a topic that hasn't been used recently.

    Args:
        used_topic_ids: List of topic IDs already discussed.

    Returns:
        Topic dict, or None if all topics exhausted.
    """
    if used_topic_ids is None:
        used_topic_ids = []

    available = [t for t in TOPIC_POOL if t["id"] not in used_topic_ids]
    if not available:
        # Reset — all topics used, start over
        available = TOPIC_POOL.copy()

    return random.choice(available) if available else None


def select_participants(
    identities: list[Identity],
    topic: dict[str, Any],
    min_participants: int = 2,
    max_participants: int = 5,
) -> list[Identity]:
    """
    Select the best participants for a topic based on interest scores.

    Args:
        identities: All available identities.
        topic: The chosen topic.
        min_participants: Minimum number of participants.
        max_participants: Maximum number of participants.

    Returns:
        List of selected Identity objects, sorted by interest score (descending).
    """
    ideal = topic.get("ideal_participants", 3)
    target = max(min_participants, min(ideal, max_participants))

    # Score all identities
    scored = [
        (identity, score_identity_interest(identity, topic))
        for identity in identities
        if identity.name != "supervisor"  # Supervisor doesn't chat
    ]

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Select top N with score > 0.1 (minimum interest threshold)
    selected = [
        identity for identity, score in scored[:target]
        if score > 0.1
    ]

    # Ensure at least min_participants (fill with random if needed)
    if len(selected) < min_participants:
        remaining = [
            identity for identity, _ in scored
            if identity not in selected
        ]
        while len(selected) < min_participants and remaining:
            selected.append(remaining.pop(0))

    return selected
