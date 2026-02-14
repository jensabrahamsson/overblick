#!/bin/bash
# Run personality scenario tests in a loop, collecting pass/fail stats
# Usage: ./tests/run_scenario_loop.sh [iterations]

ITERATIONS=${1:-10}
PASS_COUNT=0
FAIL_COUNT=0
RESULTS_FILE="/tmp/scenario_loop_results.txt"

echo "" > "$RESULTS_FILE"
echo "Running $ITERATIONS iterations of personality scenario tests..."
echo "============================================================"

for i in $(seq 1 "$ITERATIONS"); do
    echo ""
    echo "--- Iteration $i/$ITERATIONS ---"
    OUTPUT=$(./venv/bin/python3 -m pytest tests/personalities/test_single_turn_scenarios.py -m llm --tb=line -q 2>&1)
    EXIT_CODE=$?

    # Extract summary line
    SUMMARY=$(echo "$OUTPUT" | tail -1)
    echo "  $SUMMARY"

    # Extract failure names if any
    FAILURES=$(echo "$OUTPUT" | grep "FAILED" | sed 's/.*test_scenario\[/  FAILED: [/' | sed 's/\] .*/]/')
    if [ -n "$FAILURES" ]; then
        echo "$FAILURES"
    fi

    if [ $EXIT_CODE -eq 0 ]; then
        PASS_COUNT=$((PASS_COUNT + 1))
        echo "$i: PASS - $SUMMARY" >> "$RESULTS_FILE"
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "$i: FAIL - $SUMMARY" >> "$RESULTS_FILE"
        if [ -n "$FAILURES" ]; then
            echo "$FAILURES" >> "$RESULTS_FILE"
        fi
    fi
done

echo ""
echo "============================================================"
echo "FINAL RESULTS: $PASS_COUNT/$ITERATIONS iterations fully passed"
echo "Failed iterations: $FAIL_COUNT"
echo ""
echo "Detail log: $RESULTS_FILE"
cat "$RESULTS_FILE"
