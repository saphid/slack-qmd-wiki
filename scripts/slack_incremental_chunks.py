#!/usr/bin/env python3
"""Near-real-time Slack incremental chunk downloader.

This complements the full historical downloader. It keeps a lightweight
round-robin cursor over all visible conversations and a small hot-channel list
that is checked every run. Each run writes only newly observed Slack API pages to
`chunks/slack/<run-id>/...`, then the wrapper materializes those pages for QMD.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from slack_sync import (
    ROOT,
    STATE_DIR,
    SlackApiError,
    SlackClient,
    Conversation,
    conversation_matches,
    list_conversations,
    list_users,
    load_channel_filter,
    safe_slug,
    token_from_env,
    utc_now,
)

HOT_CHANNELS_FILE = ROOT / "config" / "slack_realtime_channels.txt"
DEFAULT_STATE_FILE = STATE_DIR / "slack-realtime-sync-state.json"


def log(message: str) -> None:
    print(f"{utc_now().isoformat(timespec='seconds')} {message}", flush=True)


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


def ts_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_hot_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(safe_slug(line))
    return keys


def hot_match(conv: Conversation, hot_keys: set[str]) -> bool:
    if not hot_keys:
        return False
    keys = {safe_slug(conv.id), safe_slug(conv.name)}
    return bool(keys & hot_keys)


def stable_conversations(conversations: Iterable[Conversation]) -> list[Conversation]:
    return sorted(conversations, key=lambda c: (safe_slug(c.name), c.id))


def select_targets(
    conversations: list[Conversation],
    state: dict[str, Any],
    hot_keys: set[str],
    batch_size: int,
) -> tuple[list[Conversation], int, int]:
    hot = [c for c in conversations if hot_match(c, hot_keys)]
    if not conversations or batch_size <= 0:
        selected = hot
        return selected, int(state.get("round_robin_offset", 0) or 0), len(hot)

    offset = int(state.get("round_robin_offset", 0) or 0) % len(conversations)
    batch: list[Conversation] = []
    for i in range(min(batch_size, len(conversations))):
        batch.append(conversations[(offset + i) % len(conversations)])
    next_offset = (offset + len(batch)) % len(conversations)

    by_id: dict[str, Conversation] = {}
    for conv in hot + batch:
        by_id.setdefault(conv.id, conv)
    return list(by_id.values()), next_offset, len(hot)


def api_history_pages(
    client: SlackClient,
    conv: Conversation,
    *,
    oldest: float,
    latest: float,
    previous_latest_ts: float,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    cursor = ""
    page_no = 0
    saturated = False
    all_new: list[dict[str, Any]] = []
    raw_pages: list[dict[str, Any]] = []
    while True:
        if page_no >= max_pages:
            saturated = True
            log(f"history_page_limit #{conv.name} pages={page_no}; not advancing watermark")
            break
        params: dict[str, Any] = {
            "channel": conv.id,
            "oldest": f"{oldest:.6f}",
            "latest": f"{latest:.6f}",
            "inclusive": True,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = client.api("conversations.history", params)
        page_no += 1
        messages = data.get("messages", []) or []
        new_messages = [m for m in messages if ts_float(m.get("ts")) > previous_latest_ts]
        if new_messages:
            all_new.extend(new_messages)
            raw_pages.append({
                "kind": "history_page",
                "channel": {"id": conv.id, "name": conv.name, "raw": conv.raw},
                "page": len(raw_pages) + 1,
                "fetched_at": utc_now().isoformat(timespec="seconds"),
                "oldest": params["oldest"],
                "latest": params["latest"],
                "source_method": "conversations.history",
                "messages": new_messages,
            })
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        if not cursor:
            break
    return all_new, raw_pages, page_no, saturated


def api_reply_pages(
    client: SlackClient,
    conv: Conversation,
    thread_ts: str,
    *,
    oldest: float,
    latest: float,
    previous_latest_ts: float,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    cursor = ""
    page_no = 0
    saturated = False
    all_new: list[dict[str, Any]] = []
    raw_pages: list[dict[str, Any]] = []
    while True:
        if page_no >= max_pages:
            saturated = True
            log(f"reply_page_limit #{conv.name} thread_ts={thread_ts} pages={page_no}; not advancing watermark")
            break
        params: dict[str, Any] = {
            "channel": conv.id,
            "ts": thread_ts,
            "oldest": f"{oldest:.6f}",
            "latest": f"{latest:.6f}",
            "inclusive": True,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = client.api("conversations.replies", params)
        page_no += 1
        messages = data.get("messages", []) or []
        new_messages = [m for m in messages if ts_float(m.get("ts")) > previous_latest_ts]
        if new_messages:
            all_new.extend(new_messages)
            raw_pages.append({
                "kind": "reply_page",
                "channel": {"id": conv.id, "name": conv.name, "raw": conv.raw},
                "thread_ts": thread_ts,
                "page": len(raw_pages) + 1,
                "fetched_at": utc_now().isoformat(timespec="seconds"),
                "oldest": params["oldest"],
                "latest": params["latest"],
                "source_method": "conversations.replies",
                "messages": new_messages,
            })
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        if not cursor:
            break
    return all_new, raw_pages, page_no, saturated


def remember_active_threads(
    active_threads: dict[str, Any],
    conv: Conversation,
    messages: Iterable[dict[str, Any]],
    *,
    retain_until: float,
) -> int:
    count = 0
    for msg in messages:
        ts = msg.get("ts")
        thread_ts = msg.get("thread_ts") or ts
        reply_count = int(msg.get("reply_count") or 0)
        latest_reply = ts_float(msg.get("latest_reply"), ts_float(ts))
        # Roots with replies and direct thread replies are worth rechecking soon.
        if not ts or (reply_count <= 0 and thread_ts == ts):
            continue
        root_ts = thread_ts if thread_ts else ts
        key = f"{conv.id}:{root_ts}"
        entry = active_threads.setdefault(key, {
            "channel_id": conv.id,
            "channel_name": conv.name,
            "thread_ts": root_ts,
            "latest_seen_ts": ts_float(root_ts),
        })
        entry["channel_name"] = conv.name
        entry["retain_until_ts"] = retain_until
        entry["slack_latest_reply_ts"] = max(ts_float(entry.get("slack_latest_reply_ts")), latest_reply)
        count += 1
    return count


def prune_active_threads(active_threads: dict[str, Any], now_ts: float) -> None:
    stale = [key for key, value in active_threads.items() if ts_float(value.get("retain_until_ts")) < now_ts]
    for key in stale:
        active_threads.pop(key, None)


def process_conversation(
    client: SlackClient,
    conv: Conversation,
    run_dir: Path,
    state: dict[str, Any],
    *,
    now_ts: float,
    bootstrap_lookback_secs: float,
    overlap_secs: float,
    max_history_pages: int,
) -> dict[str, Any]:
    channels = state.setdefault("channels", {})
    channel_state = channels.setdefault(conv.id, {"name": conv.name})
    previous_latest = ts_float(channel_state.get("latest_history_ts"))
    if previous_latest > 0:
        oldest = max(0.0, previous_latest - overlap_secs)
    else:
        oldest = max(0.0, now_ts - bootstrap_lookback_secs)
    conv_dir = run_dir / conv.id
    write_json(conv_dir / "conversation.json", {"id": conv.id, "name": conv.name, "slug": conv.slug, "raw": conv.raw})

    new_messages, pages, api_pages, saturated = api_history_pages(
        client,
        conv,
        oldest=oldest,
        latest=now_ts,
        previous_latest_ts=previous_latest,
        max_pages=max_history_pages,
    )

    for index, page in enumerate(pages, start=1):
        write_json(conv_dir / "history" / f"page-{index:06d}.json", page)

    latest_seen = previous_latest
    if new_messages and not saturated:
        latest_seen = max(latest_seen, max(ts_float(m.get("ts")) for m in new_messages))
        channel_state["latest_history_ts"] = f"{latest_seen:.6f}"
    if saturated:
        channel_state["history_backlog"] = True
    else:
        channel_state.pop("history_backlog", None)
    channel_state["name"] = conv.name
    channel_state["last_checked_at"] = utc_now().isoformat(timespec="seconds")
    channel_state["last_history_api_pages"] = api_pages
    channel_state["last_new_messages"] = len(new_messages)

    retain_until = now_ts + float(state.get("active_thread_retention_secs", 7 * 24 * 60 * 60))
    active_added = remember_active_threads(state.setdefault("active_threads", {}), conv, new_messages, retain_until=retain_until)

    return {
        "channel_id": conv.id,
        "channel_name": conv.name,
        "history_api_pages": api_pages,
        "history_pages_written": len(pages),
        "history_messages_written": len(new_messages),
        "active_threads_added": active_added,
        "history_backlog": saturated,
    }


def process_active_threads(
    client: SlackClient,
    conv_by_id: dict[str, Conversation],
    run_dir: Path,
    state: dict[str, Any],
    *,
    now_ts: float,
    overlap_secs: float,
    max_threads: int,
    max_reply_pages: int,
) -> dict[str, int]:
    active_threads = state.setdefault("active_threads", {})
    candidates = sorted(
        active_threads.items(),
        key=lambda item: item[1].get("last_checked_at", ""),
    )[:max_threads]
    threads_checked = 0
    pages_written = 0
    messages_written = 0
    saturated_threads = 0
    for key, entry in candidates:
        conv = conv_by_id.get(entry.get("channel_id"))
        if not conv:
            continue
        thread_ts = str(entry.get("thread_ts"))
        previous_latest = max(ts_float(entry.get("latest_seen_ts")), ts_float(thread_ts))
        oldest = max(0.0, previous_latest - overlap_secs)
        try:
            new_replies, pages, _api_pages, saturated = api_reply_pages(
                client,
                conv,
                thread_ts,
                oldest=oldest,
                latest=now_ts,
                previous_latest_ts=previous_latest,
                max_pages=max_reply_pages,
            )
        except SlackApiError as exc:
            entry["last_error"] = str(exc)
            entry["last_checked_at"] = utc_now().isoformat(timespec="seconds")
            continue
        thread_dir = run_dir / conv.id / "replies" / safe_ts(thread_ts)
        for index, page in enumerate(pages, start=1):
            write_json(thread_dir / f"page-{index:06d}.json", page)
        if new_replies and not saturated:
            entry["latest_seen_ts"] = f"{max(ts_float(m.get('ts')) for m in new_replies):.6f}"
        if saturated:
            entry["reply_backlog"] = True
            saturated_threads += 1
        else:
            entry.pop("reply_backlog", None)
        if new_replies:
            messages_written += len(new_replies)
            pages_written += len(pages)
        entry["last_checked_at"] = utc_now().isoformat(timespec="seconds")
        threads_checked += 1
    return {"threads_checked": threads_checked, "reply_pages_written": pages_written, "reply_messages_written": messages_written, "reply_backlogs": saturated_threads}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--types", default="public_channel,private_channel,mpim,im")
    ap.add_argument("--include-archived", action="store_true")
    ap.add_argument("--use-users-conversations", action="store_true")
    ap.add_argument("--hot-channels-file", default=str(HOT_CHANNELS_FILE))
    ap.add_argument("--batch-size", type=int, default=150)
    ap.add_argument("--bootstrap-lookback-minutes", type=float, default=24 * 60)
    ap.add_argument("--overlap-minutes", type=float, default=15)
    ap.add_argument("--active-thread-retention-hours", type=float, default=7 * 24)
    ap.add_argument("--max-active-threads-per-run", type=int, default=75)
    ap.add_argument("--max-history-pages-per-channel", type=int, default=5)
    ap.add_argument("--max-reply-pages-per-thread", type=int, default=5)
    ap.add_argument("--run-id-prefix", default="realtime")
    ap.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    started = utc_now()
    now_ts = started.timestamp()
    run_id = f"{args.run_id_prefix}-{started.strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = ROOT / "chunks" / "slack" / run_id
    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = ROOT / state_path
    state = load_json(state_path, {"channels": {}, "active_threads": {}, "errors": {}, "round_robin_offset": 0})
    state["active_thread_retention_secs"] = args.active_thread_retention_hours * 60 * 60

    client = SlackClient(token_from_env())
    includes, excludes = load_channel_filter()
    hot_keys = load_hot_keys(Path(args.hot_channels_file))
    log(f"enumerating types={args.types} include_archived={args.include_archived}")
    conversations = list_conversations(
        client,
        types=args.types,
        use_users_conversations=args.use_users_conversations,
        exclude_archived=not args.include_archived,
    )
    conversations = stable_conversations(c for c in conversations if conversation_matches(c, includes, excludes))
    targets, next_offset, hot_count = select_targets(conversations, state, hot_keys, args.batch_size)
    conv_by_id = {c.id: c for c in conversations}
    prune_active_threads(state.setdefault("active_threads", {}), now_ts)

    users = list_users(client)
    write_json(run_dir / "users.json", users)
    write_json(run_dir / "conversations.json", [c.raw for c in conversations])

    state["round_robin_offset"] = next_offset
    state["last_started_at"] = started.isoformat(timespec="seconds")
    state["last_run_id"] = run_id
    save_json(state_path, state)

    summaries: list[dict[str, Any]] = []
    errors: dict[str, Any] = {}
    history_pages_written = 0
    history_messages_written = 0
    history_backlogs = 0
    log(f"selected targets={len(targets)} hot={hot_count} batch_size={args.batch_size} total_conversations={len(conversations)} run_id={run_id}")
    for index, conv in enumerate(targets, start=1):
        log(f"check [{index}/{len(targets)}] #{conv.name} ({conv.id})")
        try:
            summary = process_conversation(
                client,
                conv,
                run_dir,
                state,
                now_ts=now_ts,
                bootstrap_lookback_secs=args.bootstrap_lookback_minutes * 60,
                overlap_secs=args.overlap_minutes * 60,
                max_history_pages=args.max_history_pages_per_channel,
            )
            summaries.append(summary)
            history_pages_written += int(summary["history_pages_written"])
            history_messages_written += int(summary["history_messages_written"])
            if summary.get("history_backlog"):
                history_backlogs += 1
            save_json(state_path, state)
            if summary["history_messages_written"]:
                log(f"wrote_history #{conv.name} pages={summary['history_pages_written']} messages={summary['history_messages_written']}")
        except Exception as exc:
            errors[conv.id] = {"name": conv.name, "error": str(exc), "at": utc_now().isoformat(timespec="seconds")}
            state.setdefault("errors", {})[conv.id] = errors[conv.id]
            save_json(state_path, state)
            log(f"error #{conv.name} ({conv.id}) {exc}")

    reply_summary = process_active_threads(
        client,
        conv_by_id,
        run_dir,
        state,
        now_ts=now_ts,
        overlap_secs=args.overlap_minutes * 60,
        max_threads=args.max_active_threads_per_run,
        max_reply_pages=args.max_reply_pages_per_thread,
    )
    save_json(state_path, state)

    pages_written = history_pages_written + reply_summary["reply_pages_written"]
    messages_written = history_messages_written + reply_summary["reply_messages_written"]
    summary = {
        "ok": True,
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": utc_now().isoformat(timespec="seconds"),
        "total_conversations": len(conversations),
        "targets_checked": len(targets),
        "round_robin_offset": next_offset,
        "hot_targets": hot_count,
        "history_pages_written": history_pages_written,
        "history_messages_written": history_messages_written,
        "history_backlogs": history_backlogs,
        **reply_summary,
        "pages_written": pages_written,
        "messages_written": messages_written,
        "errors": errors,
        "chunk_root": str(run_dir.relative_to(ROOT)),
    }
    state["last_summary"] = summary
    save_json(state_path, state)
    save_json(STATE_DIR / "slack-realtime-last-run.json", summary)
    log("summary " + json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
