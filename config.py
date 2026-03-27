# config.py
"""Carrega e salva configurações do jogo em config.json."""
import json
import os

CONFIG_FILE = "config.json"

DEFAULTS = {
    "scale":         1.0,
    "music_volume":  0.4,
    "sfx_volume":    1.0,
    "music_enabled": True,
    "sfx_enabled":   True,
    # hotbar: {"slots": [skill_id|null, ...], "keybinds": [pygame.K_* int, ...]}
    "hotbar": None,
}


def load() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        return {**DEFAULTS, **data}
    return dict(DEFAULTS)


def save(data: dict) -> None:
    """Salva `data` mesclando com as chaves já existentes no arquivo."""
    existing = load()
    existing.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f, indent=2)
