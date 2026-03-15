from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OrchestratorPaths:
    base_dir: Path
    data_dir: Path
    log_dir: Path
    secrets_dir: Path

    @classmethod
    def for_identity(cls, base_dir: Path, identity_name: str) -> "OrchestratorPaths":
        return cls(
            base_dir=base_dir,
            data_dir=base_dir / "data" / identity_name,
            log_dir=base_dir / "logs" / identity_name,
            secrets_dir=base_dir / "config" / "secrets",
        )
