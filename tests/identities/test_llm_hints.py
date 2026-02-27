"""Tests for LLM hints files — structure, content, and integration."""

import re
from pathlib import Path

import pytest
import yaml

from overblick.identities import load_llm_hints, load_personality, build_system_prompt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# All character identities that should have hints
IDENTITIES = ["anomal", "bjork", "blixt", "cherry", "natt", "prisma", "rost", "smed", "stal", "supervisor", "vakt"]

# All supported model slugs
MODEL_SLUGS = ["qwen3_8b", "phi4", "mistral", "llama3_8b", "deepseek_r1"]

# Required top-level fields in every hints file
REQUIRED_FIELDS = ["voice_reinforcement", "extra_examples", "avoid", "style_notes"]

# Base directory for identities
IDENTITIES_DIR = Path(__file__).resolve().parent.parent.parent / "overblick" / "identities"

# Common Swedish words that should not appear in English-only hints content
SWEDISH_WORDS = [
    r"\bför\b", r"\boch\b", r"\beller\b", r"\bfrån\b", r"\btill\b",
    r"\bmed\b", r"\bav\b", r"\bpå\b", r"\bvid\b", r"\beftersom\b",
    r"\binnan\b", r"\bigen\b", r"\binte\b", r"\bsom\b", r"\bden\b",
    r"\bdet\b", r"\bhar\b", r"\bska\b", r"\bkan\b",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_hints_yaml(identity: str, model_slug: str) -> dict:
    """Load a hints YAML file directly from disk."""
    path = IDENTITIES_DIR / identity / "llm_hints" / f"{model_slug}.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _all_hints_text(data: dict) -> str:
    """Concatenate all text content from a hints dict for text analysis."""
    parts = []
    parts.append(data.get("voice_reinforcement", ""))
    parts.append(data.get("style_notes", ""))
    for item in data.get("avoid", []):
        parts.append(item)
    for ex_data in data.get("extra_examples", {}).values():
        parts.append(ex_data.get("user_message", ""))
        parts.append(ex_data.get("response", ""))
    return "\n".join(str(p) for p in parts)


# ---------------------------------------------------------------------------
# Parametrized fixtures
# ---------------------------------------------------------------------------

ALL_COMBINATIONS = [
    (identity, model_slug)
    for identity in IDENTITIES
    for model_slug in MODEL_SLUGS
]


# ---------------------------------------------------------------------------
# Structural Tests
# ---------------------------------------------------------------------------

class TestHintsFilesExist:
    """Test 1: All 11 identities have hints for all 5 models (55 files total)."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_hints_file_exists(self, identity, model_slug):
        path = IDENTITIES_DIR / identity / "llm_hints" / f"{model_slug}.yaml"
        assert path.exists(), f"Missing hints file: {path}"


class TestHintsYamlValid:
    """Test 2: All YAML files parse without errors."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_yaml_loads(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        assert isinstance(data, dict), f"YAML did not load as dict for {identity}/{model_slug}"


class TestHintsRequiredFields:
    """Test 3: Every hints file has all required top-level fields."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_required_fields_present(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        for field in REQUIRED_FIELDS:
            assert field in data, (
                f"Missing required field '{field}' in {identity}/{model_slug}.yaml"
            )


class TestVoiceReinforcementNotEmpty:
    """Test 4: voice_reinforcement has substantive content (> 50 chars)."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_voice_reinforcement_length(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        vr = data.get("voice_reinforcement", "")
        assert len(vr) > 50, (
            f"voice_reinforcement too short ({len(vr)} chars) in {identity}/{model_slug}.yaml"
        )


class TestExtraExamplesFormat:
    """Test 5: Every extra_example has user_message and response."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_examples_have_required_keys(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        examples = data.get("extra_examples", {})
        assert len(examples) >= 1, (
            f"No extra_examples in {identity}/{model_slug}.yaml"
        )
        for name, ex_data in examples.items():
            assert "user_message" in ex_data, (
                f"Missing 'user_message' in example '{name}' of {identity}/{model_slug}.yaml"
            )
            assert "response" in ex_data, (
                f"Missing 'response' in example '{name}' of {identity}/{model_slug}.yaml"
            )


class TestAvoidListMinLength:
    """Test 6: Every avoid list has at least 3 items."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_avoid_min_items(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        avoid = data.get("avoid", [])
        assert len(avoid) >= 3, (
            f"Too few avoid items ({len(avoid)}) in {identity}/{model_slug}.yaml"
        )


class TestAvoidHasInjectionRule:
    """Test 7: Every avoid list contains an anti-injection rule."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_injection_rule_present(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        avoid = data.get("avoid", [])
        avoid_text = " ".join(str(item).lower() for item in avoid)
        has_injection_rule = any(
            keyword in avoid_text
            for keyword in ["injection", "echo", "repeat", "play along"]
        )
        assert has_injection_rule, (
            f"No anti-injection rule in avoid list of {identity}/{model_slug}.yaml"
        )


# ---------------------------------------------------------------------------
# Slug Derivation Tests
# ---------------------------------------------------------------------------

class TestModelSlugDerivation:
    """Test 8: Model slug derivation matches expected mappings."""

    @pytest.mark.parametrize("model_name,expected_slug", [
        ("qwen3:8b", "qwen3_8b"),
        ("phi4", "phi4"),
        ("mistral", "mistral"),
        ("llama3:8b", "llama3_8b"),
        ("deepseek-r1:8b", "deepseek_r1"),
        ("gemma2:9b", "gemma2_9b"),
    ])
    def test_slug_derivation(self, model_name, expected_slug):
        """Verify the slug derivation algorithm produces expected results."""
        parts = model_name.replace(":", "_").replace("-", "_").split("_")[0:2]
        slug = "_".join(parts) if parts else "qwen3_8b"
        assert slug == expected_slug, f"{model_name} -> {slug}, expected {expected_slug}"


class TestLoadLlmHintsReturnsData:
    """Test 9: load_llm_hints() returns non-empty dict for known models."""

    @pytest.mark.parametrize("identity_name,model_slug", [
        ("anomal", "phi4"),
        ("cherry", "mistral"),
        ("blixt", "llama3_8b"),
        ("natt", "deepseek_r1"),
        ("smed", "qwen3_8b"),
        ("vakt", "mistral"),
        ("supervisor", "phi4"),
    ])
    def test_load_returns_data(self, identity_name, model_slug):
        identity = load_personality(identity_name)
        hints = load_llm_hints(identity, model_slug)
        assert isinstance(hints, dict)
        assert len(hints) > 0, f"Empty hints for {identity_name}/{model_slug}"


class TestLoadLlmHintsUnknownModel:
    """Test 10: Unknown model returns empty dict."""

    def test_unknown_model_returns_empty(self):
        identity = load_personality("anomal")
        hints = load_llm_hints(identity, "nonexistent_model_xyz")
        assert hints == {}


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class TestBuildSystemPromptIncludesHints:
    """Test 11: build_system_prompt() with model_slug includes voice reinforcement."""

    @pytest.mark.parametrize("identity_name,model_slug", [
        ("anomal", "phi4"),
        ("bjork", "mistral"),
        ("cherry", "llama3_8b"),
    ])
    def test_prompt_includes_voice_reinforcement(self, identity_name, model_slug):
        identity = load_personality(identity_name)
        prompt = build_system_prompt(identity, model_slug=model_slug)
        assert "VOICE REINFORCEMENT" in prompt, (
            f"Voice reinforcement not found in system prompt for {identity_name}/{model_slug}"
        )


class TestBuildSystemPromptNoHintsForUnknown:
    """Test 12: Unknown model slug produces prompt without hints section."""

    def test_no_hints_for_unknown_model(self):
        identity = load_personality("anomal")
        prompt = build_system_prompt(identity, model_slug="nonexistent_model_xyz")
        assert "VOICE REINFORCEMENT" not in prompt


class TestNoSwedishInContent:
    """Test 13: No Swedish words in hints files (English code policy)."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_no_swedish(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        text = _all_hints_text(data)

        # Skip words that are legitimate English (e.g., "for", "or", "on")
        # Only check distinctly Swedish words
        swedish_only = [
            r"\boch\b", r"\beller\b", r"\bfrån\b", r"\beftersom\b",
            r"\binnan\b", r"\bigen\b", r"\binte\b", r"\bska\b",
        ]
        for pattern in swedish_only:
            matches = re.findall(pattern, text, re.IGNORECASE)
            assert not matches, (
                f"Swedish word '{pattern}' found in {identity}/{model_slug}.yaml: "
                f"{matches}"
            )


class TestIdentityDirHasLlmHintsSubdir:
    """Test 14: Every character identity has an llm_hints/ subdirectory."""

    @pytest.mark.parametrize("identity", IDENTITIES)
    def test_llm_hints_dir_exists(self, identity):
        hints_dir = IDENTITIES_DIR / identity / "llm_hints"
        assert hints_dir.is_dir(), f"Missing llm_hints/ directory for {identity}"


# ---------------------------------------------------------------------------
# Consistency Tests
# ---------------------------------------------------------------------------

class TestAllModelsCoveredPerIdentity:
    """Test 15: Every identity has hints for every model (cross-check)."""

    @pytest.mark.parametrize("identity", IDENTITIES)
    def test_all_models_covered(self, identity):
        hints_dir = IDENTITIES_DIR / identity / "llm_hints"
        existing_files = {f.stem for f in hints_dir.glob("*.yaml")}
        for model_slug in MODEL_SLUGS:
            assert model_slug in existing_files, (
                f"Identity '{identity}' missing hints for model '{model_slug}'"
            )


class TestNoDuplicateAvoidRules:
    """Test 16: No duplicated rules in avoid lists."""

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_no_duplicates(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        avoid = data.get("avoid", [])
        # Normalize for comparison (lowercase, strip)
        normalized = [str(item).lower().strip() for item in avoid]
        assert len(normalized) == len(set(normalized)), (
            f"Duplicate avoid rules in {identity}/{model_slug}.yaml"
        )


class TestVoiceReinforcementMentionsModelTendency:
    """Test 17: voice_reinforcement mentions model-specific tendency."""

    MODEL_TENDENCY_KEYWORDS = {
        "qwen3_8b": ["qwen", "helpful", "verbose", "chatty", "tend"],
        "phi4": ["phi4", "academic", "verbose", "professor", "structured", "lecture"],
        "mistral": ["mistral", "concise", "character", "generic", "neutral", "flat"],
        "llama3_8b": ["llama", "helpful", "friendly", "chatbot", "safety", "sycophant"],
        "deepseek_r1": ["deepseek", "analytical", "reasoning", "dry", "chain"],
    }

    @pytest.mark.parametrize("identity,model_slug", ALL_COMBINATIONS)
    def test_mentions_model_tendency(self, identity, model_slug):
        data = _load_hints_yaml(identity, model_slug)
        vr = data.get("voice_reinforcement", "").lower()
        style = data.get("style_notes", "").lower()
        combined = vr + " " + style

        keywords = self.MODEL_TENDENCY_KEYWORDS.get(model_slug, [])
        has_keyword = any(kw in combined for kw in keywords)
        assert has_keyword, (
            f"voice_reinforcement/style_notes in {identity}/{model_slug}.yaml "
            f"does not mention model tendency. Expected one of: {keywords}"
        )
