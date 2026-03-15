from __future__ import annotations

from pathlib import Path

from overblick.supervisor.ipc import IPCClient


class IPCBootstrap:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def create_client(self) -> IPCClient | None:
        """
        Create an IPC client by searching for supervisor token in standard locations.

        Search order:
          1. Environment variable (SUPERVISOR_TOKEN_PATH)
          2. Project config directory (config/supervisor_token)
          3. System temp directory (fallback)
        """
        import os
        from pathlib import Path

        # 1. Try environment variable
        env_path = os.environ.get("SUPERVISOR_TOKEN_PATH")
        if env_path and Path(env_path).exists():
            return IPCClient.from_token_file(Path(env_path))

        # 2. Try project config
        proj_path = self._base_dir / "config" / "supervisor_token"
        if proj_path.exists():
            return IPCClient.from_token_file(proj_path)

        # 3. Try temp directory
        try:
            import tempfile

            tmp_path = Path(tempfile.gettempdir()) / "overblick_supervisor_token"
            if tmp_path.exists():
                return IPCClient.from_token_file(tmp_path)
        except Exception:
            pass

        return None
