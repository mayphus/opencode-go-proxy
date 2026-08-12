# OpenCode Go Proxy

[![CI](https://github.com/zhengsanniu/opencode-go-proxy/actions/workflows/ci.yml/badge.svg)](https://github.com/zhengsanniu/opencode-go-proxy/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Zero deps](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](#)

Use your [OpenCode Go](https://opencode.ai/docs/go) subscription in the [Codex app](https://github.com/openai/codex).

Codex expects a Responses API (`/v1/responses`). Most OpenCode Go models expose an
OpenAI-compatible Chat Completions API (`/v1/chat/completions`), which this proxy bridges in
one local process. Responses-native models such as GPT 5.6 Luna are passed through unchanged:

```text
Codex app
    │
    │  POST /v1/responses (Responses API)
    ▼
opencode-go-proxy  ←── localhost:8787, zero deps, stdlib only
    │
    │  POST /v1/chat/completions or /v1/responses
    ▼
OpenCode Go  ────── open coding models, including GPT 5.6 Luna
```

## Why

OpenCode Go is $5 for the first month, then $10/month. You get access to 13 open coding models
hosted in the US, EU, and Singapore. Codex is a great agent but doesn't speak Chat Completions
natively — it requires Responses-shaped providers. This proxy fixes that.

## Quick start

```bash
# Install and run one combined Go + Zen endpoint
uvx --from git+https://github.com/zhengsanniu/opencode-go-proxy \
  opencode-go-proxy \
  --bind 127.0.0.1 \
  --port 8787 \
  --upstream combined

# Point Codex at it (~/.codex/config.toml)
```

```toml
[model_providers.opencode]
name = "OpenCode"
base_url = "http://127.0.0.1:8787/v1"
experimental_bearer_token = "any-string-here"
wire_api = "responses"

[profiles."luna-go"]
model_provider = "opencode"
model = "go/gpt-5.6-luna"
approval_policy = "untrusted"
sandbox_mode = "workspace-write"
features = { memories = false }

[profiles."deepseek-zen"]
model_provider = "opencode"
model = "zen/deepseek-v4-flash-free"
```

```bash
# Start Codex with a profile
codex -p luna-go
```

### Automatic Codex setup

Recommended: install one provider, combined prefixed catalog, and starter profiles:

```bash
uv run opencode-go-proxy --configure-codex-combined
codex -p luna-go       # Go subscription
codex -p luna-zen      # Zen billing
codex -p deepseek-zen  # Zen free model
```

The catalog uses explicit `go/<model>` and `zen/<model>` IDs. This prevents overlapping model
names from accidentally changing the billing product. Capability fallback stays within that
prefix: Go requests fall back to `go/gpt-5.6-luna`; Zen requests fall back to
`zen/gpt-5.6-luna`.

Legacy single-product setup remains available:

To configure only the OpenCode Go GPT 5.6 Luna provider/profile and model selector entry:

```bash
uv run opencode-go-proxy --configure-codex
```

This is idempotent. It creates or preserves `~/.codex/config.toml`, writes the Luna-only
catalog to `~/.codex/model-catalogs/opencode-go.json`, and adds the `gpt-5.6-luna` profile.
Set `CODEX_HOME` to use a different Codex directory. Start it with:

```bash
codex -p gpt-5.6-luna
```

### OpenCode Zen

Zen uses the same proxy with its pay-as-you-go upstream:

```bash
uv run opencode-go-proxy --upstream zen
```

To install the Zen provider, a `gpt-5.6-luna-zen` profile, and a Desktop model catalog containing
only the models supported without extra Anthropic/Gemini protocol conversion:

```bash
uv run opencode-go-proxy --configure-codex-zen
codex -p gpt-5.6-luna-zen
```

Zen `/responses` models are passed through natively. Zen `/chat/completions` models use the
existing Responses bridge for text and local function/custom tools over both HTTP and Desktop
WebSocket sessions. When a selected model lacks image input or a hosted Responses tool, the whole
request moves to `gpt-5.6-luna` so native tool items and citations survive. Override this with
`OPENCODE_CAPABILITY_MODEL`.

The automatic capability router covers image input, web search, file search, computer use, code
interpreter, image generation, MCP, tool search, hosted shell, and skills. It does not retry
arbitrary upstream errors or silently route ordinary text turns to another model.

### Web dashboard

Open the proxy root in a browser, for example `http://pb62.local:32096/`. The embedded,
zero-dependency page shows Go/Zen health, available models, an interactive capability-route
preview, and copyable Desktop configuration. Choose a model and capability to see whether the
request stays native, uses the minimal Chat bridge, or moves intact to the same-product Luna.
Its status checks call only `/health` and `/models`; loading or refreshing the page never
sends a prompt or spends model tokens.

In combined mode the dashboard lists one service and prefixed Go/Zen models. Its routing preview
shows native passthrough, Chat bridging, vision bridging, and same-product Luna fallback.

## Available models

All OpenCode Go models work through this proxy. GPT 5.6 Luna uses native Responses passthrough;
the defaults are DeepSeek V4 Flash
(cheapest general-purpose) and MiMo V2.5 (cheapest vision, used for image captioning).
Switch to whatever you want — just change the model in your Codex profile.

| Model | Slug | Best for | Requests/mo on Go |
|-------|------|----------|-------------------|
| DeepSeek V4 Flash | `deepseek-v4-flash` | Everyday coding (default) | ~158k |
| DeepSeek V4 Pro | `deepseek-v4-pro` | Complex reasoning | ~17k |
| MiMo V2.5 | `mimo-v2.5` | Vision/image captioning (default) | ~150k |
| MiMo V2.5 Pro | `mimo-v2.5-pro` | Vision + reasoning | ~16k |
| GLM-5.2 | `glm-5.2` | Frontier open model | ~4.3k |
| GLM-5.1 | `glm-5.1` | Previous-gen GLM | ~4.3k |
| Kimi K2.7 Code | `kimi-k2.7-code` | Code-specialized | ~9.3k |
| Kimi K2.6 | `kimi-k2.6` | General-purpose | ~5.8k |
| MiniMax M3 | `minimax-m3` | MiniMax flagship | ~16k |
| MiniMax M2.7 | `minimax-m2.7` | Previous-gen MiniMax | ~17k |
| Qwen3.7 Max | `qwen3.7-max` | Strong reasoning | ~4.8k |
| Qwen3.7 Plus | `qwen3.7-plus` | Mid-tier value | ~22k |
| Qwen3.6 Plus | `qwen3.6-plus` | Previous-gen Qwen | ~16k |

Request counts are estimates from [OpenCode Go docs](https://opencode.ai/docs/go) based on
typical usage patterns. Cheaper models = more requests per month.

### Switching models

Just create another profile and use `codex -p <profile-name>`:

```toml
[profiles.deepseek-v4-pro]
model_provider = "opencode-go"
model = "deepseek-v4-pro"
model_context_window = 1000000
approval_policy = "untrusted"
sandbox_mode = "workspace-write"
features = { memories = false }

[profiles.glm-5.2]
model_provider = "opencode-go"
model = "glm-5.2"
model_context_window = 272000
approval_policy = "untrusted"
sandbox_mode = "workspace-write"
features = { memories = false }

[profiles.kimi-k2.7-code]
model_provider = "opencode-go"
model = "kimi-k2.7-code"
model_context_window = 272000
approval_policy = "untrusted"
sandbox_mode = "workspace-write"
features = { memories = false }
```

```bash
codex -p deepseek-v4-pro
codex -p glm-5.2
codex -p kimi-k2.7-code
```

### How the default model is chosen

The proxy picks the upstream model based on what Codex sends:

1. If the model slug is in the [alias map](src/opencode_go_proxy/protocol.py), it's mapped
   (e.g. `gpt-5.5` → `deepseek-v4-pro`).
2. If the model slug is a known OpenCode Go model (from the catalog), it's used as-is.
3. Otherwise, it falls back to `deepseek-v4-flash`.

On OpenCode Go, images sent to Chat Completions models route through MiMo V2.5 for image
captioning, then return to the configured model. Responses-native models with native
image/search support, including GPT 5.6 Luna, bypass this fallback and keep the original image
and hosted-tool payload. On Zen, requests needing a missing native capability move intact to
Luna instead. Override the legacy Go vision model with `CODEX_IMAGE_MODEL`.

## API key

The proxy resolves your OpenCode Go API key in this order:

1. `$OPENCODE_GO_API_KEY` environment variable
2. macOS keychain entry `opencode-go-api-key` (override with `CODEX_KEYCHAIN_SERVICE`; macOS only)

```bash
# Option 1: env var (works everywhere)
export OPENCODE_GO_API_KEY="your-key-here"

# Option 2: macOS keychain (macOS only)
security add-generic-password -a "$USER" -s opencode-go-api-key -w
```

Get your API key from [OpenCode Zen](https://opencode.ai/zen) after subscribing to Go.

## Recommended: lazycodex

[lazycodex](https://github.com/code-yeongyu/oh-my-openagent) is a Codex plugin that adds
multi-model orchestration, parallel background agents, and LSP/AST-aware tools. It pairs
naturally with this proxy — you get OpenCode Go's models as the backend and lazycodex's
agent harness on top.

```bash
npm install -g lazycodex-ai
```

See the [lazycodex docs](https://github.com/code-yeongyu/oh-my-openagent) for setup.

## Features

- Responses `input` to chat `messages` translation
- `instructions` and `developer` roles mapped to system messages
- Function tool schema passthrough
- Custom/freeform tool adaptation (Codex `apply_patch` works)
- Reasoning content replay across tool-call turns
- Real-time SSE streaming (not synthesized)
- Responses WebSocket mode for Codex Desktop, bridged to upstream Responses SSE
- Go image captioning via MiMo V2.5 when tools are present (override with `CODEX_IMAGE_MODEL`)
- Zen capability-aware routing to native Luna without translating hosted tool calls
- SSRF protection on image URLs (`data:image/` and `https://` only)
- Configurable body cap, bind address guard, keychain credential resolution
- Local health and model-list endpoints
- Token-free embedded Go/Zen dashboard and Desktop setup generator
- Native Luna server-side compaction before long chats reach the context limit
- Automatic native Luna web search when Desktop omits the hosted search tool
- A live capability verifier for text, structured output, vision, search, and tool loops

Luna always receives its native `web_search` tool unless the client already supplied
`web_search` or `web_search_preview`. Browser tools remain available for interactive browsing.
Set `OPENCODE_GO_NATIVE_SEARCH=0` to disable automatic native search injection.

### Verify Luna capabilities

Run the same end-to-end checks after an upgrade or deployment:

```bash
uv run --no-editable opencode-go-verify --base-url http://127.0.0.1:8787/v1
```

For the PB62 LAN test deployment (intentionally no client token):

```bash
# Combined endpoint. This spends Go tokens; run only when a live check is needed.
uv run --no-editable opencode-go-verify \
  --base-url http://pb62.local:32096/v1 \
  --model go/gpt-5.6-luna
```

Luna requests automatically ask the upstream Responses API to compact at 800,000 tokens.
Override with `OPENCODE_GO_COMPACT_THRESHOLD`, or set it to `0` to disable. When a native
compaction item returns in later history, the proxy safely drops only items before that
canonical compacted state. OpenCode Go currently does not offer the separate
`/responses/compact` endpoint, so the proxy reports that limitation instead of returning a
normal response with the wrong shape.

## Install

### From source (no package manager)

```bash
uvx --from git+https://github.com/zhengsanniu/opencode-go-proxy opencode-go-proxy --help
```

### From a development checkout

```bash
uv sync --no-editable --reinstall-package opencode-go-proxy
uv run opencode-go-proxy --help
```

### macOS (launchd)

A launchd plist is included at `contrib/launchd/com.opencode-go.proxy.plist`.
Copy it to `~/Library/LaunchAgents/` and load:

```bash
mkdir -p ~/Library/LaunchAgents ~/.codex/logs
cp contrib/launchd/com.opencode-go.proxy.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.opencode-go.proxy.plist
```

The proxy is designed for launchd's `KeepAlive` — it restarts on crash and
starts at login. Logs go to `~/.codex/logs/opencode-go-proxy.{log,err}`.

### Windows (background + startup)

Install a per-user Task Scheduler entry that starts the proxy hidden at login and
restarts it after crashes:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\contrib\windows\opencode-go-proxy-task.ps1 -Install
```

The task uses the current checkout and starts the proxy on `127.0.0.1:8787`.
Make sure `OPENCODE_GO_API_KEY` is set as a persistent User environment variable,
not only in the current terminal session. Logs are written to
`$HOME\.codex\logs\opencode-go-proxy.log` and
`$HOME\.codex\logs\opencode-go-proxy.err.log`.

```powershell
.\contrib\windows\opencode-go-proxy-task.ps1 -Status
.\contrib\windows\opencode-go-proxy-task.ps1 -Uninstall
```

### Windows notification-area app

The zero-dependency tray app shows live status and provides Start, Stop, Open Logs, and
Copy API URL actions. Its installer also installs the background proxy task:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\contrib\windows\opencode-go-proxy-tray.ps1 -Install
```

Double-click the tray icon to start or stop the proxy. `Exit Tray` leaves the background
proxy running. To remove only the tray app:

```powershell
.\contrib\windows\opencode-go-proxy-tray.ps1 -Uninstall
```

### Container and Kubernetes

The included `Containerfile` runs the stdlib-only proxy directly from source as an
unprivileged user. `deploy/kubernetes.yaml` is a hardened single-replica combined Go + Zen
example with health probes, resource limits, a LAN-scoped NetworkPolicy, and NodePort `32096`.

Create `opencode-go-proxy-credentials` separately with the `upstream-api-key` key; never
put its value in the manifest. The example is restricted to `192.168.36.0/24` by its
NetworkPolicy and intentionally leaves client authentication disabled for local testing.
For any wider network, also configure a separate random `client-token` and expose it as
`OPENCODE_GO_PROXY_CLIENT_TOKEN`.

## Configuration

All flags have environment variable defaults:

| Flag | Env var | Default |
|------|---------|---------|
| `--bind` | `OPENCODE_GO_PROXY_BIND` | `127.0.0.1` |
| `--port` | `OPENCODE_GO_PROXY_PORT` | `8787` |
| `--upstream` | `OPENCODE_UPSTREAM` | `go` (`combined` recommended for both products) |
| `--chat-base-url` | `CHAT_COMPLETIONS_BASE_URL` | selected by `--upstream` |
| `--api-key-env` | `OPENCODE_GO_PROXY_API_KEY_ENV` | `OPENCODE_GO_API_KEY` |
| `--client-token-env` | `OPENCODE_GO_PROXY_CLIENT_TOKEN_ENV` | `OPENCODE_GO_PROXY_CLIENT_TOKEN` |
| `--timeout-sec` | `OPENCODE_GO_PROXY_TIMEOUT_SEC` | `180` |
| `--max-body-mb` | `OPENCODE_GO_PROXY_MAX_BODY_MB` | `20` |

The proxy accepts both `/responses` and `/v1/responses`.

When `OPENCODE_GO_PROXY_CLIENT_TOKEN` is set, Responses HTTP and WebSocket requests must
send that value as a bearer token. Health and model-list endpoints remain available for
local probes. Leave it unset for the normal localhost-only setup.

**One HTTP port only.** The proxy binds a single listener: `OPENCODE_GO_PROXY_PORT`
(default `8787`). There is no admin port, control channel, or secondary service. If
something else already listens on the port, the proxy fails to bind — check with
`lsof -nP -iTCP:8787 -sTCP:LISTEN` before starting a second instance (for example, do
not run the menu bar app's proxy and the launchd agent at the same time).

**Short provider name.** The long "opencode go/" label in the Codex model picker comes
from the provider config in `~/.codex/config.toml`, not from this proxy. Shorten it by
editing the provider `name`:

```toml
[model_providers.opencode-go]
name = "Go"  # shows as "Go" instead of "opencode go/"
```

The reference catalog's per-model `display_name` values are already short
("DeepSeek V4 Flash", "Kimi K2.7 Code", etc).

## Model catalog

Without a catalog entry, Codex prints a model metadata warning every turn. A reference catalog
with all OpenCode Go models is included at `contrib/opencode-go-catalog.json`. Copy it to the
proxy's default catalog path so `/models` works out of the box:

```bash
mkdir -p ~/.codex/model-catalogs
cp contrib/opencode-go-catalog.json ~/.codex/model-catalogs/opencode-go.json
```

```toml
model_catalog_json = "/home/you/.codex/model-catalogs/opencode-go.json"
```

The catalog ships with the `ModelsCache` wrapper (`fetched_at`/`etag`/`client_version`/`models`).
Codex 0.142+ desktop app requires all four top-level fields — a bare `{"models": [...]}` catalog
causes the model picker to fall back to "Custom" instead of showing the full list. The CLI
(`codex debug models`) tolerates the bare format, so this only surfaces in the desktop app.

If you want Codex's full `base_instructions` for each model, copy your Codex installation's
bundled `models.json` and append the OpenCode Go entries from the reference catalog (keep the
`ModelsCache` wrapper).

## Trace

Every request emits compact JSON lines on stderr. Important events:

- `server.start`
- `request.received`
- `request.converted`
- `credential.source`
- `upstream.start`
- `upstream.done`
- `response.converted`
- `request.failed`

## Troubleshooting

**Model metadata warning every turn**
Set `model_catalog_json` in Codex config and copy the reference catalog:
`cp contrib/opencode-go-catalog.json ~/.codex/model-catalogs/opencode-go.json`

**Connection refused on localhost:8787**
Proxy isn't running. Start it: `opencode-go-proxy` or check `launchctl list | grep opencode`.

**Port already in use**
Only one proxy instance can bind 8787. Confirm what is listening:
`lsof -nP -iTCP:8787 -sTCP:LISTEN`. If a stale instance holds the port, stop it before
starting another.

**API key not found**
Set `OPENCODE_GO_API_KEY` env var or add to macOS keychain:
`security add-generic-password -a "$USER" -s opencode-go-api-key -w`

**Upstream rate limited (429)**
OpenCode Go has 5-hour/weekly/monthly usage limits. Switch to a cheaper model (DeepSeek V4 Flash or MiMo V2.5) to stretch your quota. See [usage limits](https://opencode.ai/docs/go#usage-limits).

**Streaming not working**
Codex sends `stream: true` — the proxy handles this. If you see no SSE events, check stderr trace for `upstream.error` or `upstream.network_error`.

**Codex says "model is not supported when using ChatGPT account"**
You used `codex -m deepseek-v4-flash` instead of `codex -p deepseek-v4-flash`. The `-m` flag only changes the model name, not the provider. Use `-p` to select a profile.

## Development

```bash
uv run --no-sync python -m pytest tests -v
uvx ruff check
uv build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT. See [LICENSE](LICENSE).
