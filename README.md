# Homelab MCP 🛠

An MCP tool server for homelabs. Seven core tools plus optional backup
search, semantic search, and owner messaging. Three dependencies, one file.

Designed by SK. Built by Claude.

## Overview

A FastMCP HTTP server exposing seven tools to MCP clients (Claude Code, IDE
plugins, custom agents). Local or hosted LLMs use these tools to read files,
search the web, fetch URLs, and delegate work to Claude. Around 300 lines
of Python, single port, environment-driven configuration.

## Quick Start

```bash
git clone https://github.com/sunwoo85/homelab-mcp.git
cd homelab-mcp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional — only if you want to override defaults
./start.sh start
```

Listens on `:1603`. Roots default to `$PWD`; SearXNG defaults to
`http://localhost:8080`.

## How It Works

```
MCP client ──► :1603 (homelab-mcp) ──► Claude CLI / SearXNG / Tavily / Exa / Telegram / filesystem
```

FastMCP over streamable HTTP. Each tool is a Python function decorated with
`@mcp.tool()`.

## Tools

| Tool         | Purpose                                                       |
|--------------|---------------------------------------------------------------|
| `ask_claude` | Delegate to Claude (sonnet or opus; effort low–max; timeout auto-scales) |
| `read_file`  | Read a UTF-8 text file inside a configured root               |
| `read_doc`   | Read a local PDF / DOCX / PPTX / XLSX / XLS / MSG and convert to Markdown |
| `glob_files` | Find files by glob pattern across roots                       |
| `grep_files` | Case-insensitive regex search across roots                    |
| `web_search` | Web search via the configured primary engine (SearXNG default, or Tavily) |
| `web_fetch`  | Fetch a URL (HTML, PDF, DOCX) and convert to Markdown         |
| `web_search_backup`* | Escalation search via the configured backup engine — for when `web_search` results are inadequate |
| `web_search_semantic`* | Exa neural search — finds pages by meaning; conceptual and discovery queries |
| `send_message`* | Message the homelab owner via Telegram (plain text or Telegram HTML) |

`*` Conditional: `web_search_backup` registers when a backup engine is
configured and usable; `web_search_semantic` when `HOMELAB_MCP_EXA_KEY`
is set; `send_message` when both `HOMELAB_MCP_TELEGRAM_TOKEN` and
`HOMELAB_MCP_TELEGRAM_CHAT_ID` are set. Escalation is LLM-judged via the
tool descriptions — there is no automatic fallback logic in the server.
See **Search engines** below.

## Configuration

All via environment variables. Drop them in a `.env` next to `start.sh`;
`start.sh` sources it on launch.

| Variable                       | Default                                            | Purpose                                                              |
|--------------------------------|----------------------------------------------------|----------------------------------------------------------------------|
| `HOMELAB_MCP_HOST`             | `0.0.0.0`                                          | Listen address                                                       |
| `HOMELAB_MCP_PORT`             | `1603`                                             | Listen port                                                          |
| `HOMELAB_MCP_SEARXNG`          | `http://localhost:8080`                            | SearXNG endpoint for `web_search`                                    |
| `HOMELAB_MCP_TAVILY_KEY`       | (unset)                                            | Tavily API key — enables `tavily` as an engine (default backup when set) |
| `HOMELAB_MCP_EXA_KEY`          | (unset)                                            | Exa API key. When set, the `web_search_semantic` tool is registered  |
| `HOMELAB_MCP_TELEGRAM_TOKEN`   | (unset)                                            | Telegram bot token from @BotFather — see **Telegram setup**          |
| `HOMELAB_MCP_TELEGRAM_CHAT_ID` | (unset)                                            | Telegram chat to deliver to. Both set → `send_message` is registered |
| `HOMELAB_MCP_SEARCH_PRIMARY`   | `searxng`                                          | Engine behind `web_search`: `searxng` or `tavily`                    |
| `HOMELAB_MCP_SEARCH_BACKUP`    | `tavily` if its key is set, else `none`            | Engine behind `web_search_backup`: `searxng`, `tavily`, or `none`    |
| `HOMELAB_MCP_CLAUDE_BIN`       | `claude`                                           | Claude CLI binary used by `ask_claude`. Default looks up `claude` on `$PATH`; set to an absolute path when the binary lives outside the service's `$PATH` (e.g. `/home/me/.local/bin/claude`). |
| `HOMELAB_MCP_ROOTS`            | `$PWD`                                             | Colon-separated roots, like `$PATH`                                  |
| `HOMELAB_MCP_SKIP_DIRS`        | (see below)                                        | Comma-separated dir names skipped by `glob_files` / `grep_files` |
| `HOMELAB_MCP_MAX_FILE_BYTES`   | `10485760` (10 MB)                                 | `read_file` byte cap when called without `limit`                     |
| `HOMELAB_MCP_GLOB_LIMIT`       | `200`                                              | Result cap for `glob_files`                                          |
| `HOMELAB_MCP_GREP_LIMIT`       | `50`                                               | Default `max_results` for `grep_files`                               |
| `HOMELAB_MCP_SEARCH_LIMIT`     | `20`                                               | Result cap for `web_search`                                          |
| `HOMELAB_MCP_FETCH_MAX_CHARS`  | `50000`                                            | Character cap for `web_fetch` output                                 |
| `HOMELAB_MCP_DOC_MAX_CHARS`    | `200000`                                           | Character cap for `read_doc` output                                  |

**Roots** are the directories the server may read, glob, and grep within.
The file tools refuse paths that resolve outside this list. `HOMELAB_MCP_ROOTS`
is a colon-separated list — same convention as `$PATH`. When unset it
defaults to `$PWD`: the directory `start.sh` was launched from.

```bash
# Single root
HOMELAB_MCP_ROOTS=/home/me/projects

# Multiple roots — both are searchable; tools return absolute paths so
# results are unambiguous across roots
HOMELAB_MCP_ROOTS=/home/me/projects:/home/me/services
```

`HOMELAB_MCP_SKIP_DIRS` defaults to
`venv,.venv,node_modules,__pycache__,.git,.cache`. Setting the env var
**replaces** the default — to extend, copy the default and append.

`ask_claude` requires Claude CLI — the `claude` binary on `$PATH`. The tool
registers either way; calls fail with a clean error if `claude` is missing.

### Search engines

`web_search` and `web_search_backup` are backed by interchangeable engines
chosen in `.env` — tool names stay fixed; descriptions and parameters
follow the engine:

```bash
HOMELAB_MCP_SEARCH_PRIMARY=searxng   # searxng | tavily
HOMELAB_MCP_SEARCH_BACKUP=tavily     # searxng | tavily | none
```

Defaults: SearXNG primary; Tavily backup when its key is set, `none`
otherwise. With `tavily` as primary, the backup defaults to `searxng`.
Misconfiguration — unknown engine, same engine twice, Tavily as primary
without its key — exits at startup with a one-line reason; a backup
engine missing its key logs a warning and skips the tool.

Exa is deliberately not a role choice. With `HOMELAB_MCP_EXA_KEY` set it
registers separately as `web_search_semantic`: search by meaning rather
than keywords, with optional category restriction (companies, people,
scholarly publications, news, personal sites, financial reports).

### Telegram setup

`send_message` lets the calling LLM notify you — work finished, a finding
worth flagging, an explicit "tell me when…". One fixed recipient, chosen
by you in `.env`; the LLM cannot pick or discover chats. Messages are sent
as Telegram HTML (the LLM is told the supported tag subset in the tool
schema); if Telegram rejects the markup, the message is resent once as
plain text — delivery beats formatting. Longer than 4096 chars (Telegram's
hard limit) is truncated.

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. **Send any message to your new bot** — bots cannot initiate chats.
3. Find your chat ID:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -c \
     "import json,sys; print(json.load(sys.stdin)['result'][0]['message']['chat']['id'])"
   ```
4. Put both values in `.env` and restart the server.

## Process Manager

```bash
./start.sh start      # start (default)
./start.sh stop       # stop
./start.sh restart    # restart
./start.sh status     # state + MCP handshake
./start.sh logs       # tail log file
```

Run `./start.sh help` to see this list inline.

Optional alias:

```bash
echo 'alias mcp="~/services/homelab-mcp/start.sh"' >> ~/.bashrc
```

## Version History

| Version | Date       | Description                                                                                       |
|---------|------------|---------------------------------------------------------------------------------------------------|
| 0.1.7   | 2026-08-06 | New `send_message` tool — message the homelab owner via a Telegram bot; registered when `HOMELAB_MCP_TELEGRAM_TOKEN` + `HOMELAB_MCP_TELEGRAM_CHAT_ID` are set. Telegram HTML with plain-text fallback on parse errors; 4096-char truncation; link previews off. |
| 0.1.6   | 2026-08-02 | Search tools: per-parameter schema descriptions with when-to-use triggers (calling LLMs were sending `query` only and ignoring `time_range`/`topic`/`category`); tool descriptions slimmed accordingly. No behavior change. |
| 0.1.5   | 2026-08-02 | Configurable search engines: `HOMELAB_MCP_SEARCH_PRIMARY` / `_BACKUP` choose SearXNG or Tavily behind `web_search` / `web_search_backup`; descriptions and parameters follow the engine. New `web_search_semantic` tool — Exa neural search, registered when `HOMELAB_MCP_EXA_KEY` is set. `ask_claude` refreshed: full effort range (`low`–`max`), timeout auto-scales with model and effort, model/effort validated. |
| 0.1.4   | 2026-08-02 | Optional `web_search_backup` tool — Tavily-backed escalation search, registered when `HOMELAB_MCP_TAVILY_KEY` is set. The calling LLM decides when SearXNG results aren't good enough; `web_search` itself is unchanged. |
| 0.1.3   | 2026-05-08 | Pin MarkItDown extras (`pdf,docx,pptx,xlsx,xls,outlook`) so `read_doc` actually parses documents — was registered but inert in `0.1.2`. `start.sh logs` now follows the journal under systemd, the file otherwise. |
| 0.1.2   | 2026-05-08 | New `read_doc` tool — reads local PDF / DOCX / PPTX / XLSX via MarkItDown. Config: `HOMELAB_MCP_DOC_MAX_CHARS` (default `200000`). |
| 0.1.1   | 2026-05-08 | `HOMELAB_MCP_CLAUDE_BIN` env var lets deployments pin the Claude CLI binary path when it isn't on the service's `$PATH`. |
| 0.1.0   | 2026-05-08 | Initial release — six tools (`ask_claude`, `read_file`, `glob_files`, `grep_files`, `web_search`, `web_fetch`), multi-root, env-driven configuration |

## License

MIT
