#!/usr/bin/env python3
"""Materialize sanitized huddle/standup transcript text files for QMD.

This script does not fetch Slack. It consumes already-written sanitized
transcript `.txt` files from local `Work/Docs` roots and writes markdown under
`qmd/huddle-transcripts/` for indexing and later wiki ingestion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOTS = [
    Path.home() / "Work/Docs",
    Path("/home/alex/Work/Docs"),
]
HEADER_KEYS = {
    "date": "date",
    "duration": "duration",
    "canvas": "canvas",
    "transcript file": "transcript_file",
}
HEADER_RE = re.compile(r"^#\s*(?P<key>[^:\n]+?)(?::\s*(?P<value>.*))?$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._") or "unknown"


def root_slug(root: Path) -> str:
    resolved = root.expanduser().resolve()
    if resolved == Path("/home/alex/Work/Docs"):
        return "vm-work-docs"
    if resolved == (Path.home() / "Work/Docs").expanduser().resolve():
        return "mac-work-docs" if str(resolved).startswith("/Users/") else "work-docs"
    return safe_slug(str(resolved).strip("/").replace("/", "-"))


def yaml_value(value: object) -> str:
    if value is None:
        return '""'
    return json.dumps(str(value), ensure_ascii=False)


def frontmatter(meta: dict[str, object]) -> str:
    lines = ["---"]
    for key in sorted(meta):
        lines.append(f"{key}: {yaml_value(meta[key])}")
    lines.append("---")
    return "\n".join(lines)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_existing_source_sha(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    if end < 0:
        return ""
    match = re.search(r'^source_sha256:\s*["\']?([^"\'\n]+)', text[:end], re.MULTILINE)
    return match.group(1).strip() if match else ""


def transcript_kind(folder_name: str) -> str:
    if folder_name.startswith("standup-transcripts-"):
        return "standup"
    return "huddle"


def parse_transcript(text: str, fallback_kind: str) -> tuple[dict[str, str], str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    meta: dict[str, str] = {}
    consumed = 0

    for index, line in enumerate(lines):
        if not line.strip() and index == consumed:
            consumed += 1
            continue
        match = HEADER_RE.match(line)
        if not match:
            break
        raw_key = match.group("key").strip()
        value = (match.group("value") or "").strip()
        key = raw_key.lower()
        if key in ("huddle transcript", "standup transcript"):
            meta["title"] = value or raw_key
        elif key in HEADER_KEYS:
            meta[HEADER_KEYS[key]] = value
        else:
            break
        consumed = index + 1

    while consumed < len(lines) and not lines[consumed].strip():
        consumed += 1

    meta.setdefault("title", f"{fallback_kind.title()} Transcript")
    return meta, "\n".join(lines[consumed:]).strip()


def source_roots(values: list[str] | None) -> list[Path]:
    if values:
        return [Path(value).expanduser() for value in values]
    roots: list[Path] = []
    for candidate in DEFAULT_SOURCE_ROOTS:
        expanded = candidate.expanduser()
        if expanded.exists() and expanded not in roots:
            roots.append(expanded)
    return roots


def find_transcripts(roots: Iterable[Path]) -> list[tuple[Path, Path]]:
    files: list[tuple[Path, Path]] = []
    for root in roots:
        if not root.exists():
            continue
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            if not (folder.name.startswith("huddle-transcripts-") or folder.name.startswith("standup-transcripts-")):
                continue
            for path in sorted(folder.rglob("*.txt")):
                files.append((root, path))
    return sorted(files, key=lambda item: (str(item[0]), str(item[1])))


def output_path(source_root: Path, source_file: Path, output_dir: Path) -> Path:
    rel = source_file.relative_to(source_root)
    return output_dir / root_slug(source_root) / rel.with_suffix(".md")


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def materialize_one(source_root: Path, source_file: Path, output_dir: Path, dry_run: bool) -> tuple[str, Path]:
    out = output_path(source_root, source_file, output_dir)
    existed = out.exists()
    source_hash = sha256_file(source_file)
    if read_existing_source_sha(out) == source_hash:
        return "skipped", out

    source_text = source_file.read_text(encoding="utf-8", errors="replace")
    source_folder = source_file.relative_to(source_root).parts[0]
    kind = transcript_kind(source_folder)
    parsed_meta, body = parse_transcript(source_text, kind)
    generated_at = utc_now()
    meta: dict[str, object] = {
        "type": "huddle-transcript",
        "transcript_kind": kind,
        "source_root": str(source_root.resolve()),
        "source_folder": source_folder,
        "source_file": str(source_file.relative_to(source_root)),
        "source_path": str(source_file.resolve()),
        "source_sha256": source_hash,
        "generated_at": generated_at,
    }
    meta.update(parsed_meta)

    title = str(meta["title"])
    lines = [
        frontmatter(meta),
        "",
        f"# {title}",
        "",
        f"Source: `{display_path(out)}` source_path=`{source_file.resolve()}`",
        "",
    ]
    if body:
        lines.append(body)
        lines.append("")
    content = "\n".join(lines)

    if dry_run:
        return ("updated" if existed else "created"), out

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(out)
    return ("updated" if existed else "created"), out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize sanitized huddle/standup transcript .txt files into markdown for QMD.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source-root", action="append", default=None, help="Root containing huddle-transcripts-* or standup-transcripts-* folders. Repeatable.")
    parser.add_argument("--output-dir", default=str(ROOT / "qmd/huddle-transcripts"), help="Markdown output directory.")
    parser.add_argument("--dry-run", action="store_true", help="Report intended writes without changing files.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum transcript files to process.")
    args = parser.parse_args(argv)

    roots = source_roots(args.source_root)
    output_dir = Path(args.output_dir).expanduser()
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    files = find_transcripts(roots)
    if args.limit is not None:
        files = files[: args.limit]

    counts = {"created": 0, "updated": 0, "skipped": 0}
    outputs: list[str] = []
    for source_root, source_file in files:
        action, out = materialize_one(source_root, source_file, output_dir, args.dry_run)
        counts[action] += 1
        outputs.append(display_path(out))

    print(json.dumps({
        "source_roots": [str(root) for root in roots],
        "output_dir": display_path(output_dir),
        "transcripts": len(files),
        **counts,
        "outputs": outputs[:20],
        "dry_run": args.dry_run,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
