#!/usr/bin/env python3
"""Create a wiki inbox manifest for materialized huddle transcripts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "qmd/huddle-transcripts"
DEFAULT_STATE_FILE = ROOT / ".state/huddle-transcript-wiki-manifest-state.json"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        match = re.match(r"^([a-zA-Z0-9_-]+):\s*(.*)$", line)
        if not match:
            continue
        key, raw_value = match.groups()
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            value = raw_value.strip().strip("\"'")
        meta[key] = str(value)
    return meta


def load_state(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): str(value) for key, value in data.get("manifested", {}).items()}


def write_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"manifested": state}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def find_sources(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    return sorted(path for path in input_dir.rglob("*.md") if path.is_file())


def manifest_body(sources: list[tuple[Path, dict[str, str], str]], input_dir: Path) -> str:
    generated_at = utc_now()
    lines = [
        "# Huddle transcript ingest manifest",
        "",
        f"Generated at: `{generated_at}`",
        f"Source root: `{display_path(input_dir)}`",
        "",
        "## LLM wiki workflow",
        "",
        "1. Read `wiki/index.md` and recent `wiki/log.md` entries.",
        "2. Read each transcript source below and summarize durable outcomes only.",
        "3. Update/create source summaries under `wiki/sources/huddle-transcripts/`, plus relevant channel, topic, decision, and question pages.",
        "4. Cite transcript evidence compactly; do not copy whole transcripts into the wiki.",
        "5. Append a `wiki/log.md` ingest entry with touched pages and unresolved follow-ups.",
        "",
        "Citation format:",
        "",
        "```md",
        "Source: `qmd/huddle-transcripts/...` source_path=`/absolute/source.txt`",
        "```",
        "",
        "## Sources",
        "",
    ]
    for path, meta, digest in sources:
        rel = display_path(path)
        title = meta.get("title", "Transcript")
        date = meta.get("date", "")
        source_path = meta.get("source_path", "")
        source_sha = meta.get("source_sha256", digest)
        details = [f"title={json.dumps(title)}"]
        if date:
            details.append(f"date={json.dumps(date)}")
        if source_path:
            details.append(f"source_path=`{source_path}`")
        details.append(f"source_sha256={source_sha}")
        lines.append(f"- [ ] `{rel}` " + " ".join(details))
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create an inbox manifest for new or changed materialized huddle transcript markdown files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR), help="Materialized transcript markdown directory.")
    parser.add_argument("--output", default=None, help="Manifest output path. Defaults to inbox/huddle-transcript-ingest-<timestamp>.md.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file used to remember already-manifested transcript versions.")
    parser.add_argument("--all", action="store_true", help="Include every transcript, not only new or changed files.")
    parser.add_argument("--dry-run", action="store_true", help="Report the manifest candidate set without writing files or state.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum transcript markdown files to include.")
    args = parser.parse_args(argv)

    input_dir = Path(args.input_dir).expanduser()
    state_file = Path(args.state_file).expanduser()
    state = load_state(state_file)

    candidates: list[tuple[Path, dict[str, str], str]] = []
    for path in find_sources(input_dir):
        digest = sha256_file(path)
        rel = display_path(path)
        if args.all or state.get(rel) != digest:
            candidates.append((path, parse_frontmatter(path), digest))
    if args.limit is not None:
        candidates = candidates[: args.limit]

    output = Path(args.output).expanduser() if args.output else ROOT / "inbox" / f"huddle-transcript-ingest-{utc_stamp()}.md"
    if candidates and not args.dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        tmp = output.with_suffix(output.suffix + ".tmp")
        tmp.write_text(manifest_body(candidates, input_dir), encoding="utf-8")
        tmp.replace(output)
        for path, _meta, digest in candidates:
            state[display_path(path)] = digest
        write_state(state_file, state)

    print(json.dumps({
        "input_dir": display_path(input_dir),
        "output": display_path(output),
        "sources": len(candidates),
        "dry_run": args.dry_run,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
