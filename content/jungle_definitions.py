"""
jungle_definitions.py — Tabela central de TIPOS de monstro de jungle
estilo MOBA (13/08/2026, pedido do usuário). Mesmo espírito de
content/tower_definitions.py: propositalmente SEPARADA de MOB_TABLE —
jungle mob/boss não passa pelo lookup de XP/ouro por nome/level de
MOB_TABLE (ver server/server_death_handler.py), tem regra de
recompensa PRÓPRIA (por proximidade, não por dano — mesmo espírito de
Minion, boss ainda restringe por TIME de quem deu o golpe final).

Cada entrada define o TIPO (aparência/atributos/recompensa) —
parâmetros de INSTÂNCIA (facção, posição, respawn_s) NÃO entram aqui,
vêm da colocação no mapa (maps/{mapa}_entities.json::"jungle_mobs"/
"jungle_bosses", ver engine/map_loader.py).

Entidade construída via `engine.entity_factory.create_enemy()` (mesma
função de mob hostil "clássico" — dá AIControlled/aggro/chase/leash de
graça, comportamento correto pra "monstro parado que persegue quem
chega perto e volta pro spawn"), com `JungleMob` anexado por cima
(engine/components.py) pra marcar a regra de recompensa própria.

attributes: MESMO formato de MOB_TABLE["attributes"] (ver
content/mob_definitions.py) — obrigatório porque
engine/entity_factory.py::_build_combat_entity (também usada pelo
CLIENTE pra reconstruir o espelho remoto) lê essa forma exata.

"aggro_range_tiles"/"attack_range_tiles" NÃO existem aqui de propósito
— `create_enemy()` usa os mesmos raios GLOBAIS de qualquer mob hostil
comum (`ENEMY_DETECTION_RADIUS`/`ENEMY_MELEE_ATTACK_RANGE`, engine/
entity_factory.py), nunca lidos de MOB_TABLE-like tables hoje (só
Minion/Tower têm campo próprio, porque MinionSystem/TowerSystem leem
direto do componente deles, não de _build_combat_entity). Ajuste por
tipo, se algum dia for pedido, precisa de mudança em create_enemy —
fora de escopo agora.

xp_reward/gold_min/gold_max: recompensa FLAT (mob normal) ou POOL
(boss — dividida por quem está perto do time vencedor) — sempre lida
via `JungleMob`, nunca via `XPReward` (removido logo após a criação,
ver WorldServer._create_jungle_camps) nem via xp_given_by_lvl.
"""

JUNGLE_MOB_TABLE: dict[str, dict] = {
    "jungle_lobo_alfa": {
        "display_name": "Lobo Alfa",
        "entity_class": "Guerreiro",
        "race": "Fera",
        "color": (90, 70, 60),
        "is_ranged": False,
        "attributes": {
            "health": 25,
            "armor": 5,
            "attack_min": 1,
            "attack_max": 3,
            "attack_power": 1,
            "move_speed_pct": 100,
            "attack_speed": 2.9,
            "acerto": 95,
            "crit_chance": 2,
        },
        # Ponto de partida, não decisão final de balanceamento.
        "xp_reward": 60,
        "gold_min": 5,
        "gold_max": 10,
        "sounds": {},
    },

    # A partir daqui: dados de PLACEHOLDER (16/08/2026, pedido explícito
    # do usuário — "crie agora com dados aleatórios e eu altero depois")
    # pra popular os 14 camps identificados em maps/concepts/
    # moba_bg_entities.png (ver PROBLEMAS_ARQUITETURA.md). Nenhum destes
    # números é balanceamento final.

    "jungle_javali_alfa": {
        # Camp SOLITÁRIO (1 mob só, mais forte) — M2/M4/M11/M13.
        "display_name": "Javali Alfa",
        "entity_class": "Guerreiro",
        "race": "Fera",
        "color": (110, 70, 40),
        "is_ranged": False,
        "attributes": {
            "health": 28,
            "armor": 8,
            "attack_min": 2,
            "attack_max": 3,
            "attack_power": 1,
            "move_speed_pct": 100,
            "attack_speed": 2.8,
            "acerto": 95,
            "crit_chance": 4,
        },
        "xp_reward": 70,
        "gold_min": 10,
        "gold_max": 18,
        "sounds": {},
    },
    "jungle_aranha_venenosa": {
        # Camp de TRIO — M5/M8/M10.
        "display_name": "Aranha Venenosa",
        "entity_class": "Guerreiro",
        "race": "Bestial",
        "color": (60, 20, 70),
        "is_ranged": False,
        "attributes": {
            "health": 28,
            "armor": 3,
            "attack_min": 2,
            "attack_max": 5,
            "attack_power": 2,
            "move_speed_pct": 100,
            "attack_speed": 2.9,
            "acerto": 92,
            "crit_chance": 5,
        },
        "xp_reward": 50,
        "gold_min": 4,
        "gold_max": 9,
        "sounds": {},
    },
    "jungle_harpia": {
        # Camp de TRIO, ranged — M7/M14.
        "display_name": "Harpia",
        "entity_class": "Arqueiro",
        "race": "Bestial",
        "color": (150, 120, 60),
        "is_ranged": True,
        "attributes": {
            "health": 35,
            "armor": 2,
            "attack_min": 3,
            "attack_max": 5,
            "attack_power": 2,
            "move_speed_pct": 100,
            "attack_speed": 2.7,
            "acerto": 90,
            "crit_chance": 8,
        },
        "xp_reward": 55,
        "gold_min": 5,
        "gold_max": 10,
        "sounds": {},
    },
    "jungle_raptor": {
        # Camp de QUARTETO (4 mobs fracos em grupo) — M3/M12.
        "display_name": "Raptor",
        "entity_class": "Guerreiro",
        "race": "Bestial",
        "color": (80, 90, 40),
        "is_ranged": False,
        "attributes": {
            "health": 25,
            "armor": 2,
            "attack_min": 2,
            "attack_max": 5,
            "attack_power": 2,
            "move_speed_pct": 100,
            "attack_speed": 2.5,
            "acerto": 90,
            "crit_chance": 10,
        },
        "xp_reward": 45,
        "gold_min": 3,
        "gold_max": 7,
        "sounds": {},
    },
}

JUNGLE_BOSS_TABLE: dict[str, dict] = {
    "jungle_boss_ancestral": {
        "display_name": "Ancestral da Jungle",
        "entity_class": "Guerreiro",
        "race": "Fera",
        "color": (150, 40, 40),
        "is_ranged": False,
        "attributes": {
            "health": 800,
            "armor": 15,
            "attack_min": 15,
            "attack_max": 18,
            "attack_power": 2,
            "move_speed_pct": 90,
            "attack_speed": 1.9,
            "acerto": 95,
            "crit_chance": 15,
        },
        # Ponto de partida, não decisão final de balanceamento.
        "xp_reward": 300,
        "gold_min": 50,
        "gold_max": 80,
        "sounds": {},
        # Ciclo de buff por abate — índice 0 é o 1º abate, e por diante.
        # Depois de esgotar a lista, o ÚLTIMO buff se repete pra sempre
        # (decisão do usuário, 13/08/2026) — ver
        # WorldServer._grant_jungle_boss_buff. `attribute`/`type` usam o
        # mesmo formato de engine.components.Modifier (ver
        # engine/skill_handlers.py::_skill_cancao_inspiracao pro
        # precedente de buff percentual temporário). `haste_rating` só
        # afeta velocidade de ATAQUE (attack_interval, engine/stat_fns.py)
        # — não existe modificador temporário de velocidade de MOVIMENTO
        # hoje (TileMovement.speed fica fora do sistema de
        # Modifier/timed_modifiers); catálogo inicial usa só o que já
        # existe, sem inventar mecanismo novo.
        "buff_cycle": [
            {"modifiers": [{"attribute": "attack_power", "value": 0.10, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "haste_rating", "value": 0.15, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "attack_power", "value": 0.10, "type": "percentage"},
                           {"attribute": "haste_rating", "value": 0.15, "type": "percentage"}],
             "duration": 90.0},
        ],
    },

    # PLACEHOLDER (16/08/2026, mesmo pedido do bloco de mobs normais
    # acima) — B2/B3/B4 de maps/concepts/moba_bg_entities.png. B1 usa o
    # "jungle_boss_ancestral" já existente.

    "jungle_boss_guardiao_de_pedra": {
        "display_name": "Guardião de Pedra",
        "entity_class": "Guerreiro",
        "race": "Elemental",
        "color": (110, 110, 120),
        "is_ranged": False,
        "attributes": {
            "health": 500,
            "armor": 30,
            "attack_min": 15,
            "attack_max": 18,
            "attack_power": 15,
            "move_speed_pct": 80,
            "attack_speed": 2.2,
            "acerto": 90,
            "crit_chance": 5,
        },
        "xp_reward": 320,
        "gold_min": 55,
        "gold_max": 85,
        "sounds": {},
        "buff_cycle": [
            {"modifiers": [{"attribute": "armor", "value": 0.15, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "stamina", "value": 0.10, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "armor", "value": 0.15, "type": "percentage"},
                           {"attribute": "stamina", "value": 0.10, "type": "percentage"}],
             "duration": 90.0},
        ],
    },
    "jungle_boss_dragao_jovem": {
        "display_name": "Dragão Jovem",
        "entity_class": "Mago",
        "race": "Dragão",
        "color": (180, 40, 150),
        "is_ranged": True,
        "attributes": {
            "health": 450,
            "armor": 10,
            "attack_min": 5,
            "attack_max": 25,
            "attack_power": 6,
            "move_speed_pct": 95,
            "attack_speed": 1.8,
            "acerto": 95,
            "crit_chance": 18,
        },
        "xp_reward": 310,
        "gold_min": 55,
        "gold_max": 85,
        "sounds": {},
        "buff_cycle": [
            {"modifiers": [{"attribute": "spell_power", "value": 0.15, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "crit_rating", "value": 0.10, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "spell_power", "value": 0.15, "type": "percentage"},
                           {"attribute": "crit_rating", "value": 0.10, "type": "percentage"}],
             "duration": 90.0},
        ],
    },
    "jungle_boss_horror_das_profundezas": {
        "display_name": "Horror das Profundezas",
        "entity_class": "Guerreiro",
        "race": "Morto-Vivo",
        "color": (40, 90, 60),
        "is_ranged": False,
        "attributes": {
            "health": 465,
            "armor": 18,
            "attack_min": 12,
            "attack_max": 19,
            "attack_power": 4,
            "move_speed_pct": 85,
            "attack_speed": 1.6,
            "acerto": 93,
            "crit_chance": 8,
        },
        "xp_reward": 320,
        "gold_min": 55,
        "gold_max": 85,
        "sounds": {},
        "buff_cycle": [
            {"modifiers": [{"attribute": "attack_power", "value": 0.12, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "crit_rating", "value": 0.10, "type": "percentage"}],
             "duration": 90.0},
            {"modifiers": [{"attribute": "attack_power", "value": 0.12, "type": "percentage"},
                           {"attribute": "crit_rating", "value": 0.10, "type": "percentage"}],
             "duration": 90.0},
        ],
    },
}
