import json

from starlette.testclient import TestClient

from overblick.dashboard.app import create_app
from overblick.dashboard.config import DashboardConfig


def _write_nostradamus_fixture(base_dir):
    identity_dir = base_dir / "overblick" / "identities" / "nostradamus"
    identity_dir.mkdir(parents=True)
    (identity_dir / "personality.yaml").write_text(
        """
operational:
  plugins:
    - oracle
    - ai_digest
  oracle:
    topics:
      - name: russia_ukraine
        description: Russia-Ukraine conflict developments
        keywords: [russia, ukraine]
        market_slugs:
          - russia-x-ukraine-ceasefire-before-2027
      - name: iran_regime
        description: Iranian regime stability
        keywords: [iran, regime]
        market_slugs:
          - will-the-iranian-regime-fall-by-the-end-of-2026
    domain_experts:
      - name: geopolitics
        topics: [russia_ukraine, iran_regime]
    graph_edges:
      - from: russia_ukraine
        to: iran_regime
        weight: 0.4
        direction: positive
""",
    )

    data_dir = base_dir / "data" / "nostradamus"
    data_dir.mkdir(parents=True)
    (data_dir / "topic_tracker_state.json").write_text(
        json.dumps(
            {
                "russia_ukraine": {
                    "last_assessment": {
                        "probability": 0.24,
                        "confidence": 61,
                        "reasoning": "Test assessment",
                    }
                }
            }
        )
    )
    shared_dir = base_dir / "data" / "shared"
    shared_dir.mkdir(parents=True)
    (shared_dir / "oracle_health.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "agent": "nostradamus",
                "freshness": {
                    "degraded": True,
                    "degraded_reason": "test stale topic",
                    "oldest_topic_age_hours": 13.5,
                    "stale_topic_count": 1,
                },
            }
        )
    )

    pohlman_dir = base_dir / "data" / "pohlman"
    pohlman_dir.mkdir(parents=True)
    (pohlman_dir / "topic_tracker_state.json").write_text(
        json.dumps(
            {
                "weather_noise": {
                    "last_assessment": {
                        "probability": 0.9,
                        "confidence": 99,
                        "reasoning": "Must not leak into Oracle",
                    }
                }
            }
        )
    )


def _app(base_dir):
    return create_app(
        DashboardConfig(
            test_mode=True,
            base_dir=str(base_dir),
            secret_key="test-secret-key-for-polytrader-dashboard",
        )
    )


def test_oracle_api_uses_nostradamus_world_model(tmp_path):
    _write_nostradamus_fixture(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/oracle/topics")

    assert response.status_code == 200
    data = response.json()
    topic_ids = {topic["topic_id"] for topic in data["topics"]}
    topics = {topic["topic_id"]: topic for topic in data["topics"]}

    assert topic_ids == {"russia_ukraine", "iran_regime"}
    assert data["stats"]["total_topics"] == 2
    assert data["categories"] == ["geopolitics"]
    assert topics["russia_ukraine"]["category"] == "geopolitics"
    assert data["graph_edges"][0]["source"] == "russia_ukraine"
    assert data["graph_edges"][0]["target"] == "iran_regime"
    assert data["stats"]["freshness"]["degraded"] is True
    assert "weather_noise" not in topic_ids


def test_polymarket_page_has_no_inline_onclick_handlers(tmp_path):
    _write_nostradamus_fixture(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/polymarket")

    assert response.status_code == 200
    assert 'onclick="' not in response.text
    assert "onclick='" not in response.text


def test_oracle_page_has_no_inline_script_or_onclick(tmp_path):
    _write_nostradamus_fixture(tmp_path)

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/oracle")

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert 'onclick="' not in response.text
    assert "onclick='" not in response.text
