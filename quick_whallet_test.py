#!/usr/bin/env python3
"""Quick test for Whallet recovery using proper libraries"""

import sys
import os

# Activate virtual environment if needed
venv_path = os.path.join(os.path.dirname(__file__), "venv")
if os.path.exists(venv_path):
    sys.path.insert(0, os.path.join(venv_path, "lib", "python3.13", "site-packages"))


def test_with_eth_account():
    """Test using eth-account library (proper implementation)"""
    print("=== Testing with eth-account ===")

    try:
        from eth_account import Account
        from mnemonic import Mnemonic

        print("✓ Libraries imported successfully")

        # Test with BIP39 test vector
        test_phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

        # Create mnemonic object
        mnemo = Mnemonic("english")

        # Validate the phrase
        if mnemo.check(test_phrase):
            print("✓ Mnemonic is valid")
        else:
            print("✗ Mnemonic is invalid")
            return

        # Generate seed
        seed = mnemo.to_seed(test_phrase, passphrase="")
        print(f"✓ Seed generated: {seed.hex()[:32]}...")

        # Create account from private key (using test vector known private key)
        # For the test phrase "abandon...about", the first private key is:
        # 0x5ca5d4d2dca5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5c5

        # Instead, let's create a new account to test the flow
        Account.enable_unaudited_hdwallet_features()

        # Create account from mnemonic
        acct = Account.from_mnemonic(test_phrase, passphrase="", account_path="m/44'/60'/0'/0/0")

        print(f"✓ Account created from mnemonic")
        print(f"  Address: {acct.address}")
        print(f"  Private key: {acct.key.hex()[:16]}...")

        # Test deriving multiple accounts
        print("\n=== Testing multiple account derivation ===")
        for i in range(3):
            path = f"m/44'/60'/0'/0/{i}"
            acct_i = Account.from_mnemonic(test_phrase, passphrase="", account_path=path)
            print(f"  Account {i} ({path}): {acct_i.address}")

        return True

    except ImportError as e:
        print(f"✗ Import error: {e}")
        print("Make sure you have installed: pip install eth-account mnemonic")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    print("Whallet Quick Test")
    print("=" * 50)

    success = test_with_eth_account()

    print("\n" + "=" * 50)
    if success:
        print("✅ Test completed successfully!")
        print("\nNext steps for Whallet integration:")
        print("1. Use eth-account library for proper BIP39/BIP32/BIP44 support")
        print("2. Implement recovery phrase input in your Whallet UI")
        print("3. Add proper error handling and security warnings")
        print("4. Test with your actual recovery phrases (OFFLINE ONLY)")
    else:
        print("❌ Test failed")
        print("\nTroubleshooting:")
        print("1. Make sure virtual environment is activated")
        print("2. Install required packages: pip install eth-account mnemonic web3")
        print("3. Check Python path and imports")


if __name__ == "__main__":
    main()
