"""
Homelab MCP — universal tool server for the homelab.

Designed by SK. Built by Claude.
"""

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from pydantic import Field
from markitdown import MarkItDown
from mcp.server.fastmcp import FastMCP

# ── config ────────────────────────────────────────────────────────

HOST       = os.environ.get("HOMELAB_MCP_HOST", "0.0.0.0")
PORT       = int(os.environ.get("HOMELAB_MCP_PORT", "1603"))
SEARXNG    = os.environ.get("HOMELAB_MCP_SEARXNG", "http://localhost:8080")
TAVILY_KEY = os.environ.get("HOMELAB_MCP_TAVILY_KEY", "").strip()
EXA_KEY    = os.environ.get("HOMELAB_MCP_EXA_KEY", "").strip()
CLAUDE_BIN = os.environ.get("HOMELAB_MCP_CLAUDE_BIN", "claude")

TELEGRAM_TOKEN   = os.environ.get("HOMELAB_MCP_TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("HOMELAB_MCP_TELEGRAM_CHAT_ID", "").strip()

# Which engine backs web_search (primary) and web_search_backup (backup).
# Choices: searxng, tavily ('none' disables backup). Defaults preserve the
# pre-v0.1.5 behavior: searxng primary; tavily backup iff its key is set.
SEARCH_PRIMARY = os.environ.get("HOMELAB_MCP_SEARCH_PRIMARY", "searxng").strip().lower()
SEARCH_BACKUP  = os.environ.get(
    "HOMELAB_MCP_SEARCH_BACKUP",
    "searxng" if SEARCH_PRIMARY == "tavily" else ("tavily" if TAVILY_KEY else "none"),
).strip().lower()

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
DOC_MAX_CHARS    = int(os.environ.get("HOMELAB_MCP_DOC_MAX_CHARS", "200000"))

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

_EFFORT_TIMEOUTS = {"low": 300, "medium": 600, "high": 900, "xhigh": 1200, "max": 1800}


@mcp.tool()
def ask_claude(
    prompt: str,
    model: str = "sonnet",
    effort: str = "medium",
    timeout: int = 0,
) -> str:
    """Ask Claude (a more capable model) when you need deep research, complex reasoning, or analysis you can't do yourself. Returns Claude's answer. model: 'sonnet' (fast, capable) or 'opus' (hardest problems). effort: 'low' / 'medium' / 'high' / 'xhigh' / 'max' — use 'high' or above for hard problems. timeout: seconds; 0 (default) scales with model and effort (5-30 min)."""
    if model not in ("sonnet", "opus"):
        return _err("model must be 'sonnet' or 'opus'")
    if effort not in _EFFORT_TIMEOUTS:
        return _err("effort must be 'low', 'medium', 'high', 'xhigh', or 'max'")
    if timeout <= 0:
        timeout = _EFFORT_TIMEOUTS[effort]
        if model == "opus":
            timeout = min(int(timeout * 1.5), 1800)

    cmd = [
        CLAUDE_BIN, "-p",
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
        return _err(f"Claude CLI not found: {CLAUDE_BIN}")

    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, 9)
        proc.wait()
        return _err(f"Timed out after {timeout}s")

    ms = round((time.monotonic() - t0) * 1000, 1)

    if proc.returncode != 0:
        return _err(f"Claude failed: {stderr[:500]}")

    try:
        data = json.loads(stdout)
    except ValueError:
        return _err(f"Unexpected Claude CLI output: {stdout[:300]}")
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
def read_doc(path: str) -> str:
    """Read a document file (PDF, Word, Excel, PowerPoint, or Outlook .msg) and return the content as Markdown text. Use this for binary documents that read_file can't parse. path: full path, or relative path under the first root."""
    p = _safe_path(path)
    if not p:
        return _err("Path outside roots")
    if not os.path.isfile(p):
        return _err(f"Not found: {path}")

    try:
        result = _md.convert(p)
        text = result.text_content or ""
    except Exception as e:
        return _err(f"Conversion failed: {e}")

    if len(text) > DOC_MAX_CHARS:
        text = text[:DOC_MAX_CHARS] + f"\n\n... [truncated, {len(text)} chars total]"

    return text


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
# web_search / web_search_backup are registered from the engine functions
# below according to SEARCH_PRIMARY / SEARCH_BACKUP — see the registration
# block after the engines. Exa is deliberately not a role choice; it has
# its own tool (web_search_semantic, further down).

def _search_searxng(
    query: Annotated[str, Field(description="The search query.")],
    time_range: Annotated[str, Field(description="Set 'day', 'week', 'month', or 'year' when recency matters (news, releases, current data). '' = any time.")] = "",
    language: Annotated[str, Field(description="Set a code like 'en' or 'ko' when the query targets a specific language or region; 'all' = no restriction.")] = "all",
) -> str:
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


def _search_tavily(
    query: Annotated[str, Field(description="The search query.")],
    time_range: Annotated[str, Field(description="Set 'day', 'week', 'month', or 'year' when recency matters (news, releases, current data). '' = any time.")] = "",
    topic: Annotated[str, Field(description="'news' for current events and breaking stories, 'finance' for markets, tickers, and companies, 'general' (default) for everything else.")] = "general",
    depth: Annotated[str, Field(description="'advanced' (default) for best relevance; 'basic' only for quick, simple lookups.")] = "advanced",
) -> str:
    if topic not in ("general", "news", "finance"):
        return _err("topic must be 'general', 'news', or 'finance'")
    if depth not in ("advanced", "basic"):
        return _err("depth must be 'advanced' or 'basic'")

    body = {
        "query": query,
        "topic": topic,
        "search_depth": depth,
        "max_results": min(SEARCH_LIMIT, 20),
    }
    if time_range:
        body["time_range"] = time_range

    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {TAVILY_KEY}"},
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return _err(f"Tavily search failed: {e}")

    results = [
        {
            "title":   r.get("title", ""),
            "url":     r.get("url", ""),
            "content": r.get("content", ""),
            "score":   round(r.get("score") or 0, 3),
        }
        for r in resp.json().get("results", [])
    ]
    return json.dumps({"query": query, "count": len(results), "results": results}, indent=2)


# ── tools · web · role registration ───────────────────────────────

_SEARCH_ENGINES = {"searxng": _search_searxng, "tavily": _search_tavily}

_SEARCH_DESCRIPTIONS = {
    ("searxng", "primary"):
        "Search the web. Returns a list of results with title, url, snippet, and source engine.",
    ("tavily", "primary"):
        "Search the web via the Tavily API. Returns a list of results with title, url, snippet, and relevance score (0-1).",
    ("searxng", "backup"):
        "Backup web search via SearXNG metasearch — independent of web_search's engine. "
        "Use when web_search results don't serve the query: off-topic or low-quality hits, snippets too thin to answer from, "
        "stale pages, few or no results, or the primary erroring (e.g. quota exhausted). "
        "Returns a list of results with title, url, snippet, and source engine.",
    ("tavily", "backup"):
        "Backup web search via the Tavily API — higher quality, independent of web_search's engines. "
        "Use when web_search results don't serve the query: off-topic or low-quality hits, snippets too thin to answer from, "
        "stale pages, few or no results, or degraded (rate-limited / CAPTCHA-blocked) engines. "
        "Returns a list of results with title, url, snippet, and relevance score (0-1).",
}


def _register_search_tools() -> None:
    if SEARCH_PRIMARY not in _SEARCH_ENGINES:
        sys.exit(f"HOMELAB_MCP_SEARCH_PRIMARY={SEARCH_PRIMARY!r}: must be 'searxng' or 'tavily'")
    if SEARCH_BACKUP not in (*_SEARCH_ENGINES, "none"):
        sys.exit(f"HOMELAB_MCP_SEARCH_BACKUP={SEARCH_BACKUP!r}: must be 'searxng', 'tavily', or 'none'")
    if SEARCH_PRIMARY == SEARCH_BACKUP:
        sys.exit("HOMELAB_MCP_SEARCH_PRIMARY and HOMELAB_MCP_SEARCH_BACKUP must differ")
    if SEARCH_PRIMARY == "tavily" and not TAVILY_KEY:
        sys.exit("HOMELAB_MCP_SEARCH_PRIMARY=tavily requires HOMELAB_MCP_TAVILY_KEY")

    mcp.tool(
        name="web_search",
        description=_SEARCH_DESCRIPTIONS[(SEARCH_PRIMARY, "primary")],
    )(_SEARCH_ENGINES[SEARCH_PRIMARY])

    if SEARCH_BACKUP == "none":
        return
    if SEARCH_BACKUP == "tavily" and not TAVILY_KEY:
        print("web_search_backup disabled: HOMELAB_MCP_SEARCH_BACKUP=tavily but HOMELAB_MCP_TAVILY_KEY is unset")
        return
    mcp.tool(
        name="web_search_backup",
        description=_SEARCH_DESCRIPTIONS[(SEARCH_BACKUP, "backup")],
    )(_SEARCH_ENGINES[SEARCH_BACKUP])


_register_search_tools()


# ── tools · web · semantic search (conditional on Exa) ────────────
# Registered only when HOMELAB_MCP_EXA_KEY is set. Not a role choice for
# primary/backup — a different capability: search by meaning, not keywords.

if EXA_KEY:

    @mcp.tool()
    def web_search_semantic(
        query: Annotated[str, Field(description="What the page should be about, phrased as a description of the target page ('startups building home robots'), not keywords — this is semantic search.")],
        time_range: Annotated[str, Field(description="Set 'day', 'week', 'month', or 'year' when recency matters (news, releases, current data). '' = any time.")] = "",
        category: Annotated[str, Field(description="Set when the query clearly targets one corpus: 'publication' (academic papers), 'news', 'company', 'people', 'personal site' (blogs), 'financial report' (SEC filings / earnings). '' = whole web.")] = "",
    ) -> str:
        """Semantic web search via the Exa API — finds pages by meaning rather than keywords. Use for conceptual and discovery queries ("startups building X", "papers about Y approach") or category-restricted hunts; for ordinary keyword or navigational lookups use web_search. Returns a list of results with title, url, snippet, published date, and author."""
        categories = ("", "company", "people", "publication", "news", "personal site", "financial report")
        if category not in categories:
            return _err("category must be '' or one of: " + ", ".join(c for c in categories if c))
        days = {"": None, "day": 1, "week": 7, "month": 31, "year": 365}
        if time_range not in days:
            return _err("time_range must be '', 'day', 'week', 'month', or 'year'")

        body = {
            "query": query,
            "type": "auto",
            "numResults": min(SEARCH_LIMIT, 100),
            "contents": {"highlights": True},
        }
        if category:
            body["category"] = category
        if days[time_range]:
            start = datetime.now(timezone.utc) - timedelta(days=days[time_range])
            body["startPublishedDate"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            resp = httpx.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": EXA_KEY},
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return _err(f"Exa search failed: {e}")

        results = [
            {
                "title":     r.get("title") or "",
                "url":       r.get("url", ""),
                "content":   " … ".join(r.get("highlights") or []),
                "published": (r.get("publishedDate") or "")[:10],
                "author":    r.get("author") or "",
            }
            for r in resp.json().get("results", [])
        ]
        return json.dumps({"query": query, "count": len(results), "results": results}, indent=2)


# ── tools · messaging (conditional on Telegram config) ────────────
# Registered only when both HOMELAB_MCP_TELEGRAM_TOKEN and
# HOMELAB_MCP_TELEGRAM_CHAT_ID are set.

if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:

    @mcp.tool()
    def send_message(
        text: Annotated[str, Field(description="Message body. Plain text, or Telegram HTML for formatting: <b> <i> <u> <s> <code> <pre> <a href> <blockquote>. Escape literal <, >, & as &lt; &gt; &amp;. Max 4096 chars; longer is truncated.")],
    ) -> str:
        """Send a message to the homelab owner (delivered to their Telegram). Use when the owner should hear about something: work finished, a finding worth flagging, or an explicit ask to be notified."""
        suffix = "\n… [truncated]"
        if len(text) > 4096:
            text = text[:4096 - len(suffix)] + suffix

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        body = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True}}
        try:
            resp = httpx.post(url, json=body, timeout=30)
            if resp.status_code == 400 and "can't parse entities" in resp.text:
                body.pop("parse_mode")  # malformed HTML → deliver as plain text
                resp = httpx.post(url, json=body, timeout=30)
        except httpx.HTTPError as e:
            # httpx error strings embed the request URL — scrub the token
            return _err(f"Telegram send failed: {str(e).replace(TELEGRAM_TOKEN, '***')}")

        data = resp.json()
        if not data.get("ok"):
            return _err(f"Telegram send failed: {data.get('description', resp.status_code)}")

        return json.dumps({
            "sent":       True,
            "message_id": data["result"].get("message_id"),
            "format":     "html" if "parse_mode" in body else "plain",
        }, indent=2)

elif TELEGRAM_TOKEN or TELEGRAM_CHAT_ID:
    print("send_message disabled: HOMELAB_MCP_TELEGRAM_TOKEN and HOMELAB_MCP_TELEGRAM_CHAT_ID must both be set")


# ── main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
