# Day 7 Task 12 — multi-stage build.
#
# Build:  docker build -t aico:day7 .
# Run:    docker run --rm -p 8000:8000 aico:day7
#
# See README.md "Docker" section for the full build/run reference,
# including how to mount data/config for a real endpoint and how to run
# the Day 7 eval harness inside this same image.
#
# syntax=docker/dockerfile:1

# ── Stage 1: builder — reproducible uv install from the locked deps ──────
#
# Official uv static binary (astral-sh/uv's own recommended pattern -
# https://docs.astral.sh/uv/guides/integration/docker/), pinned to an
# exact version rather than `:latest` - the whole point of `uv.lock` is a
# byte-identical dependency set on every build, and an unpinned tool image
# would undermine that the moment upstream published a new default tag.
FROM ghcr.io/astral-sh/uv:0.5.11 AS uv

FROM python:3.13-slim AS builder
COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependency manifests only, first — this layer only invalidates when
# pyproject.toml/uv.lock actually change, not on every source edit.
# README.md is required too: pyproject.toml declares it as `[project]
# readme`, and hatchling (the configured build backend) refuses to build
# the package without the file it names actually existing.
COPY pyproject.toml uv.lock README.md ./

# --frozen: install exactly what uv.lock records, never re-resolve or
#   silently drift (the "reproducible" half of this requirement — the
#   working rule "CI and Docker use the committed lockfile").
# --no-dev: production dependencies only — pytest/httpx (the dev group)
#   never enter the image at all, not even the builder stage.
# --no-install-project: install the locked third-party dependencies only
#   at this point; the project's own package needs src/, copied next, so
#   installing it is deferred to the second `uv sync` below. Keeps this
#   (expensive, network-using) layer cacheable independent of source code
#   changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Now the application source, and finish installing the project itself
# (editable, uv's default) into the same locked venv.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ── Stage 2: runtime — clean, minimal, no build tooling ──────────────────
#
# Fresh base image: no uv binary, no C compiler/build headers slim's
# builder layers may have pulled in, no pip cache, no dev dependency
# group, and — because this stage's build context is this Dockerfile's
# COPY --from list alone, never a bare `COPY . .` — no .git, no local
# .venv, no .env, no test fixtures. See .dockerignore for the belt-and-
# braces version of the same guarantee.
FROM python:3.13-slim AS runtime

# gosu: the one small addition needed to start as root and still run the
# actual application as an unprivileged user - see docker-entrypoint.sh.
# Nothing else from apt (no compiler, no dev headers) - `rm -rf` of the
# apt list cache keeps this from leaving package-index cruft in the layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --user-group appuser
WORKDIR /app

# Only the locked venv and the source it was installed against (editable
# installs record the source path at install time, so the same relative
# layout — /app/src under the same /app WORKDIR — has to carry over from
# the builder stage). Never the developer's own local .venv: that directory
# is not part of this Dockerfile's build context to begin with
# (.dockerignore), and even if it were, nothing here ever copies it — the
# only source of /app/.venv in this stage is the builder's own `uv sync`
# output, built fresh from uv.lock inside the image.
COPY --from=builder --chown=appuser:appuser /app/.venv ./.venv
COPY --from=builder --chown=appuser:appuser /app/src ./src
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# No credentials, endpoint values, or environment-specific config are
# baked into this image — none of config/model-routing.yaml, data/,
# evals/, or .env is copied above, and this Dockerfile never runs a
# command that could embed one. The Model Gateway authenticates via
# azure.identity.DefaultAzureCredential at container run time (README
# "Identity, not a key"), and AICO_AUTH_JWT_SECRET / AICO_FOUNDRY_ENDPOINT
# / config/model-routing.yaml / the golden dataset + index are all
# supplied when the container is *run* (env vars / a mounted config file
# / mounted volumes — see README "Docker"), never at build time.
#
# Deliberately no `USER appuser` here: this container's *entry point*
# starts as root so docker-entrypoint.sh can fix ownership of a
# bind-mounted host directory (data/artifacts for the eval harness - see
# README "Docker") before the real command ever runs - it always hands
# off to `gosu appuser` before executing anything from CMD, so the actual
# application process (the API server, or the eval harness) still runs
# fully unprivileged, exactly as a static `USER appuser` would have
# guaranteed, without requiring `--user root` on every `docker run` that
# happens to write to a mounted volume.
ENTRYPOINT ["docker-entrypoint.sh"]

EXPOSE 8000

CMD ["uvicorn", "aico.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
