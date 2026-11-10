---
id: correctness-and-security
summary: Is there a logic error, resource leak, race, injection or data-exposure problem in what the PR adds?
may_block: if-verified
---

# Lens: correctness-and-security

## When to apply
Always, on every file with behaviour. This is the lens whose findings are most valuable and most often blocking.

## What to do before commenting
1. Read the whole changed file at the head revision, not only the diff hunks.
2. Walk the new logic with a concrete input, including the boundary ones: inverted conditions, off-by-one, wrong comparison, wrong operand, unhandled enum value.
3. Check resources and concurrency: streams and connections closed, shared mutable state, ordering, retries that repeat side effects.
4. Check security: untrusted input reaching queries, paths, templates or expressions; missing permission checks on a new endpoint; secrets or personal data in logs and messages.
5. For each new public method or bean, find a production caller. A method nothing calls is either future work (then ask) or a gap.
6. Confirm by reading or running something (a test, a trace, a query). Write down what you used in the draft as 'Verified by'.

## How to phrase it
When you verified it, say it plainly and specifically: what input, what happens, what should happen. A verified bug does not need to be phrased as a question. When you only suspect it, ask.

## Example comments (synthetic, for tone only)
- This shows the viewer's currency, not the one stored on the row, so a customer paid in a different currency sees the wrong unit.
- If `items` is empty this throws on `get(0)`. Could it be empty when the customer has no orders yet?
- Nothing in `src/main` calls this method. Is the wiring coming in a follow-up?

## Not a finding when
The input you used cannot reach the code (a caller or the type rules it out) and you can say which.
