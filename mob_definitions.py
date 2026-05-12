"""
mob_definitions.py — Tabela central de mobs do jogo.

Cada entrada define cor visual, raça, classe, tipo (melee/ranged) e sons.

Sons: campo "sounds" com chaves base dos arquivos OGG (sem extensão).
  Variações _2/_3/_4 são tentadas automaticamente pelo SoundManager.
  Campo ausente ou string vazia → sem som para aquele evento.

Classes de combate disponíveis:
  Warrior  — melee corpo-a-corpo
  Mage     — ranged mágico (projétil laranja)
  Warlock  — ranged sombrio (projétil roxo)
  Hunter   — ranged físico (flecha marrom; disengage ao ser alcançado)

Regra: Feras são sempre Warrior/melee.
       Humanoides e outros podem ter qualquer classe.
"""

_NO_SOUNDS: dict = {}   # atalho para mobs sem sons definidos ainda

# Parâmetros de projétil por entity_class — extensível sem editar EnemyAISystem.
# Adicionar nova classe de mob ranged: inserir entrada aqui.
PROJECTILE_BY_CLASS: dict[str, dict] = {
    "Warlock":  {"color": (160,   0, 220), "is_arrow": False},
    "Bruxo":    {"color": (160,   0, 220), "is_arrow": False},
    "Mage":     {"color": (255,  80,   0), "is_arrow": False},
    "Mago":     {"color": (255,  80,   0), "is_arrow": False},
    "Hunter":   {"color": (120,  80,  40), "is_arrow": True},
    "Arqueiro": {"color": (120,  80,  40), "is_arrow": True},
    # Padrão para classes não listadas:
    "_default": {"color": (200, 200,  50), "is_arrow": False},
}

MOB_TABLE: dict[str, dict] = {
    # ── Feras ─────────────────────────────────────── race="Fera", sempre melee
    "Aranha": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (55, 42, 62),   # cinza-roxo escuro
        "sounds": _NO_SOUNDS,
    },
    "Rato": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (118, 96, 76),  # marrom claro
        "sounds": _NO_SOUNDS,
    },
    "Escorpião": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (158, 138, 36), # amarelo-oliva
        "sounds": _NO_SOUNDS,
    },
    "Cobra": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (38, 108, 42),  # verde escuro
        "sounds": _NO_SOUNDS,
    },
    "Lobo": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (128, 116, 104), # cinza quente
        "sounds": { 
            "aggro":            "mob_lobo_aggro",    
            "death":            "mob_lobo_death",
            "attack_melee":     "mob_bite_melee",
            "attack_ranged":    None,
            "attack_magic":     None,
            "crit":             "mob_bite_crit",
            "emote_attack":     "mob_lobo_emote_attack",
            "emote_get_crit":   "mob_lobo_get_crit",
        }
    },
    "Urso": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (108, 72, 40),  # marrom escuro
        "sounds": { 
            "aggro":            "mob_urso_aggro",    
            "death":            "mob_urso_death",
            "attack_melee":     "mob_bite_melee",
            "attack_ranged":    None,
            "attack_magic":     None,
            "crit":             "mob_bite_crit",
            "emote_attack":     "mob_urso_emote_attack",
            "emote_get_crit":   "mob_urso_get_crit",
        },
    },

    # ── Humanoides / Outros ───────────────────── podem ter qualquer classe
    "Goblin": {
        "race": "Humanoide", "entity_class": "Hunter", "is_ranged": True,
        "color": (98, 158, 58),  # verde brilhante
        "sounds": {
            "aggro":            "mob_goblin_aggro",    
            "death":            "mob_goblin_death",
            "attack_melee":     None,
            "attack_ranged":    "mob_bow",
            "attack_magic":     None,
            "crit":             "mob_goblin_crit",
            "emote_attack":     "mob_goblin_emote_attack",
            "emote_get_crit":   "mob_goblin_get_crit",
        },
    },
    "Zumbi": {
        "race": "Morto-Vivo", "entity_class": "Warrior", "is_ranged": False,
        "color": (88, 118, 78),  # verde pálido
        "sounds": {
            "aggro":          "mob_zumbi_aggro",
            "death":          "mob_zumbi_death",
            "attack_melee":   "mob_bite_melee",
            "attack_ranged":  "mob_zumbi_attack_ranged",
            "attack_magic":   "mob_zumbi_attack_magic",
            "crit":           "mob_zumbi_crit",
            "emote_attack":   "mob_zumbi_emote_attack",
            "emote_get_crit": "mob_zumbi_emote_get_crit",
        },
    },
    "Orc": {
        "race": "Orc", "entity_class": "Warrior", "is_ranged": False,
        "color": (58, 108, 42),  # verde musgo
        "sounds": _NO_SOUNDS,
    },
    "Troll": {
        "race": "Troll", "entity_class": "Warrior", "is_ranged": False,
        "color": (78, 118, 62),  # verde-cinza
        "sounds": _NO_SOUNDS,
    },
    "Elfo": {
        "race": "Humanoide", "entity_class": "Hunter", "is_ranged": True,
        "color": (78, 188, 108), # verde claro vibrante
        "sounds": _NO_SOUNDS,
    },
    "Minotauro": {
        "race": "Bestial", "entity_class": "Warrior", "is_ranged": False,
        "color": (118, 58, 38),  # marrom-avermelhado
        "sounds": _NO_SOUNDS,
    },
    "Vampiro": {
        "race": "Morto-Vivo", "entity_class": "Warlock", "is_ranged": True,
        "color": (88, 0, 118),   # roxo escuro
        "sounds": _NO_SOUNDS,
    },
    "Dragão": {
        "race": "Dragão", "entity_class": "Mage", "is_ranged": True,
        "color": (178, 28, 18),  # vermelho sangue
        "sounds": _NO_SOUNDS,
    },
}
