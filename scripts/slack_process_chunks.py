#!/usr/bin/env python3
"""Process downloaded Slack chunks into llm-wiki raw markdown files."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slack_sync import ROOT, STATE_DIR, day_from_ts, dedupe_messages, utc_now, write_day_markdown, write_manifest, SlackClient, token_from_env


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


def iter_messages(conv_dir: Path):
    for page in sorted((conv_dir / "history").glob("page-*.json")):
        data = json.loads(page.read_text())
        yield from data.get("messages", [])
    replies = conv_dir / "replies"
    if replies.exists():
        for page in sorted(replies.glob("*/page-*.json")):
            data = json.loads(page.read_text())
            yield from data.get("messages", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="all-feeds")
    parser.add_argument("--download-state", default=str(STATE_DIR / "slack-chunk-download-state.json"))
    parser.add_argument("--process-state", default=str(STATE_DIR / "slack-chunk-process-state.json"))
    parser.add_argument("--only-channel-id")
    parser.add_argument("--limit-conversations", type=int)
    parser.add_argument("--permalinks", action="store_true")
    args = parser.parse_args()

    download_state_path = Path(args.download_state)
    if not download_state_path.is_absolute():
        download_state_path = ROOT / download_state_path
    process_state_path = Path(args.process_state)
    if not process_state_path.is_absolute():
        process_state_path = ROOT / process_state_path
    download_state = load_json(download_state_path, {})
    process_state = load_json(process_state_path, {"processed": {}, "errors": {}, "written": []})
    chunk_root = ROOT / download_state.get("chunk_root", f"chunks/slack/{args.run_id}")
    users = load_json(chunk_root / "users.json", {})
    client = SlackClient(token_from_env()) if args.permalinks else None
    synced_at = utc_now().isoformat(timespec="seconds")
    oldest_iso = download_state.get("window_start", "unknown")
    latest_iso = download_state.get("window_end", "unknown")

    completed = [(cid, meta) for cid, meta in download_state.get("conversations", {}).items() if meta.get("status") == "complete"]
    if args.only_channel_id:
        completed = [(cid, meta) for cid, meta in completed if cid == args.only_channel_id]
    if args.limit_conversations:
        completed = completed[: args.limit_conversations]
    log(f"processing completed_conversations={len(completed)} chunk_root={chunk_root.relative_to(ROOT)}")

    written_all = [ROOT / p for p in process_state.setdefault("written", []) if (ROOT / p).exists()]
    for cid, meta in completed:
        if cid in process_state.get("processed", {}):
            log(f"skip processed #{meta.get('name')} ({cid})")
            continue
        conv_dir = chunk_root / cid
        conv = load_json(conv_dir / "conversation.json", {"id": cid, "name": meta.get("name", cid), "slug": meta.get("name", cid)})
        try:
            messages = dedupe_messages(iter_messages(conv_dir))
            by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for msg in messages:
                if msg.get("ts"):
                    by_day[day_from_ts(msg["ts"])].append(msg)
            channel_paths: list[str] = []
            for date, day_messages in sorted(by_day.items()):
                path = write_day_markdown(
                    date=date,
                    conv=type("ConversationLike", (), conv),
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
            process_state["processed"][cid] = {"name": conv.get("name"), "message_count": len(messages), "files": channel_paths}
            process_state["written"] = sorted({str(p.relative_to(ROOT)) for p in written_all if p.exists()})
            save_json(process_state_path, process_state)
            log(f"processed #{conv.get('name')} messages={len(messages)} files={len(channel_paths)}")
        except Exception as exc:
            process_state.setdefault("errors", {})[cid] = {"name": conv.get("name"), "error": str(exc)}
            save_json(process_state_path, process_state)
            log(f"error #{conv.get('name')} ({cid}) {exc}")
    manifest = write_manifest([p for p in written_all if p.exists()], synced_at=synced_at, oldest_iso=oldest_iso, latest_iso=latest_iso)
    if manifest:
        log(f"manifest {manifest.relative_to(ROOT)} raw_files={len(written_all)}")
    log(f"complete processed={len(process_state.get('processed', {}))} errors={len(process_state.get('errors', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
