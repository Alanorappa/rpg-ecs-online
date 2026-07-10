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
  effects           dict   — sons/VFX por fase: {"cast_start"|"launch"|"impact"|"miss": {"sound"|"sounds": ...}}
                             sound  = str  (nome único no registry de sound_manager.py)
                             sounds = list (variações aleatórias; play_random escolhe uma)

Params de DANO (skills físicas — damage_calculator.ability_physical_damage,
fórmula única: arma×dmg_weapon_pct + AP×(damage_multiplier + 0.01×skill_level_da_arma)):
  damage_multiplier float  — coeficiente do attack_power (lido SÓ daqui;
                             handlers nunca hardcodeiam)
  dmg_weapon_pct    float  — fração do dano da arma somada (default 1.0;
                             0.0 = skill sem arma, ex. Punho no Queixo —
                             também desliga o bônus de skill level da arma)
Params de DANO (magias — damage_calculator.spell_damage):
  dmg_weapon_pct    float  — fração do dano da arma (cajado) somada
  dmg_sp_coeff      float  — coeficiente do spell_power
"""
NUM_SLOTS = 10

# Teclas padrão por índice de slot (K_1 … K_0)
# Valores inteiros diretos (pygame.K_1=49 … pygame.K_0=48) — sem depender de pygame aqui,
# pois skill_config é importado pelo servidor para SKILL_CATALOG.
DEFAULT_KEYBINDS: list[int] = [49, 50, 51, 52, 53, 54, 55, 56, 57, 48]

# ---------------------------------------------------------------------------
# Catálogo de skills — fonte única de dados para todas as skills do jogo
# ---------------------------------------------------------------------------
SKILL_CATALOG: dict[str, dict] = {

    # ── Guerreiro — skills de treinador ─────────────────────────────────────
    "golpe_poderoso": {
        "name":             "Golpe Poderoso",
        "desc":             "O guerreiro transforma sua raiva em dano desferindo um golpe poderoso contra o alvo",
        "cooldown":         0.0,
        "rage_cost":        15,
        "proc_attr":        "embalo_charges",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_golpe_poderoso"}},
        "params": {"damage_multiplier": 0.75},
    },
    "vitoria_iminente": {
        "name":             "Vitória Iminente",
        "desc":             "O guerreiro é consumido por um embalo que o faz curar sua vida acertando seu próximo alvo após um abate",
        "cooldown":         0.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "on_kill":          "charge",
        "effects":          {"impact": {"sound": "skill_vitoria_iminente"}},
        "params": {"damage_multiplier": 0.75, 
                   "heal_pct": 0.30},
    },
    "impacto": {
        "name":             "Impacto",
        "desc":             "Gira com sua arma acertando vários alvos a sua volta",
        "cooldown":         15.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "needs_target":     False,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_impacto"}},
        "params": {"damage_multiplier": 0.75, 
                   "radius_tiles": 3},
    },
    "executar": {
        "name":             "Executar",
        "desc":             "O guerreiro após anos de luta percebe que o alvo está a beira da morte, e desfere um golpe de misericórdia.",
        "cooldown":         0.0,
        "rage_cost":        10,
        "proc_attr":        "free_executar_charges",
        "proc_ignores_cost": True,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_executar"}},
        "params": {"damage_multiplier": 3.0, 
                   "hp_threshold": 0.30},
    },
    "interceptar": {
        "name":             "Interceptar",
        "desc":             "Avança contra o alvo com toda sua velocidade",
        "cooldown":         22.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_interceptar"}},
        "params": {"min_range": 2, 
                   "max_range": 6, 
                   "duration": 0.18},
    },

    # ── Guerreiro — skills de talento (build Cavaleiro) ──────────────────────
    "golpe_debilitante": {
        "name":             "Golpe Debilitante",
        "desc":             "Mira nas pernas do alvo, diminuindo sua velocidade de movimento significativamente",
        "cooldown":         0.0,
        "rage_cost":        5,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_golpe_debilitante"}},
        "params": {"damage_multiplier": 0.50, 
                   "slow_pct": 0.50, 
                   "slow_duration": 5.0},
    },
    "brado_provocativo": {
        "name":             "Brado Provocativo",
        "desc":             "O grito de guerra do guerreiro faz com que os alvos em sua volta enlouqueçam e o ataquem.",
        "cooldown":         45.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "needs_target":     False,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_brado_provocativo"}},
        "params": {"radius_tiles": 3, 
                   "duration": 10.0},
    },
    "punho_no_queixo": {
        "name":             "Punho no Queixo",
        "desc":             "Após inúmeros golpes bem sucedidos, o alvo fica com a guarda baixa, possibilitando um golpe atordoante.",
        "cooldown":         15.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_punho_no_queixo"}},
        "params": {"damage_multiplier": 0.45,
                   "dmg_weapon_pct": 0.0,   # soco: não usa a arma nem skill level dela
                   "hits_required": 3},
    },
    "fatiador_de_corpos": {
        "name":             "Fatiador de Corpos",
        "desc":             "O guerreiro gira em seu próprio eixo como um furacão de lâminas, causando um dano massivo a todos ao seu alcance.",
        "cooldown":         45.0,
        "rage_cost":        0,
        "proc_attr":        "",
        "proc_ignores_cost": False,
        "needs_target":     False,
        "class_id":         "guerreiro",
        "effects":          {"impact": {"sound": "skill_fatiador_de_corpos"}},
        "params": {
            "damage_multiplier": 1.75,   # % do AP por tick (arma soma por padrão)
            "duration":          5.0,
            "tick_interval":     1.0,
            "radius_tiles":      2,
        },
    },

    # ── Mago — skills de treinador ───────────────────────────────────────────
    "bola_de_fogo": {
        "name":              "Bola de Fogo",
        "desc":              "Lança uma bola de fogo no alvo.",
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
        "effect_durations":  {"exhaustion": 6.0},
    },
    "calamidade_flamejante": {
        "name":             "Calamidade Flamejante",
        "desc":             "O mago invoca uma chuva de fogo causado dano e exaustão a todos os alvos dentro da área selecionada.",
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
        "desc":             "O mago invoca todo o frio para o chão, congelando os pés de todos a sua volta.",
        "cooldown":         6.0,
        "mana_cost":        10,
        "cast_time":        1.0,
        "cast_range":       3,
        "interruptible":    True,
        "dmg_sp_coeff":     0.5,
        "cast_time_reduction_attr": "ice_cast_time_reduction",
        "needs_target":     False,
        "class_id":         "mago",
        "school":           "gelo",
        "effects":          {"impact": {"sound": "skill_nova_congelante_impact"}},
        "effect_durations": {"root": 5.0},
    },
    "bloco_de_gelo": {
        "name":             "Bloco de Gelo",
        "desc":             "Usa poder de gelo para se envolver em um bloco de gelo, ficando imune a todo dano e efeito negativo.",
        "cooldown":         45.0,
        "mana_cost":        0,
        "cast_time":        0.0,
        "cast_range":       0,
        "class_id":         "mago",
        "school":           "gelo",
        "offensive":        False,
        "effects":          {"impact": {"sound": "skill_bloco_de_gelo"}},
    },
    "polimorfia": {
        "name":             "Polimorfia",
        "desc":             "Transforma o alvo em um animal durante um tempo, é uma visão perturbadora",
        "cooldown":         0.0,
        "mana_cost":        0,
        "mana_cost_pct":    0.10,
        "cast_time":        1.5,
        "cast_range":       7,
        "interruptible":    True,
        "class_id":         "mago",
        "school":           "arcano",
        "offensive":        False,
        "effect_durations": {"polymorph": 6.0},
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
        "params": {
            "base_dmg":    150,
            "sp_coeff":    1.50,
        },
        "effect_durations": {"disoriented": 3.0},
    },

    # ── Arqueiro ─────────────────────────────────────────────────────────────
    "tiro_multiplo": {
        "name":      "Tiro Múltiplo",
        "desc":      "Dispara flechas em cone de 90° na direção do mouse. 1pt=2 alvos, 2pt=3 alvos, 3pt=ilimitado.",
        "cooldown":  90.0,
        "cast_time": 1.5,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,
        "effects":   {
            "cast_start": {"sound": "arrow_nock"},
            "impact":     {"sound": "arrow_release"},
        },
        "params": {
            "concentration_cost": 60,
            "damage_multiplier":  3.0,   # dano = arma + AP×(3.0 + skill level do arco)
            "cone_half_angle":    45.0,  # graus — cone total de 90°
            "range_tiles":        12,    # raio FOV padrão do arqueiro
        },
    },
    "camuflagem": {
        "name":      "Camuflagem",
        "desc":      "O arqueiro se disfarça com sua capa por 5s. Inalvejável (PvP/PvE) e DOTs são dispelados. Velocidade 60%. 50 Concentração.",
        "cooldown":  45.0,
        "cast_time": 0.0,
        "cast_range": 0,
        "class_id":  "arqueiro",
        "offensive": False,
        "params": {
            "concentration_cost": 50,
            "duration":           5.0,
            "speed_pct":          0.60,   # 60% da velocidade normal
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
        "effects":   {
            "cast_start": {"sound": "arrow_nock"},
            "launch":     {"sound": "arrow_release"},
            "impact":     {"sound": "arrow_impact"},
        },
        "params": {
            "concentration_cost": 100,
            "knockback_tiles":    5,
            "stun_duration":      3.0,
            "damage_multiplier":  1.5,   # dano = arma + AP×(1.5 + skill level do arco)
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
        "offensive": False,
        "effects":   {"cast_start": {"sound": "skill_cancao_ninar"}},
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
        "effects":   {
            "cast_start": {"sound": "arrow_nock"},
            "launch":     {"sound": "arrow_release"},
            "impact":     {"sound": "arrow_impact"},
        },
        "params": {
            "concentration_cost": 20,
            "damage_multiplier":  1.5,
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
        "effects":   {
            "cast_start": {"sound": "arrow_nock"},
            "launch":     {"sound": "arrow_release"},
            "impact":     {"sound": "arrow_impact"},
        },
        "params": {
            "concentration_cost": 80,
            "arrow_count":        2,
            "arrow_delay":        0.25,   # segundos entre a 1ª e 2ª flecha
            "damage_multiplier":  2.0,    # dano = arma + flecha + AP×(2.0 + skill level do arco)
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
    # Guerreiro
    "golpe_poderoso":   1,
    "impacto":          2,
    "vitoria_iminente": 4,
    "interceptar":      8,
    "executar":         16,
    # Mago
    "bola_de_fogo":     1,
    "polimorfia":       2,
    "nova_congelante":  4,
    "bloco_de_gelo":    8,
    # Arqueiro
    "recarregar":       1,    
    "cancao_ninar":     2,
    "picada_escorpiao": 4,
    "flecha_reiterada": 8,
}

SKILL_COSTS: dict[str, int] = {
    # Guerreiro
    "golpe_poderoso":   0,
    "impacto":          100,
    "vitoria_iminente": 200,
    "interceptar":      400,
    "executar":         1600,
    # Mago
    "bola_de_fogo":     0,
    "polimorfia":       100,
    "nova_congelante":  200,
    "bloco_de_gelo":    400,
    # Arqueiro
    "recarregar":       0,
    "cancao_ninar":     100,
    "picada_escorpiao": 200,
    "flecha_reiterada": 400,

}

SKILL_ORDER_BY_CLASS: dict[str, list] = {
    "guerreiro": ["golpe_poderoso", "impacto", "vitoria_iminente", "interceptar", "executar"],
    "mago":      ["bola_de_fogo", "polimorfia", "nova_congelante", "bloco_de_gelo"],
    "arqueiro":  ["recarregar", "cancao_ninar", "picada_escorpiao", "flecha_reiterada"],
}

# Skills concedidas automaticamente ao criar um personagem novo (custo 0, nível 1).
# Chave = class_id, valor = lista de skill_ids iniciais.
INITIAL_SKILLS_BY_CLASS: dict[str, list] = {
    "guerreiro": [],
    # Mago: nenhuma skill inicial — todas (incl. Bola de Fogo, grátis a partir
    # do nível 1) são aprendidas no treinador. Ver quest "iniciacao_arcana"
    # (quests_data.py) e SKILL_LEVEL_REQUIREMENTS/SKILL_COSTS acima.
    "mago":      [],
    "arqueiro":  [],
}
