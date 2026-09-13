# ocr — the OCR engine service

Kontrak B (`document.v1`) engine: receives documents from `ocr-gateway`, runs
the extraction pipeline on a SAQ worker, publishes `document.processed.v1` to
Kafka through its own transactional outbox. Contract source of truth:
`docs/reference/api-ocr.md`; Baidu adapter reference (observed wire shape):
`docs/reference/api-ocr-baidu.md`; provider/mode configuration rationale:
`docs/explanation/ocr/provider-config.md`.

```
expense → ocr-gateway (Kontrak A) → ocr (Kontrak B, this repo) → Kafka
                                        ↑ worker: download → inspect → OCR → extract → validate → outbox
```

## Three processes, one codebase

| Process | Entrypoint | Role |
|---|---|---|
| `api` | `ocr-api` (port `OCR_GRPC_PORT`, default 9094) | gRPC `SubmitDocument`/`GetDocument`, validation, idempotency |
| `worker` | `ocr-worker` | SAQ pipeline jobs + result-retention cron |
| `outbox` | `ocr-outbox` | poll `outbox_events` → Kafka `ocr.document.processed.v1` |

They deploy separately on purpose: "your job is queued" must stay true when
the heavy OCR workers are down.

## Quick start (local)

```bash
# 1. database + roles: add the `ocr` database once (needs the postgres superuser)
psql -U postgres -h localhost -f ../scripts/init-db.sql        # workspace-level script

# 2. config
cp .env.example .env                                           # fill OCR_BAIDU_TOKEN etc.

# 3. install + generate + migrate
make sync generate
make migrate-up                                                # alembic upgrade head

# 4. run
make run-api & make run-worker & make run-outbox

# 5. tests (unit suite never calls Baidu; adapter tests use recorded responses)
make test
```

The Kafka topic `ocr.document.processed.v1` must exist
(`infra/messaging/scripts/create-topic.sh`); Redis needs AOF persistence in
production — SAQ job state lives there.

## Layout

```
src/ocr_engine/
├── api/          # gRPC servicer (validation order = contract), server + health
├── worker/       # SAQ settings: concurrency, 3 tries, 300s timeout, cleanup cron
├── pipeline/     # download (allowlist, streaming) → magic bytes → digital-born → OCR
├── ocr/          # OcrProvider: BaiduProvider (sync|async) - the only Baidu-aware code
├── extraction/   # YAML schema loader, label/regex/positional matchers, money parser
├── events/       # document.processed envelope + outbox→Kafka publisher
├── repo/         # asyncpg store: documents + outbox, finish = one transaction
├── obs/          # structlog JSON + OTLP push
├── settings.py   # pydantic-settings, fail-fast (bad combos never boot)
└── cli/          # the three entrypoints
proto/document/v1/document.proto   # Kontrak B source of truth (Go gen still in platform/)
migrations/                        # alembic: extensions, documents, outbox_events
schemas/receipt.yaml               # extraction schema (schema_id "receipt@1")
tests/unit|golden|integration      # unit: recorded-Baidu fixtures; integration: live PG
```

Dependency direction is one-way: `api/worker → pipeline → ocr/extraction → repo`.
Ruff + mypy strict; `make lint typecheck`.

## Pipeline stages

1. **Download** — S3 via read-only credentials, bucket allowlist + path check
   *before* any request; streaming with `OCR_MAX_FILE_BYTES` cap. No URL
   fetching anywhere (no SSRF surface).
2. **Inspect** — magic bytes decide the type (declared_mime is a hint); page
   (`OCR_MAX_PAGES`) and size limits fail with `RESOURCE_LIMIT`.
3. **Digital-born branch** — a PDF with a usable text layer goes straight to
   Lines (confidence 1.0); no Baidu call, no quota spent.
4. **OCR** — `OCR_PROVIDER=baidu_aistudio` (default): sync
   `POST /layout-parsing` or async jobs API, picked by `OCR_BAIDU_MODE`.
   Normalizes `prunedResult.overall_ocr_res` (per-line score + bbox) — never
   the markdown blob. `paddleocr_local` is wired in config but raises at
   build until the model path lands.
5. **Extract** — schema YAML drives label (fuzzy) / regex / positional
   matchers per field; money parser handles the observed `27.800`/`27,800`
   chaos into integer smallest units.
6. **Validate** — required fields missing or under threshold →
   `needs_review` + issues; clean → `completed`.
7. **Finish** — document update + outbox row in **one transaction**; the
   outbox process publishes with the contract headers. Terminal statuses
   only (`completed`/`needs_review`/`failed`) ever become events.

## Failure semantics

| Failure | Class | What happens |
|---|---|---|
| Baidu 429/503/504, timeout, network | transient | SAQ retries (3 tries, 300s timeout); document stays `processing` |
| Baidu 403/413/422, async job `failed` | permanent | document → `failed` + `document.processed` with `error` |
| Bucket not allowlisted, bad path, bad schema_id | invalid request | rejected at submit (`INVALID_ARGUMENT`), never queued |
| Object missing at download | permanent | `failed` (`STORAGE_OBJECT_MISSING`) |
| Enqueue fails after insert | — | submit rolls the row back; the caller retries safely |

## Guarantees

- **Idempotency at submit**: `documents.idempotency_key UNIQUE`; duplicate
  submit → `ALREADY_EXISTS` + original `document_id`.
- **Result+event atomicity**: outbox row written in the same transaction as
  the terminal status; the outbox publisher only marks `published_at` after
  Kafka acknowledges.
- **At-least-once delivery**: consumers dedupe by `event_id` (gateway does
  this in Redis); the publisher never tries to be exactly-once.

## Configuration

The vocabulary matches the Go services: domain knobs carry the `OCR_` prefix,
the shared platform knobs (`SERVICE_NAME`, `SERVICE_ENV`, `LOG_LEVEL`,
`LOG_FORMAT`) and the OpenTelemetry spec names (`OTEL_*`) are unprefixed so
one deployment mechanism can set the same variable for every service.
Everything is validated at boot — see `.env.example` for the full annotated
list and `docs/explanation/ocr/provider-config.md` for the sync/async
decision. Secrets (`OCR_BAIDU_TOKEN`, storage keys) are `SecretStr` and never
logged; logs never carry OCR result text (PII) — only counts and confidences.

Logging is one pipeline for the whole process: structlog output and library
logs (grpcio, saq, botocore) render identically — JSON with `trace_id`/
`span_id` when a span is active (`LOG_FORMAT=text` switches to a console
renderer for development). Traces and metrics both push over OTLP to the
central collector (`OTEL_EXPORTER_OTLP_ENDPOINT`, scheme selects TLS); with
the endpoint unset or `OTEL_SDK_DISABLED=true` the SDK is never installed
and recording is a no-op. Instruments: `ocr_rpc_*` (RED per gRPC method),
`ocr_documents_*` / `ocr_pipeline_duration_seconds` / `ocr_document_lines`
(per-document pipeline outcomes), `ocr_outbox_*` (outbox delivery) and the
constant gauge `ocr_process_running` - the liveness signal push telemetry
needs instead of `up`. Grafana: `Expense Platform - OCR Detail` in
infra/observability (uid `ocr-detail`).

## Deployment

`Dockerfile` is multi-stage (uv build → slim runtime, non-root, stubs
generated at build time). The `baidu_aistudio` image carries no model
weights by design. `docker-compose.yml` runs the three processes (api,
worker, outbox) — Postgres/Redis/storage come from `infra/data`, Kafka from
`infra/messaging`, and traces/metrics push to the one central collector in
`infra/observability` (there is deliberately no per-service collector).
Kubernetes grace period must exceed `job_timeout` (300s) so SIGTERM lets
in-flight jobs finish; killed jobs recover via SAQ heartbeat sweep.

## Environment boundary

This database owns `documents` + `outbox_events` — nothing else. It never
reads expense's or gateway's tables; the only inbound trust is the verified
gateway caller (network policy + internal auth on Kontrak B).
