# config.py
"""Carrega e salva configurações do jogo em config.json.

O arquivo fica SEMPRE ao lado do executável (build) ou na raiz do projeto
(dev) — local fácil de encontrar/editar pelo jogador (ex.: server_host).
Criado automaticamente com os defaults no primeiro load().
"""
import json
import os
import sys


def _config_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)   # pasta do .exe (PyInstaller)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_FILE = os.path.join(_config_dir(), "config.json")

DEFAULTS = {
    "scale":         1.0,
    "server_host":   "localhost",
    "server_port":   8765,
    "net_user":      "teste",
    "net_pass":      "123456",
    "music_volume":  0.4,
    "sfx_volume":    1.0,
    "music_enabled": True,
    "sfx_enabled":   True,
    # hotbar: {"slots": [skill_id|null, ...], "keybinds": [pygame.K_* int, ...]}
    "hotbar": None,
    # atalhos de menus — chave: nome da ação, valor: pygame.K_* (int)
    "menu_keybinds": {
        "inventario":  105,   # K_i
        "talentos":    116,   # K_t
        "mapa":        109,   # K_m
        "diario":      106,   # K_j
        "habilidades": 104,   # K_h  — painel de skills aprendidas
        "skill_level": 108,   # K_l  — painel de Skill Level (Tibia-like)
    },
}


def load() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        return {**DEFAULTS, **data}
    # Primeiro run: materializa o arquivo com os defaults pro jogador
    # encontrar e editar (server_host/porta ficam visíveis ao lado do exe).
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULTS, f, indent=2)
    except OSError:
        pass
    return dict(DEFAULTS)


def save(data: dict) -> None:
    """Salva `data` mesclando com as chaves já existentes no arquivo."""
    existing = load()
    existing.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f, indent=2)
