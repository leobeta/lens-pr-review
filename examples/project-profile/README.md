# Project profile template

Copy this folder to `.lens-pr-review/` at the root of a repository, or to `skills/lens-pr-review/profiles/<name>/` in this
plugin, and edit:

- `profile.json`: `extends`, `detect` (so it is picked automatically), `convention_files` (docs the reviewer must read),
  scopes, risk signals, built-in check options.
- `rules.json`: mechanical rules, only for verified conventions that no build or test already enforces.
- `lens-notes/<lens-id>.md`: facts about this project that a lens needs (where helpers live, how queries are built...).

A repository-local `.lens-pr-review/profile.json` always wins over the built-in profiles.
