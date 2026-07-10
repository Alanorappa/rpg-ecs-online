"""
mob_definitions.py — Tabela central de mobs do jogo.

Cada entrada define raça, classe, cor visual, atributos, habilidades, loot,
xp por level e sons. Tudo aqui é a baseline de LEVEL 1 / TIER "normal" — os
multiplicadores de tier (ENEMY_TIER_CONFIGS) e o scaling por level (ambos em
entity_factory.py::create_enemy) são aplicados POR CIMA destes valores.

attributes (todos os campos são opcionais; fallback se omitido):
  health         — HP máximo (vira CombatStats.base_stamina, 1:1).
  armor          — % de redução de dano físico (ex: 20 = 20%). Convertido
                   internamente para "armor rating" em create_enemy():
                   rating = armor_pct * 10 (fórmula real: 1 rating = 0.1% de
                   redução — ARMOR_REDUCTION_PER_POINT em damage_calculator.py).
  attack_min/max — dano base do mob, faixa aleatória (ex: 1 a 3).
  attack_power   — soma ao dano base (ex: attack_min/max=1-3 + attack_power=1
                   → dano final rola entre 2 e 4).
  move_speed_pct — % da velocidade base de mob (ENEMY_SPEED, em
                   entity_factory.py). 100 = velocidade padrão atual.
  attack_speed   — intervalo entre ataques, em segundos.
  acerto         — % de chance de acertar o ataque (0-100, direto).
  crit_chance    — % de chance de crítico (0-100; convertido pra fração 0-1).

  Mob físico (Warrior/Hunter) usa attack_min/max+attack_power direto. Mob
  "mágico" (entity_class Mage/Mago/Warlock/Bruxo) espelha attack_power e a
  média de attack_min/attack_max pra spell_power/magical_damage também —
  causa dano "magical" (ignora armadura do alvo, mitigado só por
  resistência elemental) em vez de "physical", igual ao comportamento
  original. base_attack_power continua setado mesmo nesse caso (alimenta o
  multiplicador de dano de habilidade, ver abilities abaixo).

  Level do mob NÃO é um campo daqui — vem da SpawnZone no JSON do mapa
  (level_min/level_max, {map}_entities.json). health/attack_power (e o
  espelho spell_power/magical_damage) MULTIPLICAM pelo level em
  create_enemy(); armor/acerto/crit_chance/attack_min/attack_max/velocidade
  ficam fixos (só tier, sem scaling por level).

abilities: lista de ability_id (de enemy_abilities_data.py::ABILITY_DEFS).
  Cada entrada pode ser:
    "rotting_bite"            — usa o default_cooldown da própria AbilityDef.
    ("poison_bite", 10.0)     — cooldown customizado pra este mob específico.

loot: dict {item_key: chance} — item_key é a chave em loot_tables.py::_T.
  Fonte única de verdade (loot_tables.py não duplica mais essa lista; só
  aplica o multiplicador de tier e instancia os Items via _T[item_key]()).

gold_chance/gold_min/gold_max (todos opcionais — sem eles, cai no genérico
  por tier em loot_tables.COIN_DROPS, mesmo comportamento de antes):
  gold_chance — 0.0 a 1.0, chance de dropar gold NESTE kill. 0.0 pra mobs
                que não fazem sentido carregar moedas (Feras: Lobo, Aranha,
                Escorpião, Cobra, Urso, Rato).
  gold_min/gold_max — faixa de gold na tier "normal" (mesma convenção de
                `attributes`: baseline, escalado por _TIER_MULT de
                loot_tables.py pra elite/rare/boss). Sem os dois, usa a
                faixa genérica do tier (COIN_DROPS).
  Rolado por loot_tables.roll_mob_coins(mob_name, tier).

alt_variant (opcional, str — nome de outra entrada em MOB_TABLE): usado por
  entity_factory._resolve_mob_race_variant quando uma spawn zone pede um
  "type" (melee/ranged, ver map_1_entities.json) que NÃO bate com o
  is_ranged fixo desta raça. Ex.: "Goblin" é sempre ranged (Hunter); uma
  zona com {"type": "melee", "race": "Goblin", ...} troca automaticamente
  pra "Goblin Guerreiro" (alt_variant do Goblin) se essa entrada existir e
  for melee de verdade. Sem alt_variant (ou variant que também não bate o
  modo pedido) — usa a própria raça pedida sem mudança, igual sempre foi.

xp_given_by_lvl: XP concedido por level do mob ao morrer (xp_total =
  level_do_mob × xp_given_by_lvl × multiplicador_de_tier["xp"]). Usado pelo
  servidor (server/server_death_handler.py) e pelo XPReward offline
  (entity_factory.py).

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

Mobs SEM entrada aqui (ex: "Elemental", definidos só na SpawnZone do mapa)
caem no template genérico antigo (2 templates fixos por is_ranged) em
create_enemy() — não afetados por esta migração.
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
    "Rato": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (118, 96, 76),  # marrom claro
        "attributes": {
            "health": 15, "armor": 0,
            "attack_min": 1, "attack_max": 2, "attack_power": 0,
            "move_speed_pct": 70, "attack_speed": 2.2,
            "acerto": 80, "crit_chance": 5,
        },
        "abilities": [],
        "loot": {"cloth_boots": 0.05, "padded_gloves": 0.03, "cloth_wrists": 0.01},
        "gold_chance": 0.0,   # Fera — não faz sentido carregar moedas
        "xp_given_by_lvl": 8,
        "sounds": _NO_SOUNDS,
    },
    "Aranha": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (55, 42, 62),   # cinza-roxo escuro
        "attributes": {
            "health": 20, "armor": 5,
            "attack_min": 1, "attack_max": 2, "attack_power": 0,
            "move_speed_pct": 80, "attack_speed": 2.0,
            "acerto": 82, "crit_chance": 8,
        },
        "abilities": ["web_bite"],
        "loot": {"padded_gloves": 0.05, "cloth_wrists": 0.03, "worn_hood": 0.02},
        "gold_chance": 0.0,   # Fera — não faz sentido carregar moedas
        "xp_given_by_lvl": 10,
        "sounds": _NO_SOUNDS,
    },
    "Escorpião": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (158, 138, 36), # amarelo-oliva
        "attributes": {
            "health": 22, "armor": 8,
            "attack_min": 1, "attack_max": 2, "attack_power": 1,
            "move_speed_pct": 55, "attack_speed": 2.6,
            "acerto": 80, "crit_chance": 8,
        },
        "abilities": ["poison_bite"],
        "loot": {"padded_wrists": 0.04, "light_boots": 0.03, "light_hood": 0.02, "light_shoulders": 0.01},
        "gold_chance": 0.0,   # Fera — não faz sentido carregar moedas
        "xp_given_by_lvl": 10,
        "sounds": _NO_SOUNDS,
    },
    "Cobra": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (38, 108, 42),  # verde escuro
        "attributes": {
            "health": 20, "armor": 5,
            "attack_min": 1, "attack_max": 3, "attack_power": 1,
            "move_speed_pct": 85, "attack_speed": 2.0,
            "acerto": 85, "crit_chance": 10,
        },
        "abilities": [("poison_bite", 5.0)],   # cooldown diferente do Escorpião (default 3.0)
        "loot": {"light_boots": 0.04, "padded_gloves": 0.03, "leather_vest": 0.02},
        "gold_chance": 0.0,   # Fera — não faz sentido carregar moedas
        "xp_given_by_lvl": 10,
        "sounds": _NO_SOUNDS,
    },
    "Lobo": {
        "race": "Fera", "entity_class": "Warrior", "is_ranged": False,
        "color": (128, 116, 104), # cinza quente
        "attributes": {
            "health": 35, "armor": 8,
            "attack_min": 2, "attack_max": 4, "attack_power": 1,
            "move_speed_pct": 95, "attack_speed": 2.0,
            "acerto": 88, "crit_chance": 12,
        },
        "abilities": [],
        "loot": {"leather_vest": 0.05, "light_boots": 0.03, "light_hood": 0.02},
        "gold_chance": 0.0,   # Fera — não faz sentido carregar moedas
        "xp_given_by_lvl": 14,
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
        "attributes": {
            "health": 65, 
            "armor": 15,
            "attack_min": 3, "attack_max": 6, 
            "attack_power": 2,
            "move_speed_pct": 60, 
            "attack_speed": 2.8,
            "acerto": 85, "crit_chance": 10,
        },
        "abilities": ["lacerate"],
        "loot": {"leather_vest": 0.05, "bone_shield": 0.03, "cracked_club": 0.02, "iron_breastplate": 0.02},
        "gold_chance": 0.0,   # Fera — não faz sentido carregar moedas
        "xp_given_by_lvl": 20,
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
        "attributes": {
            "health": 30,
            "armor": 5,
            "attack_min": 2, "attack_max": 4,
            "attack_power": 1,
            "move_speed_pct": 75,
            "attack_speed": 2.4,
            "acerto": 85, "crit_chance": 10,
        },
        "abilities": ["poison_arrow"],
        "loot": {"hunter_bow": 0.05, 
                 "basic_quiver": 0.04, 
                 "iron_gauntlets": 0.03, 
                 "iron_greaves": 0.02},
        "xp_given_by_lvl": 16,
        # alt_variant: raça a usar quando uma spawn zone pede "type": "melee"
        # pra "Goblin" (sempre ranged) — ver entity_factory._resolve_mob_race_variant.
        "alt_variant": "Goblin Guerreiro",
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
    "Goblin Guerreiro": {
        "race": "Humanoide", "entity_class": "Warrior", "is_ranged": False,
        "color": (98, 158, 58),  # mesma cor do Goblin — mesma raça, outro estilo de combate
        "attributes": {
            "health": 25, 
            "armor": 8,
            "attack_min": 3, "attack_max": 8, 
            "attack_power": 1,
            "move_speed_pct": 65, 
            "attack_speed": 1.9,
            "acerto": 85, 
            "crit_chance": 10,
        },
        "abilities": [],
        "loot": {"bone_sword": 0.03, 
                 "iron_gauntlets": 0.01, 
                 "iron_greaves": 0.01},
        "gold_chance": 0.55, "gold_min": 5, "gold_max": 25,
        "xp_given_by_lvl": 16,
        # alt_variant simétrico — se uma zona apontar essa raça direto e pedir
        # "ranged", volta pro Goblin arqueiro.
        "alt_variant": "Goblin",
        "sounds": {
            "aggro":            "mob_goblin_aggro",
            "death":            "mob_goblin_death",
            "attack_melee":     "hit_normal",
            "attack_ranged":    None,
            "attack_magic":     None,
            "crit":             "hit_crit",
            "emote_attack":     "mob_goblin_emote_attack",
            "emote_get_crit":   "mob_goblin_get_crit",
        },
    },
    "Zumbi": {
        "race": "Morto-Vivo", "entity_class": "Warrior", "is_ranged": False,
        "color": (88, 118, 78),  # verde pálido
        "attributes": {
            "health": 50, 
            "armor": 20,
            "attack_min": 1, "attack_max": 3, 
            "attack_power": 1,
            "move_speed_pct": 50, 
            "attack_speed": 3.2,
            "acerto": 65, 
            "crit_chance": 15,
        },
        "abilities": ["rotting_bite"],
        "loot": {"bone_sword": 0.04, "iron_coif": 0.01, "bone_shield": 0.02, "bone_wristguards": 0.02},
        "gold_chance": 0.35, "gold_min": 3, "gold_max": 12,
        "xp_given_by_lvl": 25,
        "sounds": {
            "aggro":          "mob_zumbi_aggro",
            "death":          "mob_zumbi_death",
            "attack_melee":   "mob_bite_melee",
            "attack_ranged":  "mob_zumbi_attack_ranged",
            "attack_magic":   "mob_zumbi_attack_magic",
            "crit":           "mob_zumbi_crit",
            "emote_attack":   "mob_zumbi_emote_attack",   # emote de ataque também usa sons de mordida
            "emote_get_crit": "mob_zumbi_emote_get_crit",
        },
    },
    "Orc": {
        "race": "Orc", "entity_class": "Warrior", "is_ranged": False,
        "color": (58, 108, 42),  # verde musgo
        "attributes": {
            "health": 60, "armor": 15,
            "attack_min": 3, "attack_max": 5, "attack_power": 2,
            "move_speed_pct": 80, "attack_speed": 2.8,
            "acerto": 88, "crit_chance": 12,
        },
        "abilities": [],
        "loot": {"iron_sword": 0.03, "iron_mace": 0.02, "chain_vest": 0.01, "iron_shield": 0.005},
        "xp_given_by_lvl": 22,
        "sounds": _NO_SOUNDS,
    },
    "Troll": {
        "race": "Troll", "entity_class": "Warrior", "is_ranged": False,
        "color": (78, 118, 62),  # verde-cinza
        "attributes": {
            "health": 90, "armor": 18,
            "attack_min": 4, "attack_max": 7, "attack_power": 3,
            "move_speed_pct": 65, "attack_speed": 3.0,
            "acerto": 85, "crit_chance": 10,
        },
        "abilities": [],
        "loot": {"cracked_club": 0.05, "bone_shield": 0.03, "iron_mace": 0.02, "chain_vest": 0.01},
        "xp_given_by_lvl": 30,
        "sounds": _NO_SOUNDS,
    },
    "Elfo": {
        "race": "Humanoide", "entity_class": "Hunter", "is_ranged": True,
        "color": (78, 188, 108), # verde claro vibrante
        "attributes": {
            "health": 45, "armor": 8,
            "attack_min": 2, "attack_max": 4, "attack_power": 2,
            "move_speed_pct": 90, "attack_speed": 2.2,
            "acerto": 90, "crit_chance": 15,
        },
        "abilities": ["poison_arrow"],
        "loot": {"hunter_helm": 0.03, "hunter_boots": 0.03, "arcane_wand": 0.02, "silk_robe": 0.01},
        "xp_given_by_lvl": 22,
        "sounds": _NO_SOUNDS,
    },
    "Minotauro": {
        "race": "Bestial", "entity_class": "Warrior", "is_ranged": False,
        "color": (118, 58, 38),  # marrom-avermelhado
        "attributes": {
            "health": 110, "armor": 20,
            "attack_min": 5, "attack_max": 9, "attack_power": 4,
            "move_speed_pct": 70, "attack_speed": 3.2,
            "acerto": 88, "crit_chance": 15,
        },
        "abilities": [],
        "loot": {"apprentice_axe": 0.03, "chain_vest": 0.02, "iron_helm": 0.02, "iron_shield": 0.01},
        "xp_given_by_lvl": 35,
        "sounds": _NO_SOUNDS,
    },
    "Vampiro": {
        "race": "Morto-Vivo", "entity_class": "Warlock", "is_ranged": True,
        "color": (88, 0, 118),   # roxo escuro
        "attributes": {
            "health": 80, "armor": 12,
            "attack_min": 3, "attack_max": 6, "attack_power": 3,
            "move_speed_pct": 75, "attack_speed": 2.5,
            "acerto": 90, "crit_chance": 18,
        },
        "abilities": ["drain_life"],
        "loot": {"arcane_gloves": 0.03, "runed_wrists": 0.02, "shadow_blade": 0.01, "ring_power": 0.005},
        "xp_given_by_lvl": 40,
        "sounds": _NO_SOUNDS,
    },
    "Dragão": {
        "race": "Dragão", "entity_class": "Mage", "is_ranged": True,
        "color": (178, 28, 18),  # vermelho sangue
        "attributes": {
            "health": 200, "armor": 25,
            "attack_min": 8, "attack_max": 14, "attack_power": 6,
            "move_speed_pct": 60, "attack_speed": 3.0,
            "acerto": 92, "crit_chance": 20,
        },
        "abilities": [],
        "loot": {"plate_armor": 0.02, "war_hammer": 0.02, "lich_robe": 0.01, "mystic_staff": 0.005},
        "xp_given_by_lvl": 80,
        "sounds": _NO_SOUNDS,
    },
}
