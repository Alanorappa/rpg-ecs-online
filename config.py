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
        "estatisticas": 99,   # K_c  — modal de estatísticas do personagem (Fase E)
    },
}


def load() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Vazio/corrompido — acontece de verdade quando 2 clientes na
            # MESMA pasta (2 testers no mesmo PC, cada um com sua própria
            # janela) salvam quase ao mesmo tempo: o open(CONFIG_FILE, "w")
            # de um processo trunca o arquivo pra 0 bytes um instante antes
            # de escrever o JSON novo, e o outro processo lê exatamente
            # nesse instante (bug real relatado pelo usuário 20/07/2026 —
            # crash ao entrar na arena, JSONDecodeError "Expecting value").
            # Nunca derruba o jogo por isso — save() (abaixo) também virou
            # write atômico (tmp + os.replace) pra fechar a janela de
            # corrida em vez de só tolerar o sintoma aqui.
            data = {}
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
    """Salva `data` mesclando com as chaves já existentes no arquivo.

    Write atômico (arquivo temporário + os.replace): um `open(CONFIG_FILE,
    "w")` direto trunca o arquivo pra 0 bytes ANTES de escrever o JSON —
    se outro processo (2 clientes na mesma pasta) ler nesse meio-tempo,
    recebe conteúdo vazio. os.replace() troca o arquivo inteiro de uma vez
    só (atômico no Windows e no POSIX) — quem ler antes ou depois sempre
    vê um JSON completo, nunca um estado parcial."""
    existing = load()
    existing.update(data)
    tmp_path = CONFIG_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(existing, f, indent=2)
    os.replace(tmp_path, CONFIG_FILE)
