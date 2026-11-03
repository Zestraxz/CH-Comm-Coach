# GPT additive-only collaboration review — CH-Comm-Coach

Date: 2026-11-03
Repository: Zestraxz/CH-Comm-Coach
Review mode: GitHub tree inventory and selected README / agent contract examination, no runtime execution

## Initial evidence and concern

Public framework repository separates generic coaching kernel and evaluation from a private workspace. Public hooks are intentionally restrictive; even innocuous new documents must be checked against allowlists and privacy guards.

## Prioritized additive enhancements

1. Publish only a generic handshake and review supplement under an allowed docs path.
2. Keep all real profile, counterpart, workplace and conversation details out of public commits, examples and metadata.
3. Propose a regression test that public-facing additions cannot contain private user materials; evaluate hooks before merging.

## Validation for this repository

No private content copied; run public hook allowlist and test-hooks in isolated checkout before merge.

## Collaboration protocol across GPT, Claude and Antigravity

**Absolute additive-only mandate:** Never modify, overwrite, rename, remove, rewrite, revert or reformat existing repository artifacts, source files, configuration or history. Do not mutate `main` directly. Every proposed improvement must have a distinct previously absent path. Existing instructions to update status files, change logs, indexes, coverage maps or agent configuration are **not** authorization for this initiative to edit them. Use a new supplement or log entry instead. The existing domain safety and privacy rules remain in force.

### Immutable GitHub handshake

1. GPT, Claude or Antigravity creates new `docs/ai-cowork/events/<timestamp>-<task>-propose.json` on its own branch, with repository, base SHA, specific source citations, task ID, output paths, acceptance checks, privacy class and owner decision requirements.
2. Another agent creates a **new** ACK file referencing the proposal path and SHA, not editing the proposal.
3. The implementer creates a new CLAIM event. Claims for the same task must not be silently combined; competing claims require owner arbitration.
4. Implementation uses an isolated checkout or branch, with one writing agent per working directory. Source files remain untouched; successors are newly named files and must specify their predecessor paths.
5. A different agent creates a new REVIEW event containing evidence, tests actually executed, risks and approval/rejection. Findings are hypotheses until primary-source verified.
6. Human-approved maintainer can merge a draft PR only after branch freshness and add-only CI checks. No automatic approval, push to default, deployment or destructive operation.

**Event schema example (each file is immutable):**

```json
{
  "schema": "ch-ai-cowork-event/1",
  "task_id": "20261103-example-unique",
  "event_id": "20261103T000000Z-gpt-propose-unique",
  "event_type": "PROPOSE",
  "agent": "gpt",
  "repository": "owner/repo",
  "base_sha": "40-digit git SHA",
  "predecessor_event": null,
  "source_paths": [],
  "new_artifact_paths": [],
  "acceptance_checks": [],
  "evidence_links": [],
  "risk_class": "low|medium|high|restricted",
  "decision": "pending"
}
```

Use sequence references and SHA hashes to establish causal ordering; timestamps alone are not authoritative. Never transmit credentials, financial values, personal conversations or internal corporate information in handshake payloads.

### Strict validation

Compare proposed commits to the latest target branch. All file statuses must be A (new). Any M, D, R, C or T is a hard rejection. Confirm each proposed destination did not exist at the reference SHA, and repeat validation immediately before merge. If the target branch changes, review potential name collision; do not overwrite. Protect active branches with human review and a fail-closed check. Existing build or formatter scripts may rewrite generated files, so only execute after determining they are safe under the add-only mandate.

## Boundaries and next steps

This is a **source-inspected initial repository review and new protocol proposal**, not a completed comprehensive audit, independent multi-agent execution, test run or deploy. All suggested implementation needs a separate new artifact and independent validation. Draft pull request stays unmerged pending review.
