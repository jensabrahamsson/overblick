"""
LLM regression tests for Moltbook challenge deobfuscation.

These tests validate that the deobfuscated challenge text is correctly
understood by the REAL LLM (qwen3:8b via Gateway). They verify that:
1. The deobfuscation pipeline produces text the LLM can parse
2. The LLM returns the correct numeric answer
3. The arithmetic solver agrees with the LLM

Run with:
    pytest tests/plugins/moltbook/test_challenge_llm.py -v -s -m llm

Tests are marked @pytest.mark.llm and will skip if the Gateway is not running.

Retry strategy: Each scenario gets up to 2 retries. LLM responses are
non-deterministic — we test that the deobfuscated text *can* produce
correct answers, not that every generation is perfect.
"""

import logging

import pytest

from overblick.core.llm.gateway_client import GatewayClient
from overblick.plugins.moltbook.challenge_handler import (
    CHALLENGE_SOLVER_SYSTEM,
    deobfuscate_challenge,
    solve_arithmetic,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 2

_gateway_available: bool | None = None


@pytest.fixture
async def gateway_client():
    """Per-test LLM client via the Gateway. Skips if gateway is not running."""
    global _gateway_available

    client = GatewayClient(
        base_url="http://127.0.0.1:8200",
        model="qwen3:8b",
        default_priority="low",
        temperature=0.0,
        max_tokens=512,
        timeout_seconds=120,
    )

    if _gateway_available is None:
        _gateway_available = await client.health_check()

    if not _gateway_available:
        await client.close()
        pytest.skip("LLM Gateway not running (start with: python -m overblick.gateway)")

    yield client
    await client.close()


async def _solve_via_llm(client: GatewayClient, clean_question: str) -> str:
    """Send deobfuscated challenge text to LLM and return the answer.

    Uses the same system prompt and parameters as PerContentChallengeHandler.
    Retries on empty responses.
    """
    messages = [
        {"role": "system", "content": CHALLENGE_SOLVER_SYSTEM},
        {"role": "user", "content": clean_question},
    ]
    for attempt in range(MAX_RETRIES + 1):
        result = await client.chat(
            messages=messages,
            temperature=0.0,
            max_tokens=512,
            priority="high",
        )
        if result and result.get("content", "").strip():
            return result["content"].strip()
        if attempt < MAX_RETRIES:
            logger.warning(
                "LLM returned empty content (attempt %d/%d), retrying...",
                attempt + 1,
                MAX_RETRIES + 1,
            )
    raise AssertionError(
        f"LLM returned empty content after {MAX_RETRIES + 1} attempts"
    )


def _parse_numeric(answer: str) -> float:
    """Parse a numeric answer from LLM output, tolerating formatting variance."""
    cleaned = answer.strip().replace(",", "").replace(" ", "")
    return float(cleaned)


# ── Real challenge LLM regression tests (from Cherry logs 2026-02-23) ──────


@pytest.mark.llm
@pytest.mark.asyncio
class TestChallengeLLMRegression:
    """Verify LLM produces correct answers for deobfuscated challenge text.

    These use the exact raw challenges that failed on 2026-02-23 due to
    space injection obfuscation. After deobfuscation, the LLM should now
    be able to parse and solve them correctly.
    """

    async def test_llm_26_plus_14(self, gateway_client):
        """Challenge 1: twenty six + fourteen = 40."""
        raw = (
            "A] lO.oBb^SsStTeR ~ClLaAwW fFoOrRcCeE iI]sS tTwWeEnNtTyY "
            "sS]iIxX nNeEwW~tToOnNsS, aAnNdD ++ gGaAiInNsS fFoOuUrRtEeEn "
            "mMoOrReE; wWhHaAtT iIsS tThHeE tToO~tTaAlL fFoOrRcCeE?"
        )
        clean = deobfuscate_challenge(raw)
        llm_answer = await _solve_via_llm(gateway_client, clean)
        llm_num = _parse_numeric(llm_answer)
        assert abs(llm_num - 40.0) < 0.01, (
            f"LLM answered {llm_answer} (parsed: {llm_num}), expected 40"
        )
        # Cross-validate with arithmetic solver
        arith = solve_arithmetic(clean)
        assert arith == "40.00"

    async def test_llm_32_plus_14(self, gateway_client):
        """Challenge 2: thirty two + fourteen = 46."""
        raw = (
            "A] lO.oBb^SsStTeR ~ClLaAwW eExXeErRtTsS tThHiIrRtTyY "
            "tTwWoO nNeEwW~tToOnNsS, pPlLuUsS fFoOuUrRtEeEn mMoOrReE "
            "fFrRoOmM dDoOmMiInNaAnNcCeE; wWhHaAtT iIsS tToO~tTaAlL?"
        )
        clean = deobfuscate_challenge(raw)
        llm_answer = await _solve_via_llm(gateway_client, clean)
        llm_num = _parse_numeric(llm_answer)
        assert abs(llm_num - 46.0) < 0.01, (
            f"LLM answered {llm_answer} (parsed: {llm_num}), expected 46"
        )
        arith = solve_arithmetic(clean)
        assert arith == "46.00"

    async def test_llm_45_plus_23(self, gateway_client):
        """Challenge 3: forty five + twenty three = 68."""
        raw = (
            "A] dDoOmMiInNaAnNtT lO.oBb^SsStTeR ~ClLaAwW iIsS "
            "fFoOrR~tTyY fFiIvVeE nNeEwW~tToOnNsS; cChHaAlLlLeEnNgGeErR "
            "hHaAsS tTwWeEnNtTyY tThHrReEeE nNeEwW~tToOnNsS. "
            "wWhHaAtT iIsS cCoOmMbBiInNeEdD fFoOrRcCeE?"
        )
        clean = deobfuscate_challenge(raw)
        llm_answer = await _solve_via_llm(gateway_client, clean)
        llm_num = _parse_numeric(llm_answer)
        assert abs(llm_num - 68.0) < 0.01, (
            f"LLM answered {llm_answer} (parsed: {llm_num}), expected 68"
        )
        arith = solve_arithmetic(clean)
        assert arith == "68.00"

    async def test_llm_deobfuscated_text_is_readable(self, gateway_client):
        """Verify the deobfuscated text is human-readable for the LLM."""
        raw = (
            "A] lO.oBb^SsStTeR ~ClLaAwW fFoOrRcCeE iI]sS tTwWeEnNtTyY "
            "sS]iIxX nNeEwW~tToOnNsS, aAnNdD ++ gGaAiInNsS fFoOuUrRtEeEn "
            "mMoOrReE; wWhHaAtT iIsS tThHeE tToO~tTaAlL fFoOrRcCeE?"
        )
        clean = deobfuscate_challenge(raw)
        # The clean text should contain recognizable English words
        assert "lobster" in clean
        assert "claw" in clean
        assert "force" in clean
        assert "twenty" in clean
        assert "newtons" in clean

    async def test_llm_subtraction_25_minus_7(self, gateway_client):
        """Subtraction challenge: twenty five loses seven = 18."""
        raw = (
            "ThIs] LoOooBssst-Er S^wImS[ iN aC-iDd WaTeR, ShAkInG aNtEnNaS "
            "aNd MuLtInG; ItS VeLoOociTy Is T w/eN tY- fIvE mE^tErS PeR "
            "sEcOnD, BuT dUrInG a DoMiNaNcE fIgHt It LoOsEs SeVeN oF iTs "
            "SpEeD, So wHaT Is T"
        )
        clean = deobfuscate_challenge(raw)
        llm_answer = await _solve_via_llm(gateway_client, clean)
        llm_num = _parse_numeric(llm_answer)
        assert abs(llm_num - 18.0) < 0.01, (
            f"LLM answered {llm_answer} (parsed: {llm_num}), expected 18"
        )
        arith = solve_arithmetic(clean)
        assert arith == "18.00"

    async def test_llm_split_thirty_five_plus_twelve(self, gateway_client):
        """Space-injected 'tHiR tY fIvE' + twelve = 47."""
        raw = (
            "A] lO b-.StEr ClAw] FoR cE Is^ tHiR tY] fIvE nEu-TonS um~ "
            "aNd| AfTeR- a DoMinAnCe] PiNcH gAiNs^ tWeLvE nEu>ToNs, "
            "wHaT Is< tHe ToTaL- FoRcE?"
        )
        clean = deobfuscate_challenge(raw)
        llm_answer = await _solve_via_llm(gateway_client, clean)
        llm_num = _parse_numeric(llm_answer)
        assert abs(llm_num - 47.0) < 0.01, (
            f"LLM answered {llm_answer} (parsed: {llm_num}), expected 47"
        )
        arith = solve_arithmetic(clean)
        assert arith == "47.00"
