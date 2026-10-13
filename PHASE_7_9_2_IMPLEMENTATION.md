# Phase 7.9.2 - Analyst Workflow

## Scope

Phase 7.9.2 adds analyst-owned workflow state around the canonical radar projection.
`RadarAnnotation` stores `favorite`, `note`, and normalized `tags` by `(chain, token_address)`.
It is an independent table and is never copied into `Token`, smart-money snapshots, risk
snapshots, or radar feature JSON.

## Declarative alert rules

`AlertRule` accepts a list of safe conditions with `all` or `any` matching. The initial
field allow-list is:

- `smart_money_score`
- `smart_wallet_count`
- `risk`
- `liquidity`

Supported operators are `==`, `!=`, `>`, `>=`, `<`, `<=`, and `in`. Unknown fields,
operators, missing values, malformed `in` operands, and invalid channels are rejected at
the API and service boundary. No Python expression, `eval`, or arbitrary code is accepted.

## Notifications and deduplication

`in-app` and `browser` deliveries are persisted in `AlertNotification`; browser delivery
is represented as a durable notification payload and can be connected to the existing
event stream (`ALERT_TRIGGERED`). `webhook` is retained as an adapter seam and is marked
`ADAPTER_REQUIRED` until a transport is configured.

`AlertHistory` hashes the rule-relevant state. Repeated evaluations for the same chain,
token, rule, and state are deduplicated. A changed state creates a new history entry;
the configured cooldown suppresses repeated delivery until it expires.

## API

- `GET/PUT/DELETE /api/radar/annotations/{chain}/{token_address}`
- `GET /api/radar/annotations`
- `POST/PATCH/DELETE /api/radar/alert-rules[/<id>]`
- `POST /api/radar/alert-rules/{id}/evaluate`
- `GET /api/radar/alert-history`

Database migration: `20260912_0018_analyst_workflow.py`.

## Tests

`tests/unit/test_phase7_9_2_analyst_workflow.py` covers rule composition, invalid and
unknown fields, annotation CRUD, and notification deduplication/cooldown.
