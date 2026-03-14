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

# Install backend Python dependencies from lockfile
if [ -f backend/pyproject.toml ]; then
  (cd backend && uv sync)
else
  echo "No backend/pyproject.toml yet — skipping uv sync"
fi
