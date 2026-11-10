---
id: behavior-preservation
summary: Does a refactor or replacement keep everything the old code did?
may_block: if-verified
---

# Lens: behavior-preservation

## When to apply
A PR replaces, moves, simplifies or deletes existing logic, or swaps one API call for another.

## What to do before commenting
1. Read the old version (`git show <base>:<path>`) next to the new one.
2. List what the old code did: permission checks, validations, state checks, default values, ordering, exception behaviour, transaction scope, side effects.
3. Confirm each item still happens in the new code, or was removed on purpose (look for a test or a PR note).
4. Pay extra attention to checks that disappear quietly and exceptions that become silently ignored.

## How to phrase it
State the old behaviour and the new one and ask whether the change is intended. Mark as blocking only when you verified that a permission, validation or data-integrity behaviour is lost and no test covers it.

## Example comments (synthetic, for tone only)
- The old code threw when the order was not approved. Now it just returns. Is that intended?
- I think the permission limit check on the customer admin got lost in this refactor. Could you confirm?
- Should we keep the old behaviour here? Now the error is swallowed.

## Not a finding when
The removal is the stated purpose of the PR, or a test proves the behaviour is preserved.
