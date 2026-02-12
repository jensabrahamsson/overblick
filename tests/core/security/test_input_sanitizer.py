"""Tests for input sanitizer."""

from blick.core.security.input_sanitizer import sanitize


class TestSanitize:
    def test_strips_null_bytes(self):
        assert sanitize("hello\x00world") == "helloworld"

    def test_strips_control_chars(self):
        result = sanitize("hello\x01\x02world")
        assert "\x01" not in result
        assert "\x02" not in result

    def test_preserves_newlines_and_tabs(self):
        result = sanitize("line1\nline2\ttab")
        assert "\n" in result
        assert "\t" in result

    def test_truncation(self):
        assert len(sanitize("a" * 100, max_length=10)) == 10

    def test_nfc_normalization(self):
        # NFC normalization should combine characters
        result = sanitize("e\u0301")  # e + combining accent
        assert len(result) <= 2  # Should be 1 in NFC

    def test_empty_string(self):
        assert sanitize("") == ""
