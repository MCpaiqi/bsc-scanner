# Complete candidate browser implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline and one fresh read-only review at the end.

**Goal:** R9 covers the complete current candidate universe with server-side filtering, stable sorting and pagination, while selected research/statistics refresh and unavailable/error/stale states remain explicit.

**Spec:** docs/RESEARCH_RELIABILITY.md R9. Preserve BSC/PAPER scope, canonical visibility, original R1–R14 objective and verified GitHub pushes.

**Architecture:** A normalized SQL candidate index joins current projection/signal identities and canonical pair context. Shared SQL predicates run before page selection; one statement returns page entities and global filtered/tab counts, including an empty page. Hydrate evidence only for the selected page. Serialization uses those selected immutable revisions and one cutoff; an epoch/revision fence rejects canonical corrections during the read. The browser sends query/view/filter/offset to the server and renders its order/counts, coalesces polling/SSE refresh, refreshes selected research without resetting drafts, and polls model statistics with explicit loading/empty/error/stale states.

**Approaches:** Downloading every page to filter locally retains a growing browser workload and mixes request generations. Hydrating every candidate before slicing is complete but makes each GET scale as many per-candidate queries. Chosen: filter/sort/count in the database and hydrate a bounded page.

**Rulings:** Existing R9 scope and autonomous continuation authorize this design. Pagination is a live current-state view, not a frozen historical dataset: updates can reorder later requests. Stable ties and one-statement counts/page guarantee consistent static results within a read; historical reproducibility remains the immutable dataset/audit workflow. Ordinary newer snapshots do not invalidate a previously valid read; canonical invalidation/supersession does. The discovery tab becomes all canonical discoveries, including stale observations, with an explicit freshness filter; rejected facts stay outside the default research universe. Watch includes unscored non-high-risk observations explicitly, without treating unknown scores as measured zero.

**Tech stack:** SQLAlchemy/PostgreSQL/SQLite, FastAPI, React/TypeScript/Vitest; no provider calls or schema migration.

## Review focus

- Search, filters, facets and all four sort keys must cover records beyond the old500 cap and precede pagination.
- Zero, missing, scientific notation and nonfinite numeric inputs must preserve known/unknown meaning; unknowns sort last in either direction.
- Current projection/signal/pair fields must match the selection snapshot; canonical invalidation during hydration must fail closed, without locking writers.
- Query changes must discard previous pages/requests; shrinking result sets must recover from out-of-range offsets without looping.
- Poll/SSE/statistics/detail refresh must not overwrite annotation drafts or show an old response as current; errors are distinct from empty and unavailable.
- Database selection must not hydrate the entire universe or fetch full input manifests for every candidate.

## Tasks

- [x] Add RED SQL/API regressions for global search/page/counts, age ordering, numeric unknowns/zero, a frozen cutoff and concurrent canonical correction.
- [x] Implement src/smms/radar/query.py for normalized selection, shared predicates, stable sort/ties, one-statement facets/page and bounded hydration; adapt views/API with epoch/revision validation.
- [x] Add frontend RED tests for server query/pagination, stale request rejection, refresh/out-of-range behavior, stats/error states and periodic selected-detail refresh.
- [x] Implement server-driven filters/pager/counts and coalesced continuous research/statistics refresh using existing UI patterns and draft protection.
- [x] Verify full backend/frontend, meaningful PostgreSQL/concurrency and large-universe checks, typecheck/build/Ruff/whitespace, one review/fix pass; update ledger, scoped commit and push.


## Query and refresh contract

- GET /api/radar/candidates supports q (literal case-insensitive substring over token identity, pair and platform), view=all|high_signal|watch|high_risk, freshness=all|fresh|stale|unknown, risk, exact platform, maximum age in seconds and minimum priority/smart_money/liquidity/volume. All predicates precede sorting/paging. An omitted minimum retains unknown values; an explicit zero excludes them. priority and min_priority combine by the stronger threshold.
- sort=priority|age|liquidity|volume, direction=asc|desc; age means discovery time and volume means 5m volume, using legacy volume only if the 5m key is absent. Known zero remains zero. Unknown metrics sort last in either direction; chain/address/ID resolve ties. Decimal/scientific JSON values use finite IEEE-754 browser range, up to80 input characters and three exponent digits. Overflow, underflow and nonfinite values stay UNKNOWN.
- total counts the active view after common filters. tab_counts counts every view under those same common filters. universe_total counts candidate subjects before filtering; it can include rejected/awaiting-rebuild subjects. Counts and page share one SQL statement/cutoff; empty and out-of-range pages retain counts. Evidence expansion and manifest digest reads are page bounded.
- Browser pages contain50 rows, with no complete-universe download/cap. A query change resets the page, aborts old requests and discards their responses. An out-of-range live page clamps to the last surviving page; live updates may reorder later requests.
- Candidates, selected research and forward statistics reconcile every15s without overlapping requests in the same generation. Stream recovery additionally invalidates old candidate/detail responses. Research polling preserves annotations and focused drafts; failed background reads explicitly mark retained research stale. Strict recovery failures and HTTP409 hide invalidated research.
- Forward statistics are usable only with data_status=CURRENT. REBUILDING/unknown/unavailable has no measured cohort count; genuine current sample_count=0 remains zero. A read timestamp is labelled as a read, separately from observation time.

## Final review and decisions

One fresh read-only reviewer found three Important issues, no Critical/minor issues. RED regressions reproduced small-number rounding / three-digit scientific exponents, explicit zero priority minima retaining unknown scores, and REBUILDING statistics displayed as current zero. The single fix pass changes numeric result conversion/range handling, distinguishes omitted thresholds from zero, and exposes withheld statistics without a fabricated zero denominator. Targeted tests are GREEN; final full verification is recorded in the programme ledger.

Final: Ruling: live pagination can reorder across requests — this is a current observation browser; each query is internally consistent and static ties are stable — users needing a frozen cohort must use immutable research datasets.
Final: Ruling: a newer ordinary valid snapshot may leave a selected earlier valid revision readable — canonical epoch changes and selected-revision invalidation/supersession still reject the read — the UI can be one polling interval behind an ordinary update.
Final: Ruling: discovery includes stale observations with explicit filters, rejected rows are excluded by default and Watch includes unscored non-high-risk subjects — missing research stays visible without scoring it as zero — view counts can overlap and Watch does not imply a completed score.
Final: Ruling: missing volume_5m can use legacy volume; explicit null/unknown/zero cannot — preserves legacy availability without inventing a 5m measurement — legacy generic volume can reflect its original provider window.
Final: Ruling: bounded per-page evidence/config reads remain, broader query latency belongs to R11 — hydration does not scale with the whole universe — large pages or dense evidence still require latency work.
Final: Ruling: annotation save/alert scopes remain R10, and unrelated programme/deployment/provider work remains open — this batch only certifies R9 — the original overall goal is not complete.
No deferred minor findings. Query cleanup, shrinking offsets and annotation draft preservation were reviewed without additional actionable findings.


Final verification: backend768 passed/35 optional skips; actual PostgreSQL68 passed; frontend62 passed; TypeScript/production build, full Ruff and whitespace checks passed. Synthetic browser QA verified second-page access, subject505 global search/page reset and390px cards. All three Important review findings are fixed by RED→GREEN tests. Temporary verification services were stopped after confirming no owned schemas/other connections. Publish this scoped, verified batch to the user-authorized existing branch; R3 and R10–R14 remain open.
