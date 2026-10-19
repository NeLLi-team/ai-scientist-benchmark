#!/usr/bin/env bash
# Build the participant image from pinned inputs.
#
# Usage: docker/build.sh [IMAGE_TAG]
# The agent binaries come from the host; their SHA-256 values are pinned below,
# so a different build of the same version fails the build.
set -euo pipefail

IMAGE_TAG="${1:-benchmark-participant:2026-10-19}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CODEX_VERSION="0.163.0"
CODEX_SHA256="399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215"
CODEX_PACKAGE_DIR="${CODEX_PACKAGE_DIR:-$HOME/.local/npm-global/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl}"

CLAUDE_VERSION="2.1.303"
CLAUDE_SHA256="375988f457756524234695cf2d8f64ace99c5f68682e47ea24862a843c191450"
CLAUDE_VERSIONS_DIR="${CLAUDE_VERSIONS_DIR:-$HOME/.local/share/claude/versions}"

PIXI_VERSION="0.72.0"
PIXI_SHA256="2c086608809f7bdd9918323cf6f6278bb43b025f4d957ddfd55295cf151c6f21"

docker build \
    --provenance=false --sbom=false \
    --file "$HERE/Dockerfile" \
    --build-context "codexpkg=$CODEX_PACKAGE_DIR" \
    --build-context "claudebin=$CLAUDE_VERSIONS_DIR" \
    --build-arg "CODEX_VERSION=$CODEX_VERSION" \
    --build-arg "CODEX_SHA256=$CODEX_SHA256" \
    --build-arg "CLAUDE_VERSION=$CLAUDE_VERSION" \
    --build-arg "CLAUDE_SHA256=$CLAUDE_SHA256" \
    --build-arg "PIXI_VERSION=$PIXI_VERSION" \
    --build-arg "PIXI_SHA256=$PIXI_SHA256" \
    --tag "$IMAGE_TAG" \
    "$HERE"

docker image inspect "$IMAGE_TAG" --format 'image id: {{.Id}}'
