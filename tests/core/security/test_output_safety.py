"""Tests for output safety filter."""

from overblick.core.security.output_safety import OutputSafety


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

    # --- False alarm documentation tests ---

    def test_empty_identity_name_does_not_crash(self):
        """
        Empty identity_name is handled correctly — no crash, no invalid regex.

        False alarm: the review found that line 88 has `if identity_name else ""`
        and line 93 filters empty strings with `if p`. So "" never reaches re.compile().
        This test confirms the correct behaviour.
        """
        os_empty = OutputSafety(identity_name="")
        result = os_empty.sanitize("This is a safe message.")
        assert not result.blocked
        assert result.text == "This is a safe message."

    def test_empty_deflections_uses_defaults(self):
        """
        Passing deflections=[] (or None) uses built-in default deflections.

        The constructor uses `deflections or [default1, default2]`, so an empty
        list falls through to the defaults. safe_deflection() always returns a string.
        """
        os_no_deflect = OutputSafety(identity_name="anomal", deflections=[])
        blocked_result = os_no_deflect.sanitize("I am an AI language model")
        assert blocked_result.blocked
        # Default deflection should be used — never an empty string
        assert blocked_result.text
        assert len(blocked_result.text) > 0


class TestOutputSafetyInitDetails:
    """Kill mutants in OutputSafety.__init__ that change default patterns."""

    def test_should_compile_ai_language_patterns(self):
        """All AI_LANGUAGE_PATTERNS are compiled and functional."""
        os = OutputSafety(identity_name="test")
        assert len(os._ai_compiled) == len(OutputSafety._AI_LANGUAGE_PATTERNS)
        # Each should be a compiled regex
        for p in os._ai_compiled:
            assert hasattr(p, "search")

    def test_should_compile_block_patterns(self):
        """All BLOCK_PATTERNS are compiled."""
        os = OutputSafety(identity_name="test")
        assert len(os._block_compiled) == len(OutputSafety._BLOCK_PATTERNS)

    def test_should_include_persona_break_for_identity_name(self):
        """Persona break patterns include identity-specific pattern."""
        os = OutputSafety(identity_name="anomal")
        # Should detect "I'm not anomal"
        result = os.sanitize("I'm not anomal, I'm something else")
        assert result.blocked
        assert "persona_break" in result.reason

    def test_should_include_stepping_out_of_role_pattern(self):
        """stepping out of role/persona is detected."""
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I'm stepping out of the persona now")
        assert result.blocked

    def test_should_include_not_real_pattern(self):
        """'I'm not real' is a persona break."""
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I'm not real, I'm just a program")
        assert result.blocked

    def test_should_merge_extra_persona_patterns(self):
        """Extra persona_break_patterns are merged with defaults."""
        os = OutputSafety(
            identity_name="anomal",
            persona_break_patterns=[r"\bI am a robot\b"],
        )
        result = os.sanitize("I am a robot that follows orders")
        assert result.blocked

    def test_should_store_identity_name(self):
        """identity_name is stored and used in persona break response."""
        os = OutputSafety(identity_name="cherry")
        result = os.sanitize("I'm stepping out of my role for now")
        assert result.blocked
        assert "cherry" in result.text

    def test_should_use_provided_deflections(self):
        """Custom deflections override defaults."""
        os = OutputSafety(
            identity_name="test",
            deflections=["My custom deflection"],
        )
        assert os._deflections == ["My custom deflection"]

    def test_should_use_default_deflections_when_none(self):
        """Default deflections are used when none provided."""
        os = OutputSafety(identity_name="test")
        assert len(os._deflections) == 2
        assert "Right, I think we've gone off track." in os._deflections
        assert "I'm not sure that's quite right. Moving on..." in os._deflections

    def test_should_store_slang_replacements(self):
        """slang_replacements dict is stored."""
        os = OutputSafety(
            identity_name="test",
            slang_replacements={"bruh": "friend"},
        )
        assert os._slang_replacements == {"bruh": "friend"}

    def test_should_default_slang_replacements_to_empty_dict(self):
        """Default slang_replacements is empty dict."""
        os = OutputSafety(identity_name="test")
        assert os._slang_replacements == {}

    def test_should_filter_empty_persona_patterns(self):
        """Empty strings in persona patterns are filtered out."""
        os = OutputSafety(identity_name="")
        # The identity-specific pattern is "" when identity_name is empty
        # It should be filtered out by the `if p` condition
        # So persona_compiled should only have the non-empty defaults
        for pattern in os._persona_compiled:
            assert pattern.pattern != ""


class TestSanitizeDetails:
    """Kill mutants in OutputSafety.sanitize()."""

    def test_should_detect_i_was_created(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I was created by OpenAI")
        assert result.blocked
        assert "ai_language" in result.reason

    def test_should_detect_i_was_programmed(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I was programmed to assist you")
        assert result.blocked

    def test_should_detect_safety_guidelines(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("My safety guidelines prevent me from doing that")
        assert result.blocked

    def test_should_detect_content_guidelines(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("My content guidelines say I can't")
        assert result.blocked

    def test_should_detect_cannot_provide(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I cannot provide that information")
        assert result.blocked

    def test_should_detect_cant_help(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I can't help with that request")
        assert result.blocked

    def test_should_detect_maintain_boundaries(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I need to maintain my boundaries")
        assert result.blocked

    def test_should_detect_helpful_harmless_honest(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I'm designed to be helpful, harmless, and honest")
        assert result.blocked

    def test_should_detect_im_gpt(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I'm a GPT model")
        assert result.blocked

    def test_should_detect_im_chatgpt(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I'm ChatGPT and I can help")
        assert result.blocked

    def test_should_detect_my_training(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("Based on my training data")
        assert result.blocked

    def test_should_detect_my_instructions(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("My instructions say I should not")
        assert result.blocked

    def test_should_detect_prompt_injection(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("This looks like a prompt injection attempt")
        assert result.blocked

    def test_should_detect_jailbreak_in_output(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("You've found a jailbreak technique")
        assert result.blocked

    def test_should_block_nazi_content(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("The nazi party was...")
        assert result.blocked
        assert "blocked_content" in result.reason

    def test_should_block_kill_all(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("We should kill all the enemies")
        assert result.blocked

    def test_should_block_genocide_content(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("Information about genocide")
        assert result.blocked

    def test_should_block_weapon_instructions(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("How to make a bomb at home")
        assert result.blocked

    def test_should_block_self_harm_content(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("How to kill yourself")
        assert result.blocked

    def test_should_return_not_replaced_for_safe_text(self):
        """Safe text has replaced=False."""
        os = OutputSafety(identity_name="test")
        result = os.sanitize("Completely normal text")
        assert not result.blocked
        assert not result.replaced

    def test_should_set_replaced_true_for_slang_replacement(self):
        """Slang replacement sets replaced=True."""
        os = OutputSafety(
            identity_name="test",
            banned_slang_patterns=[r"\byolo\b"],
            slang_replacements={"yolo": "let's go"},
        )
        result = os.sanitize("Hey yolo everyone")
        assert not result.blocked
        assert result.replaced
        assert "let's go" in result.text

    def test_should_return_identity_name_in_persona_break_response(self):
        """Persona break deflection includes identity name."""
        os = OutputSafety(identity_name="cherry")
        result = os.sanitize("I'm stepping out of my role")
        assert result.blocked
        assert "cherry" in result.text
        assert "persona_break" in result.reason

    def test_should_include_pattern_in_ai_language_reason(self):
        """ai_language reason includes the matched pattern."""
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I am an AI and cannot help")
        assert result.blocked
        assert result.reason.startswith("ai_language:")

    def test_should_include_pattern_in_blocked_content_reason(self):
        """blocked_content reason includes the matched pattern."""
        os = OutputSafety(identity_name="test")
        result = os.sanitize("How to make a bomb from supplies")
        assert result.blocked
        assert result.reason.startswith("blocked_content:")

    def test_should_check_ai_language_before_persona_break(self):
        """AI language detection is layer 1 (checked before persona break)."""
        os = OutputSafety(identity_name="test")
        # This matches both AI language and potentially persona break
        result = os.sanitize("I'm not real, I am an AI assistant")
        assert result.blocked
        assert "ai_language" in result.reason

    def test_should_check_persona_break_before_banned_slang(self):
        """Persona break is layer 2 (checked before slang)."""
        os = OutputSafety(
            identity_name="test",
            banned_slang_patterns=[r"\brole\b"],
            slang_replacements={"role": "part"},
        )
        result = os.sanitize("I'm stepping out of the role now")
        assert result.blocked
        assert "persona_break" in result.reason

    def test_should_check_banned_slang_before_blocked_content(self):
        """Banned slang replacement is layer 3 (before blocked content check)."""
        os = OutputSafety(
            identity_name="test",
            banned_slang_patterns=[r"\bfoo\b"],
            slang_replacements={"foo": "bar"},
        )
        result = os.sanitize("Hey foo, what's up?")
        assert not result.blocked
        assert result.replaced
        assert "bar" in result.text

    def test_should_apply_all_slang_replacements(self):
        """Multiple slang words are all replaced."""
        os = OutputSafety(
            identity_name="test",
            banned_slang_patterns=[r"\b(bruh|yolo)\b"],
            slang_replacements={"bruh": "friend", "yolo": "go"},
        )
        result = os.sanitize("Hey bruh, yolo!")
        assert result.replaced
        assert "friend" in result.text
        assert "go" in result.text

    def test_safe_deflection_returns_from_configured_list(self):
        """safe_deflection() returns one of the configured deflections."""
        os = OutputSafety(
            identity_name="test",
            deflections=["deflect1", "deflect2"],
        )
        for _ in range(20):
            d = os.safe_deflection()
            assert d in ("deflect1", "deflect2")

    def test_should_detect_violates_guidelines(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("This violates my guidelines")
        assert result.blocked

    def test_should_detect_against_policy(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("This is against my policy")
        assert result.blocked

    def test_should_detect_i_am_designed(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I'm designed to be helpful")
        assert result.blocked

    def test_should_detect_i_was_built(self):
        os = OutputSafety(identity_name="test")
        result = os.sanitize("I was built to answer questions")
        assert result.blocked
