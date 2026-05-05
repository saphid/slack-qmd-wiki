#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
export QMD_LLAMA_GPU="${QMD_LLAMA_GPU:-false}"
export QMD_EMBED_MODEL="${QMD_EMBED_MODEL:-hf:mixedbread-ai/mxbai-embed-large-v1/gguf/mxbai-embed-large-v1-f16.gguf}"
# Re-index both markdown collections and refresh embeddings for hybrid search.
# This indexes only markdown (`raw/slack/**/*.md` and `wiki/**/*.md`), not live credentials or JSON chunks.
qmd update
qmd embed --max-docs-per-batch "${QMD_MAX_DOCS_PER_BATCH:-200}" --max-batch-mb "${QMD_MAX_BATCH_MB:-64}"
qmd status
