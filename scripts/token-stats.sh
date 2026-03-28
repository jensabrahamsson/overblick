#!/usr/bin/env bash
# token-stats.sh — Query LLM Gateway token usage
#
# Usage:
#   ./scripts/token-stats.sh              # Summary for last 24h
#   ./scripts/token-stats.sh 1h           # Summary for last hour
#   ./scripts/token-stats.sh 7d           # Summary for last 7 days
#   ./scripts/token-stats.sh csv          # Raw CSV for last 24h
#   ./scripts/token-stats.sh csv 1h       # Raw CSV for last hour
#   ./scripts/token-stats.sh raw          # Raw JSON for last 24h

GATEWAY="http://127.0.0.1:8200"
PERIOD="24h"
MODE="summary"

# Parse args
for arg in "$@"; do
    case "$arg" in
        1h|6h|24h|7d|30d) PERIOD="$arg" ;;
        csv) MODE="csv" ;;
        raw) MODE="raw" ;;
        -h|--help)
            echo "Usage: $0 [1h|6h|24h|7d|30d] [csv|raw]"
            echo ""
            echo "  (default)  Human-readable summary"
            echo "  csv        Raw CSV export"
            echo "  raw        Raw JSON"
            exit 0
            ;;
    esac
done

# Check gateway is running
if ! curl -sf "$GATEWAY/health" > /dev/null 2>&1; then
    echo "❌ Gateway not running at $GATEWAY"
    exit 1
fi

case "$MODE" in
    csv)
        curl -sf "$GATEWAY/stats/tokens?period=$PERIOD&format=csv"
        ;;
    raw)
        curl -sf "$GATEWAY/stats/tokens?period=$PERIOD" | python3 -m json.tool
        ;;
    summary)
        curl -sf "$GATEWAY/stats/tokens/summary?period=$PERIOD" | python3 -c "
import sys, json

d = json.load(sys.stdin)
t = d.get('total', {})
period = d.get('period', '?')

requests = t.get('requests', 0)
prompt = t.get('prompt_tokens', 0)
completion = t.get('completion_tokens', 0)
total = t.get('total_tokens', 0)
avg_ms = t.get('avg_response_ms', 0)

print(f'=== Token Usage ({period}) ===')
print()

if requests == 0:
    print('No requests in this period.')
    sys.exit(0)

print(f'Requests:    {requests}')
print(f'Tokens:      {total:,} total ({prompt:,} prompt + {completion:,} completion)')
print(f'Avg/request: {total // requests:,} tokens')
print(f'Avg latency: {avg_ms / 1000:.1f}s')
print()

backends = d.get('per_backend', [])
if backends:
    print('--- Per Backend ---')
    for b in backends:
        name = b.get('backend', '?')
        reqs = b.get('requests', 0)
        tok = b.get('total_tokens', 0)
        avg = b.get('avg_response_ms', 0) / 1000
        print(f'  {name:12s}  {reqs:4d} reqs  {tok:>8,} tokens  {avg:5.1f}s avg')
    print()

identities = d.get('per_identity', [])
if identities:
    print('--- Per Identity ---')
    for i in identities:
        hint = i.get('identity_hint', '?')
        # Extract identity name from system prompt hint
        name = hint
        if 'You are ' in hint:
            name = hint.split('You are ')[1].split(',')[0].split('.')[0]
        reqs = i.get('requests', 0)
        tok = i.get('total_tokens', 0)
        print(f'  {name:16s}  {reqs:4d} reqs  {tok:>8,} tokens')
"
        ;;
esac
