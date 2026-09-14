#!/usr/bin/env python3
"""Read-only Microsoft Graph client for the Nyx Hermes assistant."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import msal
import requests


CLIENT_ID = "701f65b7-155a-4553-868e-f6bb3006bfa2"
TENANT_ID = "e442e1ab-fd6b-4ba3-abf3-b020eb50df37"
SCOPES = [
    "Calendars.Read",
    "Chat.Read",
    "ChatMember.Read",
    "ChatMessage.Read",
    "Mail.Read",
    "MailboxSettings.Read",
    "User.Read",
]
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def token_cache_path() -> Path:
    configured = os.environ.get("HERMES_MSGRAPH_TOKEN_CACHE")
    return Path(configured) if configured else Path.home() / ".hermes" / "profiles" / "assistant" / "msgraph_token_cache.json"


def save_cache(cache: msal.SerializableTokenCache, path: Path) -> None:
    if not cache.has_state_changed:
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(cache.serialize())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def access_token() -> str:
    path = token_cache_path()
    if not path.is_file():
        raise RuntimeError(f"Microsoft OAuth cache is missing: {path}")

    cache = msal.SerializableTokenCache()
    cache.deserialize(path.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError("Microsoft OAuth cache has no account")

    result = app.acquire_token_silent_with_error(SCOPES, account=accounts[0])
    save_cache(cache, path)
    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description", (result or {}).get("error", "no cached token"))
        raise RuntimeError(f"Microsoft OAuth refresh failed; reauthenticate the custom enterprise app and reseed SOPS: {detail}")
    return str(result["access_token"])


def graph_get(token: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{GRAPH_ROOT}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=45,
    )
    response.raise_for_status()
    return response.json()


def bounded(value: int) -> int:
    return max(1, min(value, 50))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("me")

    mail = subparsers.add_parser("mail")
    mail.add_argument("--max", type=int, default=10)

    calendar = subparsers.add_parser("calendar")
    calendar.add_argument("--days", type=int, default=7)
    calendar.add_argument("--max", type=int, default=20)

    chats = subparsers.add_parser("teams-chats")
    chats.add_argument("--max", type=int, default=10)

    messages = subparsers.add_parser("teams-messages")
    messages.add_argument("chat_id")
    messages.add_argument("--max", type=int, default=20)

    args = parser.parse_args()
    token = access_token()

    if args.command == "status":
        results = {}
        for name, path in {
            "profile": "/me?$select=id",
            "mail": "/me/messages?$top=1&$select=id",
            "calendar": "/me/events?$top=1&$select=id",
            "teams": "/me/chats?$top=1&$select=id",
        }.items():
            try:
                graph_get(token, path)
                results[name] = "ok"
            except requests.RequestException as error:
                results[name] = f"error: {error}"
        print(json.dumps(results, indent=2))
        return

    if args.command == "me":
        data = graph_get(token, "/me", {"$select": "displayName,mail,userPrincipalName"})
    elif args.command == "mail":
        data = graph_get(
            token,
            "/me/messages",
            {
                "$top": bounded(args.max),
                "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,importance,hasAttachments",
                "$orderby": "receivedDateTime desc",
            },
        )
    elif args.command == "calendar":
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=max(1, min(args.days, 31)))
        data = graph_get(
            token,
            "/me/calendarView",
            {
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                "$top": bounded(args.max),
                "$select": "id,subject,start,end,location,organizer,isCancelled,showAs,webLink",
                "$orderby": "start/dateTime",
            },
        )
    elif args.command == "teams-chats":
        data = graph_get(
            token,
            "/me/chats",
            {"$top": bounded(args.max), "$select": "id,topic,chatType,createdDateTime,lastUpdatedDateTime,webUrl"},
        )
    else:
        data = graph_get(
            token,
            f"/chats/{args.chat_id}/messages",
            {"$top": bounded(args.max), "$select": "id,createdDateTime,lastModifiedDateTime,from,body,importance,webUrl"},
        )

    print(json.dumps(data.get("value", data), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, requests.RequestException) as error:
        raise SystemExit(f"error: {error}") from error
