"""
Homelab MCP — universal tool server for the homelab.

Designed by SK. Built by Claude.
"""

import glob
import json
import os
import re
import subprocess
import time

import httpx
from markitdown import MarkItDown
from mcp.server.fastmcp import FastMCP

# ── config ────────────────────────────────────────────────────────

HOST    = os.environ.get("HOMELAB_MCP_HOST", "0.0.0.0")
PORT    = int(os.environ.get("HOMELAB_MCP_PORT", "1603"))
SEARXNG = os.environ.get("HOMELAB_MCP_SEARXNG", "http://localhost:8080")

ROOTS = [
    os.path.realpath(os.path.expanduser(p))
    for p in os.environ.get("HOMELAB_MCP_ROOTS", os.getcwd()).split(":")
    if p
]

SKIP_DIRS = frozenset(
    os.environ.get(
        "HOMELAB_MCP_SKIP_DIRS",
        "venv,.venv,node_modules,__pycache__,.git,.cache",
    ).split(",")
)

MAX_BYTES        = int(os.environ.get("HOMELAB_MCP_MAX_FILE_BYTES", str(10 * 1024 * 1024)))
GLOB_LIMIT       = int(os.environ.get("HOMELAB_MCP_GLOB_LIMIT", "200"))
GREP_LIMIT       = int(os.environ.get("HOMELAB_MCP_GREP_LIMIT", "50"))
SEARCH_LIMIT     = int(os.environ.get("HOMELAB_MCP_SEARCH_LIMIT", "20"))
FETCH_MAX_CHARS  = int(os.environ.get("HOMELAB_MCP_FETCH_MAX_CHARS", "50000"))

mcp = FastMCP("Homelab MCP", host=HOST, port=PORT)
_md = MarkItDown()


# ── helpers ───────────────────────────────────────────────────────

def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def _safe_path(path: str) -> str | None:
    """Resolve path against any configured root; reject if outside all."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(ROOTS[0], expanded)
    resolved = os.path.realpath(expanded)
    for r in ROOTS:
        if resolved == r or resolved.startswith(r + os.sep):
            return resolved
    return None


def _walk(base: str):
    """Walk directory tree, skipping noisy dirs and files."""
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        yield root, [f for f in files if not f.startswith("._") and f != ".DS_Store"]


# ── tools · claude ────────────────────────────────────────────────

@mcp.tool()
def ask_claude(
    prompt: str,
    model: str = "sonnet",
    effort: str = "medium",
    timeout: int = 600,
) -> str:
    """Ask Claude (a more capable model) when you need deep research, complex reasoning, or analysis you can't do yourself. Returns Claude's answer. model: 'sonnet' or 'opus'. effort: 'medium' or 'max'. timeout: seconds (default 600)."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--tools", "Read,Glob,Grep,WebSearch,WebFetch",
        "--permission-mode", "bypassPermissions",
        "--output-format", "json",
        "--effort", effort,
    ]

    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        return _err("Claude CLI not found on PATH")

    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, 9)
        proc.wait()
        return _err(f"Timed out after {timeout}s")

    ms = round((time.monotonic() - t0) * 1000, 1)

    if proc.returncode != 0:
        return _err(f"Claude failed: {stderr[:500]}")

    data = json.loads(stdout)
    usage = data.get("usage", {})

    return json.dumps({
        "content":     data.get("result", ""),
        "model":       data.get("model", model),
        "duration_ms": ms,
        "tokens": {
            "prompt":     usage.get("input_tokens"),
            "completion": usage.get("output_tokens"),
        },
    }, indent=2)


# ── tools · files ─────────────────────────────────────────────────

@mcp.tool()
def read_file(path: str, limit: int = 0) -> str:
    """Read a text file. path: full path, or relative path under the first root. limit: read only the first N lines (0 = whole file)."""
    p = _safe_path(path)
    if not p:
        return _err("Path outside roots")
    if os.path.basename(p).startswith(".env"):
        return _err("Refusing to read .env file")
    if not os.path.isfile(p):
        return _err(f"Not found: {path}")

    if limit > 0:
        with open(p) as f:
            lines = []
            for i, line in enumerate(f):
                if i >= limit:
                    break
                lines.append(line)
        return "".join(lines)

    size = os.path.getsize(p)
    if size > MAX_BYTES:
        return _err(
            f"File too large: {size} bytes > {MAX_BYTES} cap. "
            f"Pass limit=N to read first N lines."
        )

    with open(p) as f:
        return f.read()


@mcp.tool()
def glob_files(pattern: str, path: str = "") -> str:
    """Find files and folders matching a pattern. Use ** for any subdirectories. Examples: '**/*.py' (all Python files), 'src/*.md' (Markdown files in src). path: optional folder to search in; leave empty to search all configured roots. Returns full paths; folders end with /."""
    if path:
        base = _safe_path(path)
        if not base:
            return _err("Path outside roots")
        bases = [base]
    else:
        bases = list(ROOTS)

    matches = []
    for base in bases:
        for rel in glob.iglob(pattern, root_dir=base, recursive=True):
            if any(part in SKIP_DIRS for part in rel.split(os.sep)):
                continue
            full = os.path.join(base, rel)
            if os.path.isdir(full):
                full = full + "/"
            matches.append(full)
            if len(matches) >= GLOB_LIMIT:
                break
        if len(matches) >= GLOB_LIMIT:
            break

    return json.dumps(sorted(matches), indent=2)


@mcp.tool()
def grep_files(
    pattern: str,
    path: str = "",
    max_results: int = 0,
) -> str:
    """Search inside files for a pattern (case-insensitive regex). Returns matches as a list of {file, line, text}. path: optional folder to search; leave empty to search all roots. max_results: cap on results (0 = use server default)."""
    if max_results <= 0:
        max_results = GREP_LIMIT

    if path:
        base = _safe_path(path)
        if not base:
            return _err("Path outside roots")
        bases = [base]
    else:
        bases = list(ROOTS)

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return _err(f"Invalid regex: {e}")

    results = []
    for base in bases:
        for root, files in _walk(base):
            if len(results) >= max_results:
                break
            for f in files:
                full = os.path.join(root, f)
                try:
                    with open(full) as fh:
                        for i, line in enumerate(fh, 1):
                            if regex.search(line):
                                results.append({
                                    "file": full,
                                    "line": i,
                                    "text": line.rstrip()[:200],
                                })
                                if len(results) >= max_results:
                                    break
                except (UnicodeDecodeError, PermissionError):
                    continue
        if len(results) >= max_results:
            break

    return json.dumps(results, indent=2)


# ── tools · web ───────────────────────────────────────────────────

@mcp.tool()
def web_search(
    query: str,
    time_range: str = "",
    language: str = "all",
) -> str:
    """Search the web. Returns a list of results with title, url, snippet, and source engine. time_range: '' for any time, or 'day' / 'month' / 'year'. language: language code like 'en', or 'all'."""
    params: dict = {"q": query, "format": "json"}
    if time_range:
        params["time_range"] = time_range
    if language != "all":
        params["language"] = language

    try:
        resp = httpx.get(f"{SEARXNG}/search", params=params, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return _err(f"Search failed: {e}")

    data = resp.json()
    results = []
    for r in data.get("results", [])[:SEARCH_LIMIT]:
        results.append({
            "title":   r.get("title", ""),
            "url":     r.get("url", ""),
            "content": r.get("content", ""),
            "engine":  r.get("engine", ""),
        })

    return json.dumps({
        "query":       query,
        "count":       len(results),
        "results":     results,
        "suggestions": data.get("suggestions", []),
    }, indent=2)


@mcp.tool()
def web_fetch(url: str) -> str:
    """Fetch a URL (web page, PDF, or Office document) and return the content as plain Markdown text."""
    try:
        result = _md.convert_url(url)
        text = result.text_content or ""
    except Exception as e:
        return _err(f"Fetch failed: {e}")

    if len(text) > FETCH_MAX_CHARS:
        text = text[:FETCH_MAX_CHARS] + f"\n\n... [truncated, {len(text)} chars total]"

    return text


# ── main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
