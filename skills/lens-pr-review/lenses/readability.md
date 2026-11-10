---
id: readability
summary: Will the next reader understand this: names, comments, TODOs, structure?
may_block: never
---

# Lens: readability

## When to apply
A PR adds names, comments, TODOs, tests names or long methods. Use sparingly: at most two comments of this kind per review.

## What to do before commenting
1. Does the name say what the thing is now (not what it was in an earlier iteration)?
2. Is any comment stale, unexplained or referring to development history ('warm cache', 'old way')?
3. Is a TODO explained, or can it be removed?
4. Would a rename make a boolean read without a negation?

## How to phrase it
Keep it very short and prefix with 'Minor:'. Suggest the better name.

## Example comments (synthetic, for tone only)
- Minor: `triggeredByCustomer` might say more than `flag` here.
- I am not sure what this TODO refers to. Could we explain it or remove it?
- Could `isIosVersionAtLeast` save us the negation at the only call site?

## Not a finding when
The naming follows the surrounding code, even if you would have chosen differently.
