#!/usr/bin/env python3
"""Monitored/resumable Slack backfill for llm-wiki.

This is intentionally verbose: it logs every conversation, history page, reply
batch, written file count, and checkpoint update so long backfills do not look
hung while large channels are being exported.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slack_sync import (
    ROOT,
    STATE_DIR,
    SlackApiError,
    SlackClient,
    day_from_ts,
    dedupe_messages,
    list_conversations,
    list_users,
    load_channel_filter,
    conversation_matches,
    token_from_env,
    utc_now,
    write_day_markdown,
    write_manifest,
)


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def existing_completed_channel_ids() -> set[str]:
    completed: set[str] = set()
    for path in (ROOT / "raw/slack").glob("*/*.md"):
        try:
            for line in path.read_text(errors="ignore").splitlines()[:10]:
                if line.startswith("channel_id:"):
                    completed.add(line.split(":", 1)[1].strip())
                    break
        except OSError:
            continue
    return completed


def fetch_history_monitored(
    client: SlackClient,
    channel_id: str,
    conv_name: str,
    *,
    oldest: float,
    latest: float,
    max_messages: int | None,
) -> list[dict[str, Any]]:
    cursor = ""
    messages: list[dict[str, Any]] = []
    page = 0
    while True:
        page += 1
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
        batch = data.get("messages", [])
        messages.extend(batch)
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        log(f"history #{conv_name} page={page} batch={len(batch)} total={len(messages)} has_more={bool(cursor)}")
        if max_messages and len(messages) >= max_messages:
            return messages[:max_messages]
        if not cursor:
            return messages


def fetch_replies_monitored(client: SlackClient, channel_id: str, conv_name: str, thread_ts: str) -> list[dict[str, Any]]:
    cursor = ""
    replies: list[dict[str, Any]] = []
    page = 0
    while True:
        page += 1
        params: dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            data = client.api("conversations.replies", params)
        except SlackApiError as exc:
            log(f"warning replies_failed #{conv_name} thread_ts={thread_ts} error={exc}")
            return replies
        batch = data.get("messages", [])
        replies.extend(batch)
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        log(f"replies #{conv_name} thread_ts={thread_ts} page={page} batch={len(batch)} total={len(replies)} has_more={bool(cursor)}")
        if not cursor:
            return replies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=10000)
    parser.add_argument("--types", default="public_channel,private_channel,mpim,im")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--use-users-conversations", action="store_true", help="Only conversations the user is a member of. Default uses conversations.list for all readable public channels too.")
    parser.add_argument("--max-messages-per-channel", type=int)
    parser.add_argument("--limit-conversations", type=int)
    parser.add_argument("--start-after-id")
    parser.add_argument("--no-user-names", action="store_true")
    parser.add_argument("--permalinks", action="store_true")
    parser.add_argument("--skip-existing-raw", action="store_true")
    parser.add_argument("--state-file", default=str(STATE_DIR / "slack-backfill-monitored-state.json"))
    args = parser.parse_args()

    token = token_from_env()
    client = SlackClient(token)
    latest = utc_now().timestamp()
    oldest = latest - (args.days * 24 * 60 * 60)
    synced_at = utc_now().isoformat(timespec="seconds")
    oldest_iso = datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat(timespec="seconds")
    latest_iso = datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(timespec="seconds")

    log(f"enumerating types={args.types} include_archived={args.include_archived} method={'users.conversations' if args.use_users_conversations else 'conversations.list'} window={oldest_iso}..{latest_iso}")
    includes, excludes = load_channel_filter()
    conversations = list_conversations(
        client,
        types=args.types,
        use_users_conversations=args.use_users_conversations,
        exclude_archived=not args.include_archived,
    )
    conversations = [c for c in conversations if conversation_matches(c, includes, excludes)]
    if args.start_after_id:
        seen = False
        trimmed = []
        for conv in conversations:
            if seen:
                trimmed.append(conv)
            elif conv.id == args.start_after_id:
                seen = True
        conversations = trimmed
    if args.limit_conversations:
        conversations = conversations[: args.limit_conversations]
    log(f"enumerated conversations={len(conversations)}")

    users = {} if args.no_user_names else list_users(client)
    log(f"loaded users={len(users)}")

    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    state = load_json(state_path, {"completed": {}, "errors": {}, "written": []})
    completed: dict[str, Any] = state.setdefault("completed", {})
    errors: dict[str, Any] = state.setdefault("errors", {})
    written_all = [ROOT / p for p in state.setdefault("written", []) if (ROOT / p).exists()]
    existing_completed = existing_completed_channel_ids() if args.skip_existing_raw else set()
    if existing_completed:
        log(f"skip_existing_raw channel_ids={len(existing_completed)}")

    for idx, conv in enumerate(conversations, start=1):
        if conv.id in completed:
            log(f"skip checkpoint [{idx}/{len(conversations)}] #{conv.name} ({conv.id})")
            continue
        if conv.id in existing_completed:
            completed[conv.id] = {"name": conv.name, "skipped_existing_raw": True}
            save_json(state_path, state)
            log(f"skip existing raw [{idx}/{len(conversations)}] #{conv.name} ({conv.id})")
            continue

        log(f"start [{idx}/{len(conversations)}] #{conv.name} ({conv.id})")
        try:
            roots = fetch_history_monitored(client, conv.id, conv.name, oldest=oldest, latest=latest, max_messages=args.max_messages_per_channel)
            all_messages = list(roots)
            threaded = [m for m in roots if m.get("reply_count") and m.get("thread_ts", m.get("ts")) == m.get("ts")]
            log(f"history_done #{conv.name} roots={len(roots)} threaded_roots={len(threaded)}")
            for t_idx, msg in enumerate(threaded, start=1):
                log(f"thread_start #{conv.name} {t_idx}/{len(threaded)} ts={msg['ts']}")
                all_messages.extend(fetch_replies_monitored(client, conv.id, conv.name, msg["ts"]))

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
                channel_paths.append(str(path.relative_to(ROOT)))
                written_all.append(path)
            completed[conv.id] = {"name": conv.name, "message_count": len(messages), "files": channel_paths}
            state["written"] = sorted({str(p.relative_to(ROOT)) for p in written_all if p.exists()})
            save_json(state_path, state)
            log(f"done #{conv.name} messages={len(messages)} files={len(channel_paths)} checkpoint={state_path.relative_to(ROOT)}")
        except Exception as exc:
            errors[conv.id] = {"name": conv.name, "error": str(exc)}
            save_json(state_path, state)
            log(f"error #{conv.name} ({conv.id}) {exc}")
            continue

    manifest = write_manifest([p for p in written_all if p.exists()], synced_at=synced_at, oldest_iso=oldest_iso, latest_iso=latest_iso)
    if manifest:
        log(f"manifest {manifest.relative_to(ROOT)} raw_files={len(written_all)}")
    log(f"complete completed={len(completed)} errors={len(errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
