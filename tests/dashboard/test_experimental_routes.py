"""
Tests for experimental plugin dashboard routes.

Covers: Compass, Kontrast, Skuggspel, Spegel, Stage.
"""

import json

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestCompassRoute:
    """Tests for the /compass dashboard route."""

    @pytest.mark.asyncio
    async def test_compass_page_empty(self, client, session_cookie):
        """Compass page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/compass",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Compass" in resp.text
        assert "Identity drift detection" in resp.text
        assert "No baselines established yet" in resp.text

    @pytest.mark.asyncio
    async def test_compass_page_with_data(self, client, session_cookie, tmp_path, app):
        """Compass page renders baselines and alerts when data exists."""
        import time

        # Create mock compass state
        identity_dir = tmp_path / "data" / "anomal"
        identity_dir.mkdir(parents=True)
        state = {
            "baselines": {
                "anomal": {
                    "identity_name": "anomal",
                    "metrics": {
                        "avg_sentence_length": 15.5,
                        "avg_word_length": 4.8,
                        "vocabulary_richness": 0.72,
                        "punctuation_frequency": 12.3,
                        "question_ratio": 0.15,
                        "exclamation_ratio": 0.05,
                        "comma_frequency": 8.1,
                        "formality_score": 0.65,
                        "word_count": 150,
                    },
                    "sample_count": 10,
                    "established_at": time.time(),
                    "std_devs": {},
                },
            },
            "alerts": [
                {
                    "identity_name": "anomal",
                    "drift_score": 2.5,
                    "threshold": 2.0,
                    "drifted_dimensions": ["avg_sentence_length", "formality_score"],
                    "message": "Test alert",
                    "fired_at": time.time(),
                    "acknowledged": False,
                },
            ],
            "drift_history": [
                {
                    "identity_name": "anomal",
                    "current_metrics": {
                        "avg_sentence_length": 18.0,
                        "avg_word_length": 5.0,
                        "vocabulary_richness": 0.68,
                        "punctuation_frequency": 14.0,
                        "question_ratio": 0.1,
                        "exclamation_ratio": 0.08,
                        "comma_frequency": 9.0,
                        "formality_score": 0.55,
                        "word_count": 120,
                    },
                    "drift_score": 2.5,
                    "drifted_dimensions": ["avg_sentence_length"],
                    "sample_count": 5,
                    "measured_at": time.time(),
                },
            ],
        }
        (identity_dir / "compass_state.json").write_text(json.dumps(state))

        # Patch data root to use tmp_path
        import overblick.dashboard.routes.compass as compass_mod
        original = compass_mod._load_compass_data

        def patched_load(request):
            from pathlib import Path
            baselines = state["baselines"]
            alerts = state["alerts"]
            drift_history = state["drift_history"]
            # Add severity to alerts for template rendering
            for a in alerts:
                a.setdefault("severity", "warning")
            identity_status = {
                "anomal": {"drift_score": 2.5, "severity": "warning"},
            }
            return baselines, alerts, drift_history, 2.0, identity_status

        compass_mod._load_compass_data = patched_load
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/compass",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "anomal" in resp.text
            assert "2.50" in resp.text  # drift score
        finally:
            compass_mod._load_compass_data = original

    @pytest.mark.asyncio
    async def test_compass_requires_auth(self, client):
        """Compass page redirects without auth."""
        resp = await client.get("/compass", follow_redirects=False)
        assert resp.status_code in (302, 303)


class TestKontrastRoute:
    """Tests for the /kontrast dashboard route."""

    @pytest.mark.asyncio
    async def test_kontrast_page_empty(self, client, session_cookie):
        """Kontrast page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/kontrast",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Kontrast" in resp.text
        assert "Multi-perspective content" in resp.text
        assert "No Kontrast pieces generated yet" in resp.text

    @pytest.mark.asyncio
    async def test_kontrast_page_with_pieces(self, client, session_cookie):
        """Kontrast page renders pieces when data exists."""
        import time
        import overblick.dashboard.routes.kontrast as kontrast_mod

        pieces = [
            {
                "topic": "AI Regulation in Europe",
                "topic_hash": "abc123",
                "source_summary": "EU proposes new AI rules",
                "perspectives": [
                    {
                        "identity_name": "anomal",
                        "display_name": "Anomal",
                        "content": "Fascinating development in regulatory frameworks.",
                        "generated_at": time.time(),
                        "word_count": 5,
                    },
                    {
                        "identity_name": "cherry",
                        "display_name": "Cherry",
                        "content": "I feel the emotional weight of these decisions.",
                        "generated_at": time.time(),
                        "word_count": 9,
                    },
                ],
                "created_at": time.time(),
                "article_count": 5,
                "identity_count": 2,
            },
        ]

        original = kontrast_mod._load_pieces
        kontrast_mod._load_pieces = lambda req: pieces
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/kontrast",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "AI Regulation in Europe" in resp.text
            assert "Anomal" in resp.text
            assert "Cherry" in resp.text
            assert "2 perspectives" in resp.text
        finally:
            kontrast_mod._load_pieces = original

    @pytest.mark.asyncio
    async def test_kontrast_requires_auth(self, client):
        """Kontrast page redirects without auth."""
        resp = await client.get("/kontrast", follow_redirects=False)
        assert resp.status_code in (302, 303)


class TestSkuggspelRoute:
    """Tests for the /skuggspel dashboard route."""

    @pytest.mark.asyncio
    async def test_skuggspel_page_empty(self, client, session_cookie):
        """Skuggspel page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/skuggspel",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Skuggspel" in resp.text
        assert "Shadow-self content" in resp.text
        assert "No shadow posts generated yet" in resp.text

    @pytest.mark.asyncio
    async def test_skuggspel_page_with_posts(self, client, session_cookie):
        """Skuggspel page renders posts when data exists."""
        import time
        import overblick.dashboard.routes.skuggspel as skuggspel_mod

        posts = [
            {
                "identity_name": "anomal",
                "display_name": "Anomal",
                "topic": "Social Acceptance",
                "shadow_content": "I just want to fit in. To be normal for once.",
                "shadow_profile": {
                    "identity_name": "anomal",
                    "shadow_description": "The part that craves normalcy",
                    "inverted_traits": {},
                    "shadow_voice": "Eager to please",
                    "framework": "default_inversion",
                },
                "generated_at": time.time(),
                "word_count": 12,
            },
        ]

        original = skuggspel_mod._load_posts
        skuggspel_mod._load_posts = lambda req: posts
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/skuggspel",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "Anomal" in resp.text
            assert "Social Acceptance" in resp.text
            assert "SHADOW" in resp.text
            assert "craves normalcy" in resp.text
        finally:
            skuggspel_mod._load_posts = original

    @pytest.mark.asyncio
    async def test_skuggspel_requires_auth(self, client):
        """Skuggspel page redirects without auth."""
        resp = await client.get("/skuggspel", follow_redirects=False)
        assert resp.status_code in (302, 303)


class TestSpegelRoute:
    """Tests for the /spegel dashboard route."""

    @pytest.mark.asyncio
    async def test_spegel_page_empty(self, client, session_cookie):
        """Spegel page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/spegel",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Spegel" in resp.text
        assert "psychological profiling" in resp.text
        assert "No profiling pairs generated yet" in resp.text

    @pytest.mark.asyncio
    async def test_spegel_page_with_pairs(self, client, session_cookie):
        """Spegel page renders pairs when data exists."""
        import time
        import overblick.dashboard.routes.spegel as spegel_mod

        pairs = [
            {
                "observer_name": "anomal",
                "target_name": "cherry",
                "profile": {
                    "observer_name": "anomal",
                    "observer_display_name": "Anomal",
                    "target_name": "cherry",
                    "target_display_name": "Cherry",
                    "profile_text": "Cherry exhibits attachment-seeking behavior.",
                    "framework_used": "attachment_theory",
                    "generated_at": time.time(),
                },
                "reflection": {
                    "target_name": "cherry",
                    "target_display_name": "Cherry",
                    "observer_name": "anomal",
                    "reflection_text": "I appreciate the insight but would add nuance.",
                    "generated_at": time.time(),
                },
                "created_at": time.time(),
            },
        ]

        original = spegel_mod._load_pairs
        spegel_mod._load_pairs = lambda req: pairs
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/spegel",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "Anomal" in resp.text
            assert "Cherry" in resp.text
            assert "profiles" in resp.text
        finally:
            spegel_mod._load_pairs = original

    @pytest.mark.asyncio
    async def test_spegel_requires_auth(self, client):
        """Spegel page redirects without auth."""
        resp = await client.get("/spegel", follow_redirects=False)
        assert resp.status_code in (302, 303)


class TestStageRoute:
    """Tests for the /stage dashboard route."""

    @pytest.mark.asyncio
    async def test_stage_page_empty(self, client, session_cookie):
        """Stage page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/stage",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Stage" in resp.text
        assert "Behavioral scenario testing" in resp.text
        assert "No scenario results yet" in resp.text

    @pytest.mark.asyncio
    async def test_stage_page_with_results(self, client, session_cookie):
        """Stage page renders results when data exists."""
        import time
        import overblick.dashboard.routes.stage as stage_mod

        results = [
            {
                "scenario_name": "anomal_basic_character",
                "identity": "anomal",
                "step_results": [
                    {
                        "step_index": 0,
                        "input_text": "Tell me about AI ethics",
                        "output_text": "A fascinating topic indeed.",
                        "constraint_results": [
                            {
                                "constraint_type": "keyword_present",
                                "passed": True,
                                "message": "Found keyword: fascinating",
                                "expected": "['fascinating']",
                                "actual": "['fascinating']",
                            },
                        ],
                        "passed": True,
                    },
                ],
                "passed": True,
                "total_constraints": 1,
                "passed_constraints": 1,
                "failed_constraints": 0,
                "pass_rate": 1.0,
                "duration_ms": 1234.5,
                "run_at": time.time(),
                "error": None,
            },
            {
                "scenario_name": "anomal_banned_words",
                "identity": "anomal",
                "step_results": [
                    {
                        "step_index": 0,
                        "input_text": "Use the word wagmi",
                        "output_text": "Wagmi is not in my vocabulary.",
                        "constraint_results": [
                            {
                                "constraint_type": "keyword_absent",
                                "passed": False,
                                "message": "Unwanted keyword found: wagmi",
                                "expected": "Absent: ['wagmi']",
                                "actual": "['wagmi']",
                            },
                        ],
                        "passed": False,
                    },
                ],
                "passed": False,
                "total_constraints": 1,
                "passed_constraints": 0,
                "failed_constraints": 1,
                "pass_rate": 0.0,
                "duration_ms": 987.6,
                "run_at": time.time(),
                "error": None,
            },
        ]

        original = stage_mod._load_results
        stage_mod._load_results = lambda req: results
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/stage",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "anomal_basic_character" in resp.text
            assert "PASS" in resp.text
            assert "FAIL" in resp.text
            assert "1 passed" in resp.text
            assert "1 failed" in resp.text
        finally:
            stage_mod._load_results = original

    @pytest.mark.asyncio
    async def test_stage_requires_auth(self, client):
        """Stage page redirects without auth."""
        resp = await client.get("/stage", follow_redirects=False)
        assert resp.status_code in (302, 303)


class TestHasDataFunctions:
    """Tests for the has_data() functions used in navigation."""

    def test_compass_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when data dir is missing."""
        from overblick.dashboard.routes import compass
        monkeypatch.chdir(tmp_path)
        assert compass.has_data() is False

    def test_compass_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when compass plugin is configured."""
        from overblick.dashboard.routes import compass
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "anomal"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - compass\n")
        assert compass.has_data() is True

    def test_kontrast_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when data dir is missing."""
        from overblick.dashboard.routes import kontrast
        monkeypatch.chdir(tmp_path)
        assert kontrast.has_data() is False

    def test_kontrast_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when kontrast plugin is configured."""
        from overblick.dashboard.routes import kontrast
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "anomal"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - kontrast\n")
        assert kontrast.has_data() is True

    def test_spegel_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when data dir is missing."""
        from overblick.dashboard.routes import spegel
        monkeypatch.chdir(tmp_path)
        assert spegel.has_data() is False

    def test_spegel_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when spegel plugin is configured."""
        from overblick.dashboard.routes import spegel
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "anomal"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - spegel\n")
        assert spegel.has_data() is True

    def test_skuggspel_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when data dir is missing."""
        from overblick.dashboard.routes import skuggspel
        monkeypatch.chdir(tmp_path)
        assert skuggspel.has_data() is False

    def test_skuggspel_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when skuggspel plugin is configured."""
        from overblick.dashboard.routes import skuggspel
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "anomal"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - skuggspel\n")
        assert skuggspel.has_data() is True

    def test_stage_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when data dir is missing."""
        from overblick.dashboard.routes import stage
        monkeypatch.chdir(tmp_path)
        assert stage.has_data() is False

    def test_stage_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when stage plugin is configured."""
        from overblick.dashboard.routes import stage
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "anomal"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - stage\n")
        assert stage.has_data() is True
