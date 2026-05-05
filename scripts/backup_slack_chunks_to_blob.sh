#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
default_slack_run_id() {
  python3 - <<'PY'
import json, pathlib
state = pathlib.Path('.state/slack-chunk-download-state.json')
if state.exists():
    try:
        print(json.loads(state.read_text()).get('run_id') or 'all-feeds')
        raise SystemExit
    except Exception:
        pass
print('all-feeds')
PY
}
ACCOUNT="${AZURE_STORAGE_ACCOUNT:-cawalexarchives001}"
CONTAINER="${AZURE_STORAGE_CONTAINER:-llm-wiki-slack-backups}"
RUN_ID="${SLACK_BACKUP_RUN_ID:-$(default_slack_run_id)}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR=".state/backups"
mkdir -p "$BACKUP_DIR"
ARCHIVE="$BACKUP_DIR/llm-wiki-slack-${RUN_ID}-${STAMP}.tar.zst"
MANIFEST="$BACKUP_DIR/llm-wiki-slack-${RUN_ID}-${STAMP}.manifest.json"
BLOB_PREFIX="displayr-llm-wiki/${RUN_ID}"
BLOB_NAME="$BLOB_PREFIX/$(basename "$ARCHIVE")"
MANIFEST_BLOB_NAME="$BLOB_PREFIX/$(basename "$MANIFEST")"
LATEST_BLOB_NAME="$BLOB_PREFIX/latest.manifest.json"

printf "started_at=%s\n" "$(date -Is)"
printf "account=%s container=%s blob=%s\n" "$ACCOUNT" "$CONTAINER" "$BLOB_NAME"
printf "archive=%s\n" "$ARCHIVE"

python3 - <<PY > "$MANIFEST"
import json, os, subprocess, time
from pathlib import Path
root = Path.cwd()
run_id = "$RUN_ID"
chunk_root = root / "chunks" / "slack" / run_id
state_path = root / ".state" / "slack-chunk-download-state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {}
convs = state.get("conversations", {})
statuses = {}
for v in convs.values():
    statuses[v.get("status", "unknown")] = statuses.get(v.get("status", "unknown"), 0) + 1

def count(pattern):
    return sum(1 for _ in root.glob(pattern))

def du(path):
    if not path.exists():
        return None
    out = subprocess.check_output(["du", "-sb", str(path)], text=True).split()[0]
    return int(out)
manifest = {
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "host": os.uname().nodename,
    "workspace": str(root),
    "run_id": run_id,
    "source_paths": [
        f"chunks/slack/{run_id}",
        "raw/slack",
        "inbox",
        "wiki",
        ".state/slack-chunk-download-state.json",
        ".state/slack-download-chunks.latest.log",
        "scripts/slack_download_chunks.py",
        "scripts/slack_sync.py",
    ],
    "chunk_json_files": count(f"chunks/slack/{run_id}/**/*.json"),
    "chunk_bytes": du(chunk_root),
    "raw_markdown_files": count("raw/slack/**/*.md"),
    "state_conversations": len(convs),
    "state_statuses": statuses,
    "state_errors": len(state.get("errors", {})),
    "note": "No .env, venv, OAuth token, or raw credential files are included.",
}
print(json.dumps(manifest, indent=2, sort_keys=True))
PY

# Archive the durable Slack text/chunk data. Exclude credentials and virtualenv.
tar --ignore-failed-read \
  --exclude=.env \
  --exclude=.venv \
  --exclude=".state/backups" \
  --exclude="chunks/slack/${RUN_ID}/**/*.tmp" \
  -I "zstd -T0 -3" \
  -cf "$ARCHIVE" \
  "chunks/slack/${RUN_ID}" \
  raw/slack \
  inbox \
  wiki \
  .state/slack-chunk-download-state.json \
  .state/slack-download-chunks.latest.log \
  scripts/slack_download_chunks.py \
  scripts/slack_sync.py \
  "$MANIFEST"

BYTES=$(wc -c < "$ARCHIVE" | tr -d " ")
SHA256=$(sha256sum "$ARCHIVE" | awk "{print \$1}")
python3 - <<PY
import json
from pathlib import Path
p = Path("$MANIFEST")
d = json.loads(p.read_text())
d.update({"archive_bytes": int("$BYTES"), "archive_sha256": "$SHA256", "archive_name": "$(basename "$ARCHIVE")"})
p.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")
PY

az storage blob upload \
  --account-name "$ACCOUNT" \
  --container-name "$CONTAINER" \
  --name "$BLOB_NAME" \
  --file "$ARCHIVE" \
  --auth-mode login \
  --tier Cool \
  --overwrite true \
  --only-show-errors
az storage blob upload \
  --account-name "$ACCOUNT" \
  --container-name "$CONTAINER" \
  --name "$MANIFEST_BLOB_NAME" \
  --file "$MANIFEST" \
  --auth-mode login \
  --tier Cool \
  --overwrite true \
  --only-show-errors
az storage blob upload \
  --account-name "$ACCOUNT" \
  --container-name "$CONTAINER" \
  --name "$LATEST_BLOB_NAME" \
  --file "$MANIFEST" \
  --auth-mode login \
  --tier Cool \
  --overwrite true \
  --only-show-errors

printf "uploaded_at=%s\n" "$(date -Is)"
printf "archive_bytes=%s sha256=%s\n" "$BYTES" "$SHA256"
printf "blob=https://%s.blob.core.windows.net/%s/%s\n" "$ACCOUNT" "$CONTAINER" "$BLOB_NAME"
# Keep local archive until upload succeeds, then remove it to preserve VM disk.
rm -f "$ARCHIVE"
printf "removed_local_archive=%s\n" "$ARCHIVE"
