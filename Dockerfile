# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 10001 deployground \
    && useradd --system --uid 10001 --gid deployground --no-create-home --shell /usr/sbin/nologin deployground

COPY --from=builder /opt/venv /opt/venv

USER 10001:10001
EXPOSE 8080

ENTRYPOINT ["python", "-m", "deployground"]
