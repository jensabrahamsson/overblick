#!/usr/bin/env python3
"""
Store Gmail App Password credentials for an Överblick identity.

Prerequisites:
    1. Enable 2-step verification on your Google Account
    2. Create an App Password: Google Account → Security → App Passwords

Usage:
    python scripts/setup_gmail.py --identity stal
    python scripts/setup_gmail.py --identity stal --email user@gmail.com
    python scripts/setup_gmail.py --identity stal --email user@gmail.com --send-as alias@domain.com
"""

import argparse
import getpass
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(
        description="Store Gmail App Password for an Överblick identity",
    )
    parser.add_argument(
        "--identity",
        required=True,
        help="Överblick identity name (e.g., 'stal')",
    )
    parser.add_argument(
        "--email",
        help="Gmail address (prompted if not specified)",
    )
    parser.add_argument(
        "--send-as",
        help="Optional From address (Gmail 'Send mail as' alias)",
    )
    args = parser.parse_args()

    print(f"\n{'=' * 50}")
    print("Överblick Gmail Setup")
    print(f"{'=' * 50}")
    print(f"Identity: {args.identity}")
    print()
    print("You need a Google App Password.")
    print("Create one at: Google Account → Security → App Passwords")
    print(f"{'=' * 50}\n")

    email = args.email or input("Gmail address: ").strip()
    if not email:
        print("Error: email address is required")
        sys.exit(1)

    password = getpass.getpass("App Password (16 characters, no spaces): ").strip()
    password = password.replace(" ", "")  # Remove spaces from pasted passwords
    if not password:
        print("Error: app password is required")
        sys.exit(1)

    # Store in SecretsManager
    from overblick.core.security.secrets_manager import SecretsManager

    project_root = Path(__file__).parent.parent
    secrets_dir = project_root / "config" / "secrets"
    sm = SecretsManager(secrets_dir=secrets_dir)

    sm.set(args.identity, "gmail_address", email)
    sm.set(args.identity, "gmail_app_password", password)

    send_as = args.send_as
    if not send_as:
        send_as = input("Send as address (leave empty to use Gmail address): ").strip()
    if send_as:
        sm.set(args.identity, "gmail_send_as", send_as)

    print(f"\n{'=' * 50}")
    print("Done!")
    print(f"{'=' * 50}")
    print(f"Identity:      {args.identity}")
    print(f"Login:         {email}")
    if send_as:
        print(f"Send as:       {send_as}")
    secrets_list = "gmail_address, gmail_app_password"
    if send_as:
        secrets_list += ", gmail_send_as"
    print(f"Secrets saved: {secrets_list}")
    print(f"Location:      {secrets_dir / f'{args.identity}.yaml'}")
    print(f"\n{args.identity.capitalize()} can now access Gmail.")


if __name__ == "__main__":
    main()
