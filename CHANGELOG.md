# Changelog

## [Unreleased]

### Added

- Capability evidence reports for the token-free dashboard, with verified, rejected, and
  untested states from opt-in live verifier runs.
- Zero-dependency read-only web dashboard for Go/Zen health, model discovery, capability routing,
  and copyable Desktop setup; its probes use only `/health` and `/models` and spend no tokens.
- Combined Go + Zen mode on one port with separate `/zen/go/v1` and `/zen/v1` provider
  endpoints, ordinary unprefixed model IDs, one shared Codex catalog, and same-product Luna
  capability fallback.
- OpenCode Zen mode with compatible Responses and Chat Completions model discovery, native
  passthrough where available, a separate Codex provider/catalog installer, and a PB62 test
  service on NodePort `32096`.
- Capability-aware routing that preserves the original Responses request and moves it to GPT
  5.6 Luna when the selected Zen model lacks image, search, code interpreter, image generation,
  MCP, hosted shell, skills, computer use, file search, or compaction support.
- Native macOS menu bar app in `macos/MenuBarApp` (Swift/AppKit, SwiftPM): short status
  icon, live health check, start/stop of the proxy as a child process, open logs,
  reveal log file, copy port. Build with `swift build` in `macos/MenuBarApp` (macOS 13+).
- Native Windows notification-area controller in `contrib/windows`, backed by the existing
  crash-restarting scheduled task and using only built-in PowerShell/WinForms.
- Minimal rootless `Containerfile` and hardened Kubernetes test manifest.
- Optional client bearer-token authentication for non-localhost deployments.

### Changed

- Luna Desktop requests now preserve `prompt_cache_options`, use stateless
  `reasoning.context = "all_turns"`, and advertise verbosity and automatic skill instructions.
- Combined setup writes and configures a shared unprefixed ModelsCache catalog at
  `~/.codex/model-catalogs/opencode.json` for both endpoint-routed providers.
- PB62 now exposes one combined service on NodePort `32096` instead of separate Go/Zen ports.
- README: document that the proxy exposes a single HTTP port (`OPENCODE_GO_PROXY_PORT`,
  default 8787) with no admin/control channel, how to verify what is listening
  (`lsof -nP -iTCP:8787 -sTCP:LISTEN`), and how to shorten the Codex provider label
  (edit `[model_providers.opencode-go] name` in `~/.codex/config.toml`).
- GPT 5.6 Luna keeps its native Responses transport for text, hosted web search, image input,
  tools, streaming, and Desktop WebSocket sessions. Its catalog now advertises the official
  1.05M context window and full reasoning-effort range.
- Repository-owned install/service links now target `zhengsanniu/opencode-go-proxy`.

### Fixed

- Reference catalog `contrib/opencode-go-catalog.json` now ships with the `ModelsCache` wrapper
  (`fetched_at`/`etag`/`client_version`/`models`). Codex 0.142+ desktop app requires all four
  top-level fields — the previous bare `{"models": [...]}` caused the model picker to fall back
  to "Custom" instead of showing the full list. The CLI tolerated the bare format, so this only
  surfaced in the desktop app.
- SIGTERM graceful shutdown: `serve_forever` now runs on a background thread so the signal
  handler's `server.shutdown()` no longer deadlocks on the main thread, leaving the process
  unkillable via SIGTERM (launchd/systemd stop, menu bar Stop).
- Abrupt WebSocket disconnects no longer emit a server traceback.
- Current `uv` builds use the matching build backend.
- Desktop WebSocket sessions now operate statelessly for OpenCode Go: unsupported
  `previous_response_id` state is removed, `store` is disabled, and supplied reasoning
  items are replayed with the full history.
- Empty Desktop WebSocket creates complete locally instead of producing upstream HTTP 400,
  and successful upstream sessions emit an explicit completion trace.
- Desktop `additional_tools` definitions are extracted on both HTTP streaming and WebSocket
  transports and forwarded as native Responses tools, allowing Luna to invoke client-side
  browser and other app tools.
- Install and CI workflows use non-editable wheel installs to avoid malformed editable-path
  files in affected `uv`/macOS combinations.

## [0.1.2] - 2026-06-21

Bug fixes + removed AUR packaging.

### Fixed

- `call_upstream_chat` now catches `json.JSONDecodeError` — invalid JSON from upstream returns 502 instead of crashing
- Streaming crash: if `handle_streaming_request` raised after SSE headers sent, sends `response.error` SSE event instead of corrupted HTTP response
- Streaming + missing API key: `resolve_api_key` moved before `response.created` so error event reaches client
- README launchd path: `~/Library/.codex/logs` → `~/.codex/logs` (matches plist)
- LICENSE copyright year 2025 → 2026

### Removed

- AUR package (`aur/opencode-go-proxy-git/`) — not launching on AUR
- PyPI — not launching on PyPI; `uvx --from git+...` is the install path

## [0.1.1] - 2026-06-21

Graceful shutdown + launchd service file.

### Added

- SIGTERM handler for graceful shutdown on `launchctl bootout` / `systemctl stop`
- launchd plist at `contrib/launchd/com.opencode-go.proxy.plist`
- README: launchd setup instructions with copy + bootstrap commands

## [0.1.0] - 2026-06-21

Initial public release.

### Added

- Responses `input` to chat `messages` translation
- `instructions` and `developer` roles mapped to system messages
- Function tool schema passthrough
- Custom/freeform tool adaptation (Codex `apply_patch` works)
- `reasoning_content` replay across tool-call turns
- Real-time SSE streaming
- Image captioning via MiMo V2.5 when tools are present
- SSRF protection on image URLs (`data:image/` and `https://` only)
- Configurable body cap, bind address guard
- macOS keychain credential resolution
- Local health and model-list endpoints
- Reference model catalog with all 13 OpenCode Go models
- systemd user service at `contrib/systemd/`
- 41 tests (unit + integration) covering protocol, credentials, HTTP round-trip, alias map, tool calls, streaming tool calls, streaming error handling, streaming crash recovery, invalid upstream JSON, SSRF, and image captioning

### Security

- SSRF validation on image URLs
- Non-negative Content-Length validation
- Generic error messages to client (full bodies only in trace logs)
- No path reflection in 404 responses
- Bind address guard warns on non-localhost
