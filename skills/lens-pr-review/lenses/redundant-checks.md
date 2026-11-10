---
id: redundant-checks
summary: Is this check, filter or guard already guaranteed by the caller or the query, or missing where nothing guarantees it?
may_block: never
---

# Lens: redundant-checks

## When to apply
A PR adds a null check, status filter, condition, validation or defensive branch.

## What to do before commenting
1. Trace upstream: who calls this, and which query or earlier check already guarantees the condition?
2. Cite where the guarantee comes from (file and method). If you cannot find one, the guard may be needed.
3. Do the reverse too: a value that upstream does not guarantee and the code assumes.
4. A defensive check is fine when the cost is tiny. Say that it is optional instead of insisting.

## How to phrase it
Show the upstream guarantee and ask whether the check is still needed. Offer a clearer alternative (for example a better method name that makes the guarantee explicit).

## Example comments (synthetic, for tone only)
- The query above already filters on `finished_date`, so I think this null check can never fire. Could we drop it, or rename the query to say it?
- Filtering on status `SUCCESS` looks redundant, `findOrdersByAccountId` already does it. Fine to keep as a safety net if you prefer.
- Can this ever be null here? I could not find a path where it is, but I may be missing one.

## Not a finding when
The guarantee is far away or likely to change, and the check documents an assumption.
