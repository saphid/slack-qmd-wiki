#!/usr/bin/env python3
"""Local CLI search for displayr-llm-wiki QMD indexes."""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from typing import Iterable

COLLECTIONS = {
    "raw": ["slack-raw"],
    "slack": ["slack-raw"],
    "chunks": ["slack-api-chunks"],
    "api-chunks": ["slack-api-chunks"],
    "conversation": ["slack-conversations"],
    "conversations": ["slack-conversations"],
    "batches": ["slack-conversations"],
    "meeting": ["huddle-transcripts"],
    "meetings": ["huddle-transcripts"],
    "huddle": ["huddle-transcripts"],
    "huddles": ["huddle-transcripts"],
    "transcripts": ["huddle-transcripts"],
    "wiki": ["llm-wiki"],
    "all": ["slack-api-chunks", "slack-conversations", "huddle-transcripts", "slack-raw", "llm-wiki"],
}


def qmd_path() -> str:
    path = shutil.which("qmd")
    if not path:
        raise SystemExit("qmd not found. Install with: npm install -g @tobilu/qmd")
    return path


def add_collection_args(args: list[str], collections: Iterable[str]) -> None:
    for name in collections:
        args.extend(["-c", name])


def build_args(ns: argparse.Namespace) -> list[str]:
    qmd = qmd_path()
    collection_names: list[str] = []
    for selected in (ns.collection or ["all"]):
        collection_names.extend(COLLECTIONS[selected])
    # Preserve order but avoid duplicates.
    collection_names = list(dict.fromkeys(collection_names))

    if ns.mode == "lex":
        args = [qmd, "search", ns.query]
    elif ns.mode == "vec":
        args = [qmd, "vsearch", ns.query]
    else:
        # Hybrid QMD query. This may be slower on the CPU-only VM, but gives
        # expansion/vector/rerank behavior once embeddings are ready.
        query = ns.query if ns.query.startswith(("lex:", "vec:", "hyde:", "intent:", "expand:")) else f"expand: {ns.query}"
        args = [qmd, "query", query]
        if ns.no_rerank:
            args.append("--no-rerank")
        if ns.candidate_limit:
            args.extend(["-C", str(ns.candidate_limit)])

    args.extend(["-n", str(ns.limit)])
    add_collection_args(args, collection_names)

    if ns.full:
        args.append("--full")
    if ns.line_numbers:
        args.append("--line-numbers")
    if ns.json:
        args.append("--json")
    elif ns.files:
        args.append("--files")
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Search the llm-wiki QMD indexes for Slack chunks, huddle transcripts, raw Slack markdown, and generated wiki pages.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("query", help="Search query. For --mode hybrid this may also be a QMD typed query document like 'lex: auth\nvec: how auth works'.")
    parser.add_argument("-m", "--mode", choices=["lex", "hybrid", "vec"], default="lex", help="Search mode. lex is fastest; hybrid uses QMD expansion/vector/reranking and can be slow on CPU.")
    parser.add_argument("-c", "--collection", choices=sorted(COLLECTIONS), action="append", default=None, help="Corpus to search. Repeatable. Defaults to all corpora.")
    parser.add_argument("-n", "--limit", type=int, default=10, help="Maximum results.")
    parser.add_argument("--json", action="store_true", help="Return QMD JSON.")
    parser.add_argument("--files", action="store_true", help="Return matching files only.")
    parser.add_argument("--full", action="store_true", help="Return full documents instead of snippets.")
    parser.add_argument("--line-numbers", action="store_true", default=True, help="Include line numbers in snippets where supported.")
    parser.add_argument("--no-line-numbers", dest="line_numbers", action="store_false", help="Disable line numbers.")
    parser.add_argument("--no-rerank", action="store_true", help="For hybrid mode, skip LLM reranking.")
    parser.add_argument("--candidate-limit", type=int, default=20, help="For hybrid mode, cap rerank candidates.")
    ns = parser.parse_args(argv)

    try:
        args = build_args(ns)
        proc = subprocess.run(args, text=True)
        return proc.returncode
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if ns.json:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
