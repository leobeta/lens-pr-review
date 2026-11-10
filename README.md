# lens-pr-review

A Claude Code plugin that reviews a pull request and writes **a local draft of suggested review comments**. A person reads
the draft, decides which comments are worth it, and posts them. It never writes to GitHub and never approves.

It combines two things:

- A **deterministic scanner** that checks every added line against mechanical rules and ranks files by risk, plus a
  confidence-scored *human review map* that says which files a human must still read.
- A set of **review lenses** (reuse, ownership, queries, tests, rollout, business values, correctness...) that capture how
  experienced reviewers on the team look at a change, written as questions and proposals, not verdicts.

## Install

The repository is also a plugin marketplace (`lens-tools`), so it installs straight from GitHub. Inside Claude Code:

```
/plugin marketplace add leobeta/lens-pr-review
/plugin install lens-pr-review@lens-tools
```

Or from a terminal:

```
claude plugin marketplace add leobeta/lens-pr-review
claude plugin install lens-pr-review@lens-tools
```

You need read access to the repository (a private repo works if your `git` or `gh` is already authenticated).
Restart Claude Code, or run `/reload-plugins`, so the skill is picked up.

To update later: `/plugin marketplace update lens-tools`, then update the plugin.

To try local changes without pushing, point the marketplace at the folder instead:
`/plugin marketplace add ~/repo/lens-pr-review`.

## Use

In a repository, ask "review PR 123" or run `/lens-pr-review:lens-pr-review 123`. For your own branch before opening a PR,
ask for a self-review. The draft lands in `.pr-review/lens/pr-123/draft.md`. Make sure `.pr-review/` is git-ignored in
that repository, and read the draft before posting anything: the skill never posts for you.

## How it is organised

```
skills/lens-pr-review/
  SKILL.md              the procedure Claude follows
  lenses/               one Markdown file per lens: edit or add lenses here
  profiles/
    generic/            security, reliability, tests, risk triage. No team conventions
    clean-code-java/    opt-in: null safety, abstraction over branching, constructor injection
    onion-layers/       opt-in: domain/application/infrastructure/api layering
  references/           comment voice, draft template, diagram rules, review-map scoring
  engine/               scanner, map builder, lens assembler, draft checker (no project knowledge)
examples/project-profile/   template for your own project profile
```

**Engine vs profile.** The engine is the same everywhere. A profile is configuration: which rules, which signals, which
lenses, which docs to read. Profiles extend each other (`"extends": "generic"`). A repository can also ship its own
`.lens-pr-review/profile.json`, which wins over the built-in ones.

**Lenses.** A lens is one file with front matter (`id`, `summary`, `may_block`) and sections: when to apply, what to do
before commenting, how to phrase it, example comments, when it is *not* a finding. `may_block: never` means the lens can
only produce `discuss`, `minor` or `praise`. `may_block: if-verified` allows `blocking-suggested`, and only with a filled-in
*Verified by*. A profile can add project facts to a lens with `profiles/<name>/lens-notes/<lens-id>.md`.

**Earlier review threads.** `engine/threads.py` reads (never writes) the PR's existing review threads and, for each, the code
that changed in that file since the comment. The skill gives every thread a status with evidence, reviews the fix like new
code, and `check_draft.py --threads` refuses a draft that skips a thread.

**The draft** (`references/draft-template.md`) is checked by `engine/check_draft.py`: structure, comment length,
no rule ids or AI mentions, evidence required for blockers, diagrams need a source line, anchors and symbols must exist at
the reviewed commit.

## Add a project

The simplest way: copy `examples/project-profile/` to `.lens-pr-review/` at the root of the repository you review. A
repository-local profile always wins and keeps your conventions out of this plugin. You can also add it under
`skills/lens-pr-review/profiles/<name>/`.

1. Edit `profile.json`: it extends `generic` (add `clean-code-java` / `onion-layers` if you want them).
2. Set `detect.any_path_exists` so it is picked automatically, and `convention_files` (for example `AGENTS.md`).
3. Add `rules.json` only for rules that are verified conventions **and** not already enforced by a build or test.
4. Add `lens-notes/` with the project facts a lens needs (where the helpers live, how queries are built...).
5. Run the tests.

## Tests

```
python3 -B -m unittest discover -s skills/lens-pr-review/engine/tests -v
```

## Status and known limits

- v0.1. Built on a deterministic PR scanner and a confidence-scored review map, plus lenses derived from a sample of
  real review comments (about 300 comments across 110 PRs in one codebase). The example comments in the lenses are
  paraphrased, not quotes.
- **Not yet validated at scale.** The pipeline was run end to end on one real PR as a smoke test. The planned validation
  is a backtest: run the skill blind on PRs that were not used to write the lenses and compare with what the human
  reviewers actually said.
- Only one project profile has been exercised in real use. `generic`, `clean-code-java` and `onion-layers` are covered by
  unit tests only.
- The lenses were distilled from one codebase's review culture. Expect to tune them for yours: that is why each lens is
  a single file.
- Posting comments is deliberately not implemented. If it is ever added, it must be opt-in per comment.
