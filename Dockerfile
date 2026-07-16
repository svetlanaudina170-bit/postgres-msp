FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0
WORKDIR /app
RUN apt-get update \
  && apt-get install -y libpq-dev gcc \
  && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-install-project --no-dev
COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
RUN useradd -m -s /bin/bash app
COPY --from=builder /app /app
RUN chown -R app:app /app
ENV PATH="/app/.venv/bin:$PATH"
# Disable inherited Windows system proxy for apt-get inside container
ENV http_proxy="" https_proxy="" HTTP_PROXY="" HTTPS_PROXY=""
LABEL org.opencontainers.image.description="Postgres MCP Agent"
LABEL org.opencontainers.image.source="https://github.com/crystaldba/postgres-mcp"
RUN apt-get update && apt-get install -y \
  libpq-dev \
  iputils-ping \
  dnsutils \
  net-tools \
  && rm -rf /var/lib/apt/lists/*
# Restore NO_PROXY for runtime (Gradio/httpx localhost bypass)
ENV http_proxy="" https_proxy="" HTTP_PROXY="" HTTPS_PROXY=""
ENV NO_PROXY="127.0.0.1,localhost"
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh
EXPOSE 8100
EXPOSE 7862
USER app
WORKDIR /app
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["mcp"]
