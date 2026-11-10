# Draft template

The skill writes one file: `.pr-review/lens/pr-<N>/draft.md` (or `.pr-review/lens/<branch>/draft.md` in self-review mode).
It never writes to GitHub. The human reads it, decides, and posts only the comments they agree with.
`engine/check_draft.py` validates the structure, so keep the field names exactly as below.

````markdown
# PR review draft: <title> (#<N>)

- **Head:** `<full sha>`
- **Base:** `<base ref>`
- **Profile:** `<profile name>`
- **CI:** <passing | N failing | N pending | not checked>
- **Suggested verdict:** COMMENT | REQUEST_CHANGES

> Suggestion only. Nothing here has been posted. Tick the comments you want to use.

## In plain words
Two to four sentences a non-developer could follow: what the PR does and what, if anything, worries you most.

## Where to look
<output of build_review_map.py, verbatim>

## Suggested comments

### C1 · `path/to/File.java:71` · domain-and-values · blocking-suggested
- **Evidence:** What you saw, with file references. Short.
- **Verified by:** What you ran or traced (a command, a test, a read of the old code). Required for blocking-suggested.
- **Link:** https://github.com/<owner>/<repo>/blob/<sha>/path/to/File.java#L71   (optional)
- **Comment:**
> The text a human could paste. See references/comment-voice.md.
- [ ] Post this comment

### C2 · `path/to/Other.java` · reuse-existing · discuss
...

## Diagrams
Only when a diagram says something the text cannot. See references/mermaid.md. Omit this section otherwise.

## Earlier discussion
Only when the PR has earlier review threads. One row per thread that `threads.json` lists in `needing_status`:

| Thread | Request | Status | Evidence |
|---|---|---|---|
| `4442421479` | Populate batches in the main query | Addressed | `getBatches` removed; `getList` now selects the batch (commit abc1234) |

- **Thread**: the thread id from `threads.json`, in backticks. `check_draft.py --threads` fails when a thread has no row.
- **Status**: Addressed, Partially addressed, Not addressed, No longer applicable, Deferred or Unclear.
- **Evidence**: at least a few words. Addressed and Partially addressed must cite the fix (a `symbol` or a commit sha).
  The author's reply alone is not evidence; read the delta.
- If the fix introduced a new problem, say so here and raise it as its own comment on the new line.

## Dismissed scanner hits
| Rule | Location | Reason |
|---|---|---|

## Not verified
What you could not check (tests not run, schema not visible, UI not rendered, context you did not have).
````

## Fields

- **Anchor** (in the heading): `path:line` where the line is in the **new** file at the head revision. A file-level
  comment may omit the line.
- **Lens**: an id from `lenses/` (or `scanner:<RULE-ID>` for a scanner hit you confirmed).
- **Weight**:
  - `blocking-suggested`: you believe this should stop the merge until addressed. Requires **Verified by**, and the lens
    must allow it (`may_block: if-verified`) or be a scanner rule. A human still decides.
  - `discuss`: worth a conversation; the author may have context you lack.
  - `minor`: small, optional.
  - `praise`: something good worth saying. At most two.
- **Suggested verdict**: `REQUEST_CHANGES` if any comment is `blocking-suggested`, else `COMMENT`. Never `APPROVE`:
  approval is a human decision made after reading the review.
