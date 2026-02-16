"""
Test fixtures for the setup wizard.
"""

import shutil
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from overblick.setup.app import create_setup_app


@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    """Create a temporary base directory with identity files."""
    # Copy identity files so the wizard can load them
    real_identities = Path(__file__).parent.parent.parent / "overblick" / "identities"
    test_identities = tmp_path / "overblick" / "identities"

    if real_identities.exists():
        shutil.copytree(real_identities, test_identities)

    # Copy pyproject.toml for version detection
    real_toml = Path(__file__).parent.parent.parent / "pyproject.toml"
    if real_toml.exists():
        shutil.copy2(real_toml, tmp_path / "pyproject.toml")

    return tmp_path


@pytest.fixture
def setup_app(base_dir: Path):
    """Create setup app with temp directories."""
    return create_setup_app(base_dir=base_dir)


@pytest.fixture
async def client(setup_app) -> AsyncClient:
    """httpx AsyncClient for route tests."""
    transport = ASGITransport(app=setup_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
