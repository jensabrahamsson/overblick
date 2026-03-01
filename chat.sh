#!/usr/bin/env bash
# Unix/macOS only. Cross-platform alternative: python chat.py
#
# Chat with an Överblick personality
# Usage: ./chat.sh cherry
#        ./chat.sh natt --model qwen3:8b
#        ./chat.sh --list
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/venv/bin/python3" "$DIR/chat.py" "$@"
