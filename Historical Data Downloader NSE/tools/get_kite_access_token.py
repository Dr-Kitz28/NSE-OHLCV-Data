"""Interactive helper to generate a Kite access token and store it in the OS keyring.

Usage:
  - Optionally supply `--api-key` and `--api-secret`. If not provided the script will
    try to read them from the Python keyring under service 'Kite' with keys
    'APIKey' and 'APISecret'.
  - The script prints and stores the generated access token using keyring under
    service 'Kite' and username 'AccessToken'.

This script requires `kiteconnect` and `keyring` packages.
"""
from __future__ import annotations

import argparse
import webbrowser
import sys
from kiteconnect import KiteConnect

try:
    import keyring
except Exception:
    keyring = None


def store_in_keyring(name: str, key: str, value: str) -> None:
    if not keyring:
        print("keyring module not available; cannot store credentials automatically.")
        return
    keyring.set_password(name, key, value)
    print(f"Stored {key} in keyring service '{name}'.")


def read_from_keyring(name: str, key: str) -> str | None:
    if not keyring:
        return None
    return keyring.get_password(name, key)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", help="Kite API key (optional)")
    parser.add_argument("--api-secret", help="Kite API secret (optional)")
    args = parser.parse_args()

    api_key = args.api_key or (read_from_keyring("Kite", "APIKey") if keyring else None)
    api_secret = args.api_secret or (read_from_keyring("Kite", "APISecret") if keyring else None)

    if not api_key:
        api_key = input("Kite API key: ").strip()
    if not api_secret:
        api_secret = input("Kite API secret: ").strip()

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    print("Opening browser for Kite login — complete the login and copy the 'request_token' from the callback URL.")
    print("If the browser does not open automatically, visit this URL:")
    print(login_url)
    try:
        webbrowser.open(login_url)
    except Exception:
        pass

    request_token = input("Paste the request_token parameter from the callback URL: ").strip()
    if not request_token:
        print("No request_token provided — aborting.")
        sys.exit(1)

    try:
        session = kite.generate_session(request_token, api_secret=api_secret)
    except Exception as exc:
        print(f"Failed to create session: {exc}")
        sys.exit(2)

    access_token = session.get("access_token")
    if not access_token:
        print("No access_token returned by KiteConnect.")
        sys.exit(3)

    print("Access token:", access_token)
    if keyring:
        store_in_keyring("Kite", "AccessToken", access_token)
        # Also store API key/secret for convenience
        store_in_keyring("Kite", "APIKey", api_key)
        store_in_keyring("Kite", "APISecret", api_secret)
    else:
        print("Install the 'keyring' package to save token into Windows Credential Manager automatically:")
        print("  pip install keyring")


if __name__ == "__main__":
    main()
