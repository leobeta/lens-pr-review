# Human review map: risk and verification evidence

Every changed file has two separate measures. **Inherent risk** describes what could go wrong; **verification
confidence** describes the evidence gathered during this review. Both are deterministic rubrics, not calibrated
probabilities. Only files with verification confidence **at or below 0.75** are highlighted. All others retain their
risk and evidence in the collapsed skip list.

## Inputs and revision binding

- `scan.json`: changed files, tiers, reasons, ranges and `revision` from `scan_pr.py`. A committed scan records the
  resolved commit SHA. A worktree scan records HEAD plus a hash of the tracked diff and untracked contents, including
  binary changes. Keep review output in the git-ignored `.pr-review/lens/` directory so it does not change the snapshot.
- `map.json` **schemaVersion 2**: a review record for each file, plus the exact `revision` copied from the scan.
- `summary.json`: confirmed findings in self-review mode; its schema remains unchanged. Findings must describe the
  same snapshot. PR mode records confirmed finding signals directly in `map.json`.

Old scans without `revision` and schema 1 maps are rejected: rescan and recreate the evidence records.
Every check records its own revision. Evidence for another revision cannot raise confidence. Rescan after edits,
commits or a changed PR head, and rerun affected checks; never relabel an old result as a new execution.

## Inherent risk

`inherentRisk = 1 - product(1 - penalty)` for inherent signals, rounded to two decimals. Risk is **high** at 0.25 or
more, **medium** above zero, otherwise **low**. Passing tests never removes a real risk signal.

All signals below describe inherent risk except:

- `open-blocker`, `open-major`, `open-minor`, `disputed`: unresolved findings lower verification confidence.
- `intent-unclear`, `unverified`, `untested-change`: unresolved uncertainty caps verification confidence at 0.75.

The agent records facts and evidence; the script computes the scores.

| Signal            | Penalty | Source             | Add it when                                                                                     |
|-------------------|---------|--------------------|-------------------------------------------------------------------------------------------------|
| `open-blocker`    | 0.50    | summary.json / you | A confirmed blocker is still open in the file                                                   |
| `disputed`        | 0.35    | summary.json / you | A finding in the file is disputed and needs a human decision                                    |
| `open-major`      | 0.30    | summary.json / you | A confirmed major is still open in the file                                                     |
| `security`        | 0.30    | Scanner / you      | Sandbox or allow-list, auth, secrets, file-name handling, size limits                           |
| `guard-rail`      | 0.30    | Scanner / you      | A confinement, architecture, security or escape test was changed or removed, or a test disabled |
| `contract`        | 0.25    | Scanner / you      | OpenAPI or AsyncAPI, events, downstream contracts, topic names                                  |
| `concurrency`     | 0.25    | Scanner / you      | Threads, async, locks, shared mutable state                                                     |
| `business-rule`   | 0.20    | Scanner / you      | A new or changed business rule (rounding, date parsing, eligibility, validation)                |
| `messaging`       | 0.20    | Scanner / you      | Ack or nack, retries, DLQ, idempotency, error events                                            |
| `persistence`     | 0.20    | Scanner / you      | Transactions, queries, indexes, writes that could be partial or lost                            |
| `prod-deploy`     | 0.20    | Scanner / you      | helm-prod, Dockerfile, workflows, prod configuration                                            |
| `intent-unclear`  | 0.20    | You                | You're not sure the behaviour is what the ticket or the PR description asks for              |
| `design-decision` | 0.15    | You                | A new abstraction or module, or an exception to a rule, requiring design scrutiny               |
| `unverified`      | 0.15    | You                | Behaviour you couldn't check: library or external API behaviour, runtime config, tests not run  |
| `dependencies`    | 0.12    | Scanner / you      | A new or upgraded dependency                                                                    |

**Soft signals**: smaller inherent concerns, except `open-minor` and `untested-change` which affect verification.

| Signal            | Penalty | Source             | Add it when                                                           |
|-------------------|---------|--------------------|-----------------------------------------------------------------------|
| `untested-change` | 0.08    | You                | Behaviour changed, but no test asserts the new behaviour              |
| `data-semantics`  | 0.08    | Scanner / you      | Dates, time zones, rounding, charsets, locales                        |
| `complex-logic`   | 0.06    | You                | Dense branching or stream logic that's easy to get subtly wrong       |
| `error-handling`  | 0.06    | Scanner / you      | New catch, throw or retry paths                                       |
| `large-change`    | 0.06    | Scanner            | More than 150 added lines in a production file                        |
| `open-minor`      | 0.06    | summary.json / you | A confirmed minor is still open in the file                           |

## Verification confidence

A completed review needs nonempty `verification.read` and `verification.context` descriptions. Record the full file
and diff inspected, relevant callers/consumers and the acceptance criteria checked. LOW files need a sanity pass;
for scanner SKIP files, inspect and explain why generated/vendor content is appropriate. Missing records or either
review description mean **unreviewed**, confidence **0.00**, even when the scanner finds no risk. The map automatically
supplies a review-completion question for these files.

Completed reviews start at **0.95**, never 1.00. Apply these rules:

| Evidence gap                                                                          | Confidence ceiling |
|---------------------------------------------------------------------------------------|--------------------|
| Required check missing, not applicable to an active risk, or tied to another revision | 0.75               |
| Any recorded check skipped, including unavailable Docker/Testcontainers               | 0.65               |
| Any recorded check failed                                                             | 0.50               |
| `intent-unclear`, `unverified`, or `untested-change` remains active                   | 0.75               |

Then multiply by each unresolved finding's `(1 - penalty)`. Open blockers, majors and disputes therefore stay in the
focus list even with passing tests. A later passing check does not erase a recorded failed or skipped check. If an
issue is resolved and checks are rerun, update the current record truthfully and retain prior rounds as history.

### Required checks

| Files / active signals                                                                  | Check kind      | Evidence to record                                                                 |
|-----------------------------------------------------------------------------------------|-----------------|------------------------------------------------------------------------------------|
| All HIGH/MEDIUM files                                                                   | `behavior`      | Changed behavior → test/assertion → command or CI job and result artifact          |
| `business-rule`, `data-semantics`, `complex-logic`, `error-handling`, `untested-change` | `behavior`      | Boundary and negative cases for the changed rule                                   |
| `security`, `guard-rail`                                                                | `boundary`      | Rejection, confinement or escape assertions and results                            |
| `contract`                                                                              | `contract`      | Producer → schema/topic → consumer compatibility checks and results                |
| `concurrency`                                                                           | `concurrency`   | Race, ordering or shared-state checks and results                                  |
| `messaging`, `persistence`                                                              | `integration`   | Duplicates, retries, acknowledgement timing, partial failure or transaction checks |
| `prod-deploy`, `dependencies`                                                           | `configuration` | Relevant configuration/build compatibility checks and results                      |

Checks have `kind`, `status` (`passed`, `failed`, `skipped`, `not-applicable`), `evidence`, and `revision`. Each check
must have a concrete explanation or artifact reference; bare “CI green” is insufficient. For passing checks, inspect
the assertions: would the test fail if the behavior were wrong? A report from another module is not evidence for this
file. A check can cover several files when it actually exercises each one.

`behavior: not-applicable` is allowed with an explanation only when no active signal requires a behavior check (for
example, a documentation-only change). Refactors still need evidence of preserved behavior. Active boundary,
contract, concurrency, integration and configuration requirements cannot be waived: clear an actual scanner false
positive with a reason, or record the missing verification honestly.

For sensitive changes or unresolved disputes, use an independent second review when available. Give it the code,
requirements and checks before the first review's conclusions; record its scope, findings and resolution as an
`independent-review` check. Agreement alone adds no points and cannot substitute for required checks.

These records make claims auditable; the script validates their structure and consistency, not whether a cited test
really ran. Inspect reports and command results before recording evidence.

## map.json

```json
{
  "schemaVersion": 2,
  "revision": "<exact scan.json revision>",
  "files": [
    {
      "path": "payments/src/main/java/…/AmountRounding.java",
      "signals": ["business-rule"],
      "lines": ["L40-L72"],
      "risk": "Negative amounts might round incorrectly",
      "verify": "Should -0.5 round to -1 for payroll exports?",
      "verification": {
        "read": "Read AmountRounding.java and its full diff",
        "context": "Checked PayrollExport caller and DI task's half-up rounding requirement",
        "checks": [
          {
            "kind": "behavior",
            "status": "passed",
            "revision": "<exact scan.json revision>",
            "evidence": "AmountRoundingTest#negativeHalf asserts -0.5 becomes -1; mvn test passed; payments/target/surefire-reports/TEST-AmountRoundingTest.xml"
          }
        ]
      }
    }
  ]
}
```

This file has inherent risk 0.20 and verification confidence 0.95. Without the behavior check it scores 0.75 and is
highlighted. Missing review evidence scores 0.00. Add a `risk` and concrete `verify` question for every reviewed file
at or below the threshold; otherwise the script exits with 2. Duplicate file records and malformed evidence are
rejected.

`lines` optionally replaces scanner ranges to narrow what a human should read. Scanner and summary signals are added
automatically. Only clear scanner false positives, never confirmed summary findings:

```json
{"signal": "security", "reason": "Exception message only; no secret value is handled"}
```

Place these objects in the file's `cleared` list. Cleared signals remain visible in the report.

## Render and audit

```bash
python3 <skill-dir>/engine/build_review_map.py --scan <dir>/scan.json --map <dir>/map.json \
  [--summary <dir>/summary.json] --links <pr|blob|local|absolute> [--pr <N>] [--ref <sha>] --output <dir>/map.md
```

Use `--links pr --pr <N>` for GitHub reviews, `--links blob --ref <reviewed sha>` for PR descriptions, `--links local`
for saved reports and `--links absolute` for chat. Blob output requires the exact scanned commit SHA; dirty worktree
reports must be regenerated after committing. JSON output schema 2 includes `revision`, `inherentRisk`,
`confidence`, `reviewStatus`, `verification`, `gaps`, and `needsHuman` for each file.

The report shows totals and the reviewed snapshot, followed by every focus file with risk, confidence, review status,
Risk/Why/Verify, read/context evidence, checks and gaps. The collapsed skip list preserves evidence too, so a high-risk
file with strong verification remains auditable. Files are ordered by increasing confidence, with no item limit.
Explicit `--threshold` overrides remain inclusive; overrides above 0.75 add a **Should read** band.

If many files need a human, prefer cohesive changes or review by module/commit. When a human discovers a missed issue
in a file above the threshold, record the file, revision, missing assertion/context and eventual regression test in
`.pr-review/lens/` or the issue. Use these observations to refine check requirements; do not raise scores merely to shorten
the map.
