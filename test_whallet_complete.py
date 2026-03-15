#!/usr/bin/env python3
"""Complete test for Whallet recovery functionality"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_basic_recovery():
    """Test basic recovery functionality"""
    print("=== Testing Basic Recovery ===")

    try:
        from recover_evm_key import recover_evm_private_key

        print("✓ Basic recovery module imported")

        # Test with BIP39 test vector
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        target_address = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"

        result = recover_evm_private_key(mnemonic, target_address)

        if result:
            private_key, address, account_index = result
            print(f"✓ Private key recovered: {private_key[:16]}...")
            print(f"✓ Ethereum address: {address}")
            print(f"✓ Account index: {account_index}")

            if address.lower() == target_address.lower():
                print(f"✓ Address matches expected: {target_address}")
            else:
                print(f"✗ Address mismatch. Expected: {target_address}")
                print(f"  Got: {address}")
        else:
            print("✗ No key found for target address")

    except Exception as e:
        print(f"✗ Basic recovery test failed: {e}")
        import traceback

        traceback.print_exc()


def test_whallet_recovery():
    """Test Whallet-specific recovery"""
    print("\n=== Testing Whallet Recovery ===")

    try:
        from whallet_key_recovery import recover_keys_for_address

        print("✓ Whallet recovery module imported")

        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        target_address = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"

        result = recover_keys_for_address(mnemonic, target_address, max_accounts=3)

        if result:
            keypair, account_index, address_index = result
            print(f"✓ Keypair recovered for account {account_index}, address {address_index}")
            print(f"✓ Ethereum address: {keypair.get('address', 'N/A')}")
            print(f"✓ Private key: {keypair.get('private_key_hex', 'N/A')[:16]}...")
        else:
            print("✗ No key found for target address")

    except Exception as e:
        print(f"✗ Whallet recovery test failed: {e}")
        import traceback

        traceback.print_exc()


def test_proper_recovery():
    """Test proper recovery with eth-account"""
    print("\n=== Testing Proper Recovery ===")

    try:
        from whallet_key_recovery import recover_whallet_keys

        print("✓ Whallet recovery module imported")

        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        keys = recover_whallet_keys(mnemonic, num_accounts=3)

        print(f"✓ Recovered {len(keys)} accounts")
        for i, (priv_key, addr) in enumerate(keys):
            print(f"  Account {i}: {addr}")

    except Exception as e:
        print(f"✗ Whallet recovery test failed: {e}")
        import traceback

        traceback.print_exc()


def test_proper_recovery():
    """Test proper recovery with eth-account"""
    print("\n=== Testing Proper Recovery ===")

    try:
        # Test the proper recovery by running it directly
        import subprocess

        # Use the test recovery phrase
        test_phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        test_address = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"

        # Create input for the script
        input_data = f"{test_phrase}\n{test_address}\n3\n"

        # Run the script
        result = subprocess.run(
            ["python", "whallet_proper_recovery.py"],
            input=input_data,
            text=True,
            capture_output=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        print(f"✓ Script executed (exit code: {result.returncode})")

        # Check output
        output = result.stdout + result.stderr
        if "Private key recovered" in output:
            print("✓ Private key recovery confirmed")
        if "Ethereum address" in output:
            print("✓ Address generation confirmed")
        if "Account 0:" in output:
            print("✓ Multiple accounts generated")

        # Print first few lines of output
        lines = output.split("\n")[:10]
        print("First 10 lines of output:")
        for line in lines:
            if line.strip():
                print(f"  {line}")

    except Exception as e:
        print(f"✗ Proper recovery test failed: {e}")
        import traceback

        traceback.print_exc()


def main():
    print("Whallet Recovery Test Suite")
    print("=" * 50)

    # Run all tests
    test_basic_recovery()
    test_whallet_recovery()
    test_proper_recovery()

    print("\n" + "=" * 50)
    print("Test suite completed!")


if __name__ == "__main__":
    main()
