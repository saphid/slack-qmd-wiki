#!/usr/bin/env python3
"""Sync Slack conversations into raw markdown sources for the LLM wiki.

This tool intentionally stops at source normalization. Semantic integration is done
by the LLM agent using AGENTS.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "raw" / "slack"
INBOX_DIR = ROOT / "inbox"
STATE_DIR = ROOT / ".state"
CHANNEL_FILTER_FILE = ROOT / "config" / "slack_channels.txt"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_from_ts(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")


def day_from_ts(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def safe_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or "unknown"


class SlackApiError(RuntimeError):
    pass


class SlackClient:
    def __init__(self, token: str, *, timeout: int = 60):
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def api(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"https://slack.com/api/{method}"
        while True:
            resp = self.session.get(url, params=params or {}, timeout=self.timeout)
            if resp.status_code == 429:
                retry = int(resp.headers.get("Retry-After", "5"))
                print(f"rate limited by Slack on {method}; sleeping {retry}s", file=sys.stderr)
                time.sleep(retry)
                continue
            if 500 <= resp.status_code < 600:
                retry = int(resp.headers.get("Retry-After", "10"))
                print(f"Slack {method} returned HTTP {resp.status_code}; sleeping {retry}s before retry", file=sys.stderr)
                time.sleep(retry)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                error = data.get("error", "unknown_error")
                if error == "token_expired":
                    refreshed = refresh_slack_user_token_from_env()
                    if refreshed and refreshed != self.token:
                        self.token = refreshed
                        self.session.headers.update({"Authorization": f"Bearer {refreshed}"})
                        continue
                needed = data.get("needed")
                provided = data.get("provided")
                details = f"Slack API {method} failed: {error}"
                if needed:
                    details += f"; needed={needed}; provided={provided}"
                raise SlackApiError(details)
            return data


@dataclass(frozen=True)
class Conversation:
    id: str
    name: str
    raw: dict[str, Any]

    @property
    def slug(self) -> str:
        return safe_slug(self.name or self.id)


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Tiny .env loader so the script works without an extra dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def update_dotenv_values(values: dict[str, str], path: Path = ROOT / ".env") -> None:
    """Update selected .env keys without logging secret values."""
    existing: list[str] = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    updated: list[str] = []
    for raw_line in existing:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            updated.append(raw_line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            updated.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            updated.append(raw_line)
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={value}")
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def refresh_slack_user_token_from_env() -> str | None:
    """Refresh Claude Code / Slack MCP user OAuth into a Slack Web API token.

    Claude Code's official Slack plugin uses https://mcp.slack.com/mcp with
    Slack OAuth. The resulting xoxe access token is accepted by Slack Web API,
    but expires. Store SLACK_REFRESH_TOKEN + SLACK_OAUTH_CLIENT_ID in .env so
    unattended wiki syncs can rotate it before use.
    """
    refresh_token = os.environ.get("SLACK_REFRESH_TOKEN")
    client_id = os.environ.get("SLACK_OAUTH_CLIENT_ID")
    if not refresh_token or not client_id:
        return None

    expires_at_raw = os.environ.get("SLACK_TOKEN_EXPIRES_AT")
    if os.environ.get("SLACK_USER_TOKEN") and expires_at_raw:
        try:
            expires_at = int(expires_at_raw)
        except ValueError:
            expires_at = 0
        # Refresh only when less than five minutes remain.
        if expires_at > int(time.time() * 1000) + (5 * 60 * 1000):
            return os.environ["SLACK_USER_TOKEN"]

    resp = requests.post(
        os.environ.get("SLACK_OAUTH_TOKEN_URL", "https://slack.com/api/oauth.v2.user.access"),
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok") or not data.get("access_token") or not data.get("refresh_token"):
        raise SystemExit(f"Slack OAuth refresh failed: {data.get('error', 'unknown_error')}")

    expires_at = int(time.time() * 1000) + (int(data.get("expires_in") or 43200) * 1000)
    values = {
        "SLACK_USER_TOKEN": data["access_token"],
        "SLACK_REFRESH_TOKEN": data["refresh_token"],
        "SLACK_OAUTH_CLIENT_ID": client_id,
        "SLACK_TOKEN_EXPIRES_AT": str(expires_at),
    }
    update_dotenv_values(values)
    os.environ.update(values)
    return data["access_token"]


def token_from_env() -> str:
    load_dotenv()
    token = refresh_slack_user_token_from_env() or os.environ.get("SLACK_USER_TOKEN") or os.environ.get("SLACK_BOT_TOKEN") or os.environ.get("SLACK_TOKEN")
    if not token:
        raise SystemExit(
            "Set SLACK_USER_TOKEN, SLACK_BOT_TOKEN, or SLACK_TOKEN in .env or the environment. "
            "For Claude Code Slack MCP OAuth, set SLACK_REFRESH_TOKEN and SLACK_OAUTH_CLIENT_ID. "
            "See .env.example."
        )
    return token


def load_channel_filter(path: Path = CHANNEL_FILTER_FILE) -> tuple[set[str], set[str]]:
    includes: set[str] = set()
    excludes: set[str] = set()
    if not path.exists():
        return includes, excludes
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        target = safe_slug(line[1:] if line.startswith("!") else line)
        if line.startswith("!"):
            excludes.add(target)
        else:
            includes.add(target)
    return includes, excludes


def conversation_matches(conv: Conversation, includes: set[str], excludes: set[str]) -> bool:
    keys = {safe_slug(conv.id), safe_slug(conv.name)}
    if keys & excludes:
        return False
    if includes and not (keys & includes):
        return False
    return True


def list_conversations(client: SlackClient, *, types: str, use_users_conversations: bool = True, exclude_archived: bool = True) -> list[Conversation]:
    auth = client.api("auth.test")
    user_id = auth.get("user_id")
    method = "users.conversations" if use_users_conversations and user_id else "conversations.list"
    cursor = ""
    conversations: list[Conversation] = []
    while True:
        params: dict[str, Any] = {
            "limit": 200,
            "types": types,
            "exclude_archived": exclude_archived,
        }
        if cursor:
            params["cursor"] = cursor
        if method == "users.conversations" and user_id:
            params["user"] = user_id
        try:
            data = client.api(method, params)
        except SlackApiError as exc:
            if method == "users.conversations":
                print(f"warning: users.conversations failed ({exc}); falling back to conversations.list", file=sys.stderr)
                return list_conversations(client, types=types, use_users_conversations=False, exclude_archived=exclude_archived)
            raise
        for item in data.get("channels", []):
            name = item.get("name") or item.get("name_normalized") or item.get("user") or item.get("id")
            conversations.append(Conversation(id=item["id"], name=name, raw=item))
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        if not cursor:
            break
    return conversations


def list_users(client: SlackClient) -> dict[str, str]:
    users: dict[str, str] = {}
    cursor = ""
    try:
        while True:
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = client.api("users.list", params)
            for member in data.get("members", []):
                profile = member.get("profile") or {}
                users[member["id"]] = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or member.get("real_name")
                    or member.get("name")
                    or member["id"]
                )
            cursor = data.get("response_metadata", {}).get("next_cursor") or ""
            if not cursor:
                break
    except SlackApiError as exc:
        print(f"warning: could not resolve users ({exc}); Slack user ids will be used", file=sys.stderr)
    return users


def fetch_history(
    client: SlackClient,
    channel_id: str,
    *,
    oldest: float,
    latest: float,
    max_messages: int | None,
) -> list[dict[str, Any]]:
    cursor = ""
    messages: list[dict[str, Any]] = []
    while True:
        params: dict[str, Any] = {
            "channel": channel_id,
            "oldest": f"{oldest:.6f}",
            "latest": f"{latest:.6f}",
            "inclusive": True,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = client.api("conversations.history", params)
        messages.extend(data.get("messages", []))
        if max_messages and len(messages) >= max_messages:
            return messages[:max_messages]
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        if not cursor:
            return messages


def fetch_replies(client: SlackClient, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
    cursor = ""
    replies: list[dict[str, Any]] = []
    while True:
        params: dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            data = client.api("conversations.replies", params)
        except SlackApiError as exc:
            print(f"warning: could not fetch replies channel={channel_id} thread_ts={thread_ts}: {exc}", file=sys.stderr)
            return replies
        replies.extend(data.get("messages", []))
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        if not cursor:
            return replies


def get_permalink(client: SlackClient, channel_id: str, ts: str) -> str | None:
    try:
        data = client.api("chat.getPermalink", {"channel": channel_id, "message_ts": ts})
        return data.get("permalink")
    except SlackApiError:
        return None


def message_author(message: dict[str, Any], users: dict[str, str]) -> str:
    if message.get("user"):
        return users.get(message["user"], message["user"])
    if message.get("bot_profile", {}).get("name"):
        return message["bot_profile"]["name"]
    if message.get("username"):
        return message["username"]
    return message.get("subtype") or "unknown"


def attachment_summary(message: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for file in message.get("files", []) or []:
        title = file.get("title") or file.get("name") or file.get("id")
        url = file.get("url_private") or file.get("permalink") or ""
        lines.append(f"- file: {title} {url}".rstrip())
    for attachment in message.get("attachments", []) or []:
        title = attachment.get("title") or attachment.get("fallback") or "attachment"
        url = attachment.get("title_link") or attachment.get("from_url") or ""
        lines.append(f"- attachment: {title} {url}".rstrip())
    return lines


def write_day_markdown(
    *,
    date: str,
    conv: Conversation,
    messages: list[dict[str, Any]],
    users: dict[str, str],
    synced_at: str,
    oldest_iso: str,
    latest_iso: str,
    include_permalinks: bool,
    client: SlackClient,
) -> Path:
    day_dir = RAW_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{conv.slug}.md"
    json_path = day_dir / f"{conv.slug}.json"

    # Preserve the complete Slack payload as a local sidecar. It is gitignored by default.
    json_path.write_text(json.dumps(messages, indent=2, sort_keys=True), encoding="utf-8")

    lines: list[str] = [
        "---",
        "source: slack",
        f"channel_id: {conv.id}",
        f"channel_name: {conv.name}",
        f"date: {date}",
        f"synced_at: {synced_at}",
        f"window_start: {oldest_iso}",
        f"window_end: {latest_iso}",
        f"message_count: {len(messages)}",
        "---",
        "",
        f"# Slack export: #{conv.name} on {date}",
        "",
        "This is a raw source export. Do not edit by hand; integrate durable knowledge into `wiki/`.",
        "",
    ]

    for msg in sorted(messages, key=lambda m: float(m.get("ts", "0"))):
        ts = msg.get("ts", "")
        author = message_author(msg, users)
        thread_ts = msg.get("thread_ts")
        thread_marker = ""
        if thread_ts and thread_ts != ts:
            thread_marker = f" thread_ts={thread_ts}"
        elif msg.get("reply_count"):
            thread_marker = f" thread_root replies={msg.get('reply_count')}"
        permalink = None
        if include_permalinks and ts:
            permalink = get_permalink(client, conv.id, ts)
        lines.extend([
            f"## {iso_from_ts(ts)} | {author} | ts={ts}{thread_marker}",
            "",
        ])
        if permalink:
            lines.extend([f"Permalink: {permalink}", ""])
        text = (msg.get("text") or "").strip()
        if text:
            lines.extend([text, ""])
        extras = attachment_summary(msg)
        if extras:
            lines.extend(extras + [""])
        subtype = msg.get("subtype")
        if subtype and not text:
            lines.extend([f"_Slack subtype: {subtype}_", ""])

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def dedupe_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ts: dict[str, dict[str, Any]] = {}
    for msg in messages:
        ts = msg.get("ts")
        if not ts:
            continue
        by_ts[ts] = msg
    return sorted(by_ts.values(), key=lambda m: float(m["ts"]))


def write_manifest(paths: list[Path], *, synced_at: str, oldest_iso: str, latest_iso: str) -> Path | None:
    if not paths:
        return None
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    manifest_name = f"slack-ingest-{synced_at.replace(':', '').replace('+00:00', 'Z')}.md"
    path = INBOX_DIR / manifest_name
    rel_paths = [p.relative_to(ROOT) for p in sorted(paths)]
    lines = [
        f"# Slack ingest manifest — {synced_at}",
        "",
        f"Window: {oldest_iso} to {latest_iso}",
        "",
        "Follow `AGENTS.md` Slack ingest workflow. Process these raw sources one at a time, update the wiki, then append `wiki/log.md`.",
        "",
        "## Raw sources",
        "",
    ]
    lines.extend(f"- `{rel}`" for rel in rel_paths)
    lines.extend([
        "",
        "## Suggested pi command",
        "",
        "```bash",
        f"pi \"Ingest {path.relative_to(ROOT)} into the wiki. Follow AGENTS.md exactly.\"",
        "```",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=1.0, help="Rolling window to sync, in days. Default: 1")
    parser.add_argument("--include-dms", action="store_true", help="Include IM and MPIM conversations. Default: off")
    parser.add_argument("--types", help="Override Slack conversation types CSV")
    parser.add_argument("--max-messages-per-channel", type=int, help="Safety cap per channel for the history call")
    parser.add_argument("--no-user-names", action="store_true", help="Do not call users.list; keep Slack user ids")
    parser.add_argument("--permalinks", action="store_true", help="Call chat.getPermalink for every message (slower, more API calls)")
    parser.add_argument("--conversations-list", action="store_true", help="Use conversations.list instead of users.conversations")
    parser.add_argument("--include-archived", action="store_true", help="Include archived conversations. Default: off")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = token_from_env()
    client = SlackClient(token)

    latest = utc_now().timestamp()
    oldest = latest - (args.days * 24 * 60 * 60)
    synced_at_dt = utc_now()
    synced_at = synced_at_dt.isoformat(timespec="seconds")
    oldest_iso = datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat(timespec="seconds")
    latest_iso = datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(timespec="seconds")

    types = args.types or "public_channel,private_channel"
    if args.include_dms and not args.types:
        types += ",mpim,im"

    includes, excludes = load_channel_filter()
    conversations = list_conversations(
        client,
        types=types,
        use_users_conversations=not args.conversations_list,
        exclude_archived=not args.include_archived,
    )
    conversations = [c for c in conversations if conversation_matches(c, includes, excludes)]
    print(f"syncing {len(conversations)} conversations; types={types}; window={oldest_iso}..{latest_iso}")

    users = {} if args.no_user_names else list_users(client)
    written: list[Path] = []
    state: dict[str, Any] = {
        "synced_at": synced_at,
        "window_start": oldest_iso,
        "window_end": latest_iso,
        "types": types,
        "conversations": {},
    }

    for idx, conv in enumerate(conversations, start=1):
        print(f"[{idx}/{len(conversations)}] #{conv.name} ({conv.id})", file=sys.stderr)
        try:
            roots = fetch_history(
                client,
                conv.id,
                oldest=oldest,
                latest=latest,
                max_messages=args.max_messages_per_channel,
            )
        except SlackApiError as exc:
            print(f"warning: skipping #{conv.name}: {exc}", file=sys.stderr)
            state["conversations"][conv.id] = {"name": conv.name, "error": str(exc)}
            continue

        all_messages: list[dict[str, Any]] = list(roots)
        for msg in roots:
            if msg.get("reply_count") and msg.get("thread_ts", msg.get("ts")) == msg.get("ts"):
                all_messages.extend(fetch_replies(client, conv.id, msg["ts"]))

        messages = dedupe_messages(all_messages)
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for msg in messages:
            by_day[day_from_ts(msg["ts"])].append(msg)

        channel_paths: list[str] = []
        for date, day_messages in sorted(by_day.items()):
            path = write_day_markdown(
                date=date,
                conv=conv,
                messages=day_messages,
                users=users,
                synced_at=synced_at,
                oldest_iso=oldest_iso,
                latest_iso=latest_iso,
                include_permalinks=args.permalinks,
                client=client,
            )
            written.append(path)
            channel_paths.append(str(path.relative_to(ROOT)))

        state["conversations"][conv.id] = {
            "name": conv.name,
            "message_count": len(messages),
            "files": channel_paths,
        }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "slack-sync-last.json").write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    manifest = write_manifest(written, synced_at=synced_at, oldest_iso=oldest_iso, latest_iso=latest_iso)
    if manifest:
        print(f"wrote {len(written)} raw source files")
        print(f"wrote manifest: {manifest.relative_to(ROOT)}")
    else:
        print("no messages found; no manifest written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
