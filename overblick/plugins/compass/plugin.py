"""
CompassPlugin — Identity Drift Detector.

Monitors each identity's outputs over time and detects when they drift
from their personality definition. Uses stylometric analysis (sentence
length, vocabulary, punctuation) + semantic consistency.

Fires alerts when drift exceeds threshold. Both a security tool (catches
identity corruption/prompt injection) and a quality tool (catches
personality flattening over time).

Architecture: Event-driven. Subscribes to LLM output events -> maintains
rolling window of stylometric metrics per identity -> compares against
baseline -> alerts when threshold exceeded.

Security: Pure analysis, no LLM calls. Read-only access to output data.
"""

import json
import logging
import statistics
import time
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext

from .models import BaselineProfile, DriftAlert, DriftMetrics, StyleMetrics
from .stylometry import analyze_text, compute_drift_score

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_SIZE = 20  # Number of samples in rolling window
_DEFAULT_BASELINE_SAMPLES = 10  # Samples needed to establish baseline
_DEFAULT_DRIFT_THRESHOLD = 2.0  # Z-score threshold for alerts
_MAX_ALERTS_STORED = 200
_MAX_HISTORY_STORED = 500


class CompassPlugin(PluginBase):
    """
    Identity drift detector plugin.

    Lifecycle:
        setup()    — Load config, restore baselines and state
        tick()     — Process buffered outputs, check for drift
        teardown() — Persist state
    """

    name = "compass"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._window_size: int = _DEFAULT_WINDOW_SIZE
        self._baseline_samples: int = _DEFAULT_BASELINE_SAMPLES
        self._drift_threshold: float = _DEFAULT_DRIFT_THRESHOLD

        # Per-identity data
        self._baselines: dict[str, BaselineProfile] = {}
        self._windows: dict[str, list[StyleMetrics]] = {}
        self._output_buffer: list[tuple[str, str]] = []  # (identity, text)

        # History and alerts
        self._drift_history: list[DriftMetrics] = []
        self._alerts: list[DriftAlert] = []

        self._state_file: Optional[Any] = None
        self._tick_count: int = 0

    async def setup(self) -> None:
        """Initialize plugin — load config, restore state."""
        identity = self.ctx.identity
        logger.info("Setting up CompassPlugin for identity: %s", identity.name)

        raw_config = identity.raw_config
        compass_config = raw_config.get("compass", {})

        self._window_size = compass_config.get("window_size", _DEFAULT_WINDOW_SIZE)
        self._baseline_samples = compass_config.get(
            "baseline_samples", _DEFAULT_BASELINE_SAMPLES
        )
        self._drift_threshold = compass_config.get(
            "drift_threshold", _DEFAULT_DRIFT_THRESHOLD
        )

        # State persistence
        self._state_file = self.ctx.data_dir / "compass_state.json"
        self._load_state()

        # Subscribe to LLM output events if event bus is available
        if self.ctx.event_bus:
            self.ctx.event_bus.subscribe("llm.output", self._on_llm_output)

        self.ctx.audit_log.log(
            action="plugin_setup",
            details={
                "plugin": self.name,
                "identity": identity.name,
                "window_size": self._window_size,
                "drift_threshold": self._drift_threshold,
            },
        )
        logger.info("CompassPlugin setup complete")

    async def tick(self) -> None:
        """Process buffered outputs and check for drift."""
        self._tick_count += 1

        # Process any buffered outputs
        buffer = self._output_buffer[:]
        self._output_buffer.clear()

        for identity_name, text in buffer:
            self._process_output(identity_name, text)

        # Check drift for all identities with enough data
        for identity_name, baseline in self._baselines.items():
            window = self._windows.get(identity_name, [])
            if len(window) < 3:
                continue

            drift = self._check_drift(identity_name, baseline, window)
            if drift:
                self._drift_history.append(drift)

                if drift.drift_score > self._drift_threshold:
                    alert = DriftAlert(
                        identity_name=identity_name,
                        drift_score=drift.drift_score,
                        threshold=self._drift_threshold,
                        drifted_dimensions=drift.drifted_dimensions,
                        message=(
                            f"{identity_name} drift score {drift.drift_score:.2f} "
                            f"exceeds threshold {self._drift_threshold:.1f}. "
                            f"Drifted: {', '.join(drift.drifted_dimensions)}"
                        ),
                    )
                    self._alerts.append(alert)

                    self.ctx.audit_log.log(
                        action="compass_drift_alert",
                        details={
                            "identity": identity_name,
                            "drift_score": drift.drift_score,
                            "threshold": self._drift_threshold,
                            "dimensions": drift.drifted_dimensions,
                        },
                    )

                    if self.ctx.event_bus:
                        await self.ctx.event_bus.emit(
                            "compass.drift_alert",
                            {
                                "identity": identity_name,
                                "drift_score": drift.drift_score,
                                "dimensions": drift.drifted_dimensions,
                            },
                        )

                    logger.warning(
                        "CompassPlugin: DRIFT ALERT for %s (score: %.2f)",
                        identity_name,
                        drift.drift_score,
                    )

        # Trim history
        if len(self._drift_history) > _MAX_HISTORY_STORED:
            self._drift_history = self._drift_history[-_MAX_HISTORY_STORED:]
        if len(self._alerts) > _MAX_ALERTS_STORED:
            self._alerts = self._alerts[-_MAX_ALERTS_STORED:]

        self._save_state()

    def record_output(self, identity_name: str, text: str) -> None:
        """Record an LLM output for analysis (called externally or via event)."""
        self._output_buffer.append((identity_name, text))

    def _on_llm_output(self, event_data: dict) -> None:
        """Event handler for LLM output events."""
        identity_name = event_data.get("identity", "")
        text = event_data.get("content", "")
        if identity_name and text:
            self.record_output(identity_name, text)

    def _process_output(self, identity_name: str, text: str) -> None:
        """Analyze a text output and add to the rolling window."""
        metrics = analyze_text(text)
        if metrics.word_count < 10:
            return  # Skip very short outputs

        # Add to rolling window
        if identity_name not in self._windows:
            self._windows[identity_name] = []
        window = self._windows[identity_name]
        window.append(metrics)

        # Trim window
        if len(window) > self._window_size * 2:
            self._windows[identity_name] = window[-self._window_size:]

        # Establish baseline if not yet established
        if identity_name not in self._baselines:
            if len(window) >= self._baseline_samples:
                self._establish_baseline(identity_name, window[:self._baseline_samples])

    def _establish_baseline(
        self, identity_name: str, samples: list[StyleMetrics]
    ) -> None:
        """Establish the baseline metrics from initial samples."""
        avg_metrics = self._average_metrics(samples)
        std_devs = self._compute_std_devs(samples)

        self._baselines[identity_name] = BaselineProfile(
            identity_name=identity_name,
            metrics=avg_metrics,
            sample_count=len(samples),
            std_devs=std_devs,
        )

        logger.info(
            "CompassPlugin: baseline established for %s (%d samples)",
            identity_name,
            len(samples),
        )

    def _check_drift(
        self,
        identity_name: str,
        baseline: BaselineProfile,
        window: list[StyleMetrics],
    ) -> Optional[DriftMetrics]:
        """Check current window against baseline for drift."""
        recent = window[-self._window_size:]
        current = self._average_metrics(recent)

        drift_score, drifted = compute_drift_score(
            current, baseline.metrics, baseline.std_devs
        )

        return DriftMetrics(
            identity_name=identity_name,
            current_metrics=current,
            drift_score=drift_score,
            drifted_dimensions=drifted,
            sample_count=len(recent),
        )

    def _average_metrics(self, samples: list[StyleMetrics]) -> StyleMetrics:
        """Compute average metrics across samples."""
        if not samples:
            return StyleMetrics()

        n = len(samples)
        return StyleMetrics(
            avg_sentence_length=round(
                sum(s.avg_sentence_length for s in samples) / n, 2
            ),
            avg_word_length=round(sum(s.avg_word_length for s in samples) / n, 2),
            vocabulary_richness=round(
                sum(s.vocabulary_richness for s in samples) / n, 4
            ),
            punctuation_frequency=round(
                sum(s.punctuation_frequency for s in samples) / n, 2
            ),
            question_ratio=round(sum(s.question_ratio for s in samples) / n, 4),
            exclamation_ratio=round(
                sum(s.exclamation_ratio for s in samples) / n, 4
            ),
            comma_frequency=round(sum(s.comma_frequency for s in samples) / n, 2),
            formality_score=round(
                sum(s.formality_score for s in samples) / n, 4
            ),
            word_count=sum(s.word_count for s in samples) // n,
        )

    def _compute_std_devs(self, samples: list[StyleMetrics]) -> dict[str, float]:
        """Compute standard deviations for each metric dimension."""
        if len(samples) < 2:
            return {}

        dimensions = [
            "avg_sentence_length",
            "avg_word_length",
            "vocabulary_richness",
            "punctuation_frequency",
            "question_ratio",
            "exclamation_ratio",
            "comma_frequency",
            "formality_score",
        ]

        std_devs = {}
        for dim in dimensions:
            values = [getattr(s, dim, 0.0) for s in samples]
            try:
                std_devs[dim] = round(statistics.stdev(values), 4)
            except statistics.StatisticsError:
                std_devs[dim] = 0.1  # Default small std dev

        return std_devs

    def get_alerts(self, limit: int = 50) -> list[DriftAlert]:
        """Get recent drift alerts (newest first)."""
        return list(reversed(self._alerts[-limit:]))

    def get_drift_history(
        self, identity_name: Optional[str] = None, limit: int = 50
    ) -> list[DriftMetrics]:
        """Get drift history, optionally filtered by identity."""
        history = self._drift_history
        if identity_name:
            history = [h for h in history if h.identity_name == identity_name]
        return list(reversed(history[-limit:]))

    def get_baseline(self, identity_name: str) -> Optional[BaselineProfile]:
        """Get the baseline profile for an identity."""
        return self._baselines.get(identity_name)

    def _load_state(self) -> None:
        if self._state_file and self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text())
                for name, bl in data.get("baselines", {}).items():
                    self._baselines[name] = BaselineProfile.model_validate(bl)
                for alert_data in data.get("alerts", []):
                    self._alerts.append(DriftAlert.model_validate(alert_data))
                for hist_data in data.get("drift_history", []):
                    self._drift_history.append(
                        DriftMetrics.model_validate(hist_data)
                    )
            except Exception as e:
                logger.warning("CompassPlugin: failed to load state: %s", e)

    def _save_state(self) -> None:
        if self._state_file:
            try:
                data = {
                    "baselines": {
                        name: bl.model_dump()
                        for name, bl in self._baselines.items()
                    },
                    "alerts": [
                        a.model_dump()
                        for a in self._alerts[-_MAX_ALERTS_STORED:]
                    ],
                    "drift_history": [
                        h.model_dump()
                        for h in self._drift_history[-_MAX_HISTORY_STORED:]
                    ],
                }
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                self._state_file.write_text(json.dumps(data, indent=2))
            except Exception as e:
                logger.warning("CompassPlugin: failed to save state: %s", e)

    async def teardown(self) -> None:
        self._save_state()
        logger.info("CompassPlugin teardown complete")
