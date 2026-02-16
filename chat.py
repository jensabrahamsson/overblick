#!/usr/bin/env python3
"""
Interactive chat CLI for Överblick personalities.

Chat with any personality from the stable using local Ollama.
Streams responses token-by-token. Thinking mode is disabled for fast responses.

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
import json
import sys
import time
from pathlib import Path

import aiohttp

# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent))

from overblick.identities import (
    build_system_prompt,
    list_identities,
    load_identity,
)

# Terminal codes
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
    names = list_identities()
    print(f"\n{BOLD}Available identities:{RESET}")
    for name in sorted(names):
        p = load_identity(name)
        desc = ""
        if p.identity_info:
            desc = p.identity_info.get("description", "")
        if not desc and p.voice:
            desc = p.voice.get("base_tone", "")
        print(f"  {GREEN}{name:10s}{RESET}  {DIM}{desc[:60]}{RESET}")
    print()


def pick_identity() -> str:
    """Interactive identity picker."""
    names = sorted(list_identities())
    print(f"\n{BOLD}Pick an identity:{RESET}\n")
    for i, name in enumerate(names, 1):
        p = load_identity(name)
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


async def check_ollama(base_url: str, model: str) -> bool:
    """Check that Ollama is reachable and model is available."""
    api_url = f"{base_url}/api/tags"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    print(f"{RED}Ollama is not running (status {resp.status}).{RESET}")
                    return False
                data = await resp.json()
                models = [m.get("name") for m in data.get("models", [])]
                model_base = model.split(":")[0]
                if not any(model_base in m for m in models):
                    print(f"{RED}Model '{model}' not found. Available: {models}{RESET}")
                    return False
                return True
    except Exception:
        print(f"{RED}Ollama is not running or not reachable.{RESET}")
        print(f"{DIM}Start it with: ollama serve{RESET}")
        print(f"{DIM}Pull model with: ollama pull {model}{RESET}")
        return False


async def stream_response(
    session: aiohttp.ClientSession,
    base_url: str,
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> str | None:
    """Stream a chat response token-by-token using Ollama native API.

    Uses think=false to disable Qwen3 reasoning for instant responses.
    """
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }

    full_content = ""
    start_time = time.monotonic()

    try:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                print(f"{RED}(API error {resp.status}: {error_text[:200]}){RESET}")
                return None

            # Ollama native streaming: one JSON object per line
            async for line in resp.content:
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                try:
                    chunk = json.loads(text)
                except json.JSONDecodeError:
                    continue

                # Native API: {"message": {"content": "token"}, "done": false}
                token = chunk.get("message", {}).get("content", "")
                if token:
                    sys.stdout.write(token)
                    sys.stdout.flush()
                    full_content += token

                if chunk.get("done", False):
                    break

    except asyncio.TimeoutError:
        print(f"\n{RED}(timeout after 300s){RESET}")
        return None
    except aiohttp.ClientError as e:
        print(f"\n{RED}(connection error: {e}){RESET}")
        return None

    elapsed = time.monotonic() - start_time
    print(f"\n{DIM}  [{elapsed:.1f}s]{RESET}\n")
    return full_content.strip()


async def chat_loop(
    personality_name: str,
    model: str,
    base_url: str,
    temperature: float,
    max_tokens: int,
) -> None:
    """Main chat loop with streaming responses."""
    personality = load_identity(personality_name)
    system_prompt = build_system_prompt(personality, platform="CLI")
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if not await check_ollama(base_url, model):
        return

    print_banner(personality.name, personality.display_name)

    session = aiohttp.ClientSession()
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
                        personality = load_identity(new_name)
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

            content = await stream_response(
                session, base_url, model, messages,
                temperature, max_tokens,
            )

            if content is None:
                print(f"{RED}(no response){RESET}\n")
                messages.pop()  # Remove failed user message
                continue

            messages.append({"role": "assistant", "content": content})

    except KeyboardInterrupt:
        print(f"\n{DIM}Interrupted.{RESET}")
    finally:
        await session.close()

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
        "--url",
        default="http://localhost:11434",
        help="Ollama base URL (default: http://localhost:11434)",
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
        name = pick_identity()

    # Validate identity exists
    try:
        load_identity(name)
    except Exception as e:
        print(f"{RED}Error loading identity '{name}': {e}{RESET}")
        print_personas()
        sys.exit(1)

    asyncio.run(chat_loop(name, args.model, args.url, args.temperature, args.max_tokens))


if __name__ == "__main__":
    main()
