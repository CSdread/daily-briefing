#!/usr/bin/env python3
"""
One-time OAuth2 authorization for Google Calendar.
Run this locally (not in k8s) to generate token.json.

Usage:
  pip install google-auth-oauthlib
  python authorize.py
  # Opens browser for Google sign-in
  # Saves token.json on completion

Then create the Kubernetes secret:
  kubectl create secret generic gcal-oauth \
    --from-file=credentials.json \
    --from-file=token.json \
    -n agents
"""

import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def main():
    if not Path(CREDENTIALS_FILE).exists():
        print(f"ERROR: {CREDENTIALS_FILE} not found.")
        print("Download it from Google Cloud Console:")
        print("  APIs & Services → Credentials → OAuth 2.0 Client IDs → Download JSON")
        return

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }

    Path(TOKEN_FILE).write_text(json.dumps(token_data, indent=2))
    print(f"Authorization complete. Token saved to {TOKEN_FILE}")
    print()
    print("Next steps:")
    print(f"  kubectl create secret generic gcal-oauth \\")
    print(f"    --from-file=credentials.json \\")
    print(f"    --from-file=token.json \\")
    print(f"    -n agents")


if __name__ == "__main__":
    main()
