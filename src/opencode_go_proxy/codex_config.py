from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

MODEL_SLUG = "gpt-5.6-luna"
PROVIDER_NAME = "opencode-go"
CATALOG_FILENAME = "opencode-go.json"

CATALOG: dict[str, Any] = {
    "fetched_at": "2026-08-12T00:00:00.000000Z",
    "etag": "W/\"opencode-go-gpt-5.6-luna-v1\"",
    "client_version": "0.147.0",
    "models": [
        {
            "slug": MODEL_SLUG,
            "display_name": "GPT 5.6 Luna",
            "description": "Responses-native GPT model with image input and web search.",
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": effort} for effort in ("none", "low", "medium", "high", "xhigh", "max")
            ],
            "shell_type": "shell_command",
            "visibility": "list",
            "supported_in_api": True,
            "priority": 100,
            "additional_speed_tiers": [],
            "service_tiers": [],
            "availability_nux": None,
            "upgrade": None,
            "model_messages": {
                "instructions_template": "You are Codex, an agent based on GPT-5. You and the user share one workspace, and your job is to collaborate with them until their goal is genuinely handled.",
                "instructions_variables": None,
                "approvals": None,
                "collaboration_modes": None,
                "auto_review": None,
                "permissions": None,
                "token_budget": {
                    "reminder_threshold_tokens": 6144,
                    "auto_compact_token_limit": 1000000,
                },
            },
            "include_skills_usage_instructions": False,
            "include_plugin_usage_instructions": True,
            "supports_reasoning_summaries": True,
            "default_reasoning_summary": "none",
            "support_verbosity": False,
            "default_verbosity": "low",
            "apply_patch_tool_type": "freeform",
            "web_search_tool_type": "text_and_image",
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "supports_parallel_tool_calls": True,
            "supports_image_detail_original": True,
            "context_window": 1050000,
            "max_context_window": 1050000,
            "effective_context_window_percent": 95,
            "experimental_supported_tools": [],
            "input_modalities": ["text", "image"],
            "supports_search_tool": True,
            "use_responses_lite": True,
            "tool_mode": "code_mode_only",
            "multi_agent_version": "v2",
            "auto_compact_token_limit": 1000000,
        }
    ],
}


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    header = f"[{section}]"
    for index, line in enumerate(lines):
        if line.strip() != header:
            continue
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            if re.match(r"^\s*\[[^]]+\]\s*$", lines[next_index]):
                end = next_index
                break
        return index, end
    return None


def _ensure_section(lines: list[str], section: str, entries: list[str]) -> None:
    bounds = _section_bounds(lines, section)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines.extend(entries)
        return

    _start, end = bounds
    existing = "\n".join(lines[_start:end])
    missing = [entry for entry in entries if entry.split("=", 1)[0].strip() not in existing]
    if missing:
        lines[end:end] = missing


def _ensure_top_level_key(lines: list[str], key: str, value: str) -> None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f'{key} = {json.dumps(value)}'
            return
    insertion = 0
    while insertion < len(lines) and not re.match(r"^\s*\[[^]]+\]\s*$", lines[insertion]):
        insertion += 1
    lines[insertion:insertion] = [f'{key} = {json.dumps(value)}', ""]


def configure_codex() -> tuple[Path, Path]:
    """Create the Luna provider/profile and model selector entry idempotently."""
    codex_home = _codex_home()
    codex_home.mkdir(parents=True, exist_ok=True)
    config_path = codex_home / "config.toml"
    catalog_path = codex_home / "model-catalogs" / CATALOG_FILENAME
    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
    _ensure_top_level_key(lines, "model_catalog_json", str(catalog_path))
    _ensure_section(
        lines,
        f"model_providers.{PROVIDER_NAME}",
        [
            'name = "OpenCode Go"',
            'base_url = "http://127.0.0.1:8787/v1"',
            'experimental_bearer_token = "local-proxy"',
            'wire_api = "responses"',
        ],
    )
    profile_section = f'profiles."{MODEL_SLUG}"'
    legacy_profile_section = f"profiles.{MODEL_SLUG}"
    legacy_bounds = _section_bounds(lines, legacy_profile_section)
    if legacy_bounds is not None and _section_bounds(lines, profile_section) is None:
        lines[legacy_bounds[0]] = f"[{profile_section}]"
    _ensure_section(
        lines,
        profile_section,
        [
            f'model_provider = "{PROVIDER_NAME}"',
            f'model = "{MODEL_SLUG}"',
            "model_context_window = 1050000",
            'approval_policy = "untrusted"',
            'sandbox_mode = "workspace-write"',
            "features = { memories = false }",
        ],
    )
    config_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    catalog_path.write_text(json.dumps(CATALOG, indent=2) + "\n", encoding="utf-8")
    return config_path, catalog_path
