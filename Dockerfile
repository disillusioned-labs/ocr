# Multi-stage build; uv never ships in the final image. The baidu_aistudio
# provider needs no model weights - this image stays small on purpose.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime

RUN groupadd -r ocr && useradd -r -g ocr ocr
WORKDIR /app
COPY --from=builder --chown=ocr:ocr /app/.venv /app/.venv
COPY --from=builder --chown=ocr:ocr /app/src /app/src
COPY --from=builder --chown=ocr:ocr /app/schemas /app/schemas
COPY --from=builder --chown=ocr:ocr /app/migrations /app/migrations
COPY --from=builder --chown=ocr:ocr /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=ocr:ocr /app/proto /app/proto
COPY --from=builder --chown=ocr:ocr /app/scripts /app/scripts
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

# Stubs are generated at build time so the runtime image never needs grpcio-tools.
RUN python scripts/generate_proto.py

USER ocr
