# The workshop: where Codex builds Ahab's sites, in a box.
#
# Node, git and the Codex CLI, nothing else. It runs as the unprivileged
# "node" user, on a Docker network with no way out except the allowlist
# proxy (proxy.py). ahab/workshop.py builds this image and runs it; the
# details of how it's locked down are there, in run_container().

FROM node:22-bookworm-slim

# Pinned so a build behaves the same until we choose to move it.
ARG CODEX_VERSION=0.155.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force \
 # node_modules lives in its own Docker volume per site (faster than the
 # Windows folder, and symlinks work there). A new volume copies this
 # folder's owner, so the node user can write to it.
 && mkdir -p /work/node_modules \
 && chown -R node:node /work

USER node
WORKDIR /work
