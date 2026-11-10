---
id: test-quality
summary: Do the tests prove the behaviour, including the awkward cases, and would they fail if it broke?
may_block: never
---

# Lens: test-quality

## When to apply
A PR adds or changes behaviour, or adds or changes tests.

## What to do before commenting
1. For each behaviour changed in production code, find the test that asserts it. No test means a question, not an accusation.
2. Ask of each test: if the code were wrong, would this fail? Watch for asserts that only check size or non-null.
3. Look for missing cases: empty, null, negative, boundaries, the failure path.
4. Check the project's own assertion helpers and matchers are used, and that test names say what they prove.
5. Check new classes with behaviour have a test that exercises them (a class with no test at all is worth one comment).

## How to phrase it
Name the missing case concretely and say why it matters. Offer the helper or pattern to use. Keep naming remarks short, prefixed with 'Minor:'.

## Example comments (synthetic, for tone only)
- Would a test with an empty map be relevant? I think that can happen in production.
- These asserts could use the project's `containsInAnyOrder` matcher, which also makes more cases easy to add.
- Minor: the test name says `null` but the case passes an empty list.

## Not a finding when
The behaviour is covered by an existing test you can name, or the code is a plain data holder.
