---
name: lens-pr-review
description: Use when asked to review a pull request or a branch ("review PR 123", "review this PR", "check my branch before I open a PR", "is this ready to merge"). Runs a deterministic scanner plus a set of review lenses, and writes a local draft (draft.md) with suggested, human-sounding review comments for the person to read, adapt and post themselves. Never posts to GitHub and never approves.
---

# lens-pr-review

Reviews a PR the way a senior colleague who knows the codebase would: a deterministic scanner checks every added line,
then review lenses (reuse, ownership, queries, tests, rollout, business values, correctness...) are applied to every
behavioural file. The result is **one local Markdown draft**. The human reads it, decides which comments are worth
posting, and posts them (or not).

## Hard rules

1. **Never write to GitHub.** No review, no comment, no status, no label, no `gh pr review`, no `gh api` with POST,
   PATCH or DELETE, no MCP write tool. Reading (`gh pr view`, `gh api` GET, MCP read tools) is fine.
2. **Never approve.** The suggested verdict is `COMMENT` or `REQUEST_CHANGES`, and only a suggestion.
3. **Never modify the code under review**, never check out the PR branch over the user's work, never push.
4. **A comment may be `blocking-suggested` only with evidence you verified** (see *Weights*). Without it, it is a question.
5. **Be honest about gaps**: what you did not run, could not see or did not know goes in *Not verified*.

`SKILL_DIR` below is the base directory of this skill (shown when it loads). Run the scripts with `python3`.

## Where things live

| Path | What | Edit it to... |
|---|---|---|
| `lenses/*.md` | One file per review lens (when to apply, what to check, how to phrase, examples, whether it may block) | add, change or tune a lens |
| `profiles/<name>/` | `profile.json` (scopes, signals, enabled lenses, `extends`), `rules.json` (mechanical rules), `lens-notes/<lens>.md` (project facts for a lens) | adapt to a project or team |
| `references/comment-voice.md` | How the suggested comments must sound | tune the tone |
| `references/draft-template.md`, `references/mermaid.md` | Draft format and diagram rules | change the output |
| `references/review-map.md` | Risk and confidence scoring of the human review map | rarely |
| `engine/` | Scanner, map builder, lens assembler, draft checker (no project knowledge inside) | fix bugs only |

## Procedure

### 1. Resolve what to review

- `git fetch origin <base> --quiet`. The scanner picks the base from the profile (`origin/master`, `origin/main`).
- **PR**: `git fetch origin pull/<N>/head:pr-<N> --quiet` and use `--head pr-<N>`. Read the title, body, author, head SHA, CI
  status, and the earlier reviews and inline threads (all read-only). Do not repeat a point already raised. Give each
  earlier request a status: Addressed, Partially addressed, Not addressed, No longer applicable, Deferred or Unclear, with evidence.
- If the branch or title has a ticket key, read the ticket for the intent and acceptance criteria when a connector is available.
- **Self-review** (no PR yet): use `--worktree`, which includes uncommitted changes.
- Output directory: `.pr-review/lens/pr-<N>/` or `.pr-review/lens/<branch>/`.

### 2. Scan

```bash
python3 SKILL_DIR/engine/scan_pr.py --head pr-<N> --output .pr-review/lens/pr-<N>/scan.json
python3 SKILL_DIR/engine/scan_pr.py --head pr-<N> --format markdown --output .pr-review/lens/pr-<N>/scan.md
```

The profile is detected automatically (`--profile <name>` overrides; a repo-local `.lens-pr-review/profile.json` wins).
Read `stats`, `findings`, `triage` and **`convention_docs`**: read every listed doc (for example the root and module
`AGENTS.md`) before reviewing. They override this skill's generic advice. Findings are **candidates**: confirm or dismiss
each one with a concrete reason. None is dropped silently.

### 3. Load the lenses

```bash
python3 SKILL_DIR/engine/lenses.py --profile <name-from-scan.json>
```

This prints the playbook for the active profile: each enabled lens plus the profile's notes for it. Follow it.
`--list` shows one line per lens.

### 4. Review

For every HIGH and MEDIUM file, read the **whole file at head** (`git show <head>:<path>`) and its diff. LOW files get a
quick sanity pass (do the tests assert behaviour? do the docs match?).

- Always apply `correctness-and-security`. Apply the other lenses where their *When to apply* matches.
- Do the lens work, not just the reading: search for existing helpers, trace callers, read the old code, derive the SQL,
  run or read the tests. A comment that rests on a search you did not make is a guess.
- Record, per file, what you read, which context you checked, and the checks you made (behaviour, boundary, integration...),
  as `map.json` schema 2 (see `references/review-map.md`), bound to the scan's `revision`.
- **Large PR** (over about 25 HIGH+MEDIUM files or 1500 added production lines): split into batches by module and review
  them in parallel with sub-agents. Give each the absolute paths of the lens playbook and `references/comment-voice.md`,
  its files and its scanner findings, and ask for **candidate findings as JSON** (anchor, lens, evidence, verified-by,
  weight) and ledger rows, not for finished comments. Then merge, remove duplicates, check cross-batch concerns yourself,
  and write all final comments yourself so the voice is consistent. If you cannot start sub-agents, do the batches one by one.

### 5. Choose what to say

- Fewer, better. Around eight comments for a mid-size PR; drop weak ones.
- One comment per point; no repeats of earlier threads; only lines the PR adds or changes (or old code it makes worse).
- Weights: `blocking-suggested` (verified, lens allows it), `discuss`, `minor`, `praise` (at most two).
- Write each comment following `references/comment-voice.md`: short, a question or a proposal, a real symbol, no rule ids.

### 6. Write the draft

Follow `references/draft-template.md` exactly. Use a Mermaid diagram only under the conditions in `references/mermaid.md`.
Put the human review map under *Where to look*:

```bash
python3 SKILL_DIR/engine/check_draft.py .pr-review/lens/pr-<N>/draft.md --head pr-<N> --summary-out .pr-review/lens/pr-<N>/summary.json
python3 SKILL_DIR/engine/build_review_map.py --scan .../scan.json --map .../map.json --summary .../summary.json --links local --output .../map.md
```

`check_draft.py` fails on structure, length, rule ids, AI mentions, missing evidence for blockers, diagrams without a
source line, and anchors that do not exist at head. Fix every error and re-run. Read its warnings: they flag order-like
wording and symbols that do not exist at head (fine only for names you are proposing).

### 7. Reply in chat

Give: the suggested verdict, counts per weight, the three things that matter most, what you could not verify, and the
path to `draft.md`. Do not paste the whole draft. Say plainly that nothing was posted.

## Weights and verification

| Weight | Use when | Needs |
|---|---|---|
| `blocking-suggested` | You believe the merge should wait | `Verified by` filled in; lens `may_block: if-verified` or a scanner rule |
| `discuss` | Worth a conversation; the author may know something you do not | Evidence |
| `minor` | Small, optional | Evidence |
| `praise` | Something good the next reader should copy | Evidence |

A lens marked `may_block: never` can only produce `discuss`, `minor` or `praise`, however sure you feel.

## Maintenance

- New lens: add `lenses/<id>.md` with front matter (`id`, `summary`, `may_block: never|if-verified`) and enable it in a profile.
- New project: add `profiles/<name>/profile.json` (+ `rules.json`, `lens-notes/`). Extend `generic`; add `clean-code-java` or
  `onion-layers` only if the team wants those opinionated rules.
- Tests: `python3 -m unittest discover -s engine/tests -v` from `SKILL_DIR`.
