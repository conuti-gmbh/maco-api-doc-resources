# Dev/tooling image for maco-api-doc-resources — the Python + vacuum half of the
# pipeline (generator scripts, tests, $ref check, OpenAPI lint, bundler). Pinned
# to the same versions CI uses so a local `make` run matches the runner.
#
# It deliberately does NOT contain PHP (templater) or Rust (translator): the
# full sync is multi-runtime + cross-repo and lives in .github/workflows/sync.yaml.
# Build context is the repo root: docker build -f docker/dev/Dockerfile -t <img> .
FROM python:3.13-slim

# Keep in sync with .github/workflows (VACUUM_VERSION) — the templater image
# ships the same vacuum.
ARG VACUUM_VERSION=0.29.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git rsync \
    && rm -rf /var/lib/apt/lists/*

# vacuum (OpenAPI 3.1 linter) — same binary + version as CI / the templater image.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) va=linux_x86_64 ;; \
        arm64) va=linux_arm64 ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/daveshanley/vacuum/releases/download/v${VACUUM_VERSION}/vacuum_${VACUUM_VERSION}_${va}.tar.gz" \
        | tar -xz -C /usr/local/bin vacuum; \
    chmod +x /usr/local/bin/vacuum; \
    vacuum version

# Python deps (ruamel + pytest) pinned via the repo's requirements.txt.
COPY scripts/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /work
