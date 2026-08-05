# The serving image. Built in CI, pushed to GHCR, pulled on the VPS —
# `uv` never runs on a 2-vCPU box whose build cache had already reached
# 2.2 GB (ADR 0031).
#
# Two things here are not boilerplate. The runtime installs **libgomp1**
# because `fastembed` pulls `onnxruntime`, which needs it and which
# `python:slim` does not ship — without it the first query dies on an
# import, not at build time. And the BM25 model is **downloaded at build
# time** into `FASTEMBED_CACHE_PATH`: `fastembed` otherwise fetches it from
# HuggingFace on the first query, which puts a third-party outage on the
# answer path and inside the node timeout.

FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.1 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/opt/fastembed

WORKDIR /app

# Dependencies first, from the lockfile only: this layer is reused on every
# build that does not change `uv.lock`, which is most of them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# Bake the sparse-vector model. `Qdrant/bm25` is small (stopwords + a
# tokenizer), and having it resident is what keeps HuggingFace off the
# request path.
RUN /app/.venv/bin/python -c \
    "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25')"


FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Fixed uid: the named volume's ownership is set from this image, and a uid
# that moves between builds would strand the data written by the last one.
RUN useradd --create-home --uid 10001 fie

COPY --from=builder --chown=fie:fie /app /app
COPY --from=builder --chown=fie:fie /opt/fastembed /opt/fastembed

# `data/` is where SQLite and the corpus live; create it owned by `fie` so a
# fresh named volume inherits that ownership instead of arriving root-owned.
RUN mkdir -p /app/data && chown fie:fie /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    API_LOG_JSON=true

USER fie
WORKDIR /app
EXPOSE 8000

# One worker, and that is a constraint rather than a tuning choice: the job
# registry is in-process asyncio (decision 3.1), so a second worker would
# hold a second semaphore and the concurrency cap — the OOM control — would
# admit twice what the memory limit was sized for.
CMD ["uvicorn", "--factory", "fie.api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
