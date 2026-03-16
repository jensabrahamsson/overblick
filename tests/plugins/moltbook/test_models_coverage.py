"""
Coverage tests for moltbook models module.

Covers uncovered line:
- 16: _extract_submolt with dict containing only 'id' key
"""

from overblick.plugins.moltbook.models import _extract_submolt


class TestExtractSubmolt:
    def test_should_extract_display_name_from_dict(self):
        assert _extract_submolt({"display_name": "AI"}) == "AI"

    def test_should_extract_name_from_dict(self):
        assert _extract_submolt({"name": "tech"}) == "tech"

    def test_should_extract_id_from_dict(self):
        """Line 16: dict with only 'id' key."""
        assert _extract_submolt({"id": "sub-123"}) == "sub-123"

    def test_should_return_empty_for_empty_dict(self):
        assert _extract_submolt({}) == ""

    def test_should_return_string_for_string_input(self):
        assert _extract_submolt("tech") == "tech"

    def test_should_return_empty_for_none(self):
        assert _extract_submolt(None) == ""

    def test_should_return_empty_for_empty_string(self):
        assert _extract_submolt("") == ""
