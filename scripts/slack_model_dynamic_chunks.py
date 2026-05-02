#!/usr/bin/env python3
"""Use a model to choose dynamic semantic Slack chunks.

Unlike `slack_model_chunk_plan.py`, this does not ask the model to split fixed
windows. It walks the downloaded message/thread units from the current cursor and
asks the model to choose the *next* coherent chunk boundary from the content. The
context window is only a model budget; the chunk boundary is semantic and may be
short or long depending on what is actually in the Slack content.
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


def clean_text(value: str, limit: int = 460) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit] + ("…" if len(value) > limit else "")


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
            return json.loads(text[start : end + 1])
        raise


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
        for r in replies[:4]:
            samples.append({"ts": r.get("ts"), "author": author(r, users), "text": clean_text(r.get("text", ""), 240)})
        texts = [m.get("text", "")] + [r.get("text", "") for r in replies]
        units.append(
            {
                "unit_id": ts,
                "start_ts": ts,
                "end_ts": replies[-1].get("ts", ts) if replies else ts,
                "time": iso_from_ts(ts),
                "author": author(m, users),
                "text": clean_text(m.get("text", "")),
                "reply_count_downloaded": len(replies),
                "reply_samples": samples,
                "content_hint": clean_text(" ".join(texts), 300),
            }
        )
    return sorted(units, key=lambda u: float(u["start_ts"]))


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


def prompt_for_next_chunk(
    *,
    channel: dict[str, Any],
    buffer: list[dict[str, Any]],
    previous_chunks: list[dict[str, Any]],
    may_need_more: bool,
    force_chunk: bool,
) -> str:
    prev = [
        {
            "title": c.get("title"),
            "kind": c.get("kind"),
            "start_ts": c.get("start_ts"),
            "end_ts": c.get("end_ts"),
            "summary": c.get("summary"),
        }
        for c in previous_chunks[-4:]
    ]
    return f"""
You are choosing dynamic semantic chunk boundaries for a Slack-fed engineering wiki.

You are given a buffer of downloaded Slack message/thread units starting exactly at the next unchunked unit. Choose ONLY THE NEXT CHUNK. Do not split the whole buffer. Do not use a fixed message count, fixed date window, or uniform chunk size. The chunk should be as long or short as the actual content warrants.

Channel: #{channel.get('name')} ({channel.get('id')})
Previous chunks for continuity: {json.dumps(prev, ensure_ascii=False)}

Boundary rules:
- Start the chunk at the first unit in the buffer.
- End the chunk where the topic/purpose naturally changes, or where a coherent incident/design/decision/status/social run ends.
- A chunk may be 1 unit if it is standalone.
- A chunk may be very long if the same topic continues.
- Preserve low-value/noise/social runs as chunks too, marked low priority.
- If the visible buffer is clearly mid-topic at the end and more units are needed, return action "need_more". {'Do not use need_more; you must choose a best boundary from this buffer.' if force_chunk else 'Use need_more only when a semantic boundary is not visible and more context would likely change the boundary.'}
- end_ts MUST be the start_ts/unit_id of one unit in the buffer. Use the final included root unit, not a reply ts.

Return JSON only:
{{
  "action": "chunk"{' | "need_more"' if may_need_more and not force_chunk else ''},
  "chunk": {{
    "title": "short descriptive title",
    "priority": "high|medium|low",
    "kind": "incident|decision|design|question|status|social|noise|mixed",
    "start_ts": "{buffer[0]['start_ts']}",
    "end_ts": "one buffer unit_id/start_ts",
    "representative_unit_ids": ["ts", "ts"],
    "summary": "1-3 sentences describing the content and why it belongs together",
    "wiki_targets": ["suggested topic/page names"],
    "boundary_reasoning": "why this exact end boundary was chosen from the content"
  }},
  "need_more_reason": "only if action is need_more",
  "confidence": 0.0
}}

Buffer units:
{json.dumps(buffer, ensure_ascii=False)}
""".strip()


def choose_next_chunk(
    *,
    channel: dict[str, Any],
    units: list[dict[str, Any]],
    cursor: int,
    previous_chunks: list[dict[str, Any]],
    initial_context_units: int,
    max_context_units: int,
    model: str | None,
    timeout: int,
) -> tuple[dict[str, Any], int]:
    context_size = min(initial_context_units, len(units) - cursor)
    while True:
        buffer = units[cursor : cursor + context_size]
        force = cursor + context_size >= len(units) or context_size >= max_context_units
        prompt = prompt_for_next_chunk(
            channel=channel,
            buffer=buffer,
            previous_chunks=previous_chunks,
            may_need_more=not force,
            force_chunk=force,
        )
        try:
            decision = call_model(prompt, model=model, timeout=timeout)
        except Exception as exc:
            if context_size > 8:
                next_size = max(8, context_size // 2)
                log(f"model_error_reduce_context cursor={cursor} context={context_size}->{next_size} error={type(exc).__name__}: {exc}")
                context_size = next_size
                continue
            raise
        if decision.get("action") == "need_more" and not force:
            next_size = min(max_context_units, len(units) - cursor, max(context_size + 40, int(context_size * 1.6)))
            if next_size > context_size:
                log(f"model_need_more cursor={cursor} context={context_size}->{next_size} reason={decision.get('need_more_reason')}")
                context_size = next_size
                continue

        chunk = decision.get("chunk") if isinstance(decision.get("chunk"), dict) else None
        if not chunk:
            # Last-resort fallback: one unit, but make the fallback explicit.
            chunk = {
                "title": "Fallback single-message chunk",
                "priority": "low",
                "kind": "mixed",
                "start_ts": units[cursor]["start_ts"],
                "end_ts": units[cursor]["start_ts"],
                "representative_unit_ids": [units[cursor]["unit_id"]],
                "summary": "Model did not return a valid chunk; kept one unit to preserve progress.",
                "wiki_targets": [],
                "boundary_reasoning": "fallback",
            }
        valid_end = {u["start_ts"]: i for i, u in enumerate(buffer)}
        end_ts = str(chunk.get("end_ts") or "")
        if end_ts not in valid_end:
            # Try representative ids, then fallback to first unit.
            for candidate in chunk.get("representative_unit_ids", []):
                if str(candidate) in valid_end:
                    end_ts = str(candidate)
                    chunk["end_ts"] = end_ts
                    break
        if end_ts not in valid_end:
            end_ts = buffer[0]["start_ts"]
            chunk["end_ts"] = end_ts
            chunk["boundary_reasoning"] = str(chunk.get("boundary_reasoning", "")) + " (end_ts corrected to first valid unit)"
        end_index = cursor + valid_end[end_ts]
        chunk.setdefault("start_ts", units[cursor]["start_ts"])
        chunk["start_ts"] = units[cursor]["start_ts"]
        chunk["unit_count"] = end_index - cursor + 1
        chunk["context_units_seen"] = context_size
        return chunk, end_index + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="all-feeds")
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--initial-context-units", type=int, default=80)
    parser.add_argument("--max-context-units", type=int, default=240)
    parser.add_argument("--max-chunks", type=int)
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

    chunks: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(units):
        if args.max_chunks and len(chunks) >= args.max_chunks:
            log(f"max_chunks reached chunks={len(chunks)} cursor={cursor}/{len(units)}")
            break
        log(f"choose_next_chunk index={len(chunks)+1} cursor={cursor}/{len(units)} start={units[cursor]['start_ts']}")
        chunk, next_cursor = choose_next_chunk(
            channel=channel,
            units=units,
            cursor=cursor,
            previous_chunks=chunks,
            initial_context_units=args.initial_context_units,
            max_context_units=args.max_context_units,
            model=args.model,
            timeout=args.timeout,
        )
        chunks.append(chunk)
        out = conv_dir / "dynamic-model-plans" / f"chunk-{len(chunks):05d}.json"
        write_json(out, chunk)
        log(f"chunk_decided {len(chunks)} units={chunk.get('unit_count')} title={chunk.get('title')} end={chunk.get('end_ts')} path={out.relative_to(ROOT)}")
        cursor = max(next_cursor, cursor + 1)

    plan = {
        "strategy": "dynamic-next-boundary",
        "channel": channel,
        "message_count": len(messages),
        "unit_count": len(units),
        "planned_unit_count": sum(int(c.get("unit_count", 0)) for c in chunks),
        "complete": cursor >= len(units),
        "next_cursor": cursor,
        "chunks": chunks,
    }
    write_json(conv_dir / "dynamic-model-chunk-plan.json", plan)
    log(f"wrote {(conv_dir / 'dynamic-model-chunk-plan.json').relative_to(ROOT)} chunks={len(chunks)} complete={plan['complete']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
