---
id: minimal-surface
summary: Is everything added needed: methods, parameters, fields, setters, delegates, unrelated edits?
may_block: never
---

# Lens: minimal-surface

## When to apply
A PR adds public methods, parameters, fields, delegates or abstractions, or touches code unrelated to its goal.

## What to do before commenting
1. For each new public member, grep for production callers. No caller or a single caller means it may not need to be public or separate.
2. For each new parameter, check it is actually used.
3. Look for commented-out code, leftover TODOs, setters for values that are now derived, and edits that have nothing to do with the PR.
4. Check whether an existing method could take the new case with a small change instead of a parallel one.

## How to phrase it
Ask what the extra thing is for, then say what you think is simpler. If you checked and it is clearly unused, say that you checked.

## Example comments (synthetic, for tone only)
- Do we need a separate method to fetch the batches? Maybe the existing query can return them directly.
- `createCreditNote` takes a list of order ids but only ever receives one. Should it take a single id?
- This change looks unrelated to the PR. Should we revert it or move it to its own commit?

## Not a finding when
The extra member is part of an intended extension point that is documented or has a second caller in the same PR.
