# Homelab MCP 🛠

An MCP tool server for homelabs. Six tools, three dependencies, one file.

Designed by SK. Built by Claude.

## Overview

A FastMCP HTTP server exposing six tools to MCP clients (Claude Code, IDE
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
MCP client ──► :1603 (homelab-mcp) ──► Claude CLI / SearXNG / filesystem
```

FastMCP over streamable HTTP. Each tool is a Python function decorated with
`@mcp.tool()`.

## Tools

| Tool         | Purpose                                                |
|--------------|--------------------------------------------------------|
| `ask_claude` | Delegate to Claude (opus or sonnet, medium or max)     |
| `read_file`  | Read a UTF-8 text file inside a configured root        |
| `glob_files` | Find files by glob pattern across roots                |
| `grep_files` | Case-insensitive regex search across roots             |
| `web_search` | SearXNG-aggregated public web search                   |
| `web_fetch`  | Fetch a URL (HTML, PDF, DOCX) and convert to Markdown  |

## Configuration

All via environment variables. Drop them in a `.env` next to `start.sh`;
`start.sh` sources it on launch.

| Variable                       | Default                                            | Purpose                                                              |
|--------------------------------|----------------------------------------------------|----------------------------------------------------------------------|
| `HOMELAB_MCP_HOST`             | `0.0.0.0`                                          | Listen address                                                       |
| `HOMELAB_MCP_PORT`             | `1603`                                             | Listen port                                                          |
| `HOMELAB_MCP_SEARXNG`          | `http://localhost:8080`                            | SearXNG endpoint for `web_search`                                    |
| `HOMELAB_MCP_CLAUDE_BIN`       | `claude`                                           | Claude CLI binary used by `ask_claude`. Default looks up `claude` on `$PATH`; set to an absolute path when the binary lives outside the service's `$PATH` (e.g. `/home/me/.local/bin/claude`). |
| `HOMELAB_MCP_ROOTS`            | `$PWD`                                             | Colon-separated roots, like `$PATH`                                  |
| `HOMELAB_MCP_SKIP_DIRS`        | (see below)                                        | Comma-separated dir names skipped by `glob_files` / `grep_files` |
| `HOMELAB_MCP_MAX_FILE_BYTES`   | `10485760` (10 MB)                                 | `read_file` byte cap when called without `limit`                     |
| `HOMELAB_MCP_GLOB_LIMIT`       | `200`                                              | Result cap for `glob_files`                                          |
| `HOMELAB_MCP_GREP_LIMIT`       | `50`                                               | Default `max_results` for `grep_files`                               |
| `HOMELAB_MCP_SEARCH_LIMIT`     | `20`                                               | Result cap for `web_search`                                          |
| `HOMELAB_MCP_FETCH_MAX_CHARS`  | `50000`                                            | Character cap for `web_fetch` output                                 |

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
| 0.1.1   | 2026-05-08 | `HOMELAB_MCP_CLAUDE_BIN` env var lets deployments pin the Claude CLI binary path when it isn't on the service's `$PATH`. |
| 0.1.0   | 2026-05-08 | Initial release — six tools (`ask_claude`, `read_file`, `glob_files`, `grep_files`, `web_search`, `web_fetch`), multi-root, env-driven configuration |

## License

MIT
