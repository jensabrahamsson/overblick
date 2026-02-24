"""Tests for workspace manager."""

import pytest

from overblick.plugins.dev_agent.workspace import WorkspaceManager


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo to serve as remote."""
    import subprocess

    repo = tmp_path / "remote"
    repo.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=repo, capture_output=True)
    return repo


@pytest.fixture
def workspace(tmp_path, git_repo):
    """Create a workspace manager pointing at the temp repo."""
    ws_path = tmp_path / "workspace" / "project"
    return WorkspaceManager(
        workspace_path=ws_path,
        repo_url=str(git_repo),
        default_branch="main",
        dry_run=True,
    )


@pytest.mark.asyncio
async def test_get_state_not_cloned(workspace):
    state = await workspace.get_state()
    assert state.cloned is False
    assert state.current_branch == ""


@pytest.mark.asyncio
async def test_ensure_cloned(tmp_path):
    """Test cloning a repo with actual content."""
    import subprocess

    # Create a non-bare repo with at least one commit
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=source, capture_output=True)
    (source / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=source, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=source, capture_output=True,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "PATH": "/usr/bin:/usr/local/bin"},
    )

    ws_path = tmp_path / "workspace" / "project"
    ws = WorkspaceManager(
        workspace_path=ws_path,
        repo_url=str(source),
        dry_run=True,
    )

    result = await ws.ensure_cloned()
    assert result is True

    state = await ws.get_state()
    assert state.cloned is True
    assert state.current_branch == "main"


@pytest.mark.asyncio
async def test_is_clean_empty_workspace(tmp_path):
    """Test is_clean on a non-git directory returns False/error gracefully."""
    ws_path = tmp_path / "empty"
    ws_path.mkdir()
    ws = WorkspaceManager(workspace_path=ws_path, repo_url="", dry_run=True)
    # Should handle missing git gracefully
    result = await ws.is_clean()
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_commit_refuses_on_main(tmp_path):
    """Test that commit_and_push refuses to commit on main."""
    import subprocess

    # Create a source repo
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=source, capture_output=True)
    (source / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=source, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=source, capture_output=True,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "PATH": "/usr/bin:/usr/local/bin"},
    )

    ws_path = tmp_path / "workspace"
    ws = WorkspaceManager(
        workspace_path=ws_path,
        repo_url=str(source),
        dry_run=False,  # Not dry run — to test the safety check
    )
    await ws.ensure_cloned()

    # Should refuse to commit on main
    result = await ws.commit_and_push("test commit")
    assert result is False


@pytest.mark.asyncio
async def test_list_branches(tmp_path):
    """Test listing branches."""
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=source, capture_output=True)
    (source / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=source, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=source, capture_output=True,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "PATH": "/usr/bin:/usr/local/bin"},
    )

    ws_path = tmp_path / "workspace"
    ws = WorkspaceManager(
        workspace_path=ws_path,
        repo_url=str(source),
        dry_run=True,
    )
    await ws.ensure_cloned()

    branches = await ws.list_branches()
    assert "main" in branches
