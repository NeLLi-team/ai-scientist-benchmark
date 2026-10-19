# Participant image for native Codex CLI and Claude Code benchmark runs.
# Build it with docker/build.sh; the script pins and verifies every input.
FROM ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ARG APT_SNAPSHOT=20261015T000000Z
ARG PIXI_VERSION
ARG PIXI_SHA256
ARG CODEX_VERSION
ARG CODEX_SHA256
ARG CLAUDE_VERSION
ARG CLAUDE_SHA256

ENV DEBIAN_FRONTEND=noninteractive

# Apt reads a dated Ubuntu snapshot, so the package set is the same on every build.
# The snapshot host is https-only and the base image has no CA bundle, so the first
# two apt calls skip TLS peer checks. Apt still verifies the Ubuntu archive GPG
# signatures, which protect the packages, and TLS is on for every later call.
RUN sed -i \
        -e "s|http://archive.ubuntu.com/ubuntu/|http://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT}/|" \
        -e "s|http://security.ubuntu.com/ubuntu/|http://snapshot.ubuntu.com/ubuntu/${APT_SNAPSHOT}/|" \
        /etc/apt/sources.list.d/ubuntu.sources \
    && echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99snapshot \
    && apt-get -o Acquire::https::Verify-Peer=false update \
    && apt-get -o Acquire::https::Verify-Peer=false install -y --no-install-recommends ca-certificates \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv curl wget git build-essential pigz unzip \
        xz-utils bzip2 procps ripgrep \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/pixi.tar.gz \
        "https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/pixi-x86_64-unknown-linux-musl.tar.gz" \
    && echo "${PIXI_SHA256}  /tmp/pixi.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/pixi.tar.gz -C /usr/local/bin pixi \
    && rm /tmp/pixi.tar.gz \
    && pixi --version | grep -F "${PIXI_VERSION}"

# The image has no user named after the host user; uid 1000 is "participant".
# The runner starts the container with --user <host uid>:<host gid>.
RUN userdel -r ubuntu && useradd --create-home --uid 1000 --user-group --shell /bin/bash participant

# Codex keeps its helpers beside the binary (codex-package.json lists the layout).
COPY --from=codexpkg bin/ /opt/codex/bin/
COPY --from=codexpkg codex-path/ /opt/codex/codex-path/
COPY --from=codexpkg codex-resources/bwrap /opt/codex/codex-resources/bwrap
COPY --from=codexpkg codex-package.json /opt/codex/codex-package.json
COPY --from=claudebin ${CLAUDE_VERSION} /opt/claude/bin/claude

RUN echo "${CODEX_SHA256}  /opt/codex/bin/codex" | sha256sum -c - \
    && echo "${CLAUDE_SHA256}  /opt/claude/bin/claude" | sha256sum -c - \
    && chmod 0755 /opt/codex/bin/codex /opt/claude/bin/claude \
    && chmod -R go+rX /opt/codex /opt/claude \
    && /opt/codex/bin/codex --version | grep -F "${CODEX_VERSION}" \
    && /opt/claude/bin/claude --version | grep -F "${CLAUDE_VERSION}"

LABEL org.benchmark.codex.version="${CODEX_VERSION}" \
      org.benchmark.codex.sha256="${CODEX_SHA256}" \
      org.benchmark.claude.version="${CLAUDE_VERSION}" \
      org.benchmark.claude.sha256="${CLAUDE_SHA256}" \
      org.benchmark.pixi.version="${PIXI_VERSION}" \
      org.benchmark.apt.snapshot="${APT_SNAPSHOT}" \
      org.benchmark.base.image="ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55"

# Ubuntu marks its system Python as externally managed; allow pip installs into
# the user site (HOME is always /home/participant) so agents need no venv.
ENV LANG=C.UTF-8 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PATH=/home/participant/.local/bin:/usr/local/bin:/usr/bin:/bin

USER participant
WORKDIR /benchmark/work
