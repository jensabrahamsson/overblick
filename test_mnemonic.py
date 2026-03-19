#!/usr/bin/env python3
"""Test script to verify mnemonic module works"""

import sys

print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}")

try:
    import mnemonic

    print(f"✓ mnemonic module imported successfully")
    print(f"  Version: {mnemonic.__version__}")

    # Test basic functionality
    m = mnemonic.Mnemonic("english")
    words = m.generate(strength=128)
    print(f"✓ Generated mnemonic: {words}")

    # Test seed generation
    seed = m.to_seed(words, passphrase="")
    print(f"✓ Seed generated: {seed.hex()[:32]}...")

except ImportError as e:
    print(f"✗ Failed to import mnemonic: {e}")
    print("Trying to install...")
    import subprocess

    subprocess.run([sys.executable, "-m", "pip", "install", "mnemonic"])

    try:
        import mnemonic

        print(f"✓ mnemonic module imported after installation")
    except ImportError as e2:
        print(f"✗ Still failed: {e2}")
