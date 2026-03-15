#!/bin/bash
# Test Whallet recovery with sample phrase

echo "Testing Whallet recovery script..."
echo ""

# Use a test recovery phrase (BIP39 test vector)
TEST_PHRASE="abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

echo "Test recovery phrase: $TEST_PHRASE"
echo ""

# Run the recovery script
source /Users/jens/kod/blick/venv/bin/activate
echo "$TEST_PHRASE" | python /Users/jens/kod/blick/whallet_proper_recovery.py

echo ""
echo "Test complete!"