#!/usr/bin/env python3
"""Materialize Slack into conversation-oriented QMD chunks.

Grouping invariant:
- one channel at a time;
- Slack threads are the easy-known boring case: one thread per chunk;
- transcript markdown files are the other easy-known boring case: one transcript per chunk;
- only unthreaded in-channel Slack messages need inference. Those can be split by
  a cheap/fast LLM, with deterministic heuristic fallback when explicitly allowed.
"""
from __future__ import annotations

import argparse, concurrent.futures, json, os, re, shlex, shutil, subprocess, tempfile, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
STOP = {"the","and","for","that","this","with","have","you","are","but","not","was","just","can","will","from","all","has","had","our","your","they"}
LOW_SUBTYPES = {"channel_join", "channel_leave", "bot_message"}


def iso(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ts


def safe(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-._") or "unknown"


def load_json(p: Path, default: Any = None) -> Any:
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_if_changed(p: Path, content: str) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    if existed and p.read_text(encoding="utf-8", errors="replace") == content:
        return "skipped"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(p)
    return "updated" if existed else "created"


def yaml(v: object) -> str:
    return json.dumps("" if v is None else str(v), ensure_ascii=False)


def frontmatter(meta: dict[str, object]) -> str:
    lines = ["---"]
    for k in sorted(meta):
        v = meta[k]
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {yaml(item)}")
        else:
            lines.append(f"{k}: {yaml(v)}")
    lines.append("---")
    return "\n".join(lines)


def clean(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def toks(s: str) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(s or "") if t.lower() not in STOP}


def author(m: dict[str, Any], users: dict[str, str]) -> str:
    if m.get("user"):
        return users.get(m["user"], m["user"])
    if m.get("bot_profile", {}).get("name"):
        return m["bot_profile"]["name"]
    return m.get("username") or m.get("subtype") or "unknown"


def iter_pages(conv_dir: Path):
    yield from sorted((conv_dir / "history").glob("page-*.json"))
    yield from sorted((conv_dir / "replies").glob("*/page-*.json"))


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def load_messages(conv_dir: Path, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    channel = load_json(conv_dir / "conversation.json", {"id": conv_dir.name, "name": conv_dir.name}) or {}
    by_ts: dict[str, dict[str, Any]] = {}
    thread_roots: set[str] = set()
    for page in iter_pages(conv_dir):
        data = load_json(page, {}) or {}
        if data.get("thread_ts"):
            thread_roots.add(str(data["thread_ts"]))
        for msg in data.get("messages", []) or []:
            ts = msg.get("ts")
            if not ts:
                continue
            old = by_ts.get(ts, {})
            pages = set(old.get("_source_pages", []))
            pages.add(rel(page, root))
            merged = {**old, **msg, "_source_pages": sorted(pages)}
            by_ts[str(ts)] = merged
    return channel, sorted(by_ts.values(), key=lambda m: float(m["ts"])), thread_roots


def is_thread_root(m: dict[str, Any], thread_roots: set[str]) -> bool:
    ts = str(m.get("ts", ""))
    thread_ts = str(m.get("thread_ts") or "")
    reply_count = int(m.get("reply_count") or m.get("reply_users_count") or 0)
    return ts in thread_roots or (thread_ts == ts and reply_count > 0)


def split_threads(messages: list[dict[str, Any]], thread_roots: set[str]) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    by_ts = {str(m["ts"]): m for m in messages if m.get("ts")}
    roots = {ts for ts, m in by_ts.items() if is_thread_root(m, thread_roots)}
    replies: dict[str, list[dict[str, Any]]] = defaultdict(list)
    channel_msgs: list[dict[str, Any]] = []
    for m in messages:
        ts = str(m.get("ts"))
        tts = str(m.get("thread_ts") or "")
        if ts in roots:
            continue
        if tts and tts != ts and tts in roots:
            replies[tts].append(m)
        else:
            channel_msgs.append(m)
    chunks = []
    for ts in sorted(roots, key=float):
        root = by_ts.get(ts)
        if root:
            chunks.append([root, *sorted(replies.get(ts, []), key=lambda m: float(m["ts"]))])
    return chunks, sorted(channel_msgs, key=lambda m: float(m["ts"]))


def same_conversation(cur: list[dict[str, Any]], msg: dict[str, Any], users: dict[str, str], args: argparse.Namespace) -> bool:
    if not cur:
        return True
    gap = float(msg["ts"]) - float(cur[-1]["ts"])
    if gap <= args.short_gap_minutes * 60:
        return True
    if gap > args.hard_gap_minutes * 60:
        return False
    recent_tokens: set[str] = set()
    recent_authors: set[str] = set()
    for item in cur[-8:]:
        recent_tokens |= toks(item.get("text", ""))
        recent_authors.add(author(item, users))
    overlap = len(toks(msg.get("text", "")) & recent_tokens)
    if gap <= args.medium_gap_minutes * 60 and (author(msg, users) in recent_authors or overlap >= 2):
        return True
    return gap <= (args.hard_gap_minutes * 30) and overlap >= 4


def heuristic_channel_conversations(messages: list[dict[str, Any]], users: dict[str, str], args: argparse.Namespace) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for msg in messages:
        span = float(msg["ts"]) - float(cur[0]["ts"]) if cur else 0
        if cur and (len(cur) >= args.max_messages or span > args.max_span_hours * 3600 or not same_conversation(cur, msg, users, args)):
            out.append(cur)
            cur = []
        cur.append(msg)
    if cur:
        out.append(cur)
    return out


def llm_windows(messages: list[dict[str, Any]], args: argparse.Namespace) -> list[list[dict[str, Any]]]:
    windows: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for msg in messages:
        gap = float(msg["ts"]) - float(cur[-1]["ts"]) if cur else 0
        span = float(msg["ts"]) - float(cur[0]["ts"]) if cur else 0
        if cur and (gap > args.hard_gap_minutes * 60 or len(cur) >= args.llm_window_messages or span > args.llm_window_span_hours * 3600):
            windows.append(cur)
            cur = []
        cur.append(msg)
    if cur:
        windows.append(cur)
    return windows


def prompt_message(msg: dict[str, Any], idx: int, users: dict[str, str], max_chars: int) -> dict[str, str | int]:
    text = re.sub(r"\s+", " ", clean(msg.get("text", "")))
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return {"index": idx, "ts": str(msg.get("ts", "")), "time": iso(str(msg.get("ts", ""))), "author": author(msg, users), "text": text}


def build_llm_prompt(channel: dict[str, Any], messages: list[dict[str, Any]], users: dict[str, str], args: argparse.Namespace) -> str:
    payload = {
        "channel": {"id": channel.get("id", ""), "name": channel.get("name", "")},
        "instructions": [
            "Partition these unthreaded Slack channel messages into coherent contiguous conversations.",
            "Threads and transcripts are handled elsewhere; do not invent missing messages.",
            "Each output conversation must be a contiguous inclusive index range that exactly covers all input messages.",
            "Start a new conversation when the topic, incident, question, or work item changes, or when the time gap makes continuity unlikely.",
            "Return JSON only, no markdown or commentary.",
        ],
        "schema": {"conversations": [{"start_index": 0, "end_index": 0, "title": "short neutral topic"}]},
        "messages": [prompt_message(m, i, users, args.llm_message_chars) for i, m in enumerate(messages)],
    }
    return json.dumps(payload, ensure_ascii=False)


def extract_json(text: str) -> Any:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    starts = [pos for pos in (stripped.find("{"), stripped.find("[")) if pos >= 0]
    if not starts:
        raise ValueError("LLM response contained no JSON")
    start = min(starts)
    for end in range(len(stripped), start, -1):
        try:
            return json.loads(stripped[start:end])
        except json.JSONDecodeError:
            continue
    raise ValueError("could not parse JSON from LLM response")


def normalize_llm_conversations(data: Any, messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    convs = data.get("conversations") if isinstance(data, dict) else data
    if not isinstance(convs, list):
        raise ValueError("LLM JSON must be an array or object with conversations array")
    ranges: list[tuple[int, int]] = []
    for conv in convs:
        if not isinstance(conv, dict):
            raise ValueError("each conversation must be an object")
        start = int(conv.get("start_index", conv.get("start", conv.get("from"))))
        end = int(conv.get("end_index", conv.get("end", conv.get("to", start))))
        if start < 0 or end < start or end >= len(messages):
            raise ValueError(f"invalid conversation range {start}..{end} for {len(messages)} messages")
        ranges.append((start, end))
    ranges.sort()
    expected = 0
    out: list[list[dict[str, Any]]] = []
    for start, end in ranges:
        if start != expected:
            raise ValueError(f"ranges must exactly cover window; expected {expected}, got {start}")
        out.append(messages[start : end + 1])
        expected = end + 1
    if expected != len(messages):
        raise ValueError(f"ranges ended at {expected}, expected {len(messages)}")
    return out


def call_llm_command(prompt: str, args: argparse.Namespace) -> str:
    command = (args.llm_command or "").format(model=args.llm_model)
    if not command:
        raise ValueError("--llm-command or SLACK_CONVERSATION_LLM_CMD is required for command provider")
    proc = subprocess.run(shlex.split(command), input=prompt, text=True, capture_output=True, timeout=args.llm_timeout_seconds, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"LLM command failed with code {proc.returncode}: {proc.stderr[-1000:]}")
    return proc.stdout


def call_ollama(prompt: str, args: argparse.Namespace) -> str:
    # Keep this dependency-free. The local Ollama CLI reads the prompt on stdin
    # and returns model text on stdout; no Slack content leaves the machine.
    model = args.llm_model or "qwen2.5:7b-instruct"
    return call_llm_command(prompt, argparse.Namespace(**{**vars(args), "llm_command": f"ollama run {shlex.quote(model)}"}))


def call_pi(prompt: str, args: argparse.Namespace) -> str:
    # Pi is supported as a first-class segmenter. We pass the potentially large
    # JSON prompt as a temporary @file instead of argv/stdin to avoid shell size
    # limits and to keep the subprocess non-interactive/sessionless.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", prefix="slack-conversation-pi-", delete=False) as prompt_file:
        prompt_file.write(prompt)
        prompt_path = prompt_file.name
    try:
        command = [
            args.pi_bin,
            "--mode", "text",
            "--no-tools",
            "--no-skills",
            "--no-context-files",
            "--no-session",
            "--thinking", "off",
            "-p",
        ]
        if args.pi_model:
            command.extend(["--model", args.pi_model])
        if args.pi_extra_args:
            command.extend(shlex.split(args.pi_extra_args))
        command.extend([f"@{prompt_path}", "Return only the JSON object requested in the attached prompt."])
        proc = subprocess.run(command, text=True, capture_output=True, timeout=args.llm_timeout_seconds, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"pi failed with code {proc.returncode}: {proc.stderr[-1000:]}")
        return proc.stdout
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


def call_llm(prompt: str, args: argparse.Namespace) -> str:
    if args.llm_provider == "command":
        return call_llm_command(prompt, args)
    if args.llm_provider == "ollama":
        return call_ollama(prompt, args)
    if args.llm_provider == "pi":
        return call_pi(prompt, args)
    raise ValueError(f"unsupported LLM provider: {args.llm_provider}")


def llm_segment_window(channel: dict[str, Any], messages: list[dict[str, Any]], users: dict[str, str], args: argparse.Namespace) -> list[list[dict[str, Any]]]:
    prompt = build_llm_prompt(channel, messages, users, args)
    last_error: Exception | None = None
    for attempt in range(args.llm_retries + 1):
        try:
            return normalize_llm_conversations(extract_json(call_llm(prompt, args)), messages)
        except (ValueError, TypeError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if attempt < args.llm_retries:
                time.sleep(1 + attempt)
    raise RuntimeError(f"LLM segmentation failed for #{channel.get('name') or channel.get('id')}: {last_error}")


def llm_channel_conversations(channel: dict[str, Any], messages: list[dict[str, Any]], users: dict[str, str], args: argparse.Namespace) -> tuple[list[list[dict[str, Any]]], dict[str, int]]:
    out: list[list[dict[str, Any]]] = []
    stats = {"llm_windows": 0, "llm_fallback_windows": 0}
    for window in llm_windows(messages, args):
        if not window:
            continue
        stats["llm_windows"] += 1
        try:
            out.extend(llm_segment_window(channel, window, users, args))
        except RuntimeError:
            if args.channel_mode == "llm":
                raise
            stats["llm_fallback_windows"] += 1
            out.extend(heuristic_channel_conversations(window, users, args))
    return out, stats


def resolve_channel_mode(args: argparse.Namespace) -> str:
    if args.channel_mode != "auto":
        return args.channel_mode
    if args.llm_command or args.llm_provider == "pi":
        return "llm-with-heuristic-fallback"
    return "heuristic"


def source_pages(messages: list[dict[str, Any]]) -> list[str]:
    pages: set[str] = set()
    for m in messages:
        pages.update(m.get("_source_pages", []))
    return sorted(pages)


def title(kind: str, channel: dict[str, Any], messages: list[dict[str, Any]], users: dict[str, str]) -> str:
    text = re.sub(r"\s+", " ", clean(messages[0].get("text", ""))).strip()
    text = re.sub(r"<[^>]+>", "", text).strip() or f"{author(messages[0], users)} {messages[0].get('subtype') or 'conversation'}"
    return f"#{channel.get('name') or channel.get('id')} {kind}: {text[:80]}"


def render_messages(messages: list[dict[str, Any]], users: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for m in messages:
        ts = str(m.get("ts", ""))
        lines.append(f"## {iso(ts)} | {author(m, users)} | ts={ts} thread_ts={m.get('thread_ts','')}")
        text = clean(m.get("text", ""))
        if text:
            lines += ["", text]
        for f in m.get("files") or []:
            lines.append(f"- file: {f.get('title') or f.get('name') or f.get('id')} {f.get('permalink') or f.get('url_private') or ''}".rstrip())
        lines.append("")
    return lines


def render_chunk(run_id: str, channel: dict[str, Any], kind: str, messages: list[dict[str, Any]], users: dict[str, str], segmentation_mode: str = "deterministic", llm_model: str = "") -> str:
    start, end = str(messages[0]["ts"]), str(messages[-1]["ts"])
    low = all((m.get("subtype") in LOW_SUBTYPES) or not clean(m.get("text", "")) for m in messages)
    meta: dict[str, object] = {
        "source": "slack-conversation-chunk",
        "run_id": run_id,
        "chunk_kind": kind,
        "channel_id": channel.get("id", ""),
        "channel_name": channel.get("name", ""),
        "start_ts": start,
        "end_ts": end,
        "start_time": iso(start),
        "end_time": iso(end),
        "thread_ts": str(messages[0].get("thread_ts") or start) if kind == "thread" else "",
        "message_count": len(messages),
        "source_pages": source_pages(messages),
        "priority_hint": "low" if low else "medium",
        "segmentation_mode": segmentation_mode,
    }
    if llm_model and segmentation_mode.startswith("llm"):
        meta["llm_model"] = llm_model
    lines = [frontmatter(meta), "", f"# {title(kind, channel, messages, users)}", "", "Source pages:"]
    lines += [f"- `{p}`" for p in meta["source_pages"]]  # type: ignore[index]
    lines += [""] + render_messages(messages, users)
    return "\n".join(lines).rstrip() + "\n"


def out_path(out_root: Path, run_id: str, channel: dict[str, Any], kind: str, messages: list[dict[str, Any]]) -> Path:
    cid = safe(str(channel.get("id") or channel.get("name") or "unknown"))
    if kind == "thread":
        return out_root / run_id / cid / "threads" / f"{safe(str(messages[0].get('thread_ts') or messages[0].get('ts')))}.md"
    return out_root / run_id / cid / "channel-conversations" / f"{safe(str(messages[0]['ts']))}--{safe(str(messages[-1]['ts']))}.md"


def materialize_channel(root: Path, run_id: str, conv_dir: Path, out_root: Path, users: dict[str, str], args: argparse.Namespace) -> dict[str, int]:
    channel, messages, thread_roots = load_messages(conv_dir, root)
    threads, channel_msgs = split_threads(messages, thread_roots)
    mode = resolve_channel_mode(args)
    llm_stats = {"llm_windows": 0, "llm_fallback_windows": 0}
    if mode == "heuristic":
        convs = heuristic_channel_conversations(channel_msgs, users, args)
        segmentation_mode = "heuristic"
    else:
        convs, llm_stats = llm_channel_conversations(channel, channel_msgs, users, args)
        segmentation_mode = "llm" if llm_stats["llm_fallback_windows"] == 0 else "llm-with-heuristic-fallback"

    cid = safe(str(channel.get("id") or channel.get("name") or "unknown"))
    shutil.rmtree(out_root / run_id / cid / "channel-conversations", ignore_errors=True)

    written = skipped = 0
    for chunk in threads:
        action = write_if_changed(out_path(out_root, run_id, channel, "thread", chunk), render_chunk(run_id, channel, "thread", chunk, users, "deterministic"))
        if action == "skipped": skipped += 1
        else: written += 1
    for chunk in convs:
        action = write_if_changed(out_path(out_root, run_id, channel, "channel", chunk), render_chunk(run_id, channel, "channel", chunk, users, segmentation_mode, args.llm_model))
        if action == "skipped": skipped += 1
        else: written += 1
    return {"messages": len(messages), "threads": len(threads), "channel_conversations": len(convs), "written": written, "skipped": skipped, **llm_stats}


def materialize_transcripts(root: Path, transcript_root: Path, out_root: Path, limit: int | None) -> dict[str, int]:
    paths = sorted(transcript_root.rglob("*.md")) if transcript_root.exists() else []
    if limit is not None:
        paths = paths[:limit]
    written = skipped = 0
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta = {"source": "slack-conversation-chunk", "chunk_kind": "transcript", "source_transcript": rel(path, root), "priority_hint": "medium", "segmentation_mode": "deterministic"}
        content = frontmatter(meta) + "\n\n" + text.rstrip() + "\n"
        action = write_if_changed(out_root / "transcripts" / path.relative_to(transcript_root), content)
        if action == "skipped": skipped += 1
        else: written += 1
    return {"transcripts": len(paths), "written": written, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--run-id", default="all-feeds")
    ap.add_argument("--output-dir", default="qmd/slack-conversations")
    ap.add_argument("--transcript-root", default="qmd/huddle-transcripts")
    ap.add_argument("--channel-id", action="append")
    ap.add_argument("--limit-channels", type=int)
    ap.add_argument("--limit-transcripts", type=int)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("SLACK_CONVERSATION_WORKERS", "1")))
    ap.add_argument("--channel-mode", choices=["auto", "heuristic", "llm", "llm-with-heuristic-fallback"], default=os.environ.get("SLACK_CONVERSATION_CHANNEL_MODE", "auto"))
    ap.add_argument("--llm-provider", choices=["ollama", "command", "pi"], default=os.environ.get("SLACK_CONVERSATION_LLM_PROVIDER", "ollama"))
    ap.add_argument("--llm-model", default=os.environ.get("SLACK_CONVERSATION_LLM_MODEL", "qwen2.5:7b-instruct"))
    ap.add_argument("--llm-command", default=os.environ.get("SLACK_CONVERSATION_LLM_CMD", ""), help="Command that reads the JSON prompt on stdin and writes JSON segmentation on stdout; may use {model}.")
    ap.add_argument("--pi-bin", default=os.environ.get("SLACK_CONVERSATION_PI_BIN", "pi"), help="Pi coding-agent executable for --llm-provider pi.")
    ap.add_argument("--pi-model", default=os.environ.get("SLACK_CONVERSATION_PI_MODEL", ""), help="Optional Pi model override. Empty uses Pi's configured default.")
    ap.add_argument("--pi-extra-args", default=os.environ.get("SLACK_CONVERSATION_PI_EXTRA_ARGS", ""), help="Extra Pi CLI args, e.g. '--provider openai'.")
    ap.add_argument("--llm-timeout-seconds", type=int, default=int(os.environ.get("SLACK_CONVERSATION_LLM_TIMEOUT_SECONDS", "120")))
    ap.add_argument("--llm-retries", type=int, default=int(os.environ.get("SLACK_CONVERSATION_LLM_RETRIES", "1")))
    ap.add_argument("--llm-window-messages", type=int, default=int(os.environ.get("SLACK_CONVERSATION_LLM_WINDOW_MESSAGES", "120")))
    ap.add_argument("--llm-window-span-hours", type=float, default=float(os.environ.get("SLACK_CONVERSATION_LLM_WINDOW_SPAN_HOURS", "6")))
    ap.add_argument("--llm-message-chars", type=int, default=int(os.environ.get("SLACK_CONVERSATION_LLM_MESSAGE_CHARS", "500")))
    ap.add_argument("--short-gap-minutes", type=int, default=10)
    ap.add_argument("--medium-gap-minutes", type=int, default=45)
    ap.add_argument("--hard-gap-minutes", type=int, default=120)
    ap.add_argument("--max-messages", type=int, default=80)
    ap.add_argument("--max-span-hours", type=int, default=6)
    ap.add_argument("--no-transcripts", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    chunk_root = root / "chunks" / "slack" / args.run_id
    out_root = (root / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    transcript_root = (root / args.transcript_root).resolve() if not Path(args.transcript_root).is_absolute() else Path(args.transcript_root)
    users = load_json(chunk_root / "users.json", {}) or {}
    conv_dirs = sorted(p for p in chunk_root.iterdir() if p.is_dir() and (p / "conversation.json").exists()) if chunk_root.exists() else []
    if args.channel_id:
        wanted = set(args.channel_id)
        conv_dirs = [p for p in conv_dirs if p.name in wanted]
    if args.limit_channels is not None:
        conv_dirs = conv_dirs[:args.limit_channels]

    totals = {"channels": 0, "messages": 0, "threads": 0, "channel_conversations": 0, "written": 0, "skipped": 0, "llm_windows": 0, "llm_fallback_windows": 0}
    if max(1, args.workers) == 1:
        results = [materialize_channel(root, args.run_id, conv_dir, out_root, users, args) for conv_dir in conv_dirs]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(materialize_channel, root, args.run_id, conv_dir, out_root, users, args) for conv_dir in conv_dirs]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
    for res in results:
        totals["channels"] += 1
        for key in ["messages", "threads", "channel_conversations", "written", "skipped", "llm_windows", "llm_fallback_windows"]:
            totals[key] += res[key]
    tr = {"transcripts": 0, "written": 0, "skipped": 0}
    if not args.no_transcripts:
        tr = materialize_transcripts(root, transcript_root, out_root, args.limit_transcripts)
        totals["written"] += tr["written"]; totals["skipped"] += tr["skipped"]
    print(json.dumps({
        "run_id": args.run_id,
        "output_dir": rel(out_root, root),
        "channel_mode": resolve_channel_mode(args),
        "llm_provider": args.llm_provider if resolve_channel_mode(args).startswith("llm") else "",
        "llm_model": (args.pi_model or "pi-default") if resolve_channel_mode(args).startswith("llm") and args.llm_provider == "pi" else (args.llm_model if resolve_channel_mode(args).startswith("llm") else ""),
        **totals,
        "transcripts": tr["transcripts"],
        "transcript_written": tr["written"],
        "transcript_skipped": tr["skipped"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
