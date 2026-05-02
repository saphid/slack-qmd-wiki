#!/usr/bin/env python3
"""Use a model to decide semantic Slack chunk boundaries from downloaded host chunks.

Input is already-downloaded Slack API pages under chunks/slack/<run-id>/<channel-id>.
The model sees compact message/thread units and returns JSON chunk plans. This
keeps API collection deterministic while making ingestion chunks semantic rather
than fixed-size/date-only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from slack_sync import ROOT, iso_from_ts

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\a]*(?:\a|\x1b\\)|\x1b[@-_]")


def log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}", flush=True)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def clean_text(value: str, limit: int = 420) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def iter_pages(conv_dir: Path):
    for p in sorted((conv_dir / "history").glob("page-*.json")):
        yield p
    replies = conv_dir / "replies"
    if replies.exists():
        for p in sorted(replies.glob("*/page-*.json")):
            yield p


def load_messages(conv_dir: Path) -> list[dict[str, Any]]:
    by_ts: dict[str, dict[str, Any]] = {}
    for page in iter_pages(conv_dir):
        data = load_json(page)
        for msg in data.get("messages", []):
            ts = msg.get("ts")
            if ts:
                by_ts[ts] = msg
    return sorted(by_ts.values(), key=lambda m: float(m["ts"]))


def author(msg: dict[str, Any], users: dict[str, str]) -> str:
    if msg.get("user"):
        return users.get(msg["user"], msg["user"])
    if msg.get("bot_profile", {}).get("name"):
        return msg["bot_profile"]["name"]
    return msg.get("username") or msg.get("subtype") or "unknown"


def to_units(messages: list[dict[str, Any]], users: dict[str, str]) -> list[dict[str, Any]]:
    # Group thread replies under their root where possible, but keep orphan replies as units too.
    by_ts = {m["ts"]: m for m in messages if m.get("ts")}
    replies_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    roots: list[dict[str, Any]] = []
    for m in messages:
        ts = m.get("ts")
        thread_ts = m.get("thread_ts")
        if thread_ts and thread_ts != ts and thread_ts in by_ts:
            replies_by_root[thread_ts].append(m)
        else:
            roots.append(m)
    units = []
    for m in roots:
        ts = m["ts"]
        replies = sorted(replies_by_root.get(ts, []), key=lambda r: float(r["ts"]))
        samples = []
        for r in replies[:3]:
            samples.append({"ts": r.get("ts"), "author": author(r, users), "text": clean_text(r.get("text", ""), 220)})
        texts = [m.get("text", "")] + [r.get("text", "") for r in replies]
        units.append({
            "unit_id": ts,
            "start_ts": ts,
            "end_ts": replies[-1].get("ts", ts) if replies else ts,
            "time": iso_from_ts(ts),
            "author": author(m, users),
            "text": clean_text(m.get("text", "")),
            "reply_count_downloaded": len(replies),
            "reply_samples": samples,
            "keywords_hint": clean_text(" ".join(texts), 260),
        })
    return sorted(units, key=lambda u: float(u["start_ts"]))


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text).strip()


def extract_json(text: str) -> Any:
    text = strip_ansi(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end+1])
        raise


def call_model(prompt: str, *, model: str | None, timeout: int) -> dict[str, Any]:
    cmd = ["pi", "-p", "--no-tools", "--no-context-files", "--no-session"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"model failed rc={result.returncode}\nSTDOUT={strip_ansi(result.stdout)}\nSTDERR={strip_ansi(result.stderr)}")
    parsed = extract_json(result.stdout)
    if not isinstance(parsed, dict):
        raise RuntimeError("model output was not a JSON object")
    return parsed


def plan_window(channel: dict[str, Any], units: list[dict[str, Any]], *, model: str | None, max_chunks: int, timeout: int) -> dict[str, Any]:
    unit_payload = json.dumps(units, ensure_ascii=False)
    prompt = f"""
You are deciding semantic chunks for a Slack-fed engineering wiki.

Input: compact Slack thread/message units from one conversation. These are already downloaded to disk; you are not fetching anything. Your job is to choose semantic ingestion chunks that a later processor can expand back to the full downloaded messages by timestamp.

Channel: #{channel.get('name')} ({channel.get('id')})

Rules:
- Return JSON only. No markdown.
- Choose chunks by meaning/topic, not by fixed message count.
- Chunks should be contiguous time ranges unless a single thread is clearly standalone.
- Prefer fewer, coherent chunks. Use at most {max_chunks} chunks for this window.
- Include low-value/noise chunks too, but mark priority "low" and explain.
- Preserve traceability with start_ts/end_ts and representative unit_ids.
- If there is only noise, return one low-priority chunk.

Schema:
{{
  "channel_id": "...",
  "channel_name": "...",
  "chunks": [
    {{
      "title": "short descriptive title",
      "priority": "high|medium|low",
      "kind": "incident|decision|design|question|status|social|noise|mixed",
      "start_ts": "Slack ts string from units",
      "end_ts": "Slack ts string from units",
      "representative_unit_ids": ["ts", "ts"],
      "summary": "1-3 sentences describing why these messages belong together",
      "wiki_targets": ["suggested topic/page names"],
      "reasoning": "brief boundary rationale"
    }}
  ]
}}

Units JSON:
{unit_payload}
""".strip()
    plan = call_model(prompt, model=model, timeout=timeout)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="all-feeds")
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--window-units", type=int, default=120)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--max-chunks-per-window", type=int, default=8)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    chunk_root = ROOT / "chunks" / "slack" / args.run_id
    conv_dir = chunk_root / args.channel_id
    channel = load_json(conv_dir / "conversation.json")
    users = load_json(chunk_root / "users.json", {})
    messages = load_messages(conv_dir)
    units = to_units(messages, users)
    log(f"loaded channel=#{channel.get('name')} messages={len(messages)} units={len(units)}")
    plans = []
    windows = [units[i:i+args.window_units] for i in range(0, len(units), args.window_units)]
    if args.max_windows:
        windows = windows[:args.max_windows]
    for idx, window in enumerate(windows, start=1):
        log(f"model_window {idx}/{len(windows)} units={len(window)} start={window[0]['start_ts'] if window else ''} end={window[-1]['end_ts'] if window else ''}")
        plan = plan_window(channel, window, model=args.model, max_chunks=args.max_chunks_per_window, timeout=args.timeout)
        plan["window_index"] = idx
        plan["window_start_ts"] = window[0]["start_ts"] if window else None
        plan["window_end_ts"] = window[-1]["end_ts"] if window else None
        plans.append(plan)
        out = conv_dir / "model-plans" / f"window-{idx:04d}.json"
        write_json(out, plan)
        log(f"wrote {out.relative_to(ROOT)} chunks={len(plan.get('chunks', []))}")
    combined = {"channel": channel, "message_count": len(messages), "unit_count": len(units), "plans": plans}
    write_json(conv_dir / "model-chunk-plan.json", combined)
    log(f"wrote {(conv_dir / 'model-chunk-plan.json').relative_to(ROOT)} windows={len(plans)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
