# skill_config.py
from __future__ import annotations
"""
Catálogo único de todas as skills do jogo.

Toda skill vive aqui — independente de como é adquirida (treinador, talento,
quest, drop, etc.). A forma de aquisição é definida em outros arquivos:
  - Treinador:  SKILL_ORDER_BY_CLASS, SKILL_LEVEL_REQUIREMENTS, SKILL_COSTS
  - Talento:    talent_data.py → unlocks_skill = "skill_id"
  - (Futuro)    quests_data.py, loot_tables.py, etc.

Campos do catálogo:
  name              str    — nome exibido na UI
  desc              str    — descrição curta para tooltip
  cooldown          float  — segundos de recarga (0 = sem CD, apenas GCD)
  rage_cost         int    — custo em Raiva (0 = sem custo)
  mana_cost         int    — custo fixo de mana (0 = sem custo)
  mana_cost_pct     float  — custo como % da mana máxima (0 = usa mana_cost)
  cast_time         float  — duração do cast em segundos (0 = instantâneo)
  cast_range        int    — alcance máximo em tiles (0 = corpo-a-corpo)
  is_channeled      bool   — True se skill canalizada
  channel_duration  float  — duração da canalização
  needs_aoe_target  bool   — True se requer clique de mira AOE
  proc_attr         str    — atributo de CharacterStats que sinaliza proc (brilho)
  proc_ignores_cost bool   — proc dispensa o custo
  school            str    — escola de magia: "fogo" | "gelo" | "arcano" | ""
  offensive         bool   — False = utilitária/buff, não inicia combate (padrão True)
  class_id          str    — classe que pode usar a skill (informativo)
  sound             str    — nome base do arquivo de som (opcional)
"""
import pygame

NUM_SLOTS = 10

# Teclas padrão por índice de slot (K_1 … K_0)
DEFAULT_KEYBINDS: list[int] = [
    pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4,
    pygame.K_5, pygame.K_6, pygame.K_7, pygame.K_8,
    pygame.K_9, pygame.K_0,
]

# ---------------------------------------------------------------------------
# Catálogo de skills — fonte única de dados para todas as skills do jogo
# ---------------------------------------------------------------------------
SKILL_CATALOG: dict[str, dict] = {

    # ── Guerreiro — skills de treinador ─────────────────────────────────────
    "golpe_poderoso": {
        "name":             "Golpe Poderoso",
        "desc":             "3x dano — custa 15 Raiva",
        "cooldown":         0.0,
        "rage_cost":        15,
        "proc_attr":        "embalo_charges",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "params": {"damage_multiplier": 3.0},
    },
    "vitoria_iminente": {
        "name":             "Vitória Iminente",
        "desc":             "Mata um inimigo para carregar; cura 30% HP",
        "cooldown":         0.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "on_kill":          "charge",   # repõe carga ao matar mob
        "params": {"damage_multiplier": 2.0, "heal_pct": 0.30},
    },
    "impacto": {
        "name":             "Impacto",
        "desc":             "50% dano em area (raio 3 tiles)",
        "cooldown":         15.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "needs_target":     False,
        "class_id":         "guerreiro",
        "params": {"damage_multiplier": 0.50, "radius_tiles": 3},
    },
    "executar": {
        "name":             "Executar",
        "desc":             "5x dano (<30% HP) — custa 10 Raiva",
        "cooldown":         0.0,
        "rage_cost":        10,
        "proc_attr":        "free_executar_charges",
        "proc_ignores_cost": True,
        "class_id":         "guerreiro",
        "params": {"damage_multiplier": 5.0, "hp_threshold": 0.30},
    },
    "interceptar": {
        "name":             "Interceptar",
        "desc":             "Avanca instantaneamente ao alvo",
        "cooldown":         22.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "params": {"min_range": 2, "max_range": 6, "duration": 0.18},
    },

    # ── Guerreiro — skills de talento (build Cavaleiro) ──────────────────────
    "golpe_debilitante": {
        "name":             "Golpe Debilitante",
        "desc":             "50% dano + slow 50% por 5s. Custo: 5 Raiva.",
        "cooldown":         0.0,
        "rage_cost":        5,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "params": {"damage_multiplier": 0.50, "slow_pct": 0.50, "slow_duration": 5.0},
    },
    "brado_provocativo": {
        "name":             "Brado Provocativo",
        "desc":             "Enlouquece inimigos (raio 3 tiles) por 10s. Cooldown 45s.",
        "cooldown":         45.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "needs_target":     False,
        "sound":            "skill_brado_provocativo",
        "class_id":         "guerreiro",
        "params": {"radius_tiles": 3, "duration": 10.0},
    },
    "punho_no_queixo": {
        "name":             "Punho no Queixo",
        "desc":             "3 golpes → 1 carga: 45% AP + atordoa (duração escala com pontos).",
        "cooldown":         15.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "sound":            "skill_punho_no_queixo",
        "class_id":         "guerreiro",
        "params": {"damage_multiplier": 0.45, "hits_required": 3},
    },
    "fatiador_de_corpos": {
        "name":             "Fatiador de Corpos",
        "desc":             "Spin AoE: 65% dano + arma/s por 5s a todos ao redor (raio 2 tiles). Cooldown 45s.",
        "cooldown":         45.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "needs_target":     False,
        "class_id":         "guerreiro",
        "params": {
            "damage_multiplier": 0.65,   # % do AP por tick
            "include_weapon_dmg": True,  # adiciona dano da arma
            "duration":          5.0,
            "tick_interval":     1.0,
            "radius_tiles":      2,
        },
    },

    # ── Mago — skills de treinador ───────────────────────────────────────────
    "bola_de_fogo": {
        "name":              "Bola de Fogo",
        "desc":              "Lança uma bola de fogo no alvo selecionado.",
        "cooldown":          0.0,
        "mana_cost":         25,
        "cast_time":         1.5,
        "cast_range":        6,
        "interruptible":     True,
        "dmg_weapon_pct":    0.5,
        "dmg_sp_coeff":      1.0,
        "cast_time_reduction_attr": "fire_cast_time_reduction",  # CombatStats attr de talento
        "mana_discount_attr":       "fire_mana_discount",        # Frieza: -1 mana/ponto (flat)
        "mana_pct_discount_attr":   "pyromania_bonus",           # Piromaníaco: X% menos mana
        "class_id":          "mago",
        "school":            "fogo",
        "proc_attr":         "fire_instant_ready",
        "proc_ignores_cost": True,
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
        "school":           "fogo",
    },
    "nova_congelante": {
        "name":             "Nova Congelante",
        "desc":             "1s cast. Enraíza inimigos a 3 tiles por 5s. 50% SP. 10 mana.",
        "cooldown":         6.0,
        "mana_cost":        10,
        "cast_time":        1.0,
        "cast_range":       3,
        "interruptible":    True,
        "dmg_sp_coeff":     0.5,
        "cast_time_reduction_attr": "ice_cast_time_reduction",   # Precisão Elemental
        "needs_target":     False,
        "class_id":         "mago",
        "school":           "gelo",
    },
    "bloco_de_gelo": {
        "name":             "Bloco de Gelo",
        "desc":             "Imune e imóvel 5s. Cura 10% HP/s. 45s recarga.",
        "cooldown":         45.0,
        "mana_cost":        0,
        "cast_time":        0.0,
        "cast_range":       0,
        "class_id":         "mago",
        "school":           "gelo",
        "offensive":        False,
    },
    "polimorfia": {
        "name":             "Polimorfia",
        "desc":             "1.5s cast. Transforma o alvo: perde controle e regenera 10% HP/s. Custo: 10% mana.",
        "cooldown":         0.0,
        "mana_cost":        0,
        "mana_cost_pct":    0.10,
        "cast_time":        1.5,
        "cast_range":       7,
        "interruptible":    True,
        "class_id":         "mago",
        "school":           "arcano",
        "offensive":        False,
    },

    # ── Mago — skills de talento (build Piromania) ───────────────────────────
    "escudo_fogo": {
        "name":             "Escudo de Fogo",
        "desc":             "Envolve o corpo em chamas. Atacantes recebem 10 + 20% SP de dano. 15s.",
        "cooldown":         20.0,
        "mana_cost":        25,
        "cast_time":        0.0,
        "cast_range":       0,
        "needs_target":     False,   # self-cast: alvo é o próprio personagem
        "class_id":         "mago",
        "school":           "fogo",
        "offensive":        False,
    },
    "calcinar": {
        "name":             "Calcinar",
        "desc":             "0.6s cast — pode ser lançada em movimento. 50 + 25% SP. Sem cooldown.",
        "cooldown":         0.0,
        "mana_cost":        25,
        "cast_time":        0.6,
        "cast_range":       8,
        "interruptible":          False,
        "base_dmg":               50,
        "dmg_sp_coeff":           0.25,
        "mana_pct_discount_attr": "pyromania_bonus",  # Piromaníaco: X% menos mana (sem Frieza flat)
        "class_id":               "mago",
        "school":                 "fogo",
    },
    "pirofagia": {
        "name":             "Pirofagia",
        "desc":             "Mira cone de fogo. Clique esq. para disparar. 150 + 150% SP + desorientado 3s. 90s CD.",
        "cooldown":         90.0,
        "mana_cost":        75,
        "cast_time":        0.0,
        "cast_range":       0,
        "class_id":         "mago",
        "school":           "fogo",
        "offensive":        False,
        "needs_aoe_target": True,    # impede som automático no SkillSystem — som toca ao disparar
    },

    # ── Arqueiro ─────────────────────────────────────────────────────────────
    "tiro_multiplo": {
        "name":      "Tiro Múltiplo",
        "desc":      "Dispara flechas em cone de 90° na direção do mouse. 1pt=2 alvos, 2pt=3 alvos, 3pt=ilimitado. 100% arma + 300% AP. 60 Conc. 90s CD.",
        "cooldown":  90.0,
        "cast_time": 1.5,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,
        "params": {
            "concentration_cost": 60,
            "ap_multiplier":      3.0,   # 100% weapon + 300% AP (extra_ap = 2× AP)
            "cone_half_angle":    45.0,  # graus — cone total de 90°
            "range_tiles":        12,    # raio FOV padrão do arqueiro
        },
    },
    "camuflagem": {
        "name":      "Camuflagem",
        "desc":      "O arqueiro se disfarça de um objeto do cenário por 5s. Velocidade 30%, inimigos perdem o alvo. 50 Concentração.",
        "cooldown":  45.0,
        "cast_time": 0.0,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,
        "params": {
            "concentration_cost": 50,
            "duration":           5.0,
            "speed_pct":          0.30,   # 30% da velocidade normal
        },
    },
    "tiro_repulsivo": {
        "name":      "Tiro Repulsivo",
        "desc":      "Repele o alvo 5 tiles na direção oposta. Colisão com parede = stun 3s. 100% arma + 150% AP. 100 Concentração.",
        "cooldown":  45.0,
        "cast_time": 2.5,
        "cast_range": 8,
        "class_id":  "arqueiro",
        "offensive": True,
        "params": {
            "concentration_cost": 100,
            "knockback_tiles":    5,
            "stun_duration":      3.0,
            "ap_multiplier":      1.5,   # 1x já vem do deal_damage; +0.5 = 150% total
        },
    },
    "cancao_inspiracao": {
        "name":      "Canção da Inspiração",
        "desc":      "Toca uma música épica que aumenta em 30% o poder de ataque por 20s. CD: 360s.",
        "cooldown":  360.0,
        "cast_time": 0.0,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,
        "params": {
            "ap_bonus_pct": 0.30,   # +30% de attack_power
            "duration":     20.0,
        },
    },
    "so_um_gole": {
        "name":      "Só um Gole",
        "desc":      "O arqueiro bebe e fica mais afiado: habilidades de Concentração ficam grátis e acerto vai a 100% por 10s.",
        "cooldown":  120.0,
        "cast_time": 0.0,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,
        "params": {
            "duration":    10.0,   # segundos de buff
            "acerto_flat": 100.0,  # bônus flat de acerto (garante 100% com qualquer base)
        },
    },
    "cancao_ninar": {
        "name":      "Canção de Ninar",
        "desc":      "Canal 2s: inimigos em raio 5 tiles dormem 8s. Slow 30% por 5s ao acordar. 25 Concentração.",
        "cooldown":  45.0,
        "cast_time": 2.0,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,   # não-ofensiva: arqueiro não ataca durante o canal
        "params": {
            "concentration_cost": 25,
            "radius":             5,
            "sleep_duration":     8.0,
            "slow_duration":      5.0,
            "slow_magnitude":     0.30,
        },
    },
    "picada_escorpiao": {
        "name":      "Picada de Escorpião",
        "desc":      "Flecha precisa: dano da arma + 50% AP. Aplica slow 30% por 3s. 20 Concentração.",
        "cooldown":  5.0,
        "cast_time": 0.8,
        "cast_range": 8,
        "class_id":  "arqueiro",
        "offensive": True,
        "params": {
            "concentration_cost": 20,
            "ap_multiplier":      1.5,
            "guaranteed_hit":     True,
            "on_hit_effect":      "slow",
            "on_hit_duration":    3.0,
            "on_hit_magnitude":   0.30,
        },
    },
    "flecha_reiterada": {
        "name":      "Flecha Reiterada",
        "desc":      "Dispara 2 flechas em sequência. Requer arco + aljava. 80 Concentração.",
        "cooldown":  12.0,
        "cast_time": 2.0,
        "cast_range": 8,
        "class_id":  "arqueiro",
        "offensive": True,
        "params": {
            "concentration_cost": 80,
            "arrow_count":        2,
            "arrow_delay":        0.25,   # segundos entre a 1ª e 2ª flecha
            "ap_multiplier":      2.0,    # dano = weapon + arrow + 200% AP
        },
    },
    "recarregar": {
        "name":      "Recarregar",
        "desc":      "Reabastece a aljava com flechas da mochila. Requer flechas disponíveis.",
        "cooldown":  0.0,
        "cast_time": 1.8,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,
        "params": {
            "quiver_capacity": 100,
        },
    },
}

# Layout padrão: todos os slots vazios — skills são aprendidas com treinador
SKILL_SLOTS: list[str | None] = [None] * NUM_SLOTS

# ---------------------------------------------------------------------------
# Aquisição via treinador — skills disponíveis no NPC por classe
# (Skills de talento NÃO aparecem aqui — são desbloqueadas via talent_data.py)
# ---------------------------------------------------------------------------

SKILL_LEVEL_REQUIREMENTS: dict[str, int] = {
    "golpe_poderoso":   2,
    "impacto":          3,
    "vitoria_iminente": 5,
    "interceptar":      6,
    "executar":         10,
    "bola_de_fogo":     2,
    "nova_congelante":  4,
    "polimorfia":       5,
    "bloco_de_gelo":    8,
    # Arqueiro
    "tiro_multiplo":     1,
    "camuflagem":        1,
    "tiro_repulsivo":    1,
    "cancao_inspiracao": 1,
    "so_um_gole":        1,
    "cancao_ninar":      1,
    "picada_escorpiao": 1,
    "flecha_reiterada":   1,
}

SKILL_COSTS: dict[str, int] = {
    "golpe_poderoso":   100,
    "impacto":          200,
    "vitoria_iminente": 400,
    "interceptar":      800,
    "executar":         1600,
    "bola_de_fogo":     100,
    "nova_congelante":  200,
    "polimorfia":       300,
    "bloco_de_gelo":    600,
    # Arqueiro
    "tiro_multiplo":     0,
    "camuflagem":        0,
    "tiro_repulsivo":    0,
    "cancao_inspiracao": 0,
    "so_um_gole":        0,
    "cancao_ninar":     0,
    "picada_escorpiao": 0,
    "flecha_reiterada":   0,
}

SKILL_ORDER_BY_CLASS: dict[str, list] = {
    "guerreiro": ["golpe_poderoso", "impacto", "vitoria_iminente", "interceptar", "executar"],
    "mago":      ["bola_de_fogo", "nova_congelante", "polimorfia", "bloco_de_gelo"],
    "arqueiro":  ["cancao_ninar", "picada_escorpiao", "flecha_reiterada"],
}

# Skills concedidas automaticamente ao criar um personagem novo (custo 0, nível 1).
# Chave = class_id, valor = lista de skill_ids iniciais.
INITIAL_SKILLS_BY_CLASS: dict[str, list] = {
    "guerreiro": [],
    "mago":      [],
    "arqueiro":  ["recarregar", "cancao_ninar", "picada_escorpiao", "flecha_reiterada"],
}
