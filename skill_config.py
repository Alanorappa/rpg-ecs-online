# skill_config.py
from __future__ import annotations
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

NUM_SLOTS = 10

# Teclas padrão por índice de slot (K_1 … K_0)
DEFAULT_KEYBINDS: list[int] = [
    pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4,
    pygame.K_5, pygame.K_6, pygame.K_7, pygame.K_8,
    pygame.K_9, pygame.K_0,
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

# Layout padrão: todos os slots vazios — skills são aprendidas com treinador
SKILL_SLOTS: list[str | None] = [None] * NUM_SLOTS

# ---------- Skills do Mago ----------
SKILL_CATALOG.update({
    "bola_de_fogo": {
        "name":           "Bola de Fogo",
        "desc":           "Projétil – 1.5s cast. 50% dano + 100% SP. 25 mana.",
        "cooldown":       0.0,
        "mana_cost":      25,
        "cast_time":      1.5,
        "cast_range":     30,
        "class_id":       "mago",
    },
    "calamidade_flamejante": {
        "name":             "Calamidade Flamejante",
        "desc":             "Canaliza meteoros em área (2 tiles) por 5s. 10 mana/s.",
        "cooldown":         0.0,
        "mana_cost":        10,
        "cast_time":        0.0,
        "cast_range":       8,
        "is_channeled":     True,
        "channel_duration": 5.0,
        "needs_aoe_target": True,
        "class_id":         "mago",
    },
    "nova_congelante": {
        "name":       "Nova Congelante",
        "desc":       "Enraíza inimigos a 3 tiles por 5s. 50% SP. 10 mana.",
        "cooldown":   15.0,
        "mana_cost":  10,
        "cast_time":  0.0,
        "cast_range": 3,
        "class_id":   "mago",
    },
    "bloco_de_gelo": {
        "name":       "Bloco de Gelo",
        "desc":       "Imune e imóvel 5s. Cura 10% HP/s. 45s recarga.",
        "cooldown":   45.0,
        "mana_cost":  0,
        "cast_time":  0.0,
        "cast_range": 0,
        "class_id":   "mago",
    },
})

# Nível mínimo necessário para aprender cada skill com o treinador
SKILL_LEVEL_REQUIREMENTS: dict[str, int] = {
    "golpe_poderoso":       2,
    "impacto":              3,
    "vitoria_iminente":     5,
    "interceptar":          6,
    "executar":            10,
    # Mago
    "bola_de_fogo":         2,
    "nova_congelante":      4,
    "calamidade_flamejante": 6,
    "bloco_de_gelo":        8,
}

# Custo em ouro para aprender cada skill (começa em 100, dobra a cada skill)
SKILL_COSTS: dict[str, int] = {
    "golpe_poderoso":       100,
    "impacto":              200,
    "vitoria_iminente":     400,
    "interceptar":          800,
    "executar":            1600,
    # Mago
    "bola_de_fogo":         100,
    "nova_congelante":      200,
    "calamidade_flamejante": 400,
    "bloco_de_gelo":        800,
}

# Ordem de exibição no treinador — por classe
SKILL_ORDER_BY_CLASS: dict[str, list] = {
    "guerreiro": ["golpe_poderoso", "impacto", "vitoria_iminente", "interceptar", "executar"],
    "mago":      ["bola_de_fogo", "nova_congelante", "calamidade_flamejante", "bloco_de_gelo"],
}
