#!/usr/bin/env python3
"""Collect the earlier review threads of a pull request, and the code that changed since each comment. Read-only.

For every inline thread started by someone other than the PR author, the output says what was asked, what was answered,
and what changed in that file between the commit that was commented on and the reviewed head. The reviewer then gives each
thread a status with evidence in the draft (see references/draft-template.md), and reviews that delta like any other
change: a fix can solve the comment and introduce a new problem.

GitHub is only ever read, with `gh api <GET path>` and `gh pr view`. Nothing here can write, comment or resolve.
REST does not expose whether a thread is resolved, so a thread is marked `outdated` when GitHub no longer maps it to a
line of the current diff, and nothing more.

Usage:
  threads.py --pr N --head <ref> --out DIR [--repo owner/name] [--raw raw.json]
Writes DIR/threads.json, DIR/thread-deltas/<thread id>.diff and DIR/raw.json (the unprocessed API data).
With --raw it reads that file instead of calling GitHub (offline use and tests).
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MAX_DELTA_LINES = 400
MAX_COMMITS = 10
STATUSES = ("Addressed", "Partially addressed", "Not addressed", "No longer applicable", "Deferred", "Unclear")


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def gh_get(path: str) -> list[dict]:
    """GET a paginated list from the GitHub REST API. Only ever builds a read request."""
    command = ["gh", "api", path, "--paginate", "--jq", ".[] | @json"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"gh api {path} failed: {result.stderr.strip()}")
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def repo_from_origin() -> str:
    url = git("remote", "get-url", "origin").strip()
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise SystemExit(f"Cannot read owner/name from origin '{url}'. Pass --repo owner/name")
    return match.group(1)


def fetch_raw(repo: str, pr: int) -> dict:
    view = subprocess.run(["gh", "pr", "view", str(pr), "--repo", repo, "--json", "author,headRefOid"], capture_output=True, text=True)
    if view.returncode != 0:
        raise SystemExit(f"gh pr view failed: {view.stderr.strip()}")
    info = json.loads(view.stdout)
    return {"pr": {"author": info["author"]["login"], "headRefOid": info["headRefOid"]},
            "comments": gh_get(f"repos/{repo}/pulls/{pr}/comments"),
            "reviews": gh_get(f"repos/{repo}/pulls/{pr}/reviews")}


def commit_exists(sha: str) -> bool:
    return bool(sha) and subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode == 0


def delta_since(sha: str, head: str, path: str, out_dir: Path, thread_id: int) -> dict:
    """What changed in `path` between the commented commit and head."""
    if not path or not commit_exists(sha):
        return {"available": False, "touched": None, "commits": [], "diff_file": None,
                "note": "The commented commit is not in this clone (force-push or shallow fetch); compare by reading the file at head."}
    diff = git("diff", "--no-color", "-U3", sha, head, "--", path, check=False)
    commits = git("log", "--format=%h %s", f"-{MAX_COMMITS}", f"{sha}..{head}", "--", path, check=False).splitlines()
    diff_file = None
    if diff.strip():
        lines = diff.splitlines()
        text = "\n".join(lines[:MAX_DELTA_LINES]) + ("\n[truncated]\n" if len(lines) > MAX_DELTA_LINES else "\n")
        target = out_dir / "thread-deltas" / f"{thread_id}.diff"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        diff_file = str(target)
    return {"available": True, "touched": bool(diff.strip()), "commits": commits, "diff_file": diff_file, "note": ""}


def is_bot(user: dict) -> bool:
    return user.get("type") == "Bot" or user.get("login", "").endswith("[bot]")


def build(raw: dict, head: str, out_dir: Path) -> dict:
    author = raw["pr"]["author"]
    comments = sorted(raw["comments"], key=lambda c: c["id"])
    replies: dict[int, list[dict]] = {}
    roots = []
    for comment in comments:
        parent = comment.get("in_reply_to_id")
        if parent:
            replies.setdefault(parent, []).append(comment)
        else:
            roots.append(comment)
    threads = []
    for root in roots:
        user = root["user"]
        from_reviewer = user["login"] != author and not is_bot(user)
        thread_replies = replies.get(root["id"], [])
        last = thread_replies[-1] if thread_replies else root
        line = root.get("line") or None
        threads.append({
            "id": root["id"],
            "path": root.get("path"),
            "line": line,
            "original_line": root.get("original_line"),
            "outdated": line is None,
            "reviewer": user["login"],
            "from_reviewer": from_reviewer,
            "needs_status": from_reviewer,
            "request": root["body"],
            "replies": [{"author": r["user"]["login"], "body": r["body"]} for r in thread_replies],
            "author_replied": any(r["user"]["login"] == author for r in thread_replies),
            "last_commenter": last["user"]["login"],
            "commented_commit": root.get("original_commit_id") or root.get("commit_id"),
            "delta": delta_since(root.get("original_commit_id") or root.get("commit_id") or "", head, root.get("path") or "", out_dir, root["id"]),
        })
    latest: dict[str, str] = {}
    for review in sorted(raw["reviews"], key=lambda r: r.get("submitted_at") or ""):
        if review["state"] in ("CHANGES_REQUESTED", "APPROVED", "DISMISSED"):
            latest[review["user"]["login"]] = review["state"]
    review_notes = [{"reviewer": r["user"]["login"], "state": r["state"], "body": r["body"]}
                    for r in raw["reviews"] if (r.get("body") or "").strip() and r["state"] != "PENDING" and not is_bot(r["user"])]
    return {
        "pr_author": author,
        "head": raw["pr"].get("headRefOid"),
        "reviewed_head": git("rev-parse", head).strip(),
        "threads": threads,
        "needing_status": [t["id"] for t in threads if t["needs_status"]],
        "reviewer_states": latest,
        "open_change_requests": sorted(login for login, state in latest.items() if state == "CHANGES_REQUESTED"),
        "review_notes": review_notes,
        "statuses": list(STATUSES),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--repo")
    parser.add_argument("--head", required=True, help="Ref of the reviewed head (for example pr-123)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--raw", type=Path, help="Read this saved API data instead of calling GitHub")
    args = parser.parse_args()

    if args.raw:
        raw = json.loads(args.raw.read_text(encoding="utf-8"))
    else:
        if args.pr is None:
            parser.error("--pr is required unless --raw is given")
        raw = fetch_raw(args.repo or repo_from_origin(), args.pr)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "raw.json").write_text(json.dumps(raw, indent=1), encoding="utf-8")
    result = build(raw, args.head, args.out)
    (args.out / "threads.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    needing = len(result["needing_status"])
    print(f"{len(result['threads'])} threads, {needing} need a status; open change requests: {', '.join(result['open_change_requests']) or 'none'}")
    if result["head"] and result["head"] != result["reviewed_head"]:
        print(f"warning: the PR head is now {result['head'][:8]} but {args.head} is {result['reviewed_head'][:8]}. Fetch again before reviewing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
