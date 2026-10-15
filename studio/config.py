"""Configuration for the Nature AI Studio dashboard.

Everything is overridable through environment variables (or the repo-root
``.env`` file) so the same code runs unchanged on a Linux VPS.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

STUDIO_DIR = Path(__file__).resolve().parent
REPO_ROOT = STUDIO_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader — does not clobber real env vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _resolve_hyperframes() -> list[str]:
    """Return the argv prefix used to invoke the HyperFrames CLI."""
    override = os.getenv("STUDIO_HYPERFRAMES", "").strip()
    if override:
        return shlex.split(override, posix=(os.name != "nt"))

    # Prefer a globally installed CLI (best for a 24/7 VPS box).
    if shutil.which("hyperframes"):
        return ["hyperframes"]
    # Fall back to npx, which resolves the published package.
    return ["npx", "--yes", "hyperframes"]


class Settings:
    # ── LLM (KiloCode gateway, same model the CLI agent uses) ──
    api_key: str = os.getenv("KILOCODE_API_KEY", "").strip()
    base_url: str = os.getenv("KILOCODE_BASE_URL", "https://api.kilo.ai/api/gateway").strip()
    model: str = os.getenv("KILOCODE_MODEL", "stepfun/step-5-preview-free").strip()
    max_tokens: int = _env_int("MAX_TOKENS", 32000)
    temperature: float = _env_float("STUDIO_TEMPERATURE", 0.7)
    llm_max_attempts: int = _env_int("STUDIO_LLM_ATTEMPTS", 4)
    # Seconds x attempt number when the gateway answers 429 (quota window).
    rate_limit_backoff: int = _env_int("STUDIO_RATE_LIMIT_BACKOFF", 90)

    # ── Storage (reuses the repo-root project folders + renders/) ──
    projects_dir: Path = Path(os.getenv("STUDIO_PROJECTS_DIR", str(REPO_ROOT))).resolve()
    renders_dir: Path = Path(os.getenv("STUDIO_RENDERS_DIR", str(REPO_ROOT / "renders"))).resolve()
    db_path: Path = Path(os.getenv("STUDIO_DB", str(STUDIO_DIR / "studio.db"))).resolve()

    # ── Server ──
    host: str = os.getenv("STUDIO_HOST", "127.0.0.1")
    port: int = _env_int("STUDIO_PORT", 8787)
    password: str = os.getenv("STUDIO_PASSWORD", "").strip()
    session_secret: str = os.getenv(
        "STUDIO_SESSION_SECRET", os.getenv("KILOCODE_API_KEY", "nature-ai-studio-dev-secret")
    ).strip()
    public_url_file: Path = Path(os.getenv("STUDIO_PUBLIC_URL_FILE", str(STUDIO_DIR / "public_url.txt")))

    # ── Pipeline ──
    hyperframes: list[str] = _resolve_hyperframes()
    lint_attempts: int = _env_int("STUDIO_LINT_ATTEMPTS", 3)
    render_timeout_s: int = _env_int("STUDIO_RENDER_TIMEOUT", 3600)
    max_upload_mb: int = _env_int("STUDIO_MAX_UPLOAD_MB", 40)

    @classmethod
    def ensure_dirs(cls) -> None:
        cls.projects_dir.mkdir(parents=True, exist_ok=True)
        cls.renders_dir.mkdir(parents=True, exist_ok=True)
        cls.db_path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def public_url(cls) -> str:
        try:
            if cls.public_url_file.exists():
                # utf-8-sig: Windows PowerShell writes the URL file with a BOM.
                return cls.public_url_file.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
        except OSError:
            pass
        return ""


settings = Settings()