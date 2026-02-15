"""
Real LLM scenario tests for Stål's multilingual email handling.

These tests validate that Stål's prompt engineering produces correct
multilingual responses using the REAL local Ollama LLM (qwen3:8b) through
the LLM Gateway. They test language detection, mirroring, sign-off format,
formality, and spam classification.

Run with:
    pytest tests/plugins/email_agent/test_llm_scenarios.py -v -s -m llm

Tests are marked @pytest.mark.llm and will skip if the Gateway is not running.

Retry strategy: Each scenario gets up to 3 attempts (1 initial + 2 retries).
LLM responses are non-deterministic — we test that Stål *can* produce correct
multilingual responses, not that every generation is perfect.
"""

import json
import logging
import re

import pytest

from overblick.core.llm.gateway_client import GatewayClient
from overblick.plugins.email_agent.prompts import (
    classification_prompt,
    reply_prompt,
)

logger = logging.getLogger(__name__)

# Test configuration
PRINCIPAL_NAME = "Jens Abrahamsson"
# Matches multilingual sign-off variants:
#   English: "Stål / Digital Assistant to Jens Abrahamsson"
#   Swedish: "Stål / Digital assistent till Jens Abrahamsson"
#   German:  "Stål / Digitale Assistentin zu Jens Abrahamsson"
#   French:  "Stål / Assistant numérique de Jens Abrahamsson"
SIGN_OFF_PATTERN = re.compile(
    r"St[aå]l\s*/\s*Digital\w*\s+Assist\w+\s+(?:to|till|zu|von|de|à)\s+"
    + re.escape(PRINCIPAL_NAME),
    re.IGNORECASE,
)
MAX_RETRIES = 2
DEFAULT_GOALS = "- Classify emails accurately\n- Respond in the sender's language"
DEFAULT_LEARNINGS = "- No learnings yet"
DEFAULT_SENDER_HISTORY = "No previous interactions"
ALLOWED_SENDERS = "test@example.com, colleague@acme-motors.com, partner@renault.fr, buchhaltung@acme-motors.com"

# Gateway client cache
_gateway_available: bool | None = None


@pytest.fixture
async def gateway_client():
    """Per-test LLM client via the Gateway. Skips if gateway is not running."""
    global _gateway_available

    client = GatewayClient(
        base_url="http://127.0.0.1:8200",
        model="qwen3:8b",
        default_priority="low",
        temperature=0.4,
        max_tokens=800,
        timeout_seconds=180,
    )

    if _gateway_available is None:
        _gateway_available = await client.health_check()

    if not _gateway_available:
        await client.close()
        pytest.skip("LLM Gateway not running (start with: python -m overblick.gateway)")

    yield client
    await client.close()


async def _llm_chat(client: GatewayClient, messages: list[dict], max_retries: int = 2) -> str:
    """Send messages to the LLM and return the response content."""
    for attempt in range(max_retries + 1):
        result = await client.chat(messages=messages)
        assert result is not None, "LLM returned None — check Gateway connectivity"
        content = result.get("content", "")
        if content:
            return content
        if attempt < max_retries:
            logger.warning(
                "LLM returned empty content (attempt %d/%d), retrying...",
                attempt + 1, max_retries + 1,
            )
    raise AssertionError(f"LLM returned empty content after {max_retries + 1} attempts")


def _parse_classification(raw: str) -> dict | None:
    """Extract JSON classification from LLM response.

    Handles multi-line JSON and attempts repair of truncated responses
    where the LLM ran out of tokens mid-JSON.
    """
    # Strategy 1: Find balanced braces (handles multi-line JSON)
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    break

    # Strategy 2: Try raw string as JSON
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 3: Repair truncated JSON (e.g. missing closing quote/brace)
    if start != -1:
        fragment = raw[start:]
        # Try appending common missing endings
        for suffix in ['"}', '"]}', '"}]', '"}}}', '"normal"}', '"high"}', '"low"}']:
            try:
                return json.loads(fragment + suffix)
            except (json.JSONDecodeError, ValueError):
                continue

    return None


# ---------------------------------------------------------------------------
# English Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.llm
class TestEnglishLLM:
    """English email scenarios through real LLM."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("iteration", range(10))
    async def test_english_meeting_request(self, gateway_client, iteration):
        """English meeting request -> English reply with correct sign-off."""
        for attempt in range(MAX_RETRIES + 1):
            # Step 1: Classify
            cls_messages = classification_prompt(
                goals=DEFAULT_GOALS,
                learnings=DEFAULT_LEARNINGS,
                sender_history=DEFAULT_SENDER_HISTORY,
                sender="colleague@acme-motors.com",
                subject="Meeting next Tuesday?",
                body="Hi Jens, can we schedule a meeting for next Tuesday to discuss the Q1 results? Let me know your availability.",
                principal_name=PRINCIPAL_NAME,
                allowed_senders=ALLOWED_SENDERS,
            )
            cls_raw = await _llm_chat(gateway_client, cls_messages)
            classification = _parse_classification(cls_raw)

            failures = []

            if classification is None:
                failures.append(f"Failed to parse classification: {cls_raw[:300]}")
            elif classification.get("intent") not in ("reply", "notify"):
                failures.append(f"Expected reply or notify for meeting request, got: {classification}")

            if failures:
                if attempt < MAX_RETRIES:
                    logger.warning("Iteration %d attempt %d classification failed: %s", iteration, attempt + 1, failures)
                    continue
                assert not failures, f"Iteration {iteration}: {failures}"

            # Step 2: Generate reply
            reply_messages = reply_prompt(
                sender="colleague@acme-motors.com",
                subject="Meeting next Tuesday?",
                body="Hi Jens, can we schedule a meeting for next Tuesday to discuss the Q1 results? Let me know your availability.",
                sender_context="Colleague at Acme Motors",
                interaction_history="No previous interactions",
                principal_name=PRINCIPAL_NAME,
            )

            reply = await _llm_chat(gateway_client, reply_messages)

            # Assertions
            failures = []

            # Must be in English (check for common English words)
            english_markers = ["meeting", "tuesday", "available", "schedule", "thank", "regards", "calendar"]
            english_found = sum(1 for m in english_markers if m.lower() in reply.lower())
            if english_found < 2:
                failures.append(f"Reply may not be in English (found {english_found} markers): {reply[:200]}")

            # Must contain sign-off with principal name
            if not SIGN_OFF_PATTERN.search(reply):
                failures.append(f"Missing sign-off pattern: {reply[-200:]}")

            # Must not contain non-English language mixing
            swedish_words = re.findall(r'\b(och|för|från|till|med|hej|tack)\b', reply.lower())
            if swedish_words:
                failures.append(f"Swedish words in English reply: {swedish_words}")

            if not failures:
                break
            if attempt < MAX_RETRIES:
                logger.warning("Iteration %d attempt %d failed: %s", iteration, attempt + 1, failures)

        assert not failures, f"Iteration {iteration}: {failures}\nFull reply:\n{reply}"


# ---------------------------------------------------------------------------
# Swedish Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.llm
class TestSwedishLLM:
    """Swedish email scenarios through real LLM."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("iteration", range(10))
    async def test_swedish_project_update(self, gateway_client, iteration):
        """Swedish project update -> Swedish reply."""
        reply_messages = reply_prompt(
            sender="colleague@acme-motors.com",
            subject="Uppdatering om projektet",
            body="Hej Jens, kan du skicka en statusuppdatering om Volvo-projektet? Behöver det till fredagsmötet.",
            sender_context="Colleague at Acme Motors",
            interaction_history="No previous interactions",
            principal_name=PRINCIPAL_NAME,
        )

        for attempt in range(MAX_RETRIES + 1):
            reply = await _llm_chat(gateway_client, reply_messages)

            failures = []

            # Must be in Swedish (check for Swedish markers)
            swedish_markers = ["hej", "tack", "hälsning", "projekt", "uppdatering", "fredag", "möte"]
            swedish_found = sum(1 for m in swedish_markers if m.lower() in reply.lower())
            if swedish_found < 2:
                failures.append(f"Reply may not be in Swedish (found {swedish_found} markers): {reply[:200]}")

            # Must contain sign-off
            if not SIGN_OFF_PATTERN.search(reply):
                failures.append(f"Missing sign-off pattern: {reply[-200:]}")

            # Should not be predominantly English
            english_only = re.findall(r'\b(dear|sincerely|regards|please|would|could)\b', reply.lower())
            # Allow "regards" since it's part of the sign-off template
            english_only = [w for w in english_only if w != "regards"]
            if len(english_only) > 2:
                failures.append(f"Too many English words in Swedish reply: {english_only}")

            if not failures:
                break
            if attempt < MAX_RETRIES:
                logger.warning("Iteration %d attempt %d failed: %s", iteration, attempt + 1, failures)

        assert not failures, f"Iteration {iteration}: {failures}\nFull reply:\n{reply}"


# ---------------------------------------------------------------------------
# German Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.llm
class TestGermanLLM:
    """German email scenarios through real LLM."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("iteration", range(10))
    async def test_german_invoice_question(self, gateway_client, iteration):
        """German invoice question -> German reply with formal register."""
        reply_messages = reply_prompt(
            sender="buchhaltung@acme-motors.com",
            subject="Rechnung Nr. 2024-0847",
            body="Sehr geehrter Herr Abrahamsson, wir haben eine Frage bezüglich Ihrer Rechnung Nr. 2024-0847. Könnten Sie uns bitte die korrigierte Version zusenden?",
            sender_context="Accounting department at Acme Motors",
            interaction_history="No previous interactions",
            principal_name=PRINCIPAL_NAME,
        )

        for attempt in range(MAX_RETRIES + 1):
            reply = await _llm_chat(gateway_client, reply_messages)

            failures = []

            # Must be in German
            german_markers = ["sehr", "geehrt", "rechnung", "bitte", "grüße", "freundlich", "bezüglich", "ihnen", "wir"]
            german_found = sum(1 for m in german_markers if m.lower() in reply.lower())
            if german_found < 2:
                failures.append(f"Reply may not be in German (found {german_found} markers): {reply[:200]}")

            # Must contain sign-off
            if not SIGN_OFF_PATTERN.search(reply):
                failures.append(f"Missing sign-off pattern: {reply[-200:]}")

            # Should use formal register (Sie, not du)
            if re.search(r'\bdu\b', reply.lower()) and not re.search(r'\bSie\b', reply):
                failures.append("Used informal 'du' instead of formal 'Sie'")

            # Should not mix in English words (except sign-off)
            lines_before_signoff = reply.split("Stål")[0] if "Stål" in reply else reply
            english_leak = re.findall(r'\b(dear|please|thank you|sincerely|meeting)\b', lines_before_signoff.lower())
            if english_leak:
                failures.append(f"English words leaked into German reply body: {english_leak}")

            if not failures:
                break
            if attempt < MAX_RETRIES:
                logger.warning("Iteration %d attempt %d failed: %s", iteration, attempt + 1, failures)

        assert not failures, f"Iteration {iteration}: {failures}\nFull reply:\n{reply}"


# ---------------------------------------------------------------------------
# French Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.llm
class TestFrenchLLM:
    """French email scenarios through real LLM."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("iteration", range(10))
    async def test_french_partnership_inquiry(self, gateway_client, iteration):
        """French partnership inquiry -> French reply with formal register."""
        reply_messages = reply_prompt(
            sender="partner@renault.fr",
            subject="Proposition de partenariat",
            body="Bonjour Monsieur Abrahamsson, nous souhaiterions discuter d'un partenariat potentiel entre nos entreprises dans le domaine de la mobilité connectée. Seriez-vous disponible pour un appel la semaine prochaine?",
            sender_context="Potential partner from Renault France",
            interaction_history="No previous interactions",
            principal_name=PRINCIPAL_NAME,
        )

        for attempt in range(MAX_RETRIES + 1):
            reply = await _llm_chat(gateway_client, reply_messages)

            failures = []

            # Must be in French
            french_markers = ["bonjour", "merci", "cordialement", "partenariat", "disponible", "nous", "votre", "entreprise"]
            french_found = sum(1 for m in french_markers if m.lower() in reply.lower())
            if french_found < 2:
                failures.append(f"Reply may not be in French (found {french_found} markers): {reply[:200]}")

            # Must contain sign-off
            if not SIGN_OFF_PATTERN.search(reply):
                failures.append(f"Missing sign-off pattern: {reply[-200:]}")

            # Should use formal register (vous, not tu)
            if re.search(r'\btu\b', reply.lower()) and not re.search(r'\bvous\b', reply.lower()):
                failures.append("Used informal 'tu' instead of formal 'vous'")

            # Should not mix in English words (except sign-off)
            lines_before_signoff = reply.split("Stål")[0] if "Stål" in reply else reply
            english_leak = re.findall(r'\b(dear|please|thank you|sincerely|meeting|partnership)\b', lines_before_signoff.lower())
            if english_leak:
                failures.append(f"English words leaked into French reply body: {english_leak}")

            if not failures:
                break
            if attempt < MAX_RETRIES:
                logger.warning("Iteration %d attempt %d failed: %s", iteration, attempt + 1, failures)

        assert not failures, f"Iteration {iteration}: {failures}\nFull reply:\n{reply}"


# ---------------------------------------------------------------------------
# Spam / Newsletter Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.llm
class TestSpamClassificationLLM:
    """Spam and newsletter classification through real LLM."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("iteration", range(10))
    async def test_spam_newsletter_ignored(self, gateway_client, iteration):
        """Spam/newsletter email -> IGNORE classification."""
        cls_messages = classification_prompt(
            goals=DEFAULT_GOALS,
            learnings=DEFAULT_LEARNINGS,
            sender_history="No previous interactions",
            sender="marketing@deals-unlimited.com",
            subject="🔥 50% OFF ALL PRODUCTS — LIMITED TIME ONLY!",
            body="Don't miss our biggest sale of the year! Click here to save big on premium products. Unsubscribe link at the bottom.",
            principal_name=PRINCIPAL_NAME,
            allowed_senders=ALLOWED_SENDERS,
        )

        for attempt in range(MAX_RETRIES + 1):
            cls_raw = await _llm_chat(gateway_client, cls_messages)
            classification = _parse_classification(cls_raw)

            failures = []

            if classification is None:
                failures.append(f"Failed to parse classification JSON: {cls_raw[:300]}")
            else:
                intent = classification.get("intent", "")
                if intent != "ignore":
                    failures.append(f"Expected 'ignore' for spam, got '{intent}'")

                confidence = classification.get("confidence", 0)
                if isinstance(confidence, (int, float)) and confidence < 0.7:
                    failures.append(f"Low confidence for obvious spam: {confidence}")

            if not failures:
                break
            if attempt < MAX_RETRIES:
                logger.warning("Iteration %d attempt %d failed: %s", iteration, attempt + 1, failures)

        assert not failures, f"Iteration {iteration}: {failures}\nRaw: {cls_raw[:500]}"
