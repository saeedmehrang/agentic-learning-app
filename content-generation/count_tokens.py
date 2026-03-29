#!/usr/bin/env python3
"""
count_tokens.py — Local token count estimator for JSON, YAML, and Markdown files.

Uses tiktoken (cl100k_base encoding, ~GPT-4 vocabulary) as a local approximation
for Gemini token counts. Accuracy is typically within 10-15% of Gemini's tokenizer.
No API calls are made.

Importable API
--------------
    from count_tokens import count_tokens, count_tokens_str

    n = count_tokens("path/to/file.json")   # accepts str or Path
    n = count_tokens_str(some_text)         # count a raw string directly

CLI usage
---------
    python count_tokens.py file1.json file2.yaml file3.md
    python count_tokens.py courses/linux-basics/pipeline/generated/beginner/L01.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Union

import tiktoken

# cl100k_base is used by GPT-4 / GPT-3.5-turbo and is a reasonable approximation
# for Gemini's SentencePiece tokenizer for English + code content.
_ENCODING_NAME = "cl100k_base"
_enc: tiktoken.Encoding | None = None


def _enc_get() -> tiktoken.Encoding:
    global _enc
    if _enc is None:
        _enc = tiktoken.get_encoding(_ENCODING_NAME)
    return _enc


def count_tokens_str(text: str) -> int:
    """Return the estimated token count for a raw string."""
    return len(_enc_get().encode(text))


def count_tokens(path: Union[str, Path]) -> int:
    """Return the estimated token count for a file (JSON, YAML, or Markdown).

    The file is read as plain text — no schema parsing. This matches how the
    content is actually sent to the model (serialised text in the prompt).
    """
    text = Path(path).read_text(encoding="utf-8")
    return count_tokens_str(text)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_row(label: str, tokens: int, width: int) -> str:
    bar_max = 40
    bar_len = min(bar_max, int(bar_max * tokens / max(tokens, 1)))
    bar = "#" * bar_len
    return f"  {label:<{width}}  {tokens:>7,}  {bar}"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python count_tokens.py <file> [file ...]")
        sys.exit(1)

    paths = [Path(p) for p in sys.argv[1:]]
    results: list[tuple[str, int]] = []
    errors: list[tuple[str, str]] = []

    for path in paths:
        if not path.exists():
            errors.append((str(path), "file not found"))
            continue
        if path.suffix not in {".json", ".yaml", ".yml", ".md"}:
            errors.append((str(path), f"unsupported extension '{path.suffix}'"))
            continue
        try:
            tokens = count_tokens(path)
            results.append((str(path), tokens))
        except Exception as exc:
            errors.append((str(path), str(exc)))

    if not results and not errors:
        print("No files processed.")
        sys.exit(0)

    if results:
        max_label = max(len(r[0]) for r in results)
        print(f"\n  {'File':<{max_label}}  {'Tokens':>7}  (encoding: {_ENCODING_NAME})")
        print(f"  {'-' * max_label}  {'-' * 7}  {'-' * 20}")
        for label, tokens in results:
            print(_format_row(label, tokens, max_label))
        if len(results) > 1:
            total = sum(t for _, t in results)
            print(f"\n  {'TOTAL':<{max_label}}  {total:>7,}")

    if errors:
        print()
        for path, msg in errors:
            print(f"  ERROR  {path}: {msg}", file=sys.stderr)

    print()


if __name__ == "__main__":
    main()
