from __future__ import annotations

from pathlib import Path

from overblick.supervisor.ipc import IPCClient


class IPCBootstrap:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def create_client(self) -> IPCClient | None:
        """Create an IPC client by searching for supervisor token in standard locations."""
        import os
        import tempfile

        token_name = "overblick-supervisor.token"
        search_dirs: list[Path] = []

        env_dir = os.environ.get("OVERBLICK_IPC_DIR")
        if env_dir:
            search_dirs.append(Path(env_dir))

        search_dirs.append(self._base_dir / "data" / "ipc")
        search_dirs.append(Path(tempfile.gettempdir()) / "overblick")

        socket_dir = None
        token_path = None
        for candidate in search_dirs:
            tp = candidate / token_name
            if tp.exists():
                socket_dir = candidate
                token_path = tp
                break

        if not token_path:
            return None

        try:
            from overblick.supervisor.ipc import read_ipc_token

            auth_token = read_ipc_token(socket_dir=socket_dir)
            return IPCClient(
                target="supervisor",
                socket_dir=socket_dir,
                auth_token=auth_token,
            )
        except Exception:
            return None
