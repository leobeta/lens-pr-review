---
id: design-principles
summary: Opt-in lens for teams that adopt SOLID, DDD and layered architecture: are responsibilities and dependencies sound?
may_block: never
---

# Lens: design-principles

## When to apply
Enabled by profiles that extend clean-code-java or onion-layers. A PR adds a class with several reasons to change, depends on concrete collaborators, mixes layers, or models business rules as data bags.

## What to do before commenting
1. Single responsibility: does the class mix orchestration, mapping, validation and I/O?
2. Dependencies: are collaborators injected through abstractions the repository's docs prefer, or created inside?
3. Layers and contexts: does code reach into another context's internals or a lower layer's details?
4. Rules: do business rules live on domain objects or services, or are domain types only bags of fields?
5. Null safety: do new public APIs return nulls or take Optionals where the team forbids it?
6. Check the repository's own docs first. They override this list.

## How to phrase it
Frame as a question about change: 'what happens when we add the next variant?' Never quote a principle name as the argument.

## Example comments (synthetic, for tone only)
- If we add another report type, we would edit this class again. Would a small strategy per type help?
- This service decides eligibility and also sends the email. Would it be easier to test if eligibility lived in its own class?

## Not a finding when
The repository's docs explicitly choose the opposite (for example depending on the impl inside its own module).
