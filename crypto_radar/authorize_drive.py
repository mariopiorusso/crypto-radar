"""One-time interactive Drive authorization; never print credentials or tokens."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import requests

from dotenv import load_dotenv, set_key
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import ROOT
from .drive_report import DriveClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client_file", type=Path)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    try:
        client = json.loads(args.client_file.read_text(encoding="utf-8-sig"))["installed"]
        config = {"installed": {
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]}}
        # Existing arbitrary folders require Drive scope unless separately selected
        # through Google Picker. Google displays this permission at consent time.
        flow = InstalledAppFlow.from_client_config(config,
            scopes=["https://www.googleapis.com/auth/drive"], autogenerate_code_verifier=True)
        print("Opening Google sign-in. Choose the configured Google Drive account.", flush=True)
        credentials = flow.run_local_server(host="localhost", port=0, open_browser=True,
            timeout_seconds=600, authorization_prompt_message="Complete authorization in your browser.",
            success_message="Authorization received. You may close this tab and return to Crypto Radar.",
            access_type="offline", prompt="consent", login_hint=os.environ["GOOGLE_DRIVE_ACCOUNT"])
        if not credentials.refresh_token:
            raise ValueError("No offline refresh token returned")
        values = {"GOOGLE_DRIVE_CLIENT_ID": client["client_id"],
                  "GOOGLE_DRIVE_CLIENT_SECRET": client["client_secret"],
                  "GOOGLE_DRIVE_REFRESH_TOKEN": credentials.refresh_token}
        os.environ.update(values)
        with DriveClient():
            pass  # Verify the exact account and folder before persisting credentials.
        for name, value in values.items():
            set_key(str(ROOT / ".env"), name, value)
        print("Drive account and destination verified. Authorization saved to .env.", flush=True)
    except Exception as exc:
        print("Drive authorization did not complete:", type(exc).__name__, flush=True)
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            response = exc.response
            print("Google HTTP status:", response.status_code, flush=True)
            endpoint = urlparse(response.url)
            print("Endpoint:", endpoint.hostname, endpoint.path, flush=True)
            try:
                error = response.json().get("error", {})
                if isinstance(error, dict):
                    print("Google error status:", error.get("status", "unspecified"), flush=True)
                    reasons = [item.get("reason") for item in error.get("errors", [])]
                    reasons += [item.get("reason") for item in error.get("details", []) if isinstance(item, dict)]
                    print("Google error reasons:", reasons, flush=True)
                    for detail in error.get("details", []):
                        if isinstance(detail, dict):
                            metadata = detail.get("metadata", {})
                            for key in ("consumer", "service"):
                                if key in metadata:
                                    print("Google " + key + ":", metadata[key], flush=True)
                elif isinstance(error, str):
                    print("OAuth error code:", error, flush=True)
            except (ValueError, TypeError):
                pass
        raise SystemExit(1)


if __name__ == "__main__":
    main()
