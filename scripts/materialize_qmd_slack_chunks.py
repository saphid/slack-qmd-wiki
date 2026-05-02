#!/usr/bin/env python3
"""Materialize downloaded Slack JSON chunks into markdown for QMD indexing.

QMD indexes markdown, so this creates a compact searchable markdown view over the
raw Slack API JSON chunks. The JSON chunks remain the source of truth.
"""
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

def iso(ts: str) -> str:
    try: return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception: return ts

def safe(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-._") or "unknown"

def load_json(p: Path, default: Any=None) -> Any:
    if not p.exists(): return default
    return json.loads(p.read_text())

def author(msg: dict[str, Any], users: dict[str,str]) -> str:
    if msg.get("user"): return users.get(msg["user"], msg["user"])
    if msg.get("bot_profile", {}).get("name"): return msg["bot_profile"]["name"]
    return msg.get("username") or msg.get("subtype") or "unknown"

def clean_text(t: str) -> str:
    return (t or "").replace("\r\n", "\n").replace("\r", "\n").strip()

def page_to_md(page: Path, out: Path, users: dict[str,str]) -> int:
    data = load_json(page, {})
    channel = data.get("channel") or {}
    messages = data.get("messages") or []
    kind = data.get("kind") or ("reply_page" if "replies" in page.parts else "history_page")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "source: slack-api-chunk",
        f"kind: {kind}",
        f"channel_id: {channel.get('id','')}",
        f"channel_name: {channel.get('name','')}",
        f"thread_ts: {data.get('thread_ts','')}",
        f"page: {data.get('page','')}",
        f"fetched_at: {data.get('fetched_at','')}",
        f"message_count: {len(messages)}",
        f"source_json: {page.relative_to(ROOT)}",
        "---",
        "",
        f"# Slack chunk: #{channel.get('name', channel.get('id','unknown'))} {kind} page {data.get('page','')}",
        "",
        f"Source JSON: `{page.relative_to(ROOT)}`",
        "",
    ]
    for msg in messages:
        ts = msg.get("ts", "")
        lines.append(f"## {iso(ts)} | {author(msg, users)} | ts={ts} thread_ts={msg.get('thread_ts','')}")
        text = clean_text(msg.get("text", ""))
        if text:
            lines.append("")
            lines.append(text)
        files = msg.get("files") or []
        for f in files:
            lines.append(f"- file: {f.get('title') or f.get('name') or f.get('id')} {f.get('permalink') or f.get('url_private') or ''}".rstrip())
        lines.append("")
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(out)
    return len(messages)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="all-feeds")
    ap.add_argument("--only-complete", action="store_true", help="Only materialize channels marked complete in download state")
    ap.add_argument("--limit-pages", type=int)
    args = ap.parse_args()
    chunk_root = ROOT / "chunks/slack" / args.run_id
    out_root = ROOT / "qmd/slack-api-chunks" / args.run_id
    users = load_json(chunk_root / "users.json", {}) or {}
    state = load_json(ROOT / ".state/slack-chunk-download-state.json", {"conversations": {}})
    complete_ids = {cid for cid,v in state.get("conversations", {}).items() if v.get("status") == "complete"}
    pages = []
    for p in chunk_root.glob("*/history/page-*.json"):
        if args.only_complete and p.parts[-3] not in complete_ids: continue
        pages.append(p)
    for p in chunk_root.glob("*/replies/*/page-*.json"):
        if args.only_complete and p.parts[-4] not in complete_ids: continue
        pages.append(p)
    pages = sorted(pages)
    if args.limit_pages: pages = pages[:args.limit_pages]
    count = 0; msgs = 0
    for p in pages:
        rel = p.relative_to(chunk_root)
        out = out_root / rel.with_suffix(".md")
        msgs += page_to_md(p, out, users)
        count += 1
        if count % 1000 == 0: print(f"materialized pages={count} messages={msgs}", flush=True)
    print(json.dumps({"run_id": args.run_id, "pages": count, "messages": msgs, "out_root": str(out_root.relative_to(ROOT))}, sort_keys=True))
    return 0
if __name__ == "__main__": raise SystemExit(main())
