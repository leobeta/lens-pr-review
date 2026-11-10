---
id: reuse-existing
summary: Does the repository already have this helper, matcher, formatter, translation key or constant?
may_block: never
---

# Lens: reuse-existing

## When to apply
A PR adds a helper, utility, formatter, converter, matcher, constant, translation key or small private method that looks general.

## What to do before commenting
1. Search for an existing equivalent by behaviour, not only by name: synonyms (format/render/print), the same verb on the same noun, the same library call. Start in the same module, then the whole repository.
2. For user-facing text, search the language/translation resources for the same wording before accepting a new key.
3. For tests, search for existing assertion helpers and matchers before accepting hand-written asserts.
4. Compare what the existing one really does. If it differs in a way that matters, that is the answer: say so instead of asking.
5. Record the symbol and path you found. If you found nothing after a real search, do not comment (or praise the author's choice if they reused something well).

## How to phrase it
Point at the real symbol. Ask whether it fits, do not order the change. One sentence is usually enough. Only write 'don't we already have...?' after you searched and found something.

## Example comments (synthetic, for tone only)
- Don't we already have `TestMatchers#assertSize` for this?
- `CurrencyFormatter#format` seems to cover this. Could we use it here instead of building the string by hand?
- I looked for an existing key for "Payments" and found nothing reusable, so a new one makes sense. Maybe worth making it a common key?

## Not a finding when
The existing helper has different semantics (rounding, null handling, locale) and the author's version is deliberate. Mention the difference only if the author may not know it.
