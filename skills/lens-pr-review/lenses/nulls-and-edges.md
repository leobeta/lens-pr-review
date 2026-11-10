---
id: nulls-and-edges
summary: What happens with null, empty, zero, negative and boundary values?
may_block: if-verified
---

# Lens: nulls-and-edges

## When to apply
A PR handles input from outside the module: user input, DB rows, external APIs, optional fields, dates and amounts.

## What to do before commenting
1. Pick the realistic odd inputs: null, empty collection or map, zero, negative, very large, first and last day of a period.
2. Follow each one through the new code and see what happens: exception, silent skip, wrong total.
3. Prefer failing loudly at the start of a method over skipping silently when the input should never be missing.
4. Raise it as blocking only if you can name a concrete input that reaches the code and breaks it.

## How to phrase it
Name the input and what you think happens. Ask whether it can occur. Suggest the loud failure or the explicit guard.

## Example comments (synthetic, for tone only)
- Can this map be empty? I would expect that to happen when the customer has no orders. What should we return then?
- A null `errors` argument skips all validation, which feels risky. Could we require it at the start of the method?
- What happens for a negative amount here? I did not see it covered.

## Not a finding when
The caller or a type guarantees the value (non-null annotation, validated at the boundary).
