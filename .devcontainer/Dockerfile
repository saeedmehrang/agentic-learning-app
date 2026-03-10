# .devcontainer/Dockerfile

# --- Build args (can be overridden from devcontainer.json) ---
ARG NODE_VERSION=24

# Use the Node version in the base image tag
FROM mcr.microsoft.com/devcontainers/typescript-node:${NODE_VERSION}

# Re-declare args if you want to use them in later RUNs
ARG NODE_VERSION

# Optional: expose them as env vars inside the container
ENV NODE_VERSION=${NODE_VERSION}

# Install Python 3 (uses distro default - Python 3.13 on Debian Trixie)
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    wget && \
    rm -rf /var/lib/apt/lists/*

# Make python3 the default "python" command
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# Install uv (Astral) - fast Python package installer
RUN curl -LsSf https://astral.sh/uv/install.sh | env CARGO_HOME=/opt/uv UV_INSTALL_DIR=/usr/local/bin sh

# Install Python tools system-wide using pip
# Using --break-system-packages is acceptable in containers
# This ensures tools are available globally with correct permissions
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    ruff \
    deadcode \
    pytest \
    ty \
    google-adk

# Install Docker CLI
# https://docs.docker.com/engine/install/debian/
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update -y && \
    apt-get install -y --no-install-recommends \
    docker-ce-cli \
    docker-buildx-plugin \
    docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

# Add the node user to the docker group so they can run docker without sudo
RUN groupadd -f docker && usermod -aG docker node

# Install MCP server framework system-wide (separate from project venv to avoid httpx conflict)
RUN pip install --no-cache-dir --break-system-packages mcp>=1.0.0

# Install Terraform
# https://developer.hashicorp.com/terraform/install#linux
ARG TERRAFORM_VERSION=1.14.6
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends gnupg && \
    curl -fsSL https://apt.releases.hashicorp.com/gpg | \
    gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
    https://apt.releases.hashicorp.com $(lsb_release -cs) main" | \
    tee /etc/apt/sources.list.d/hashicorp.list > /dev/null && \
    apt-get update -y && \
    apt-get install -y terraform=${TERRAFORM_VERSION}-1 && \
    rm -rf /var/lib/apt/lists/*

# Install Cloud SQL Auth Proxy
# https://cloud.google.com/sql/docs/postgres/sql-proxy
ARG CLOUD_SQL_PROXY_VERSION=2.21.1
RUN curl -fsSL \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v${CLOUD_SQL_PROXY_VERSION}/cloud-sql-proxy.linux.amd64" \
    -o /usr/local/bin/cloud-sql-proxy && \
    chmod +x /usr/local/bin/cloud-sql-proxy

# Install Firebase CLI
RUN npm install -g firebase-tools

# Install Google Cloud SDK
RUN apt-get update -y && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    | tee /etc/apt/sources.list.d/google-cloud-sdk.list && \
    curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    apt-get update -y && \
    apt-get install -y google-cloud-cli && \
    rm -rf /var/lib/apt/lists/*

# Install jscpd and ts-prune
RUN npm install -g jscpd ts-prune

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code

# https://github.com/cli/cli/blob/trunk/docs/install_linux.md#debian
RUN apt-get update -y && \
    mkdir -p -m 755 /etc/apt/keyrings && \
    out=$(mktemp) && wget -nv -O$out https://cli.github.com/packages/githubcli-archive-keyring.gpg && \
    cat $out | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null && \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    mkdir -p -m 755 /etc/apt/sources.list.d && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update -y && \
    apt-get install -y gh && \
    rm -rf /var/lib/apt/lists/*

# https://lindevs.com/install-scc-source-code-line-counter-on-ubuntu
RUN wget -qO scc.tar.gz https://github.com/boyter/scc/releases/latest/download/scc_Linux_x86_64.tar.gz && \
    tar xf scc.tar.gz -C /usr/local/bin scc && \
    rm -rf scc.tar.gz

# --- LLM-friendly CLI tools: rg, fd, jq/yq, bat, tree, sd ---
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    ripgrep \
    fd-find \
    jq \
    yq \
    bat \
    tree \
    sd; \
    \
    # Debian/Ubuntu name quirks: fd-find => fdfind, bat => batcat
    ln -sf "$(command -v fdfind)" /usr/local/bin/fd; \
    ln -sf "$(command -v batcat)" /usr/local/bin/bat; \
    \
    # sanity check
    rg --version; fd --version; jq --version; yq --version; bat --version; tree --version; sd --version; \
    \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Install act
RUN curl -sSL https://github.com/nektos/act/releases/latest/download/act_Linux_x86_64.tar.gz -o act.tgz && \
    tar -xzf act.tgz -C /usr/local/bin act && \
    rm -rf act.tgz

# Configure Git defaults for the node user (user.name/email forwarded from host by devcontainer)
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
