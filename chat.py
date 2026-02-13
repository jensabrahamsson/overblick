#!/usr/bin/env python3
"""
Interactive chat CLI for Överblick personalities.

Chat with any personality from the stable using local Ollama.

Usage:
    python chat.py                  # Pick from a menu
    python chat.py natt             # Chat directly with Natt
    python chat.py bjork --model qwen3:8b
    python chat.py --list           # List available personalities

Commands during chat:
    /switch <name>   Switch to another personality
    /reset           Clear conversation history
    /system          Show the current system prompt
    /personas        List available personalities
    /quit            Exit (or Ctrl+C / Ctrl+D)
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent))

from overblick.core.llm.ollama_client import OllamaClient
from overblick.personalities import (
    build_system_prompt,
    list_personalities,
    load_personality,
)

# Terminal colors
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"


def print_banner(name: str, display_name: str) -> None:
    """Print a welcome banner for the personality."""
    print(f"\n{BOLD}{CYAN}{'─' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  Överblick Chat — {display_name}{RESET}")
    print(f"{DIM}  Type /quit to exit, /switch <name> to change personality{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 60}{RESET}\n")


def print_personas() -> None:
    """List available personalities."""
    names = list_personalities()
    print(f"\n{BOLD}Available personalities:{RESET}")
    for name in sorted(names):
        p = load_personality(name)
        desc = ""
        if p.identity_info:
            desc = p.identity_info.get("description", "")
        if not desc and p.voice:
            desc = p.voice.get("base_tone", "")
        print(f"  {GREEN}{name:10s}{RESET}  {DIM}{desc[:60]}{RESET}")
    print()


def pick_personality() -> str:
    """Interactive personality picker."""
    names = sorted(list_personalities())
    print(f"\n{BOLD}Pick a personality:{RESET}\n")
    for i, name in enumerate(names, 1):
        p = load_personality(name)
        desc = ""
        if p.identity_info:
            desc = p.identity_info.get("role", "")
        print(f"  {YELLOW}{i}{RESET}. {GREEN}{name:10s}{RESET}  {DIM}{desc[:50]}{RESET}")

    print()
    while True:
        try:
            choice = input(f"{BOLD}Enter number or name: {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if choice in names:
            return choice
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(names):
                return names[idx]
        except ValueError:
            pass
        print(f"{RED}  Unknown choice. Try again.{RESET}")


async def check_ollama(client: OllamaClient) -> bool:
    """Check that Ollama is reachable."""
    ok = await client.health_check()
    if not ok:
        print(f"{RED}Ollama is not running or model not found.{RESET}")
        print(f"{DIM}Start it with: ollama serve{RESET}")
        print(f"{DIM}Pull model with: ollama pull qwen3:8b{RESET}")
    return ok


async def chat_loop(
    personality_name: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> None:
    """Main chat loop."""
    personality = load_personality(personality_name)
    system_prompt = build_system_prompt(personality, platform="CLI")
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    client = OllamaClient(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=300,
    )

    if not await check_ollama(client):
        await client.close()
        return

    print_banner(personality.name, personality.display_name)

    try:
        while True:
            try:
                user_input = input(f"{BOLD}{GREEN}You:{RESET} ").strip()
            except EOFError:
                print()
                break

            if not user_input:
                continue

            # --- Slash commands ---
            if user_input.startswith("/"):
                cmd_parts = user_input.split(maxsplit=1)
                cmd = cmd_parts[0].lower()

                if cmd in ("/quit", "/exit", "/q"):
                    break

                elif cmd == "/switch":
                    if len(cmd_parts) < 2:
                        print(f"{RED}  Usage: /switch <name>{RESET}")
                        continue
                    new_name = cmd_parts[1].strip()
                    try:
                        personality = load_personality(new_name)
                        personality_name = personality.name
                        system_prompt = build_system_prompt(personality, platform="CLI")
                        messages = [{"role": "system", "content": system_prompt}]
                        print_banner(personality.name, personality.display_name)
                    except Exception as e:
                        print(f"{RED}  Could not load '{new_name}': {e}{RESET}")
                    continue

                elif cmd == "/reset":
                    messages = [{"role": "system", "content": system_prompt}]
                    print(f"{DIM}  Conversation cleared.{RESET}")
                    continue

                elif cmd == "/system":
                    print(f"\n{DIM}{system_prompt}{RESET}\n")
                    continue

                elif cmd in ("/personas", "/list"):
                    print_personas()
                    continue

                elif cmd == "/help":
                    print(f"""
{BOLD}Commands:{RESET}
  {YELLOW}/switch <name>{RESET}   Switch personality
  {YELLOW}/reset{RESET}           Clear conversation
  {YELLOW}/system{RESET}          Show system prompt
  {YELLOW}/personas{RESET}        List personalities
  {YELLOW}/quit{RESET}            Exit
""")
                    continue
                else:
                    print(f"{DIM}  Unknown command. Type /help{RESET}")
                    continue

            # --- Send to LLM ---
            messages.append({"role": "user", "content": user_input})

            print(f"\n{BOLD}{MAGENTA}{personality.display_name}:{RESET} ", end="", flush=True)

            result = await client.chat(messages=messages)

            if result is None:
                print(f"{RED}(no response — LLM error){RESET}\n")
                messages.pop()  # Remove failed user message
                continue

            content = result["content"]
            print(f"{content}\n")

            messages.append({"role": "assistant", "content": content})

    except KeyboardInterrupt:
        print(f"\n{DIM}Interrupted.{RESET}")
    finally:
        await client.close()

    print(f"\n{DIM}Goodbye.{RESET}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chat with Överblick personalities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "personality",
        nargs="?",
        help="Personality name (e.g. natt, bjork, blixt). Omit for interactive picker.",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available personalities and exit",
    )
    parser.add_argument(
        "--model", "-m",
        default="qwen3:8b",
        help="Ollama model name (default: qwen3:8b)",
    )
    parser.add_argument(
        "--temperature", "-t",
        type=float,
        default=0.7,
        help="LLM temperature (default: 0.7)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2000,
        help="Max response tokens (default: 2000)",
    )

    args = parser.parse_args()

    if args.list:
        print_personas()
        return

    name = args.personality
    if not name:
        name = pick_personality()

    # Validate personality exists
    try:
        load_personality(name)
    except Exception as e:
        print(f"{RED}Error loading personality '{name}': {e}{RESET}")
        print_personas()
        sys.exit(1)

    asyncio.run(chat_loop(name, args.model, args.temperature, args.max_tokens))


if __name__ == "__main__":
    main()
