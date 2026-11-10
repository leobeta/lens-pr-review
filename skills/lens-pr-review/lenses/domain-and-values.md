---
id: domain-and-values
summary: Do the numbers, units, dates, wording and business rules match what the business expects and what is reported elsewhere?
may_block: if-verified
---

# Lens: domain-and-values

## When to apply
A PR shows or computes money, dates, quantities, rounding, locale or user-facing text, or encodes a business rule (who sees what, and when).

## What to do before commenting
1. Follow each value from storage to display. Check the unit travels with the number: an amount shown without the currency it was stored in is a silent wrong value.
2. Check rounding, decimals and time zones against how the same value is reported or exported elsewhere.
3. Read user-facing text as the end user would. Is the term theirs or ours? Is every language filled?
4. For rules about timing or visibility, find where the rule comes from (ticket, spec, earlier discussion). If you cannot find it, ask where it comes from.
5. Blocking only when you verified a concrete wrong value or unit by reading the code path.

## How to phrase it
For missing context, ask where the rule comes from instead of asserting it is wrong. For a verified wrong value, say exactly which value, where, and why.

## Example comments (synthetic, for tone only)
- Where does the 1 February cut-off come from? I assumed it follows the reporting deadline, is that right?
- The row stores its own currency code but the amounts are formatted without it, so the viewer's currency may be shown. Could we pass the currency along?
- For a customer, would "Number of orders" be clearer than "Transactions"?

## Not a finding when
The rule is documented in the ticket or code and the output matches it.
