#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
# Re-index both markdown collections and refresh embeddings for hybrid search.
# This indexes only markdown (`raw/slack/**/*.md` and `wiki/**/*.md`), not live credentials or JSON chunks.
qmd update
qmd embed --max-docs-per-batch "${QMD_MAX_DOCS_PER_BATCH:-200}" --max-batch-mb "${QMD_MAX_BATCH_MB:-64}"
qmd status
