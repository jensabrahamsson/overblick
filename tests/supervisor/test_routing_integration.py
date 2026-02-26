"""
Tests for MessageRouter integration with Supervisor IPC.

Verifies that:
- Supervisor creates and exposes a MessageRouter
- Agents are registered/unregistered on start/stop
- route_message IPC handler routes messages correctly
- collect_messages IPC handler delivers pending messages
- PluginContext.send_to_agent() works end-to-end
- PluginContext.collect_messages() works end-to-end
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.plugin_base import PluginContext
from overblick.supervisor.ipc import IPCMessage
from overblick.supervisor.routing import MessageRouter, RouteStatus
from overblick.supervisor.supervisor import Supervisor


@pytest.fixture
def short_tmp():
    """Short temp directory for Unix sockets (macOS has 104-char path limit)."""
    d = Path(tempfile.mkdtemp(prefix="bk"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Supervisor MessageRouter integration
# ---------------------------------------------------------------------------

class TestSupervisorRouterInit:
    """Supervisor creates and wires the MessageRouter."""

    def test_supervisor_has_message_router(self, short_tmp):
        """Supervisor initializes a MessageRouter in __init__."""
        sup = Supervisor(
            identities=["anomal", "cherry"],
            socket_dir=short_tmp,
            base_dir=short_tmp,
        )
        assert isinstance(sup.message_router, MessageRouter)

    def test_router_stats_in_status(self, short_tmp):
        """get_status() includes routing stats."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        status = sup.get_status()
        assert "routing" in status
        assert "total_routed" in status["routing"]

    def test_router_shared_audit_log(self, short_tmp):
        """Router uses the same audit log as the supervisor."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        assert sup.message_router._audit is sup._audit_log


class TestSupervisorAgentRegistration:
    """Agents are registered/unregistered with the router on start/stop."""

    @pytest.mark.asyncio
    async def test_start_agent_registers_with_router(self, short_tmp):
        """start_agent() registers the agent identity with MessageRouter."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)

        with patch.object(sup, "_ipc", MagicMock()):
            # Mock AgentProcess.start to succeed without subprocess
            with patch(
                "overblick.supervisor.supervisor.AgentProcess"
            ) as MockAgent:
                mock_agent = MagicMock()
                mock_agent.start = AsyncMock(return_value=True)
                mock_agent.monitor = AsyncMock()  # Prevent monitor task errors
                mock_agent.state = "running"
                MockAgent.return_value = mock_agent

                await sup.start_agent("anomal")

        assert "anomal" in sup.message_router._capabilities

    @pytest.mark.asyncio
    async def test_stop_agent_unregisters_from_router(self, short_tmp):
        """stop_agent() unregisters the agent from MessageRouter."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)

        # Manually register and add a mock agent
        sup.message_router.register_agent("anomal")
        mock_agent = MagicMock()
        mock_agent.stop = AsyncMock(return_value=True)
        sup._agents["anomal"] = mock_agent

        await sup.stop_agent("anomal")

        assert "anomal" not in sup.message_router._capabilities

    @pytest.mark.asyncio
    async def test_multiple_agents_registered(self, short_tmp):
        """Multiple agents can be registered simultaneously."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)

        with patch.object(sup, "_ipc", MagicMock()):
            with patch(
                "overblick.supervisor.supervisor.AgentProcess"
            ) as MockAgent:
                mock_agent = MagicMock()
                mock_agent.start = AsyncMock(return_value=True)
                mock_agent.monitor = AsyncMock()  # Prevent monitor task errors
                mock_agent.state = "running"
                MockAgent.return_value = mock_agent

                await sup.start_agent("anomal")
                await sup.start_agent("cherry")
                await sup.start_agent("vakt")

        assert len(sup.message_router._capabilities) == 3
        stats = sup.message_router.get_stats()
        assert stats["registered_agents"] == 3


# ---------------------------------------------------------------------------
# IPC Handlers
# ---------------------------------------------------------------------------

class TestRouteMessageHandler:
    """Tests for _handle_route_message IPC handler."""

    @pytest.mark.asyncio
    async def test_route_success(self, short_tmp):
        """Successful routing returns success=True with message ID."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("smed")

        msg = IPCMessage(
            msg_type="route_message",
            sender="vakt",
            payload={
                "target": "smed",
                "message_type": "bug_report",
                "data": {"bug": "LLM timeout in pipeline.py"},
            },
        )

        response = await sup._handle_route_message(msg)

        assert response is not None
        assert response.payload["success"] is True
        assert response.payload["status"] == "pending"
        assert response.payload["message_id"].startswith("route-")

    @pytest.mark.asyncio
    async def test_route_unknown_target(self, short_tmp):
        """Routing to an unknown agent returns dead_letter status."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)

        msg = IPCMessage(
            msg_type="route_message",
            sender="vakt",
            payload={
                "target": "nonexistent",
                "message_type": "alert",
                "data": {},
            },
        )

        response = await sup._handle_route_message(msg)

        assert response is not None
        assert response.payload["success"] is False
        assert response.payload["status"] == "dead_letter"

    @pytest.mark.asyncio
    async def test_route_missing_target(self, short_tmp):
        """Missing target in payload returns error."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)

        msg = IPCMessage(
            msg_type="route_message",
            sender="vakt",
            payload={"message_type": "alert"},
        )

        response = await sup._handle_route_message(msg)

        assert response is not None
        assert response.payload["success"] is False
        assert "Missing" in response.payload["error"]

    @pytest.mark.asyncio
    async def test_route_missing_message_type(self, short_tmp):
        """Missing message_type in payload returns error."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)

        msg = IPCMessage(
            msg_type="route_message",
            sender="vakt",
            payload={"target": "smed"},
        )

        response = await sup._handle_route_message(msg)

        assert response is not None
        assert response.payload["success"] is False

    @pytest.mark.asyncio
    async def test_route_with_ttl(self, short_tmp):
        """Custom TTL is passed through to the router."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("smed")

        msg = IPCMessage(
            msg_type="route_message",
            sender="vakt",
            payload={
                "target": "smed",
                "message_type": "alert",
                "data": {"level": "critical"},
                "ttl_seconds": 60.0,
            },
        )

        await sup._handle_route_message(msg)

        pending = sup.message_router._pending
        assert len(pending) == 1
        assert pending[0].ttl_seconds == 60.0


class TestCollectMessagesHandler:
    """Tests for _handle_collect_messages IPC handler."""

    @pytest.mark.asyncio
    async def test_collect_returns_pending_messages(self, short_tmp):
        """Collecting returns all pending messages for the agent."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("smed")
        sup.message_router.register_agent("vakt")

        # Route two messages to smed
        sup.message_router.route("vakt", "smed", "bug_report", {"id": 1})
        sup.message_router.route("vakt", "smed", "log_alert", {"id": 2})

        msg = IPCMessage(
            msg_type="collect_messages",
            sender="smed",
            payload={},
        )

        response = await sup._handle_collect_messages(msg)

        assert response is not None
        assert response.payload["count"] == 2
        messages = response.payload["messages"]
        assert len(messages) == 2
        assert messages[0]["source_agent"] == "vakt"
        assert messages[0]["message_type"] == "bug_report"
        assert messages[1]["message_type"] == "log_alert"

    @pytest.mark.asyncio
    async def test_collect_empty_when_no_messages(self, short_tmp):
        """Collecting when no messages are pending returns empty."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("smed")

        msg = IPCMessage(
            msg_type="collect_messages",
            sender="smed",
            payload={},
        )

        response = await sup._handle_collect_messages(msg)

        assert response is not None
        assert response.payload["count"] == 0
        assert response.payload["messages"] == []

    @pytest.mark.asyncio
    async def test_collect_removes_from_pending(self, short_tmp):
        """Collected messages are removed from the pending queue."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("smed")
        sup.message_router.register_agent("vakt")

        sup.message_router.route("vakt", "smed", "alert", {"x": 1})
        assert sup.message_router.get_pending_count("smed") == 1

        msg = IPCMessage(
            msg_type="collect_messages",
            sender="smed",
            payload={},
        )
        await sup._handle_collect_messages(msg)

        assert sup.message_router.get_pending_count("smed") == 0

    @pytest.mark.asyncio
    async def test_collect_does_not_affect_other_agents(self, short_tmp):
        """Collecting for one agent does not touch another agent's messages."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("smed")
        sup.message_router.register_agent("vakt")
        sup.message_router.register_agent("anomal")

        sup.message_router.route("anomal", "smed", "fix_request", {})
        sup.message_router.route("anomal", "vakt", "log_request", {})

        msg = IPCMessage(msg_type="collect_messages", sender="smed", payload={})
        await sup._handle_collect_messages(msg)

        # smed's message collected, vakt's still pending
        assert sup.message_router.get_pending_count("smed") == 0
        assert sup.message_router.get_pending_count("vakt") == 1


# ---------------------------------------------------------------------------
# PluginContext helpers
# ---------------------------------------------------------------------------

class TestPluginContextSendToAgent:
    """Tests for PluginContext.send_to_agent()."""

    @pytest.mark.asyncio
    async def test_send_to_agent_sends_ipc_message(self, tmp_path):
        """send_to_agent() constructs and sends the correct IPC message."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=IPCMessage(
            msg_type="route_response",
            sender="supervisor",
            payload={"success": True, "message_id": "route-000001", "status": "pending", "error": None},
        ))

        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            ipc_client=mock_ipc,
        )

        result = await ctx.send_to_agent(
            target="smed",
            message_type="bug_report",
            payload={"bug": "timeout"},
        )

        assert result is not None
        assert result["success"] is True
        assert result["message_id"] == "route-000001"

        # Verify the IPC message was constructed correctly
        sent_msg = mock_ipc.send.call_args[0][0]
        assert sent_msg.msg_type == "route_message"
        assert sent_msg.sender == "vakt"
        assert sent_msg.payload["target"] == "smed"
        assert sent_msg.payload["message_type"] == "bug_report"
        assert sent_msg.payload["data"]["bug"] == "timeout"

    @pytest.mark.asyncio
    async def test_send_to_agent_no_ipc_returns_none(self, tmp_path):
        """send_to_agent() returns None when no IPC client is available."""
        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            ipc_client=None,
        )

        result = await ctx.send_to_agent("smed", "bug_report")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_to_agent_custom_ttl(self, tmp_path):
        """send_to_agent() passes custom TTL in the payload."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=IPCMessage(
            msg_type="route_response",
            sender="supervisor",
            payload={"success": True, "message_id": "route-000001", "status": "pending", "error": None},
        ))

        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            ipc_client=mock_ipc,
        )

        await ctx.send_to_agent("smed", "alert", ttl_seconds=60.0)

        sent_msg = mock_ipc.send.call_args[0][0]
        assert sent_msg.payload["ttl_seconds"] == 60.0

    @pytest.mark.asyncio
    async def test_send_to_agent_timeout_returns_none(self, tmp_path):
        """send_to_agent() returns None when IPC times out."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=None)

        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            ipc_client=mock_ipc,
        )

        result = await ctx.send_to_agent("smed", "bug_report")
        assert result is None


class TestPluginContextCollectMessages:
    """Tests for PluginContext.collect_messages()."""

    @pytest.mark.asyncio
    async def test_collect_messages_returns_list(self, tmp_path):
        """collect_messages() returns a list of message dicts."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=IPCMessage(
            msg_type="collect_response",
            sender="supervisor",
            payload={
                "messages": [
                    {
                        "message_id": "route-000001",
                        "source_agent": "anomal",
                        "message_type": "status_update",
                        "payload": {"status": "healthy"},
                        "created_at": 1740556800.0,
                    },
                ],
                "count": 1,
            },
        ))

        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            ipc_client=mock_ipc,
        )

        messages = await ctx.collect_messages()

        assert len(messages) == 1
        assert messages[0]["source_agent"] == "anomal"
        assert messages[0]["message_type"] == "status_update"

    @pytest.mark.asyncio
    async def test_collect_messages_no_ipc_returns_empty(self, tmp_path):
        """collect_messages() returns empty list when no IPC client."""
        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            ipc_client=None,
        )

        messages = await ctx.collect_messages()
        assert messages == []

    @pytest.mark.asyncio
    async def test_collect_messages_timeout_returns_empty(self, tmp_path):
        """collect_messages() returns empty list on IPC timeout."""
        mock_ipc = AsyncMock()
        mock_ipc.send = AsyncMock(return_value=None)

        ctx = PluginContext(
            identity_name="vakt",
            data_dir=tmp_path / "data" / "vakt",
            log_dir=tmp_path / "logs" / "vakt",
            ipc_client=mock_ipc,
        )

        messages = await ctx.collect_messages()
        assert messages == []


# ---------------------------------------------------------------------------
# End-to-end scenario
# ---------------------------------------------------------------------------

class TestEndToEndRouting:
    """End-to-end: route through supervisor, collect at target."""

    @pytest.mark.asyncio
    async def test_vakt_alerts_smed_about_bug(self, short_tmp):
        """Vakt routes a bug report to Smed via supervisor."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("vakt")
        sup.message_router.register_agent("smed")

        # Vakt sends bug report
        route_msg = IPCMessage(
            msg_type="route_message",
            sender="vakt",
            payload={
                "target": "smed",
                "message_type": "bug_report",
                "data": {
                    "identity": "anomal",
                    "error": "LLM timeout in pipeline.py",
                    "severity": "high",
                },
            },
        )
        route_response = await sup._handle_route_message(route_msg)
        assert route_response.payload["success"] is True

        # Smed collects messages
        collect_msg = IPCMessage(
            msg_type="collect_messages",
            sender="smed",
            payload={},
        )
        collect_response = await sup._handle_collect_messages(collect_msg)

        assert collect_response.payload["count"] == 1
        bug = collect_response.payload["messages"][0]
        assert bug["source_agent"] == "vakt"
        assert bug["message_type"] == "bug_report"
        assert bug["payload"]["identity"] == "anomal"
        assert "LLM timeout" in bug["payload"]["error"]

    @pytest.mark.asyncio
    async def test_smed_notifies_github_after_pr(self, short_tmp):
        """Smed notifies GitHub agent about a new PR."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("smed")
        sup.message_router.register_agent("blixt")

        route_msg = IPCMessage(
            msg_type="route_message",
            sender="smed",
            payload={
                "target": "blixt",
                "message_type": "pr_created",
                "data": {"repo": "jensabrahamsson/overblick", "pr_number": 42},
            },
        )
        route_response = await sup._handle_route_message(route_msg)
        assert route_response.payload["success"] is True

        # Blixt collects
        collect_msg = IPCMessage(
            msg_type="collect_messages",
            sender="blixt",
            payload={},
        )
        collect_response = await sup._handle_collect_messages(collect_msg)

        assert collect_response.payload["count"] == 1
        pr_msg = collect_response.payload["messages"][0]
        assert pr_msg["payload"]["pr_number"] == 42

    @pytest.mark.asyncio
    async def test_routing_stats_update_after_messages(self, short_tmp):
        """Routing stats reflect message activity."""
        sup = Supervisor(identities=[], socket_dir=short_tmp, base_dir=short_tmp)
        sup.message_router.register_agent("vakt")
        sup.message_router.register_agent("smed")

        # Route 3 messages
        for i in range(3):
            msg = IPCMessage(
                msg_type="route_message",
                sender="vakt",
                payload={
                    "target": "smed",
                    "message_type": "alert",
                    "data": {"seq": i},
                },
            )
            await sup._handle_route_message(msg)

        status = sup.get_status()
        routing = status["routing"]
        assert routing["total_routed"] == 3
        assert routing["pending"] == 3
