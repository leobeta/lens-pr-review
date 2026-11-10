---
id: operations-and-rollout
summary: Will this survive deployment: rolling releases, serialized state, migrations, background jobs, merge timing?
may_block: if-verified
---

# Lens: operations-and-rollout

## When to apply
A PR touches serialized classes, forms or session state, migrations or SQL scripts, background or periodic jobs, scheduled dates, public APIs or events, configuration, or data cleanup.

## What to do before commenting
1. Serialized or cached shapes: does a field change need a version bump or a compatibility step? Check the repository docs for pinned classes.
2. Migrations and scripts: do they depend on another change being released first? Are they safe to run twice?
3. Background jobs: can two nodes run it at once? Is the concurrency or lock level set on purpose?
4. Scheduled or dated values (for example a patch date): is the date valid for the intended release?
5. Contracts: would an older client or consumer break? Are all consumers updated?
6. When the answer depends on release plans you cannot see, ask.

## How to phrase it
Ask about timing and compatibility plainly. Mention the concrete failure you are worried about. Only mark blocking when a documented pin or an obvious break is verified.

## Example comments (synthetic, for tone only)
- This script needs the enum value to be released first. Should the merge wait for that?
- Should this job be limited to one node? I think running it in parallel could process the same rows twice.
- Do we need to bump the serialVersionUID of the form here?

## Not a finding when
The change is additive and backward compatible, and the docs say no extra step is needed.
