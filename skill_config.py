# skill_config.py
"""
Configuração dos slots de habilidades do jogador.

NUM_SLOTS       — número total de slots da hotbar (fixo em 7).
DEFAULT_KEYBINDS — tecla padrão de cada slot (pygame.K_*).
SKILL_SLOTS     — layout padrão; substituído em runtime pelo hotbar salvo em config.json.

IDs de habilidades disponíveis:
  "golpe_poderoso"   — 3× dano físico em alvo adjacente
  "vitoria_iminente" — Ganha 1 carga ao matar um inimigo; ao usar, cura 30% do HP máximo
  "impacto"          — 50% dano em todos os inimigos no raio de 3 tiles
  "executar"         — 5× dano em alvo com menos de 30% HP (adjacente)
  "interceptar"      — Avança instantaneamente até o tile adjacente ao alvo selecionado
"""
import pygame

NUM_SLOTS = 7

# Teclas padrão por índice de slot (K_1 … K_7)
DEFAULT_KEYBINDS: list[int] = [
    pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4,
    pygame.K_5, pygame.K_6, pygame.K_7,
]

# Catálogo completo de skills base (não-talentos)
# Formato: skill_id → dict com name/desc/cooldown/rage_cost/proc_attr/proc_ignores_cost/sound
#
#   rage_cost         — custo em Raiva para ativar (0 = sem custo)
#   proc_attr         — atributo de CharacterStats que sinaliza um proc (> 0 = procced)
#                       deixar "" se a skill não tem proc externo
#   proc_ignores_cost — True se o proc dispensa o rage_cost (ex: carga livre de Executar)
#   sound             — nome base do arquivo de som (sem extensão, sem _1/_2/etc)
#                       ex: "skill_golpe_poderoso" → assets/sounds/sfx/skill_golpe_poderoso.ogg
#                       omitir ou "" para usar o padrão automático "skill_<skill_id>"
#
SKILL_CATALOG: dict[str, dict] = {
    "golpe_poderoso": {
        "name": "Golpe Poderoso",
        "desc": "3x dano — custa 15 Raiva",
        "cooldown": 0.0,
        "rage_cost": 15,
        "proc_attr": "embalo_charges",
        "proc_ignores_cost": False,
    },
    "vitoria_iminente": {
        "name": "Vitória Iminente",
        "desc": "Mata um inimigo para carregar; cura 30% HP",
        "cooldown": 0.0,
        "rage_cost": 0,
        "proc_attr": "",
        "proc_ignores_cost": False,
    },
    "impacto": {
        "name": "Impacto",
        "desc": "50% dano em area (raio 3 tiles)",
        "cooldown": 15.0,
        "rage_cost": 0,
        "proc_attr": "",
        "proc_ignores_cost": False,
    },
    "executar": {
        "name": "Executar",
        "desc": "5x dano (<30% HP) — custa 10 Raiva",
        "cooldown": 0.0,
        "rage_cost": 10,
        "proc_attr": "free_executar_charges",
        "proc_ignores_cost": True,
    },
    "interceptar": {
        "name": "Interceptar",
        "desc": "Avanca instantaneamente ao alvo",
        "cooldown": 22.0,
        "rage_cost": 0,
        "proc_attr": "",
        "proc_ignores_cost": False,
    },
}

# Layout padrão: skill_id por slot (None = slot vazio)
SKILL_SLOTS: list[str | None] = [
    "golpe_poderoso",    # Slot 1
    "vitoria_iminente",  # Slot 2  — baseada em cargas
    "impacto",           # Slot 3
    "executar",          # Slot 4
    "interceptar",       # Slot 5
    None,                # Slot 6  — vazio (preenchido por talentos)
    None,                # Slot 7  — vazio (preenchido por talentos)
]
