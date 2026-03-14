# .devcontainer/Dockerfile

# --- Build args (can be overridden from devcontainer.json) ---
ARG NODE_VERSION=24
# Keep in sync with /.python-version
ARG PYTHON_VERSION=3.13

FROM mcr.microsoft.com/devcontainers/typescript-node:${NODE_VERSION}

ARG NODE_VERSION
ARG PYTHON_VERSION
ARG TERRAFORM_VERSION=1.14.6
ARG CLOUD_SQL_PROXY_VERSION=2.21.1

ENV NODE_VERSION=${NODE_VERSION}
ENV PYTHON_VERSION=${PYTHON_VERSION}

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
        ripgrep fd-find jq yq bat tree sd && \
    \
    # Debian name quirks: fd-find => fd, bat => bat
    ln -sf "$(command -v fdfind)" /usr/local/bin/fd && \
    ln -sf "$(command -v batcat)" /usr/local/bin/bat && \
    \
    rm -rf /var/lib/apt/lists/*

# ── Layer 2: Docker group ─────────────────────────────────────────────────────
RUN groupadd -f docker && usermod -aG docker node

# ── Layer 3: uv ───────────────────────────────────────────────────────────────
RUN curl -LsSf https://astral.sh/uv/install.sh \
        | env CARGO_HOME=/opt/uv UV_INSTALL_DIR=/usr/local/bin sh && \
    rm -rf /opt/uv

# ── Layer 4: Python (pinned via uv) ───────────────────────────────────────────
RUN uv python install ${PYTHON_VERSION} && \
    ln -sf "$(uv python find ${PYTHON_VERSION})" /usr/local/bin/python && \
    ln -sf "$(uv python find ${PYTHON_VERSION})" /usr/local/bin/python3

# ── Layer 5: Python tools (isolated via uv tool) ─────────────────────────────
RUN uv tool install ruff && \
    uv tool install ty && \
    uv tool install deadcode && \
    uv cache clean

# ── Layer 6: Python runtime libs (system-wide for global availability) ────────
RUN uv pip install --system --break-system-packages --python ${PYTHON_VERSION} \
    google-adk \
    psycopg2-binary \
    google-cloud-firestore \
    mcp && \
    uv cache clean

# ── Layer 7: Cloud SQL Auth Proxy ─────────────────────────────────────────────
# https://cloud.google.com/sql/docs/postgres/sql-proxy
RUN curl -fsSL \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v${CLOUD_SQL_PROXY_VERSION}/cloud-sql-proxy.linux.amd64" \
    -o /usr/local/bin/cloud-sql-proxy && \
    chmod +x /usr/local/bin/cloud-sql-proxy

# ── Layer 8: act (local GitHub Actions runner) ────────────────────────────────
RUN curl -fsSL https://github.com/nektos/act/releases/latest/download/act_Linux_x86_64.tar.gz \
        -o /tmp/act.tgz && \
    tar -xzf /tmp/act.tgz -C /usr/local/bin act && \
    rm /tmp/act.tgz

# ── Layer 9: scc (source line counter) ───────────────────────────────────────
RUN wget -qO /tmp/scc.tar.gz \
        https://github.com/boyter/scc/releases/latest/download/scc_Linux_x86_64.tar.gz && \
    tar xf /tmp/scc.tar.gz -C /usr/local/bin scc && \
    rm /tmp/scc.tar.gz

# ── Layer 10: npm global tools ────────────────────────────────────────────────
RUN npm install -g firebase-tools && npm cache clean --force

RUN npm install -g jscpd ts-prune && npm cache clean --force

RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# ── Layer 11: git defaults (node user) ───────────────────────────────────────
USER node
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
    git config --global --add safe.directory '*'
USER root
