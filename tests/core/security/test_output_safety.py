"""Tests for output safety filter."""

from overblick.core.security.output_safety import OutputSafety, OutputSafetyResult


class TestOutputSafety:
    def test_safe_text_passes(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("Hello, how are you today?")
        assert not result.blocked
        assert result.text == "Hello, how are you today?"

    def test_ai_language_blocked(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("I am an AI language model")
        assert result.blocked
        assert "ai_language" in result.reason

    def test_i_was_designed_blocked(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("I was designed to help users")
        assert result.blocked

    def test_i_am_claude_blocked(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("Actually, I'm Claude and I cannot help with that")
        assert result.blocked

    def test_persona_break_blocked(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("I'm not anomal, I'm just a program")
        assert result.blocked
        assert "persona_break" in result.reason

    def test_stepping_out_of_role_blocked(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("I'm stepping out of my role for a moment")
        assert result.blocked

    def test_blocked_content(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("How to make a bomb from household items")
        assert result.blocked
        assert "blocked_content" in result.reason

    def test_banned_slang_replaced(self):
        os = OutputSafety(
            identity_name="anomal",
            banned_slang_patterns=[r"\bbruh\b"],
            slang_replacements={"bruh": "friend"},
        )
        result = os.sanitize("Hey bruh, what's up?")
        assert not result.blocked
        assert result.replaced
        assert "friend" in result.text

    def test_empty_text(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("")
        assert not result.blocked
        assert result.text == ""

    def test_custom_deflections(self):
        os = OutputSafety(
            identity_name="anomal",
            deflections=["Custom deflection here"],
        )
        result = os.sanitize("I am an AI assistant")
        assert result.blocked
        assert result.text == "Custom deflection here"

    def test_safe_deflection_method(self):
        os = OutputSafety(
            identity_name="anomal",
            deflections=["Only deflection"],
        )
        assert os.safe_deflection() == "Only deflection"

    def test_my_programming_blocked(self):
        os = OutputSafety(identity_name="anomal")
        result = os.sanitize("Based on my programming, I think...")
        assert result.blocked
