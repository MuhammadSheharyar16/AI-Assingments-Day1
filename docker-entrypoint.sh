#!/bin/sh
# Day 7 Task 12 — entrypoint: fix mounted-volume ownership, then drop root.
#
# The image's own files (/app/.venv, /app/src) are already owned by
# `appuser` at build time (Dockerfile's `--chown` on both COPY --from
# lines) - this script exists only for what a build can't know in
# advance: a HOST directory bind-mounted in at `docker run` time (e.g.
# `-v ./data:/app/data`, `-v ./artifacts:/app/artifacts` for the Day 7
# eval harness - see README "Docker") can arrive owned by a uid `appuser`
# (1000 in this image) has no write access to, particularly via Docker
# Desktop's bind-mount translation on Windows/macOS. Without this, that
# mismatch used to force every such invocation to add `--user root` -
# a real fix, not a workaround: this container still starts as root (the
# image's default entrypoint user), but every actual application process
# runs as the unprivileged `appuser`, exactly as before, with no
# `--user root` needed on the `docker run` command line at all.
#
# Only ever touches the two mount points this image's own documented
# workflows write to, and only when they actually exist (i.e. were
# mounted) - a bind mount this image never writes to is left completely
# alone.
set -e

if [ "$(id -u)" = "0" ]; then
    for dir in /app/data /app/artifacts; do
        if [ -d "$dir" ]; then
            chown -R appuser:appuser "$dir" 2>/dev/null || true
        fi
    done
    exec gosu appuser "$@"
fi

# Already running as a non-root user (e.g. `docker run --user 1000 ...`) -
# nothing to fix ownership of on this process's behalf, just run it.
exec "$@"
