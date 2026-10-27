"""Runtime configuration for the Gen Studio web console.

Everything is resolved from environment variables with repo-relative
defaults, so the app can be run from a checkout without extra setup and
tests can point it at a throw-away data directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Gen Studio"
APP_SUBTITLE = "生成模型控制台"

#: Name of the session cookie. Kept simple and HttpOnly.
SESSION_COOKIE = "gen_studio_session"

#: Default credentials required by the specification.
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Settings:
    """Immutable settings object, safe to share between requests."""

    repo_root: Path
    data_dir: Path
    nvidia_smi: str = "nvidia-smi"
    session_ttl_seconds: int = 12 * 60 * 60
    health_timeout_seconds: float = 2.0
    #: Start each model in its own transient systemd user unit. Tests that
    #: construct Settings directly default to the old in-process launcher.
    model_units_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root).resolve())
        object.__setattr__(self, "data_dir", Path(self.data_dir).resolve())

    @property
    def auth_file(self) -> Path:
        return self.data_dir / "auth.json"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def model_log_path(self, model_id: str) -> Path:
        return self.log_dir / f"{model_id}.log"

    @classmethod
    def from_env(cls) -> "Settings":
        default_root = Path(__file__).resolve().parents[2]
        repo_root = Path(os.environ.get("GEN_STUDIO_REPO_ROOT", default_root))
        data_dir = Path(os.environ.get("GEN_STUDIO_DATA_DIR", repo_root / "web" / "data"))
        return cls(
            repo_root=repo_root,
            data_dir=data_dir,
            nvidia_smi=os.environ.get("GEN_STUDIO_NVIDIA_SMI", "nvidia-smi"),
            session_ttl_seconds=int(os.environ.get("GEN_STUDIO_SESSION_TTL", 12 * 60 * 60)),
            model_units_enabled=_env_bool("GEN_STUDIO_MODEL_UNITS", True),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # 认证文件、进程状态与会话日志仅当前用户可读。
        for path in (self.data_dir, self.log_dir):
            try:
                path.chmod(0o700)
            except OSError:  # 只读挂载等场景不阻断启动
                pass
