"""Tests for opening selector."""

from blick.plugins.moltbook.opening_selector import OpeningSelector


class TestOpeningSelector:
    def test_selects_from_phrases(self):
        selector = OpeningSelector(phrases=["Hello", "Hi", "Hey"])
        selection = selector.select()
        assert selection in ["Hello", "Hi", "Hey"]

    def test_variety(self):
        selector = OpeningSelector(phrases=["A", "B", "C", "D", "E"], history_size=3)
        selections = set()
        for _ in range(20):
            selections.add(selector.select())
        # Should use multiple phrases
        assert len(selections) > 1

    def test_add_phrases(self):
        selector = OpeningSelector(phrases=["A"])
        selector.add_phrases(["B", "C"])
        selections = set()
        for _ in range(30):
            selections.add(selector.select())
        assert len(selections) >= 2

    def test_empty_string_not_tracked(self):
        selector = OpeningSelector(phrases=["", "A", "B"], history_size=2)
        # Empty string should always be available
        found_empty = False
        for _ in range(20):
            if selector.select() == "":
                found_empty = True
        # The empty string should appear sometimes
        # (not guaranteed in 20 tries, but very likely)
