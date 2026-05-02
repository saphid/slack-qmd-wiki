#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any
from slack_sync import ROOT, SlackApiError, SlackClient, token_from_env

def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Slack user profile metadata for a chunk run.")
    parser.add_argument("--run-id", default="all-feeds")
    args = parser.parse_args()
    out = ROOT / "chunks/slack" / args.run_id / "user_profiles.json"
    token = token_from_env()
    client = SlackClient(token)
    profiles: dict[str, dict[str, Any]] = {}
    cursor = ""
    while True:
        params: dict[str, Any] = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = client.api("users.list", params)
        for member in data.get("members", []):
            profile = member.get("profile") or {}
            uid = member.get("id")
            if not uid:
                continue
            profiles[uid] = {
                "id": uid,
                "team_id": member.get("team_id") or "",
                "name": member.get("name") or "",
                "real_name": member.get("real_name") or profile.get("real_name") or "",
                "display_name": profile.get("display_name") or "",
                "display_name_normalized": profile.get("display_name_normalized") or "",
                "real_name_normalized": profile.get("real_name_normalized") or "",
                "title": profile.get("title") or "",
                "is_bot": bool(member.get("is_bot")),
                "deleted": bool(member.get("deleted")),
            }
        cursor = data.get("response_metadata", {}).get("next_cursor") or ""
        if not cursor:
            break
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(profiles, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(out)
    print(json.dumps({"out": str(out.relative_to(ROOT)), "profiles": len(profiles)}, sort_keys=True))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
