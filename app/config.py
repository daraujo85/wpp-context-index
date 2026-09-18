"""Project settings: reads project .env plus the machine-wide WhatsApp
allowlist files (~/.claude/secrets/whatsapp.env, whatsapp-lids.json).

No pydantic-settings/dotenv installed in this environment and no
dependency file exists yet, so this uses stdlib-only env parsing
(dataclass + a tiny .env reader) instead of adding a new dependency.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*(#.*)?$")


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser: skips blanks/comments, strips quotes."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if val and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key] = val
    return values


def _load_dotenv_into_environ(path: Path) -> None:
    for key, val in _parse_env_file(path).items():
        os.environ.setdefault(key, val)


_load_dotenv_into_environ(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class Settings:
    whatsapp_env_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "WHATSAPP_ENV_PATH", "~/.claude/secrets/whatsapp.env"
            )
        ).expanduser()
    )
    whatsapp_lids_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "WHATSAPP_LIDS_PATH", "~/.claude/secrets/whatsapp-lids.json"
            )
        ).expanduser()
    )
    vision_model: str = field(
        default_factory=lambda: os.environ.get("VISION_MODEL", "gemma4:12b")
    )
    embedding_model: str = field(
        default_factory=lambda: os.environ.get(
            "EMBEDDING_MODEL", "mxbai-embed-large"
        )
    )
    ollama_base_url: str = field(
        default_factory=lambda: os.environ.get(
            "OLLAMA_BASE_URL", "http://192.168.31.231:11434"
        )
    )
    qdrant_url: str = field(
        default_factory=lambda: os.environ.get(
            "QDRANT_URL", "http://qdrant:6333"
        )
    )
    ingest_concurrency: int = field(
        default_factory=lambda: int(os.environ.get("INGEST_CONCURRENCY", "1"))
    )
    media_concurrency: int = field(
        default_factory=lambda: int(os.environ.get("MEDIA_CONCURRENCY", "1"))
    )
    conversation_gap_minutes: int = field(
        default_factory=lambda: int(
            os.environ.get("CONVERSATION_GAP_MINUTES", "20")
        )
    )

    def sources(self) -> list[dict]:
        """Allowlisted WhatsApp sources from whatsapp.env: WPP_CONTACT_* (DMs)
        and WPP_GROUP_* (groups), keyed by alias (lowercased suffix)."""
        env_vars = _parse_env_file(self.whatsapp_env_path)
        result = []
        for key, val in env_vars.items():
            if key.startswith("WPP_CONTACT_"):
                alias, is_group = key[len("WPP_CONTACT_"):].lower(), False
            elif key.startswith("WPP_GROUP_"):
                alias, is_group = key[len("WPP_GROUP_"):].lower(), True
            else:
                continue
            result.append({"alias": alias, "jid": val, "is_group": is_group})
        return result

    def lids(self) -> dict[str, str]:
        """@lid numeric id -> alias, from whatsapp-lids.json."""
        if not self.whatsapp_lids_path.exists():
            return {}
        data = json.loads(self.whatsapp_lids_path.read_text())
        return {k: v for k, v in data.items() if not k.startswith("_")}
