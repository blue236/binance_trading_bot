from __future__ import annotations

from pathlib import Path
import yaml

from .models import UIConfig


class ConfigManager:
    def __init__(self, path: str = "web_config.yaml"):
        self.path = Path(path)

    def load(self) -> UIConfig:
        if not self.path.exists():
            cfg = UIConfig()
            self.save(cfg)
            return cfg
        raw = yaml.safe_load(self.path.read_text()) or {}
        return UIConfig(**raw)

    def save(self, cfg: UIConfig) -> None:
        self.path.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
