#!/usr/bin/env bash
set -euo pipefail

# Sync git identity from host .gitconfig
name=$(git config -f /tmp/.host-gitconfig user.name 2>/dev/null || echo "")
email=$(git config -f /tmp/.host-gitconfig user.email 2>/dev/null || echo "")
if [ -n "$name" ] && [ -n "$email" ]; then
  git config --global user.name "$name"
  git config --global user.email "$email"
else
  echo "WARNING: Could not read git user.name/user.email from host .gitconfig"
fi

# Add uv tool binaries (ruff, ty, etc.) to PATH permanently
UV_TOOL_BIN="$(uv tool dir)/bin"
if ! grep -qF "$UV_TOOL_BIN" ~/.bashrc 2>/dev/null; then
  echo "export PATH=\"$UV_TOOL_BIN:\$PATH\"" >> ~/.bashrc
fi

# Install backend Python dependencies from lockfile
if [ -f backend/pyproject.toml ]; then
  (cd backend && uv sync)
else
  echo "No backend/pyproject.toml yet — skipping uv sync"
fi
