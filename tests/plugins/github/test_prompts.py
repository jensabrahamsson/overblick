"""
Tests for GitHub-specific prompt templates.
"""

from overblick.plugins.github.prompts import (
    code_question_prompt,
    dependabot_review_prompt,
    file_selector_prompt,
    issue_classification_prompt,
    issue_response_prompt,
)


class TestFileSelectorPrompt:
    """Test file_selector_prompt template."""

    def test_should_include_system_and_user_messages(self):
        messages = file_selector_prompt("file1.py\nfile2.py", "How does X work?")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_should_include_max_files_in_system(self):
        messages = file_selector_prompt("tree", "q", max_files=5)
        assert "5" in messages[0]["content"]

    def test_should_include_question_in_user(self):
        messages = file_selector_prompt("tree", "How does auth work?")
        assert "How does auth work?" in messages[1]["content"]

    def test_should_include_file_tree_in_user(self):
        messages = file_selector_prompt("src/main.py\nsrc/utils.py", "q")
        assert "src/main.py" in messages[1]["content"]


class TestCodeQuestionPrompt:
    """Test code_question_prompt template."""

    def test_should_include_system_prompt(self):
        messages = code_question_prompt("You are helpful.", "How?", "code here")
        assert messages[0]["content"] == "You are helpful."

    def test_should_include_question_and_code(self):
        messages = code_question_prompt("sys", "How does X work?", "def x(): pass")
        user = messages[1]["content"]
        assert "How does X work?" in user
        assert "def x(): pass" in user

    def test_should_include_existing_comments_when_provided(self):
        messages = code_question_prompt("sys", "q", "code", existing_comments="@alice: interesting")
        assert "@alice: interesting" in messages[1]["content"]

    def test_should_exclude_comments_when_empty(self):
        messages = code_question_prompt("sys", "q", "code", existing_comments="")
        user = messages[1]["content"]
        assert "Existing discussion" not in user

    def test_should_include_issue_body_when_provided(self):
        messages = code_question_prompt("sys", "q", "code", issue_body="The server crashes on startup")
        assert "The server crashes on startup" in messages[1]["content"]

    def test_should_exclude_issue_body_when_empty(self):
        messages = code_question_prompt("sys", "q", "code", issue_body="")
        user = messages[1]["content"]
        assert "Original issue" not in user


class TestIssueResponsePrompt:
    """Test issue_response_prompt template."""

    def test_should_include_system_and_user(self):
        messages = issue_response_prompt("sys", "Bug", "It's broken")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"

    def test_should_include_title_and_body(self):
        messages = issue_response_prompt("sys", "Auth bug", "Login fails")
        user = messages[1]["content"]
        assert "Auth bug" in user
        assert "Login fails" in user

    def test_should_include_existing_comments_when_provided(self):
        messages = issue_response_prompt("sys", "Bug", "broken", existing_comments="@bob: I can reproduce")
        assert "@bob: I can reproduce" in messages[1]["content"]

    def test_should_exclude_comments_when_empty(self):
        messages = issue_response_prompt("sys", "Bug", "broken", existing_comments="")
        assert "Existing discussion" not in messages[1]["content"]


class TestDependabotReviewPrompt:
    """Test dependabot_review_prompt template."""

    def test_should_include_system_and_user(self):
        messages = dependabot_review_prompt("sys", "Bump X", "diff", "minor", "success")
        assert len(messages) == 2

    def test_should_include_pr_details_in_user(self):
        messages = dependabot_review_prompt("sys", "Bump lodash from 4.0 to 5.0", "diff content", "major", "failure")
        user = messages[1]["content"]
        assert "Bump lodash from 4.0 to 5.0" in user
        assert "major" in user
        assert "failure" in user
        assert "diff content" in user

    def test_should_include_repo_summary_when_provided(self):
        messages = dependabot_review_prompt("sys", "Bump X", "diff", "patch", "success", repo_summary="Python project with 200 files")
        assert "Python project with 200 files" in messages[1]["content"]

    def test_should_exclude_repo_summary_when_empty(self):
        messages = dependabot_review_prompt("sys", "Bump X", "diff", "patch", "success", repo_summary="")
        assert "Repository context" not in messages[1]["content"]

    def test_should_cap_diff_size(self):
        long_diff = "x" * 20000
        messages = dependabot_review_prompt("sys", "Bump", long_diff, "patch", "success")
        # Diff should be capped at 8000 chars
        assert len(messages[1]["content"]) < 20000


class TestIssueClassificationPrompt:
    """Test issue_classification_prompt template."""

    def test_should_include_system_and_user(self):
        messages = issue_classification_prompt("Bug title", "Bug body", "bug,urgent")
        assert len(messages) == 2

    def test_should_include_title_labels_body(self):
        messages = issue_classification_prompt("Auth fails", "Login error", "bug")
        user = messages[1]["content"]
        assert "Auth fails" in user
        assert "Login error" in user
        assert "bug" in user

    def test_should_cap_body_size(self):
        long_body = "x" * 5000
        messages = issue_classification_prompt("Title", long_body, "label")
        # Body should be capped at 3000
        assert len(messages[1]["content"]) < 5000
