"""
Debug dashboard plugin cards issue.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from overblick.dashboard.app import create_app
from overblick.config import load_config


async def main():
    """Debug dashboard data."""
    config = load_config()
    app = create_app(config)

    # Access services from app state
    identity_svc = app.state.identity_service
    supervisor_svc = app.state.supervisor_service

    # Get data
    identities = identity_svc.get_all_identities()
    agents = await supervisor_svc.get_agents()

    print("=" * 80)
    print(f"IDENTITIES ({len(identities)}):")
    print("=" * 80)
    for identity in identities:
        print(f"\nIdentity: {identity['name']}")
        print(f"  Display name: {identity.get('display_name', 'N/A')}")
        print(f"  Plugins: {identity.get('plugins', [])}")
        print(f"  Capabilities: {identity.get('capability_names', [])}")

    print("\n" + "=" * 80)
    print(f"AGENTS ({len(agents)}):")
    print("=" * 80)
    for agent in agents:
        print(f"\nAgent: {agent.get('name', 'N/A')}")
        print(f"  State: {agent.get('state', 'N/A')}")
        print(f"  PID: {agent.get('pid', 'N/A')}")

    # Now let's see what _build_plugin_cards returns
    from overblick.dashboard.routes.dashboard import _build_plugin_cards
    plugin_cards = _build_plugin_cards(identities, agents)

    print("\n" + "=" * 80)
    print(f"PLUGIN CARDS ({len(plugin_cards)}):")
    print("=" * 80)
    if not plugin_cards:
        print(">>> NO PLUGIN CARDS! <<<")
        print("\nDEBUG: Checking why...")

        has_plugins = any(identity.get("plugins") for identity in identities)
        print(f"  Any identity has plugins? {has_plugins}")

        if not has_plugins:
            print("\n  >>> ROOT CAUSE: No identity has 'plugins' field set!")
            print("  The plugin cards are built from identity.plugins[]")
            print("  If all identities have empty/missing plugins, no cards are shown.")
    else:
        for card in plugin_cards:
            print(f"\nPlugin: {card['name']}")
            print(f"  Display: {card['display_name']}")
            print(f"  Agents: {card['agent_count']}")
            print(f"  Running: {card['running_count']}")


if __name__ == "__main__":
    asyncio.run(main())
