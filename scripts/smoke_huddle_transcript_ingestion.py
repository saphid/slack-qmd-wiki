#!/usr/bin/env python3
"""Deterministic smoke check for huddle transcript materialization."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_json(args: list[str]) -> dict[str, object]:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"expected JSON stdout from {args}: {proc.stdout!r} stderr={proc.stderr!r}") from exc


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="huddle-transcript-smoke-") as tmp_name:
        tmp = Path(tmp_name)
        docs = tmp / "Work/Docs"
        source_dir = docs / "huddle-transcripts-fixture"
        source_dir.mkdir(parents=True)
        source = source_dir / "team-sync.txt"
        source.write_text(
            "\n".join([
                "# Huddle Transcript: Fixture Team Sync",
                "# Date: 2026-05-01",
                "# Duration: 17m",
                "# Canvas: https://example.invalid/canvas",
                "# Transcript file: team-sync.txt",
                "",
                "Alice: We decided to ship the transcript materializer.",
                "Bob: Action item is to wire it into QMD refresh.",
                "",
            ]),
            encoding="utf-8",
        )

        output_dir = tmp / "qmd/huddle-transcripts"
        first = run_json([
            sys.executable,
            "scripts/materialize_qmd_huddle_transcripts.py",
            "--source-root",
            str(docs),
            "--output-dir",
            str(output_dir),
        ])
        assert first["created"] == 1, first
        rendered = list(output_dir.rglob("*.md"))
        assert len(rendered) == 1, rendered
        rendered_text = rendered[0].read_text(encoding="utf-8")
        assert 'title: "Fixture Team Sync"' in rendered_text
        assert "# Fixture Team Sync" in rendered_text
        assert 'date: "2026-05-01"' in rendered_text
        assert 'duration: "17m"' in rendered_text
        assert "Alice: We decided to ship the transcript materializer." in rendered_text

        second = run_json([
            sys.executable,
            "scripts/materialize_qmd_huddle_transcripts.py",
            "--source-root",
            str(docs),
            "--output-dir",
            str(output_dir),
        ])
        assert second["skipped"] == 1, second

        manifest = tmp / "inbox/manifest.md"
        state = tmp / "state.json"
        manifest_result = run_json([
            sys.executable,
            "scripts/create_huddle_transcript_wiki_manifest.py",
            "--input-dir",
            str(output_dir),
            "--output",
            str(manifest),
            "--state-file",
            str(state),
        ])
        assert manifest_result["sources"] == 1, manifest_result
        manifest_text = manifest.read_text(encoding="utf-8")
        assert "Huddle transcript ingest manifest" in manifest_text
        assert str(rendered[0].resolve()) in manifest_text
        assert "do not copy whole transcripts into the wiki" in manifest_text

        empty_result = run_json([
            sys.executable,
            "scripts/create_huddle_transcript_wiki_manifest.py",
            "--input-dir",
            str(output_dir),
            "--output",
            str(tmp / "inbox/manifest-empty.md"),
            "--state-file",
            str(state),
        ])
        assert empty_result["sources"] == 0, empty_result

    print("huddle transcript ingestion smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
