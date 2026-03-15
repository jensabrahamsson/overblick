#!/usr/bin/env python3
"""
Whallet Proper Key Recovery med korrekt ECDSA
==============================================

Använder etablerade bibliotek för korrekt nyckelgenerering.
Installera först: pip install eth-account mnemonic web3
"""

import sys
import binascii
from typing import Optional
from mnemonic import Mnemonic
from eth_account import Account
from web3 import Web3

# ==================== KORREKT BIP39/BIP32/BIP44 ====================


def derive_ethereum_private_key_from_mnemonic(
    mnemonic: str, passphrase: str = "", account_index: int = 0, address_index: int = 0
) -> str:
    """
    Derivera Ethereum privat nyckel från mnemonic med korrekt BIP44.

    Args:
        mnemonic: BIP39 recovery phrase
        passphrase: BIP39 passphrase
        account_index: Account index
        address_index: Address index

    Returns:
        Privat nyckel som hex-sträng
    """
    # 1. BIP39: Mnemonic → Seed
    mnemo = Mnemonic("english")
    seed = mnemo.to_seed(mnemonic, passphrase)

    # 2. BIP32: Seed → Master key
    # 3. BIP44: Derivation path m/44'/60'/{account_index}'/0/{address_index}

    # Använd eth_accounts inbyggda derivation
    # Denna använder korrekt secp256k1 ECDSA
    Account.enable_unaudited_hdwallet_features()

    # Skapa account med mnemonic
    account = Account.from_mnemonic(
        mnemonic,
        passphrase=passphrase,
        account_path=f"m/44'/60'/{account_index}'/0/{address_index}",
    )

    return account.key.hex()


def get_address_from_private_key(private_key_hex: str) -> str:
    """
    Hämta Ethereum address från privat nyckel.

    Args:
        private_key_hex: Privat nyckel som hex

    Returns:
        Ethereum address med 0x
    """
    account = Account.from_key(private_key_hex)
    return account.address


# ==================== KEY RECOVERY ====================


def find_private_key_for_address(
    mnemonic: str,
    target_address: str,
    passphrase: str = "",
    max_accounts: int = 10,
    max_addresses: int = 20,
) -> Optional[dict]:
    """
    Hitta privat nyckel för specifik address.

    Args:
        mnemonic: Recovery phrase
        target_address: Måladress
        passphrase: BIP39 passphrase
        max_accounts: Max accounts att söka
        max_addresses: Max addresses per account

    Returns:
        Dictionary med key info eller None
    """
    # Normalisera address
    target_address = Web3.to_checksum_address(target_address)

    print(f"🔍 Söker nyckel för: {target_address}")
    print(f"📝 Mnemonic: {'*' * len(mnemonic.split())} ord")

    # Sök genom derivation paths
    for account_idx in range(max_accounts):
        print(f"\n📁 Account {account_idx}:")

        for address_idx in range(max_addresses):
            try:
                # Derivera privat nyckel
                private_key = derive_ethereum_private_key_from_mnemonic(
                    mnemonic, passphrase, account_idx, address_idx
                )

                # Hämta address
                address = get_address_from_private_key(private_key)

                print(f"  Address {address_idx}: {address}")

                if address.lower() == target_address.lower():
                    print(f"\n✅ TRÄFF på account {account_idx}, address {address_idx}")

                    return {
                        "private_key": private_key,
                        "address": address,
                        "account_index": account_idx,
                        "address_index": address_idx,
                        "derivation_path": f"m/44'/60'/{account_idx}'/0/{address_idx}",
                    }

            except Exception as e:
                print(f"  Fel: {e}")
                continue

    print(f"\n❌ Ingen träff i första {max_accounts} accounts")
    return None


# ==================== TEST MED RIKTIG DATA ====================


def test_with_real_example():
    """
    Testa med känd test-vektor från BIP39.
    """
    print("🧪 Test med känd test-vektor")
    print("=" * 50)

    # Test mnemonic från BIP39 spec
    test_mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
    test_passphrase = "TREZOR"

    # Känd address för denna mnemonic (första addressen)
    expected_address = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"

    print(f"Mnemonic: {test_mnemonic}")
    print(f"Passphrase: {test_passphrase}")
    print(f"Förväntad address: {expected_address}")

    result = find_private_key_for_address(
        test_mnemonic, expected_address, test_passphrase, max_accounts=1, max_addresses=1
    )

    if result:
        print("\n✅ TEST LYCADES!")
        print(f"Privat nyckel: {result['private_key']}")
        print(f"Address: {result['address']}")
        print(f"Derivation: {result['derivation_path']}")

        # Verifiera att address matchar
        derived_address = get_address_from_private_key(result["private_key"])
        if derived_address.lower() == expected_address.lower():
            print("✅ Address verifierad!")
        else:
            print("❌ Address matchar inte!")
    else:
        print("❌ TEST MISSLYCKADES")


# ==================== MAIN ====================


def main():
    """Huvudfunktion."""

    print("=" * 60)
    print("Whallet Proper Key Recovery")
    print("=" * 60)
    print("\n⚠️  KRITISK VARNING:")
    print("1. Kör ENDAST OFFLINE")
    print("2. Använd ENDAST med EGNA tillgångar")
    print("3. Rensa all historik efter användning")
    print("=" * 60)

    # Testläge eller produktionsläge
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        test_with_real_example()
        return

    # Argument
    if len(sys.argv) >= 3:
        mnemonic = sys.argv[1]
        target_address = sys.argv[2]
        passphrase = sys.argv[3] if len(sys.argv) > 3 else ""
    else:
        print("\nInteraktivt läge:")
        mnemonic = input("Recovery phrase: ").strip()
        target_address = input("Ethereum address (0x...): ").strip()
        passphrase = input("BIP39 passphrase (tom om ingen): ").strip()

    if not mnemonic or not target_address:
        print("❌ Både recovery phrase och address krävs.")
        sys.exit(1)

    # Kör recovery
    result = find_private_key_for_address(
        mnemonic, target_address, passphrase, max_accounts=10, max_addresses=20
    )

    if result:
        print("\n" + "=" * 60)
        print("✅ PRIVAT NYCKEL HITTAD")
        print("=" * 60)

        print(f"\nDerivation path: {result['derivation_path']}")
        print(f"Address: {result['address']}")
        print(f"Privat nyckel: {result['private_key']}")

        print("\n⚠️  SÄKERHETSÅTGÄRDER:")
        print("1. Kopiera nyckel MANUELLT (inte copy-paste)")
        print("2. Rensa terminal: history -c && clear")
        print("3. Ta bort denna fil efter användning")

    else:
        print("\n❌ INGEN NYCKEL HITTAD")
        print("\nTesta med:")
        print("1. --test flagga för test-vektor")
        print("2. Kontrollera BIP39 passphrase")
        print("3. Öka max_accounts/max_addresses")


if __name__ == "__main__":
    # Kontrollera dependencies
    try:
        from mnemonic import Mnemonic
        from eth_account import Account
    except ImportError:
        print("❌ Installera dependencies först:")
        print("pip install eth-account mnemonic web3")
        sys.exit(1)

    main()
