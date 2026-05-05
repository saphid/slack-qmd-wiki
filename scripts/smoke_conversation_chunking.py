#!/usr/bin/env python3
"""Smoke check for conversation-oriented Slack chunk materialization."""
from __future__ import annotations

import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_json(args: list[str]) -> dict[str, object]:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def build_fixture(root: Path) -> None:
    run = root / "chunks/slack/test-run"
    chan = run / "C123"
    write_json(run / "users.json", {"U1": "Alice", "U2": "Bob", "U3": "Carol"})
    write_json(chan / "conversation.json", {"id": "C123", "name": "team-test"})
    write_json(chan / "history/page-000001.json", {"channel": {"id": "C123", "name": "team-test"}, "messages": [
        {"ts": "1000.000000", "user": "U1", "text": "Deploy failed for service factory", "thread_ts": "1000.000000", "reply_count": 2},
        {"ts": "1600.000000", "user": "U2", "text": "Morning, looking at dashboard alerts"},
        {"ts": "1620.000000", "user": "U3", "text": "Same alert, Grafana dashboard is red"},
        {"ts": "5000.000000", "user": "U2", "text": "New topic: lunch plans"},
    ]})
    write_json(chan / "replies/1000-000000/page-000001.json", {"channel": {"id": "C123", "name": "team-test"}, "thread_ts": "1000.000000", "messages": [
        {"ts": "1000.000000", "user": "U1", "text": "Deploy failed for service factory", "thread_ts": "1000.000000"},
        {"ts": "1010.000000", "user": "U2", "text": "Logs show missing company id", "thread_ts": "1000.000000"},
        {"ts": "1020.000000", "user": "U1", "text": "Fix is in review", "thread_ts": "1000.000000"},
    ]})
    transcript = root / "qmd/huddle-transcripts/source/meeting.md"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('---\ntitle: "Transcript Fixture"\n---\n\n# Transcript Fixture\n\nOne transcript file.\n', encoding="utf-8")


def assert_output(root: Path, output_dir: str, expected_mode: str) -> None:
    out = root / output_dir
    thread_files = list(out.glob("test-run/C123/threads/*.md"))
    channel_files = list(out.glob("test-run/C123/channel-conversations/*.md"))
    transcript_files = list(out.glob("transcripts/**/*.md"))
    assert len(thread_files) == 1, thread_files
    assert len(channel_files) == 2, channel_files
    assert len(transcript_files) == 1, transcript_files
    thread_text = thread_files[0].read_text(encoding="utf-8")
    assert 'chunk_kind: "thread"' in thread_text
    assert 'segmentation_mode: "deterministic"' in thread_text
    assert "Logs show missing company id" in thread_text
    channel_text = "\n".join(p.read_text(encoding="utf-8") for p in channel_files)
    assert 'chunk_kind: "channel"' in channel_text
    assert f'segmentation_mode: "{expected_mode}"' in channel_text
    assert "Grafana dashboard is red" in channel_text
    assert "New topic: lunch plans" in channel_text
    transcript_text = transcript_files[0].read_text(encoding="utf-8")
    assert 'chunk_kind: "transcript"' in transcript_text
    assert 'segmentation_mode: "deterministic"' in transcript_text


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="conversation-chunk-smoke-") as tmp_name:
        root = Path(tmp_name) / "repo"
        build_fixture(root)

        result = run_json([sys.executable, "scripts/materialize_qmd_conversation_chunks.py", "--root", str(root), "--run-id", "test-run"])
        assert result["threads"] == 1, result
        assert result["channel_conversations"] == 2, result
        assert result["transcripts"] == 1, result
        assert result["channel_mode"] == "heuristic", result
        assert_output(root, "qmd/slack-conversations", "heuristic")

        fake_llm = root / "fake_segmenter.py"
        fake_llm.write_text(
            "import json, sys\n"
            "json.load(sys.stdin)\n"
            "print(json.dumps({'conversations':[{'start_index':0,'end_index':1,'title':'alerts'},{'start_index':2,'end_index':2,'title':'lunch'}]}))\n",
            encoding="utf-8",
        )
        llm_result = run_json([
            sys.executable,
            "scripts/materialize_qmd_conversation_chunks.py",
            "--root", str(root),
            "--run-id", "test-run",
            "--output-dir", "qmd/slack-conversations-llm",
            "--channel-mode", "llm",
            "--llm-provider", "command",
            "--llm-command", f"{sys.executable} {fake_llm}",
        ])
        assert llm_result["channel_mode"] == "llm", llm_result
        assert llm_result["llm_windows"] == 1, llm_result
        assert llm_result["llm_fallback_windows"] == 0, llm_result
        assert_output(root, "qmd/slack-conversations-llm", "llm")

        fake_pi = root / "fake_pi.py"
        fake_pi.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "assert '@' in ' '.join(sys.argv)\n"
            "print(json.dumps({'conversations':[{'start_index':0,'end_index':1,'title':'alerts'},{'start_index':2,'end_index':2,'title':'lunch'}]}))\n",
            encoding="utf-8",
        )
        fake_pi.chmod(0o755)
        pi_result = run_json([
            sys.executable,
            "scripts/materialize_qmd_conversation_chunks.py",
            "--root", str(root),
            "--run-id", "test-run",
            "--output-dir", "qmd/slack-conversations-pi",
            "--channel-mode", "llm",
            "--llm-provider", "pi",
            "--pi-bin", str(fake_pi),
        ])
        assert pi_result["llm_provider"] == "pi", pi_result
        assert pi_result["llm_windows"] == 1, pi_result
        assert_output(root, "qmd/slack-conversations-pi", "llm")
    print("conversation chunking smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
