# Ethereum Private Key Recovery Tools

⚠️ **CRITICAL SECURITY WARNING** ⚠️

These tools are for **OFFLINE USE ONLY** and should **NEVER** be used with internet connectivity. They are designed for recovering your own Ethereum private keys from BIP39 recovery phrases.

## Overview

This directory contains Python scripts for recovering Ethereum private keys from BIP39 recovery phrases (seed phrases). The tools implement BIP39, BIP32, and BIP44 standards for deterministic wallet generation.

## Available Scripts

### 1. `whallet_proper_recovery.py` ✅ **RECOMMENDED**
**Status**: Fully working with proper cryptographic libraries
**Purpose**: Interactive tool for recovering Ethereum private keys using `eth-account` and `mnemonic` libraries
**Features**:
- Proper BIP39/BIP32/BIP44 implementation
- Interactive command-line interface
- Multiple account derivation (BIP44 path: `m/44'/60'/0'/0/{index}`)
- Security warnings and offline verification
- Tested with BIP39 test vectors

**Usage**:
```bash
# Activate virtual environment first
source venv/bin/activate

# Run the script
python whallet_proper_recovery.py
```

**Example Output**:
```
============================================================
Whallet Proper Key Recovery
============================================================

⚠️  CRITICAL WARNING:
1. Run OFFLINE ONLY
2. Use ONLY with YOUR OWN assets
3. Clear all history after use
============================================================

Interactive mode:
Recovery phrase: abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about
Ethereum address (0x...): 0x9858EfFD232B4033E47d90003D41EC34EcaEda94
Number of accounts to check: 3

✓ Private key recovered!
✓ Address matches: 0x9858EfFD232B4033E47d90003D41EC34EcaEda94
✓ Account index: 0
✓ Private key: 0x1ab42cc412b618bd...

Account 0: 0x9858EfFD232B4033E47d90003D41EC34EcaEda94
Account 1: 0x6Fac4D18c912343BF86fa7049364Dd4E424Ab9C0
Account 2: 0xb6716976A3ebe8D39aCEB04372f22Ff8e6802D7A
```

### 2. `recover_evm_key.py` ⚠️ **EXPERIMENTAL**
**Status**: Basic implementation with BIP32 derivation issues
**Purpose**: Manual BIP39/BIP32 implementation for educational purposes
**Issues**:
- BIP32 derivation error: "Non-hardened derivation requires public key"
- Uses custom cryptographic implementation (not recommended for production)
- Missing proper error handling

**Usage**:
```bash
python recover_evm_key.py "word1 word2 ... word12" "0xYourEthereumAddress"
```

### 3. `whallet_key_recovery.py` ⚠️ **EXPERIMENTAL**
**Status**: Whallet-specific implementation with same BIP32 issues
**Purpose**: Extended version with Whallet-specific features
**Issues**:
- Same BIP32 derivation error as `recover_evm_key.py`
- Includes Whallet import file generation (unimplemented)

### 4. `quick_whallet_test.py` ✅ **TEST SCRIPT**
**Status**: Working test script
**Purpose**: Verify proper library functionality with BIP39 test vectors
**Features**:
- Tests `eth-account` and `mnemonic` libraries
- Validates BIP39 phrase
- Derives multiple accounts
- Shows proper BIP44 paths

**Usage**:
```bash
source venv/bin/activate
python quick_whallet_test.py
```

## Installation & Setup

### 1. Create Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Required Packages
```bash
pip install eth-account mnemonic web3
```

### 3. Verify Installation
```bash
python -c "from eth_account import Account; from mnemonic import Mnemonic; print('Libraries imported successfully')"
```

## Security Protocol

### **MUST DO (Before Use):**
1. **DISCONNECT FROM INTERNET** - Physically unplug network cable or disable WiFi
2. **Use secure computer** - No malware, keyloggers, or remote access software
3. **Clear browser history** - Remove any traces of recovery phrases
4. **Use test phrase first** - Verify with "abandon abandon ... about" before real phrases

### **NEVER DO:**
- ❌ Run scripts while connected to internet
- ❌ Share recovery phrases with anyone
- ❌ Save private keys in plain text files
- ❌ Commit recovery phrases or private keys to git
- ❌ Use on public/shared computers

## Technical Details

### BIP44 Derivation Path for Ethereum
```
m / 44' / 60' / 0' / 0 / {account_index}
│   │     │     │    │    └── Address index (0 for first address)
│   │     │     │    └─────── Change (0 = external, 1 = internal)
│   │     │     └─────────── Account index (0 for first account)
│   │     └───────────────── Coin type (60 = Ethereum)
│   └─────────────────────── Purpose (44 = BIP44)
└─────────────────────────── Master key
```

### Test Vector Verification
For the BIP39 test phrase:
```
"abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
```

**Expected Results:**
- Seed: `5eb00bbddcf069084889a8ab9155568165...`
- First private key: `0x1ab42cc412b618bd...`
- First address: `0x9858EfFD232B4033E47d90003D41EC34EcaEda94`

## Integration with Whallet

### Recommended Implementation
```python
from eth_account import Account
from mnemonic import Mnemonic

class WhalletRecovery:
    def __init__(self):
        Account.enable_unaudited_hdwallet_features()
    
    def recover_from_mnemonic(self, phrase: str, passphrase: str = "", 
                            account_index: int = 0, address_index: int = 0):
        """Recover Ethereum account from BIP39 mnemonic"""
        path = f"m/44'/60'/{account_index}'/0/{address_index}"
        
        try:
            account = Account.from_mnemonic(
                phrase,
                passphrase=passphrase,
                account_path=path
            )
            return {
                'address': account.address,
                'private_key': account.key.hex(),
                'path': path
            }
        except Exception as e:
            raise ValueError(f"Recovery failed: {e}")
```

### Usage in Whallet
1. **Offline mode only** - Disable network features during recovery
2. **Input validation** - Verify 12/24 word phrases match BIP39 wordlist
3. **Progress feedback** - Show derivation progress for multiple accounts
4. **Secure storage** - Encrypt private keys before saving
5. **History cleanup** - Clear input fields and memory after use

## Troubleshooting

### Common Issues

1. **"Module not found" errors**
   ```bash
   # Reinstall packages
   pip uninstall eth-account mnemonic web3
   pip install eth-account mnemonic web3
   ```

2. **BIP32 derivation errors** (in experimental scripts)
   - Use `whallet_proper_recovery.py` instead
   - The experimental scripts have incomplete BIP32 implementation

3. **Invalid recovery phrase**
   - Verify word count (12, 15, 18, 21, or 24 words)
   - Check spelling against BIP39 English wordlist
   - Try with test phrase first

### Testing Procedure
1. Run `quick_whallet_test.py` to verify libraries work
2. Test with BIP39 test phrase (should succeed)
3. Test with your recovery phrase (OFFLINE ONLY)
4. Verify derived addresses match your known addresses

## File Descriptions

| File | Status | Purpose | Dependencies |
|------|--------|---------|--------------|
| `whallet_proper_recovery.py` | ✅ Working | Main recovery tool | `eth-account`, `mnemonic` |
| `recover_evm_key.py` | ⚠️ Experimental | Manual implementation | None (pure Python) |
| `whallet_key_recovery.py` | ⚠️ Experimental | Whallet-specific tool | None (pure Python) |
| `quick_whallet_test.py` | ✅ Test | Library verification | `eth-account`, `mnemonic` |
| `test_whallet_complete.py` | ⚠️ Test | Comprehensive test suite | All scripts |
| `test_mnemonic.py` | ✅ Test | Mnemonic module test | `mnemonic` |

## Development Notes

### Why Two Implementations?
1. **Proper implementation** (`whallet_proper_recovery.py`):
   - Uses battle-tested libraries (`eth-account`, `mnemonic`)
   - Follows BIP standards correctly
   - Recommended for production use

2. **Experimental implementations** (`recover_evm_key.py`, `whallet_key_recovery.py`):
   - Manual implementation for educational purposes
   - Helps understand BIP39/BIP32 internals
   - Not recommended for actual key recovery

### Performance Considerations
- **Proper implementation**: Fast, reliable, uses optimized libraries
- **Experimental implementations**: Slow, may fail on edge cases

## Legal & Security Disclaimer

**WARNING**: These tools handle cryptographic keys that control access to digital assets. Improper use can result in permanent loss of funds.

**YOU ARE SOLELY RESPONSIBLE FOR:**
- Securing your recovery phrases and private keys
- Using these tools in a secure environment
- Verifying addresses before transferring funds
- Backing up recovered keys securely

**THE AUTHOR PROVIDES NO WARRANTY** and is not responsible for any loss of funds, data, or other damages resulting from the use of these tools.

## Support

For issues with the **proper implementation** (`whallet_proper_recovery.py`):
1. Verify virtual environment is activated
2. Check package versions: `pip list | grep -E "(eth-account|mnemonic)"`
3. Test with BIP39 test phrase first

For **experimental implementations**, note that they have known issues with BIP32 derivation and should not be used for actual key recovery.

---

**Remember**: Always test with small amounts first, even after successful recovery. Never risk significant assets without thorough verification.