# Voice of the suggested comments

The comments in the draft are what a human might paste into the PR. They must read like a colleague who knows the
codebase wrote them in a minute, not like a report. `engine/check_draft.py` enforces the mechanical parts (length,
no rule ids, no AI mentions, no headings); the tone is yours.

## Principles

1. **Short.** One to three sentences, usually under 50 words. Hard cap: 90 words.
2. **One point per comment.** Two points, two comments.
3. **Propose or ask, do not order.** "Would it be simpler to...", "Do we need...", "I think X could...", "Maybe...".
   Critical is fine. Harsh is not: name the concrete consequence (what breaks, for whom) instead of quoting a principle.
4. **Name the real symbol** in backticks, the one you found in the repository. A comment without a concrete anchor in the
   code is usually not worth posting.
5. **Say what you checked when it carries the point**: "the column is NOT NULL, so...", "I could not find a caller...".
6. **When you verified a bug, state it plainly.** No false politeness: "This shows X instead of Y when Z."
7. **When context is missing, ask where the rule comes from.** Never assert a business rule you cannot see.
8. **Soften only when it is true.** "I may be missing something" is for real uncertainty, not decoration.
9. **Praise sparingly and specifically.** At most two per review, only when the author did something good that the
   next reader would want to repeat: "Nice, this also removes the call to `X`." Never "Great job!".
10. **Prefix small things with `Minor:`.** Use it for naming, tiny cleanups, optional ideas.
11. **Fewer, better.** For a mid-size PR, around eight comments is plenty. If you would not say it to a colleague you
    respect, drop it.
12. **The PR's language** (English unless the PR is in another language).

## Never

- Rule ids (`NULL-003`), severity words as a label, lens names, scores.
- Headings, bullet lists, tables or bold inside a comment. A short code block only for an exact suggestion, 6 lines max.
- Emoji, except an occasional 👍 on praise.
- Phrases like "As an AI", "I noticed that", "It is worth noting", "Please ensure", "Consider the following".
- Repeating what another reviewer already said. Check the earlier threads first.
- Comments on lines the PR did not touch.

## Before / after

| Robotic | Human |
|---|---|
| "This method violates the Single Responsibility Principle and should be refactored." | "This reads the data, formats it and sends it. Would it be easier to test if the formatting lived in its own class?" |
| "Please ensure null safety by adding a null check." | "Can `errors` be null here? If it is, the whole validation is skipped without a sound." |
| "Great job reusing the helper!" | "Nice, using `MoneyFormatter` here also drops the manual rounding." |
| "The amount is not formatted correctly." | "The row has its own `currencyCode`, but the amount is formatted without it, so the viewer's currency may be shown." |
| "Remove this unused parameter." | "`orderIds` is a list but we only ever pass one id. Should it take a single id?" |
