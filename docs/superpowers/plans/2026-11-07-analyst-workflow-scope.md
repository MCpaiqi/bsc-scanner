# Reliable analyst workflow implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline, with one fresh read-only review at the end.

**Goal:** R10: annotations and token/global alerts have explicit scope and visible save results, without silent draft loss or scope expansion.

**Spec:** docs/RESEARCH_RELIABILITY.md R10 under the original BSC research-desk goal. Preserve all other R1–R14 requirements, local data and user-authorized verified GitHub pushes.

**Architecture:** Add nullable alert target/request identity and annotation revision in migration0045. Alert scope derives from the persisted target (GLOBAL if absent, TOKEN if present), with validation at API/domain seams and scope guards before any evaluation/history. Conditional annotation writes use expected_revision (0=create, >=1=compare-and-swap), rejecting missing preconditions with428 and conflicts with409. A focused AnalystWorkflow component owns annotation load/save and alert creation, remains mounted independently of research refresh, and keeps draft identity, pending submissions and visible outcomes separate.

**Approaches:** Encoding target in a rule name is not enforceable. Adding token-address conditions to the generic financial condition language mixes subject selection with metric semantics. Chosen: a persisted optional target and explicit scope in public payloads. Last-write-wins annotation writes can erase another browser's work; chosen: revisions with explicit conflict recovery. Background autosave/favorite writes can race note saves; chosen: one explicit save for the full annotation draft.

**Ruling:** The existing autonomous continuation and acceptance ledger authorize the design; keep the existing branch/workspace and implement without another approval round. New API annotation writes require expected_revision; legacy readers remain supported and existing annotations migrate to revision1. Existing alert rules remain GLOBAL: migration must not infer targets from names or silently broaden/downgrade token rules. Downgrade must refuse loss of scoped/idempotent rule metadata or used annotation revision history. Research-refresh failures must not destroy manual workflow drafts. Alert retries reuse a durable request identity; new rule definitions get distinct request identities; revisiting a cached definition reuses its identity. Existing legacy domain global rules with chain=None remain readable; new TOKEN targets require a valid BSC identity. No live providers, deployment, authentication changes or production data migration are performed here.

**Files:** src/smms/db/models/analyst.py and migrations/versions/20261107_0045_analyst_scope.py own persistence; src/smms/radar/alerts.py and new src/smms/radar/annotations.py own scope/idempotency/conditional writes; src/smms/api/analyst.py owns validated public payloads/errors; web/src/components/AnalystWorkflow.tsx owns workflow state/UI; TokenDetail only supplies identity and keeps the workflow mounted; web/src/api/client.ts owns typed write/error transport. Unit, HTTP, PostgreSQL concurrency and migration tests prove the seams.

## Review focus

- Token rules cannot fire for another token or chain through automatic or explicit evaluation, including normalized addresses, updates and legacy global rules.
- Failed/unknown annotation reads cannot become empty editable state; stale/concurrent creates/updates/deletes cannot silently overwrite saved data.
- Research recovery, rapid token switches, unmounts and late write responses cannot discard drafts or show another token's success.
- Changes typed while a save is pending remain unsaved; duplicate clicks/timeout retries cannot create duplicate alert rules; scope success matches the submitted snapshot.
- Migration preserves old annotations/global rules, uniquely fences request identities and refuses metadata-losing downgrade; real PostgreSQL concurrent writes produce one winner without unrelated rollback.

## Tasks

- [x] Add RED domain/HTTP tests for TOKEN/GLOBAL scope, contradictory/invalid targets, conditional annotation writes and idempotent alert retries.
- [x] Implement model/migration0045, scope validation/evaluation, guarded annotation transactions and API errors; verify SQLite and real PostgreSQL/migration/concurrency.
- [x] Add RED frontend tests for failed loads/save/conflict feedback, full-draft save, in-flight edits, token switches, strict research recovery and token/global alert creation/retry.
- [x] Extract AnalystWorkflow, add typed abortable writes and explicit scope/pending/success/error UI while preserving focused drafts and current research behavior.
- [x] Run full backend/frontend, PostgreSQL/migrations, build/Ruff/whitespace; one fresh review and one RED→GREEN fix pass; update ledger, scoped commit and push the verified batch.


Ruling: retain revision-only annotation tombstones on delete, clearing manual content, so re-creation cannot reuse an old editor revision (ABA). Alert deletion disables/hides a tombstone and retains request identity/history, so a delayed creation retry cannot resurrect a deleted rule. Downgrade refuses these markers. The cost is retaining small identity/revision metadata after user-visible deletion.
Ruling: drafts persist across research refresh/recovery within the active token workflow; explicit closing or changing token discards that token's unsaved local editor. Requests are aborted/ignored on identity switch and cannot overwrite the new token's draft. This is not a cross-tab/offline draft store; the visible unsaved indicator and explicit save define persistence.


## Final review and fix pass, 2026-11-09

The initial review was interrupted by a usage limit; the same reviewer resumed and delivered two Important findings, no Critical/minor findings. Actual HTTP regression reproduced PATCH turning a TOKEN rule into GLOBAL when target fields were omitted. A React regression reproduced a failed explicit conflict-recovery read losing its preservation mode when the local draft equalled its old baseline. Both are fixed RED→GREEN: PATCH uses a partial payload/merged validation and preserves omitted targets, while recovery intent survives failed reads independently of dirty state. A further RED closure/reopen regression now requires tab-local retry identity reuse, also verified across module reload and parameter revisits.

SQLite full-suite checks reproduced unsupported direct ALTER constraints in migration0045; batch-alter upgrade/downgrade now passes the two original migration checks. A raw-SQL legacy insert regression additionally reproduced a quoted false default hiding annotations on SQLite; schema defaults now use the native SQL boolean expression.

Final: Ruling: expected_revision=0 means currently absent, including a tombstone — it can create after an intervening create/delete cycle but cannot overwrite a currently present annotation; positive revisions never repeat after deletion — stronger empty-read epoch tokens are not claimed.
Final: Ruling: browser retry identities cover the recent256 rule definitions in the current tab, including closure/reopen, revisiting parameters and reload when session storage works — bounded storage avoids unbounded long-running client growth — eviction/tab closure or disabled-storage reload can end client retry continuity. Server UUID identities remain durable.
Final: Ruling: legacy POST callers may omit request_id — preserves existing client semantics while the browser supplies one — those callers must add a UUID for retry deduplication.
Final: Ruling: an aborted write may already have committed — the UI ignores late responses and reads current state on reopen; annotation revisions or retained alert UUIDs resolve retries — abort is not an undo operation.
Final: Ruling: alert PATCH edits have ordinary per-field last-writer behavior, while annotation writes require revisions — no automatic scope change occurs on omitted fields — simultaneous edits to the same explicit alert field can still overwrite each other.
Final: Ruling: preserve legacy GLOBAL chain=None rules, do not infer targets from names, retain rule deletion/history markers and refuse metadata-losing downgrades — keeps historical meaning/audit evidence — operators must explicitly narrow old globally scoped rules.
Final: Ruling: full-draft save replaces racing favorite/autosave writes; additional rule-management UI, authentication, deployment and R3/R11–R14 remain outside this batch — these are not declared complete — the original goal stays active.
No deferred minor findings. Explicit close/token-switch discarding unsaved local drafts remains the earlier recorded ruling; this batch does not provide a cross-tab/offline draft store.

A valid checksum-cased BSC contract RED regression showed the UI could reject a successful canonical lowercase target response. AnalystWorkflow now canonicalizes its BSC identity before annotation/alert requests, cache signatures and response validation. This does not change server scope semantics.


Final verification: backend781 passed/39 optional skips/13 existing warnings; frontend76 passed with TypeScript/production build; PostgreSQL66 passed including migrations0045/0044 and independent concurrency; full Ruff/whitespace passed. Actual-router browser QA in an owned schema demonstrated conflict preservation and revision3 save, with exactly two TOKEN/GLOBAL rules persisted. All owned verification schemas/connections were absent before the task-owned foreground PostgreSQL was stopped. Screenshot/log/evidence files remain outside Git. Publish only this scoped verified batch to the already authorized branch; the overall goal remains active for R3 and R11–R14.
