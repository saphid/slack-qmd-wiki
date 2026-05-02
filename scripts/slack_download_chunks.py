#!/usr/bin/env python3
"""Download Slack conversations to durable page-sized chunks before processing.

The point is to make Slack API collection resumable and observable. Each
conversations.history or conversations.replies page is written immediately to
`chunks/slack/<run-id>/<channel-id>/...` before any markdown/wiki processing.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slack_sync import (
    ROOT,
    STATE_DIR,
    SlackApiError,
    SlackClient,
    conversation_matches,
    list_conversations,
    list_users,
    load_channel_filter,
    safe_slug,
    token_from_env,
    utc_now,
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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def safe_ts(ts: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]+", "-", ts)


def existing_completed_channel_ids() -> set[str]:
    completed: set[str] = set()
    for path in (ROOT / "raw/slack").glob("*/*.md"):
        try:
            for line in path.read_text(errors="ignore").splitlines()[:12]:
                if line.startswith("channel_id:"):
                    completed.add(line.split(":", 1)[1].strip())
                    break
        except OSError:
            continue
    return completed


def read_history_thread_roots(conv_dir: Path) -> list[str]:
    roots: set[str] = set()
    for page in sorted((conv_dir / "history").glob("page-*.json")):
        data = json.loads(page.read_text())
        for msg in data.get("messages", []):
            ts = msg.get("ts")
            if ts and msg.get("reply_count") and msg.get("thread_ts", ts) == ts:
                roots.add(ts)
    return sorted(roots, key=lambda v: float(v), reverse=True)


def download_history(
    client: SlackClient,
    conv: dict[str, Any],
    conv_dir: Path,
    conv_state: dict[str, Any],
    *,
    oldest: float,
    latest: float,
    max_pages: int | None,
) -> None:
    cursor = conv_state.get("history_next_cursor") or ""
    page_no = int(conv_state.get("history_pages", 0))
    while True:
        if max_pages is not None and page_no >= max_pages:
            log(f"history_page_limit #{conv['name']} pages={page_no}")
            return
        params: dict[str, Any] = {
            "channel": conv["id"],
            "oldest": f"{oldest:.6f}",
            "latest": f"{latest:.6f}",
            "inclusive": True,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = client.api("conversations.history", params)
        page_no += 1
        next_cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        path = conv_dir / "history" / f"page-{page_no:06d}.json"
        write_json(path, {
            "kind": "history_page",
            "channel": conv,
            "page": page_no,
            "fetched_at": utc_now().isoformat(timespec="seconds"),
            "oldest": params["oldest"],
            "latest": params["latest"],
            "next_cursor": next_cursor,
            "messages": data.get("messages", []),
        })
        conv_state["history_pages"] = page_no
        conv_state["history_next_cursor"] = next_cursor
        conv_state["history_messages"] = int(conv_state.get("history_messages", 0)) + len(data.get("messages", []))
        conv_state["updated_at"] = utc_now().isoformat(timespec="seconds")
        log(f"history_chunk #{conv['name']} page={page_no} batch={len(data.get('messages', []))} total={conv_state['history_messages']} has_more={bool(next_cursor)} path={path.relative_to(ROOT)}")
        if not next_cursor:
            conv_state["history_complete"] = True
            conv_state.pop("history_next_cursor", None)
            return
        cursor = next_cursor


def download_replies(
    client: SlackClient,
    conv: dict[str, Any],
    conv_dir: Path,
    conv_state: dict[str, Any],
    *,
    max_threads: int | None,
) -> None:
    roots = read_history_thread_roots(conv_dir)
    done = set(conv_state.get("reply_threads_complete", []))
    conv_state["reply_threads_total"] = len(roots)
    for index, thread_ts in enumerate(roots, start=1):
        if thread_ts in done:
            continue
        if max_threads is not None and len(done) >= max_threads:
            log(f"reply_thread_limit #{conv['name']} complete={len(done)} total={len(roots)}")
            return
        cursor = ""
        page_no = 0
        thread_dir = conv_dir / "replies" / safe_ts(thread_ts)
        log(f"reply_thread_start #{conv['name']} {index}/{len(roots)} ts={thread_ts}")
        while True:
            params: dict[str, Any] = {"channel": conv["id"], "ts": thread_ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                data = client.api("conversations.replies", params)
            except SlackApiError as exc:
                log(f"warning reply_failed #{conv['name']} thread_ts={thread_ts} error={exc}")
                conv_state.setdefault("reply_thread_errors", {})[thread_ts] = str(exc)
                break
            page_no += 1
            next_cursor = data.get("response_metadata", {}).get("next_cursor") or ""
            path = thread_dir / f"page-{page_no:06d}.json"
            write_json(path, {
                "kind": "reply_page",
                "channel": conv,
                "thread_ts": thread_ts,
                "page": page_no,
                "fetched_at": utc_now().isoformat(timespec="seconds"),
                "next_cursor": next_cursor,
                "messages": data.get("messages", []),
            })
            conv_state["reply_messages"] = int(conv_state.get("reply_messages", 0)) + len(data.get("messages", []))
            conv_state["updated_at"] = utc_now().isoformat(timespec="seconds")
            log(f"reply_chunk #{conv['name']} thread_ts={thread_ts} page={page_no} batch={len(data.get('messages', []))} reply_messages={conv_state['reply_messages']} has_more={bool(next_cursor)} path={path.relative_to(ROOT)}")
            if not next_cursor:
                break
            cursor = next_cursor
        done.add(thread_ts)
        conv_state["reply_threads_complete"] = sorted(done, key=lambda v: float(v), reverse=True)
    conv_state["replies_complete"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=10000)
    parser.add_argument("--types", default="public_channel,private_channel,mpim,im")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--use-users-conversations", action="store_true")
    parser.add_argument("--skip-existing-raw", action="store_true")
    parser.add_argument("--only-channel-id")
    parser.add_argument("--limit-conversations", type=int)
    parser.add_argument("--max-history-pages-per-channel", type=int)
    parser.add_argument("--max-reply-threads-per-channel", type=int)
    parser.add_argument("--run-id", default="all-feeds")
    parser.add_argument("--state-file", default=str(STATE_DIR / "slack-chunk-download-state.json"))
    args = parser.parse_args()

    token = token_from_env()
    client = SlackClient(token)
    chunk_root = ROOT / "chunks" / "slack" / args.run_id
    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    latest = utc_now().timestamp()
    oldest = latest - (args.days * 24 * 60 * 60)
    window = {
        "window_start": datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat(timespec="seconds"),
        "window_end": datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(timespec="seconds"),
        "days": args.days,
    }

    state = load_json(state_path, {"conversations": {}, "errors": {}, "run_id": args.run_id, **window})
    state.update(window)
    state["chunk_root"] = str(chunk_root.relative_to(ROOT))

    includes, excludes = load_channel_filter()
    log(f"enumerating types={args.types} include_archived={args.include_archived} method={'users.conversations' if args.use_users_conversations else 'conversations.list'}")
    conversations = list_conversations(client, types=args.types, use_users_conversations=args.use_users_conversations, exclude_archived=not args.include_archived)
    conversations = [c for c in conversations if conversation_matches(c, includes, excludes)]
    if args.only_channel_id:
        conversations = [c for c in conversations if c.id == args.only_channel_id]
    if args.limit_conversations:
        conversations = conversations[: args.limit_conversations]
    log(f"enumerated conversations={len(conversations)}")
    write_json(chunk_root / "conversations.json", [c.raw for c in conversations])
    users = list_users(client)
    write_json(chunk_root / "users.json", users)
    log(f"stored users={len(users)} chunk_root={chunk_root.relative_to(ROOT)}")

    existing = existing_completed_channel_ids() if args.skip_existing_raw else set()
    if existing:
        log(f"skip_existing_raw channel_ids={len(existing)}")

    for idx, c in enumerate(conversations, start=1):
        conv = {"id": c.id, "name": c.name, "slug": c.slug, "raw": c.raw}
        conv_state = state["conversations"].setdefault(c.id, {"name": c.name, "status": "new"})
        if conv_state.get("status") == "complete":
            log(f"skip complete [{idx}/{len(conversations)}] #{c.name} ({c.id})")
            continue
        if c.id in existing:
            conv_state.update({"name": c.name, "status": "skipped_existing_raw", "updated_at": utc_now().isoformat(timespec="seconds")})
            save_json(state_path, state)
            log(f"skip existing raw [{idx}/{len(conversations)}] #{c.name} ({c.id})")
            continue
        conv_dir = chunk_root / c.id
        write_json(conv_dir / "conversation.json", conv)
        log(f"start [{idx}/{len(conversations)}] #{c.name} ({c.id})")
        try:
            conv_state["status"] = "history"
            save_json(state_path, state)
            if not conv_state.get("history_complete"):
                download_history(client, conv, conv_dir, conv_state, oldest=oldest, latest=latest, max_pages=args.max_history_pages_per_channel)
                save_json(state_path, state)
                if not conv_state.get("history_complete"):
                    continue
            conv_state["status"] = "replies"
            save_json(state_path, state)
            if not conv_state.get("replies_complete"):
                download_replies(client, conv, conv_dir, conv_state, max_threads=args.max_reply_threads_per_channel)
                save_json(state_path, state)
                if not conv_state.get("replies_complete"):
                    continue
            conv_state["status"] = "complete"
            conv_state["updated_at"] = utc_now().isoformat(timespec="seconds")
            save_json(state_path, state)
            log(f"done #{c.name} history_messages={conv_state.get('history_messages',0)} reply_messages={conv_state.get('reply_messages',0)} reply_threads={len(conv_state.get('reply_threads_complete', []))}/{conv_state.get('reply_threads_total', 0)}")
        except Exception as exc:
            state["errors"][c.id] = {"name": c.name, "error": str(exc), "updated_at": utc_now().isoformat(timespec="seconds")}
            conv_state["status"] = "error"
            save_json(state_path, state)
            log(f"error #{c.name} ({c.id}) {exc}")
            continue
    log(f"complete_or_paused state={state_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
