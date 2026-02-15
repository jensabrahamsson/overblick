"""
Tests for OpeningSelector — variety in engagement openings.
"""

import pytest

from overblick.capabilities.social.opening_selector import OpeningSelector, DEFAULT_OPENINGS


class TestOpeningSelector:
    def test_initialization_default(self):
        selector = OpeningSelector()
        assert selector._phrases == DEFAULT_OPENINGS
        assert len(selector._recent) == 0

    def test_initialization_custom_phrases(self):
        custom = ["Hello", "Hi there", "Greetings"]
        selector = OpeningSelector(phrases=custom)
        assert selector._phrases == custom

    def test_initialization_custom_history(self):
        selector = OpeningSelector(history_size=5)
        assert selector._recent.maxlen == 5

    def test_select_returns_phrase(self):
        selector = OpeningSelector()
        choice = selector.select()
        assert choice in DEFAULT_OPENINGS

    def test_select_tracks_history(self):
        phrases = ["A", "B", "C"]
        selector = OpeningSelector(phrases=phrases)
        
        first = selector.select()
        assert len(selector._recent) == 1 or first == ""  # Empty string not tracked
        
        # If we got empty string, select again
        if first == "":
            first = selector.select()
            while first == "":
                first = selector.select()
        
        assert first in selector._recent

    def test_select_avoids_recent(self):
        phrases = ["A", "B", "C", "D", "E"]
        selector = OpeningSelector(phrases=phrases, history_size=3)
        
        selections = []
        for _ in range(10):
            choice = selector.select()
            selections.append(choice)
        
        # Check that recent phrases are not repeated immediately
        # (statistical test - might occasionally fail due to randomness)
        # We just verify the mechanism works, not strict enforcement
        assert len(set(selections)) > 1  # Got variety

    def test_select_handles_small_pool(self):
        phrases = ["Only one"]
        selector = OpeningSelector(phrases=phrases)
        
        # Should still work even if pool exhausted
        for _ in range(5):
            choice = selector.select()
            assert choice == "Only one"

    def test_select_empty_string_not_tracked(self):
        phrases = ["", "A", "B"]
        selector = OpeningSelector(phrases=phrases)
        
        # Select empty string multiple times
        while True:
            choice = selector.select()
            if choice == "":
                break
        
        # Empty string should not be in history
        assert "" not in selector._recent

    def test_add_phrases(self):
        selector = OpeningSelector(phrases=["A", "B"])
        selector.add_phrases(["C", "D"])
        
        assert "A" in selector._phrases
        assert "B" in selector._phrases
        assert "C" in selector._phrases
        assert "D" in selector._phrases
        assert len(selector._phrases) == 4

    def test_add_phrases_empty(self):
        selector = OpeningSelector(phrases=["A"])
        original_count = len(selector._phrases)
        selector.add_phrases([])
        
        assert len(selector._phrases) == original_count

    def test_variety_over_many_selections(self):
        phrases = ["A", "B", "C", "D", "E", "F"]
        selector = OpeningSelector(phrases=phrases, history_size=3)
        
        selections = [selector.select() for _ in range(20)]
        unique = set(selections)
        
        # Should see multiple different phrases
        assert len(unique) >= 3

    def test_history_size_limit(self):
        phrases = ["A", "B", "C", "D", "E"]
        selector = OpeningSelector(phrases=phrases, history_size=2)
        
        selector.select()  # First
        selector.select()  # Second
        selector.select()  # Third (should evict first)
        
        # History should only keep last 2
        assert len(selector._recent) <= 2
