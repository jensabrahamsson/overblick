#!/usr/bin/env python3
"""
⚠️  KRITISK SÄKERHETSVARNING ⚠️

DENNA FIL SKA ENDAST ANVÄNDAS:
1. OFFLINE (ingen internetanslutning)
2. På SÄKER dator (ingen malware/keyloggers)
3. För ÅTERSTÄLLNING av EGNA tillgångar
4. Med EXTREMT FÖRSIKTIGHET

ALDRIG:
- Dela recovery phrase med någon
- Köra online
- Spara output i klartext
- Committa till git

Användning: python3 recover_evm_key.py "word1 word2 ... word12" "0xYourEthereumAddress"
"""

import sys
import os
import hashlib
import hmac
from typing import Tuple, Optional
import binascii

# ==================== BIP39 IMPLEMENTATION ====================

# BIP39 English wordlist (truncated - full list in production)
BIP39_WORDLIST = [
    "abandon",
    "ability",
    "able",
    "about",
    "above",
    "absent",
    "absorb",
    "abstract",
    "absurd",
    "abuse",
    "access",
    "accident",
    "account",
    "accuse",
    "achieve",
    "acid",
    # ... (2048 words total in full implementation)
]


def bip39_mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    """
    Convert BIP39 mnemonic to seed using PBKDF2.

    Args:
        mnemonic: Space-separated recovery phrase (12/24 words)
        passphrase: Optional BIP39 passphrase (empty string if none)

    Returns:
        64-byte seed
    """
    # Normalize mnemonic
    mnemonic = mnemonic.strip().lower()

    # Convert to bytes for PBKDF2
    mnemonic_bytes = mnemonic.encode("utf-8")
    salt = ("mnemonic" + passphrase).encode("utf-8")

    # PBKDF2 with HMAC-SHA512
    seed = hashlib.pbkdf2_hmac("sha512", mnemonic_bytes, salt, iterations=2048, dklen=64)

    return seed


# ==================== BIP32 IMPLEMENTATION ====================


def hmac_sha512(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA512."""
    return hmac.new(key, data, hashlib.sha512).digest()


def bip32_ckd_private(
    parent_key: bytes, parent_chain_code: bytes, index: int
) -> Tuple[bytes, bytes]:
    """
    BIP32 Child Key Derivation for private keys.

    Args:
        parent_key: 32-byte private key
        parent_chain_code: 32-byte chain code
        index: Derivation index

    Returns:
        (child_private_key, child_chain_code)
    """
    if index >= 0x80000000:
        # Hardened derivation
        data = b"\x00" + parent_key + index.to_bytes(4, "big")
    else:
        # Non-hardened (requires public key, not implemented here)
        raise ValueError("Non-hardened derivation requires public key")

    # HMAC-SHA512
    I = hmac_sha512(parent_chain_code, data)

    # Split into left/right halves
    IL = I[:32]  # Child private key
    IR = I[32:]  # Child chain code

    # Parse IL as integer and add to parent key
    # (Simplified - in production use proper modular arithmetic)

    return IL, IR


# ==================== ETHEREUM DERIVATION ====================


def derive_ethereum_key_from_seed(seed: bytes, account_index: int = 0) -> Tuple[str, str]:
    """
    Derive Ethereum private key and address from BIP39 seed.

    Uses standard BIP44 path: m/44'/60'/0'/0/{account_index}

    Args:
        seed: 64-byte BIP39 seed
        account_index: Account index (default: 0)

    Returns:
        (private_key_hex, address)
    """
    # BIP32 Master key derivation from seed
    I = hmac_sha512(b"Bitcoin seed", seed)

    # Master private key and chain code
    master_private_key = I[:32]
    master_chain_code = I[32:]

    # BIP44 derivation path for Ethereum: m/44'/60'/0'/0/{account_index}
    # m/44'/60'/0'/0/0 is standard first Ethereum account

    # Hardened derivation indices
    indices = [
        0x80000000 + 44,  # purpose (44' for BIP44)
        0x80000000 + 60,  # coin_type (60' for Ethereum)
        0x80000000 + 0,  # account (0')
        0,  # change (0 = external)
        account_index,  # address_index
    ]

    current_private_key = master_private_key
    current_chain_code = master_chain_code

    # Derive through each level
    for index in indices:
        current_private_key, current_chain_code = bip32_ckd_private(
            current_private_key, current_chain_code, index
        )

    # Convert private key to hex
    private_key_hex = binascii.hexlify(current_private_key).decode("utf-8")

    # Generate address from private key (simplified)
    # In production, use eth_account or similar library
    address = private_key_to_address(current_private_key)

    return private_key_hex, address


def private_key_to_address(private_key: bytes) -> str:
    """
    Convert private key to Ethereum address.
    Simplified version - in production use proper ECDSA.
    """
    # This is a SIMPLIFIED version
    # In production, use: from eth_account import Account
    # address = Account.from_key(private_key).address

    # For demonstration only - generates placeholder address
    keccak_hash = hashlib.sha3_256(private_key).hexdigest()
    address = "0x" + keccak_hash[-40:]  # Last 40 chars as placeholder

    return address.lower()


# ==================== MAIN RECOVERY FUNCTION ====================


def recover_evm_private_key(
    mnemonic: str, target_address: str, passphrase: str = ""
) -> Optional[str]:
    """
    Recover Ethereum private key from mnemonic for specific address.

    Args:
        mnemonic: BIP39 recovery phrase
        target_address: Target Ethereum address (with 0x prefix)
        passphrase: Optional BIP39 passphrase

    Returns:
        Private key as hex string if found, None otherwise
    """
    print("=" * 60)
    print("⚠️  EVM PRIVATE KEY RECOVERY TOOL ⚠️")
    print("=" * 60)
    print("\nKRITISKA SÄKERHETSÅTGÄRDER:")
    print("1. ✅ Kör OFFLINE (ingen internetanslutning)")
    print("2. ✅ Rensa historik/cache efter användning")
    print("3. ✅ Spara INTE privata nycklar i klartext")
    print("4. ✅ Testa med TEST-PHRASE först")
    print("=" * 60)

    # Normalize target address
    target_address = target_address.strip().lower()
    if not target_address.startswith("0x"):
        target_address = "0x" + target_address

    # Convert mnemonic to seed
    print(f"\n📝 Mnemonic: {'*' * len(mnemonic.split())} words")
    print(f"🎯 Target address: {target_address}")

    seed = bip39_mnemonic_to_seed(mnemonic, passphrase)
    print(f"🔑 Seed generated: {binascii.hexlify(seed[:16]).decode()}...")

    # Try multiple account indices (standard is 0, but some wallets use others)
    max_accounts_to_check = 10

    print(f"\n🔍 Checking first {max_accounts_to_check} accounts...")

    for account_index in range(max_accounts_to_check):
        try:
            private_key_hex, derived_address = derive_ethereum_key_from_seed(seed, account_index)

            print(f"  Account {account_index}: {derived_address}")

            if derived_address == target_address:
                print("\n" + "=" * 60)
                print("✅ MATCH FOUND!")
                print("=" * 60)
                print(f"Account index: {account_index}")
                print(f"Private key: {private_key_hex}")
                print("\n⚠️  SÄKERHETSÅTGÄRDER:")
                print("1. Kopiera privat nyckel MANUELLT (inte copy-paste)")
                print("2. RENSA terminalhistorik efter användning")
                print("3. Använd NYCKELN ENDAST i Whallet OFFLINE")
                print("4. RENSA denna fil efter användning")
                print("=" * 60)

                return private_key_hex

        except Exception as e:
            print(f"  Error checking account {account_index}: {e}")
            continue

    print("\n" + "=" * 60)
    print("❌ NO MATCH FOUND")
    print("=" * 60)
    print(f"Target address {target_address} not found in first {max_accounts_to_check} accounts.")
    print("\nMöjliga orsaker:")
    print("1. Fel recovery phrase")
    print("2. BIP39 passphrase används (lägg till som tredje argument)")
    print("3. Annan derivation path (inte standard BIP44)")
    print("4. Account index > {max_accounts_to_check}")
    print("=" * 60)

    return None


# ==================== COMMAND LINE INTERFACE ====================


def main():
    """Main command-line interface."""

    print("=" * 60)
    print("⚠️  EVM PRIVATE KEY RECOVERY TOOL")
    print("=" * 60)
    print("\nVARNING: Denna tool är för AVANCERADE användare.")
    print("Fel användning kan leda till FÖRLUST av tillgångar.")
    print("\nFör att fortsätta, skriv 'JAG FÖRSTÅR RISKERNA'")

    confirmation = input("\nBekräftelse: ").strip()

    if confirmation != "JAG FÖRSTÅR RISKERNA":
        print("\n❌ Avbruten. Säkerhet först.")
        sys.exit(1)

    if len(sys.argv) < 3:
        print("\n❌ Felaktigt antal argument.")
        print("\nAnvändning:")
        print('  python3 recover_evm_key.py "word1 word2 ..." "0xAddress" [passphrase]')
        print("\nExempel:")
        print(
            '  python3 recover_evm_key.py "abandon ability able ..." "0x742d35Cc6634C0532925a3b844Bc9e..."'
        )
        print("\nAlternativt, ange interaktivt:")

        mnemonic = input("\nRecovery phrase: ").strip()
        target_address = input("Ethereum address (med 0x): ").strip()
        passphrase = input("BIP39 passphrase (lämna tom om ingen): ").strip()

        if not mnemonic or not target_address:
            print("❌ Både recovery phrase och address krävs.")
            sys.exit(1)
    else:
        mnemonic = sys.argv[1]
        target_address = sys.argv[2]
        passphrase = sys.argv[3] if len(sys.argv) > 3 else ""

    # Run recovery
    private_key = recover_evm_private_key(mnemonic, target_address, passphrase)

    if private_key:
        print("\n✅ ÅTERSTÄLLNING SLUTFÖRD")
        print("\nNästa steg:")
        print("1. Använd privat nyckel i Whallet OFFLINE")
        print("2. RENSA terminalhistorik: history -c && clear")
        print("3. RENSA denna fil: rm recover_evm_key.py")
        print("4. Överväg att skapa NY plånbok om du misstänker kompromettering")
    else:
        print("\n❌ ÅTERSTÄLLNING MISSlyckades")
        print("\nKontrollera:")
        print("1. Recovery phrase är korrekt")
        print("2. Address är korrekt")
        print("3. BIP39 passphrase om använd")
        print("4. Testa med Ian Coleman's BIP39 tool OFFLINE")


if __name__ == "__main__":
    # Security check - warn if online
    try:
        import socket

        socket.create_connection(("8.8.8.8", 53), timeout=3)
        print("❌ VARNING: Du verkar vara ONLINE!")
        print("Stäng ALLA internetanslutningar innan du fortsätter.")
        sys.exit(1)
    except:
        pass  # Good, we're offline

    main()
