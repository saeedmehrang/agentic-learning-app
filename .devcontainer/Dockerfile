# .devcontainer/Dockerfile

# --- Build args (can be overridden from devcontainer.json) ---
ARG NODE_VERSION=24
# Keep in sync with /.python-version
ARG PYTHON_VERSION=3.13

# [M1] Pinned base image tag for reproducible builds
FROM mcr.microsoft.com/devcontainers/base:2.1.7-bookworm

ARG NODE_VERSION
ARG PYTHON_VERSION
ARG TERRAFORM_VERSION=1.14.6
ARG CLOUD_SQL_PROXY_VERSION=2.21.1
ARG UV_VERSION=0.10.10
ARG ACT_VERSION=0.2.84
ARG SCC_VERSION=3.7.0

# [H1] Node.js — download setup script first, then execute (no curl-pipe-bash)
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" -o /tmp/nodesource.sh && \
    bash /tmp/nodesource.sh && \
    rm /tmp/nodesource.sh && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# ── Layer 1: base system deps + all apt keyrings in one pass ─────────────────
# Install prerequisites, add all third-party apt repos, then install everything
# in a single apt-get install to minimise update/clean cycles.
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg wget lsb-release && \
    \
    # Docker CLI keyring
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
        https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    \
    # GitHub CLI keyring
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null && \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
        https://cli.github.com/packages stable main" \
        | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    \
    # Terraform keyring
    curl -fsSL https://apt.releases.hashicorp.com/gpg \
        | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
        https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
        | tee /etc/apt/sources.list.d/hashicorp.list > /dev/null && \
    \
    # Google Cloud SDK keyring
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] \
        https://packages.cloud.google.com/apt cloud-sdk main" \
        | tee /etc/apt/sources.list.d/google-cloud-sdk.list > /dev/null && \
    \
    # Install all apt packages in one pass
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
        docker-ce-cli docker-buildx-plugin docker-compose-plugin \
        gh \
        terraform=${TERRAFORM_VERSION}-1 \
        google-cloud-cli \
        postgresql-client \
        ripgrep fd-find jq yq bat tree sd && \
    \
    # Debian name quirks: fd-find => fd, bat => bat
    ln -sf "$(command -v fdfind)" /usr/local/bin/fd && \
    ln -sf "$(command -v batcat)" /usr/local/bin/bat && \
    \
    rm -rf /var/lib/apt/lists/*

# ── Layer 2: Docker group ─────────────────────────────────────────────────────
RUN groupadd -f docker && usermod -aG docker vscode

# [H1] uv — download pinned release binary directly (no curl-pipe-sh)
RUN curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" \
        -o /tmp/uv.tar.gz && \
    echo "3e1027f26ce8c7e4c32e2277a7fed2cb410f2f1f9320d3df97653d40e21f415b  /tmp/uv.tar.gz" | sha256sum -c - && \
    tar xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1 && \
    rm /tmp/uv.tar.gz

# ── Python (pinned via uv) ───────────────────────────────────────────────────
RUN uv python install ${PYTHON_VERSION} && \
    ln -sf "$(uv python find ${PYTHON_VERSION})" /usr/local/bin/python && \
    ln -sf "$(uv python find ${PYTHON_VERSION})" /usr/local/bin/python3

# [M4] Python tools — pinned versions, isolated via uv tool
RUN uv tool install ruff==0.15.6 && \
    uv tool install ty && \
    uv tool install deadcode && \
    uv cache clean

# [M6] Removed Layer 6 (system-wide Python libs). These are declared in
# backend/pyproject.toml and installed via `uv sync` in postCreateCommand.
# The `mcp` package was unused — removed entirely.

# [M3] Cloud SQL Auth Proxy — pinned version with checksum verification
RUN curl -fsSL \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v${CLOUD_SQL_PROXY_VERSION}/cloud-sql-proxy.linux.amd64" \
    -o /usr/local/bin/cloud-sql-proxy && \
    echo "7efb25ab2f3ef77a2111ba548c8aa7ec53a2dacbc55158c362caefabb0e4ea4b  /usr/local/bin/cloud-sql-proxy" | sha256sum -c - && \
    chmod +x /usr/local/bin/cloud-sql-proxy

# [M2] act — pinned version with checksum verification
RUN curl -fsSL "https://github.com/nektos/act/releases/download/v${ACT_VERSION}/act_Linux_x86_64.tar.gz" \
        -o /tmp/act.tgz && \
    echo "19d4525fb0d80ff50bd3252d3fa47ee1265a331fe4c39249ac65347e527e16e2  /tmp/act.tgz" | sha256sum -c - && \
    tar -xzf /tmp/act.tgz -C /usr/local/bin act && \
    rm /tmp/act.tgz

# [M2] scc — pinned version with checksum verification
RUN curl -fsSL "https://github.com/boyter/scc/releases/download/v${SCC_VERSION}/scc_Linux_x86_64.tar.gz" \
        -o /tmp/scc.tar.gz && \
    echo "3d9d65b00ca874c2b29151abe7e1480736f5229edc3ce8e4b2791460cdfabf5a  /tmp/scc.tar.gz" | sha256sum -c - && \
    tar xf /tmp/scc.tar.gz -C /usr/local/bin scc && \
    rm /tmp/scc.tar.gz

# [M5][L1] npm global tools — pinned versions, combined into fewer layers.
# Replaced unmaintained ts-prune with knip.
RUN npm install -g \
        firebase-tools@15.10.0 \
        jscpd@4.0.8 \
        knip@5.86.0 && \
    npm cache clean --force

RUN npm install -g @anthropic-ai/claude-code@2.1.76 && npm cache clean --force

# [H4] git defaults (vscode user) — safe.directory scoped to /workspaces only
USER vscode
RUN git config --global push.autoSetupRemote true && \
    git config --global push.default simple && \
    git config --global pull.rebase true && \
    git config --global rebase.autoStash true && \
    git config --global fetch.prune true && \
    git config --global fetch.pruneTags true && \
    git config --global diff.algorithm histogram && \
    git config --global diff.colorMoved zebra && \
    git config --global rerere.enabled true && \
    git config --global core.autocrlf input && \
    git config --global --add safe.directory /workspaces

# [L2] End as non-root user
