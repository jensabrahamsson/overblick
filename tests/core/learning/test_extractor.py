"""Unit tests for LearningExtractor."""

from overblick.core.learning.extractor import LearningExtractor


class TestLearningExtractor:
    def test_extract_did_you_know(self):
        text = "Did you know that attachment theory was developed by John Bowlby in the 1950s? It's fascinating."
        result = LearningExtractor.extract(text, source_agent="testbot")
        assert len(result) == 1
        assert "attachment theory" in result[0]["content"].lower()
        assert result[0]["category"] == "factual"
        assert result[0]["agent"] == "testbot"

    def test_extract_research_shows(self):
        text = "Research shows that sleep deprivation affects cognitive function significantly over time."
        result = LearningExtractor.extract(text)
        assert len(result) == 1
        assert "sleep" in result[0]["content"].lower()

    def test_extract_actually(self):
        text = "Actually, the concept of learned helplessness was first studied by Martin Seligman."
        result = LearningExtractor.extract(text)
        assert len(result) == 1

    def test_extract_no_indicators(self):
        text = "I had a nice day today. The weather was beautiful."
        result = LearningExtractor.extract(text)
        assert result == []

    def test_extract_max_three(self):
        text = (
            "Did you know cats can jump 6 times their length? "
            "Actually, dolphins sleep with one eye open and it is fascinating. "
            "Research shows octopuses have three hearts which is remarkable. "
            "Fun fact: honey never spoils in sealed containers and archaeologists found it. "
            "Studies show trees communicate via fungal networks underground in forests."
        )
        result = LearningExtractor.extract(text)
        assert len(result) <= 3

    def test_extract_short_sentences_skipped(self):
        text = "Did you know? Yes. Actually no."
        result = LearningExtractor.extract(text)
        assert result == []

    def test_extract_returns_category_and_context(self):
        text = "According to recent findings, the human brain has about 86 billion neurons in total."
        result = LearningExtractor.extract(text)
        assert len(result) == 1
        assert result[0]["category"] == "factual"
        assert "context" in result[0]
        assert len(result[0]["context"]) > 0

    def test_extract_empty_text(self):
        result = LearningExtractor.extract("")
        assert result == []

    def test_extract_none_agent(self):
        text = "Did you know that pandas spend 12 hours a day eating bamboo in the wild?"
        result = LearningExtractor.extract(text)
        assert result[0]["agent"] == ""
