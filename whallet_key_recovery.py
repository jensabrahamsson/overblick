#!/usr/bin/env python3
"""
Whallet Key Recovery Tool
=========================

Detta skript är utformat för utvecklare av Whallet (lokal wallet)
för att testa och verifiera BIP39/BIP32/BIP44 implementation.

⚠️  ENDAST FÖR UTVECKLINGS- OCH TESTÄNDAMÅL ⚠️

Användning:
    python3 whallet_key_recovery.py "recovery phrase" "0xaddress" [passphrase]

Exempel:
    python3 whallet_key_recovery.py "abandon ability able about above absent absorb abstract absurd abuse access accident" "0x1234..."
"""

import sys
import hashlib
import hmac
import binascii
from typing import Tuple, Optional, List
import json

# ==================== BIP39 IMPLEMENTATION ====================


def load_bip39_wordlist() -> List[str]:
    """Ladda BIP39 engelska ordlistan."""
    # I verklig implementation, ladda från fil
    # Här är ett urval för demonstration
    return [
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
        "acoustic",
        "acquire",
        "across",
        "act",
        "action",
        "actor",
        "actress",
        "actual",
        "adapt",
        "add",
        "addict",
        "address",
        "adjust",
        "admit",
        "adult",
        "advance",
        "advice",
        "aerobic",
        "affair",
        "afford",
        "afraid",
        "again",
        "age",
        "agent",
        # ... fortsätt med alla 2048 ord
    ]


def bip39_mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    """
    Konvertera BIP39 mnemonic till seed med PBKDF2-HMAC-SHA512.

    Args:
        mnemonic: Recovery phrase (12/24 ord)
        passphrase: Valfri BIP39 passphrase

    Returns:
        64-byte seed
    """
    # Normalisera
    mnemonic = mnemonic.strip().lower()
    passphrase = passphrase.strip()

    # Konvertera till bytes
    mnemonic_bytes = mnemonic.encode("utf-8")
    salt = ("mnemonic" + passphrase).encode("utf-8")

    # PBKDF2 med HMAC-SHA512
    seed = hashlib.pbkdf2_hmac(
        "sha512",
        mnemonic_bytes,
        salt,
        iterations=2048,  # BIP39 standard
        dklen=64,
    )

    return seed


def validate_bip39_mnemonic(mnemonic: str, wordlist: List[str]) -> bool:
    """
    Validera BIP39 mnemonic (enkel version).

    Args:
        mnemonic: Recovery phrase att validera
        wordlist: BIP39 ordlista

    Returns:
        True om mnemonic är giltig
    """
    words = mnemonic.strip().lower().split()

    # Kontrollera antal ord
    if len(words) not in [12, 15, 18, 21, 24]:
        return False

    # Kontrollera att alla ord finns i ordlistan
    for word in words:
        if word not in wordlist:
            return False

    # I full implementation: kontrollera checksum
    return True


# ==================== BIP32 IMPLEMENTATION ====================


def hmac_sha512(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA512."""
    return hmac.new(key, data, hashlib.sha512).digest()


def bip32_derive_master_key(seed: bytes) -> Tuple[bytes, bytes]:
    """
    Derivera BIP32 master key från seed.

    Args:
        seed: 64-byte BIP39 seed

    Returns:
        (master_private_key, master_chain_code)
    """
    I = hmac_sha512(b"Bitcoin seed", seed)
    return I[:32], I[32:]


def bip32_ckd_private(
    parent_key: bytes, parent_chain_code: bytes, index: int
) -> Tuple[bytes, bytes]:
    """
    BIP32 Child Key Derivation för privata nycklar.

    Args:
        parent_key: 32-byte privat nyckel
        parent_chain_code: 32-byte chain code
        index: Deriveringsindex

    Returns:
        (child_private_key, child_chain_code)
    """
    if index >= 0x80000000:
        # Hardened derivation
        data = b"\x00" + parent_key + index.to_bytes(4, "big")
    else:
        # Non-hardened (kräver public key)
        raise ValueError("Non-hardened derivation kräver public key")

    I = hmac_sha512(parent_chain_code, data)
    return I[:32], I[32:]


# ==================== ETHEREUM DERIVATION ====================


def derive_ethereum_keypair(seed: bytes, account_index: int = 0, address_index: int = 0) -> dict:
    """
    Derivera Ethereum keypair från seed.

    Använder BIP44 path: m/44'/60'/0'/0/{address_index}

    Args:
        seed: BIP39 seed
        account_index: Account index (default: 0)
        address_index: Address index (default: 0)

    Returns:
        Dictionary med keys och metadata
    """
    # Master key
    master_private_key, master_chain_code = bip32_derive_master_key(seed)

    # BIP44 derivation path för Ethereum
    # m/44'/60'/{account_index}'/0/{address_index}
    derivation_path = [
        0x80000000 + 44,  # purpose (BIP44)
        0x80000000 + 60,  # coin_type (Ethereum)
        0x80000000 + account_index,  # account
        0,  # change (0 = external)
        address_index,  # address index
    ]

    # Derivera genom varje nivå
    private_key = master_private_key
    chain_code = master_chain_code

    for i, index in enumerate(derivation_path):
        private_key, chain_code = bip32_ckd_private(private_key, chain_code, index)

    # Konvertera till hex
    private_key_hex = binascii.hexlify(private_key).decode()

    # Generera address (simplifierad - använd ecdsa i produktion)
    # För Whallet, använd samma implementation som i huvudkoden
    address = private_key_to_ethereum_address(private_key)

    return {
        "derivation_path": f"m/44'/60'/{account_index}'/0/{address_index}",
        "private_key": private_key_hex,
        "public_key": "",  # Lägg till om behövs
        "address": address,
        "account_index": account_index,
        "address_index": address_index,
    }


def private_key_to_ethereum_address(private_key: bytes) -> str:
    """
    Konvertera privat nyckel till Ethereum address.

    OBS: Denna implementation är FÖRENKLAD.
    I Whallet, använd proper ECDSA/secp256k1.

    Args:
        private_key: 32-byte privat nyckel

    Returns:
        Ethereum address med 0x prefix
    """
    # Steg 1: Generera public key från private key (ECDSA)
    # I verklig implementation:
    # from ecdsa import SigningKey, SECP256k1
    # sk = SigningKey.from_string(private_key, curve=SECP256k1)
    # public_key = sk.verifying_key.to_string()

    # Steg 2: Keccak256 hash av public key (utan första byte)
    # hash = keccak256(public_key[1:])

    # Steg 3: Ta sista 20 bytes som address
    # address = "0x" + hash[-20:].hex()

    # FÖRENKLAD VERSION för testning:
    # Använd SHA3-256 som placeholder för Keccak
    hash_obj = hashlib.sha3_256(private_key)
    hash_hex = hash_obj.hexdigest()

    # Ta sista 40 tecken som placeholder address
    placeholder_address = "0x" + hash_hex[-40:]

    return placeholder_address.lower()


# ==================== KEY RECOVERY ====================


def recover_keys_for_address(
    mnemonic: str,
    target_address: str,
    passphrase: str = "",
    max_accounts: int = 5,
    max_addresses: int = 10,
) -> Optional[dict]:
    """
    Hitta privat nyckel för specifik address.

    Args:
        mnemonic: BIP39 recovery phrase
        target_address: Måladress (med 0x)
        passphrase: BIP39 passphrase
        max_accounts: Max antal accounts att söka
        max_addresses: Max addresses per account

    Returns:
        Key information om hittad, annars None
    """
    # Normalisera address
    target_address = target_address.strip().lower()
    if not target_address.startswith("0x"):
        target_address = "0x" + target_address

    print(f"🔍 Söker nyckel för address: {target_address}")
    print(f"📝 Mnemonic: {'*' * len(mnemonic.split())} ord")

    # Generera seed
    seed = bip39_mnemonic_to_seed(mnemonic, passphrase)
    print(f"🌱 Seed: {binascii.hexlify(seed[:8]).decode()}...")

    # Sök genom accounts och addresses
    for account_idx in range(max_accounts):
        for address_idx in range(max_addresses):
            try:
                keypair = derive_ethereum_keypair(seed, account_idx, address_idx)

                if keypair["address"] == target_address:
                    print(f"\n✅ TRÄFF! Account {account_idx}, Address {address_idx}")
                    return keypair

                # Visa progress
                if address_idx == 0:
                    print(f"  Account {account_idx}: {keypair['address']}")

            except Exception as e:
                print(f"  Fel vid derivation {account_idx}/{address_idx}: {e}")
                continue

    print(f"\n❌ Ingen träff i första {max_accounts} accounts")
    return None


# ==================== WHALLET INTEGRATION ====================


def generate_whallet_import_file(keypair: dict) -> str:
    """
    Generera importfil för Whallet.

    Args:
        keypair: Key information från derive_ethereum_keypair

    Returns:
        JSON-sträng för import
    """
    import_data = {
        "version": "1.0",
        "wallet": "Whallet",
        "timestamp": "2025-03-14T00:00:00Z",
        "key_data": {
            "private_key": keypair["private_key"],
            "address": keypair["address"],
            "derivation_path": keypair["derivation_path"],
            "account_index": keypair["account_index"],
            "address_index": keypair["address_index"],
        },
        "metadata": {"source": "whallet_key_recovery.py", "warning": "⚠️  LAGRAS SÄKERT! ⚠️"},
    }

    return json.dumps(import_data, indent=2)


def save_to_whallet_format(keypair: dict, filename: str = "whallet_import.json"):
    """
    Spara keypair till fil för import i Whallet.

    Args:
        keypair: Key information
        filename: Utdatafil
    """
    import_json = generate_whallet_import_file(keypair)

    with open(filename, "w") as f:
        f.write(import_json)

    print(f"\n💾 Sparad till: {filename}")
    print("Importera denna fil i Whallet.")


# ==================== COMMAND LINE ====================


def main():
    """Huvudfunktion för kommandorad."""

    print("=" * 60)
    print("Whallet Key Recovery Tool")
    print("=" * 60)
    print("\n⚠️  ENDAST FÖR UTVECKLING OCH TEST ⚠️")
    print("Använd aldrig med riktiga tillgångar i produktion.")

    # Argument eller interaktivt
    if len(sys.argv) >= 3:
        mnemonic = sys.argv[1]
        target_address = sys.argv[2]
        passphrase = sys.argv[3] if len(sys.argv) > 3 else ""
    else:
        print("\nInteraktivt läge:")
        mnemonic = input("Recovery phrase: ").strip()
        target_address = input("Ethereum address (0x...): ").strip()
        passphrase = input("BIP39 passphrase (tom om ingen): ").strip()

    # Validera input
    if not mnemonic or not target_address:
        print("❌ Både recovery phrase och address krävs.")
        sys.exit(1)

    # Ladda ordlista
    wordlist = load_bip39_wordlist()

    # Validera mnemonic (enkel version)
    if not validate_bip39_mnemonic(mnemonic, wordlist):
        print("⚠️  Varning: Mnemonic validering är förenklad")
        print("I produktion, använd full BIP39 validering med checksum")

    # Sök nyckel
    keypair = recover_keys_for_address(
        mnemonic, target_address, passphrase, max_accounts=10, max_addresses=20
    )

    if keypair:
        print("\n" + "=" * 60)
        print("✅ KEYPAIR HITTAD")
        print("=" * 60)

        print(f"\nDerivation path: {keypair['derivation_path']}")
        print(f"Address: {keypair['address']}")
        print(f"Private key: {keypair['private_key']}")

        # Spara för import
        save = input("\n💾 Spara för Whallet import? (j/n): ").strip().lower()
        if save == "j":
            save_to_whallet_format(keypair)

        print("\n⚠️  SÄKERHETSÅTGÄRDER:")
        print("1. Rensa terminalhistorik: history -c && clear")
        print("2. Ta bort importfil efter användning")
        print("3. Testa med testnätverk först")

    else:
        print("\n❌ INGEN NYCKEL HITTAD")
        print("\nMöjliga orsaker:")
        print("1. Fel recovery phrase")
        print("2. Fel address")
        print("3. BIP39 passphrase saknas")
        print("4. Annan derivation path (inte BIP44)")
        print("5. Account/address index utanför sökområde")


if __name__ == "__main__":
    # Enkel säkerhetskontroll
    try:
        # Kontrollera om vi är online (för testning)
        import socket

        socket.create_connection(("8.8.8.8", 53), timeout=1)
        print("⚠️  VARNING: Verkar vara online")
        print("För produktion, kör OFFLINE")
    except:
        pass  # Bra, offline eller ingen kontroll

    main()
