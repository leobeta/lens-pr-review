---
id: placement-and-ownership
summary: Is this code in the class, layer and module that owns this responsibility?
may_block: never
---

# Lens: placement-and-ownership

## When to apply
A PR adds or moves a method into a handler, repository/DB class, controller, DTO, converter or utility, or makes one class reach into another one's storage.

## What to do before commenting
1. Name what the new code does: orchestration, query, conversion/mapping, validation, or business rule.
2. Look at sibling code in the same package to see where that kind of code normally lives.
3. Check whether a class now accesses another component's DB/repository or internals instead of its own.
4. If the author followed an existing local pattern, acknowledge it instead of arguing for a different one.

## How to phrase it
Describe what the code is ('this is pure mapping') and ask whether it would sit better elsewhere. If the code follows what the neighbours do, say that and let it go.

## Example comments (synthetic, for tone only)
- This only converts between two shapes. Would it fit better in the converter than in the handler?
- This handler reads from `InvoiceDao`. The rest of the class only uses its own DB class, so maybe the query could move next to it?
- I see the other date logic lives in handlers too, so I think this is fine here.

## Not a finding when
The surrounding code already puts this kind of logic in the same place and the PR is consistent with it.
