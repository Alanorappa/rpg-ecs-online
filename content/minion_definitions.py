"""
minion_definitions.py — Tabela central de TIPOS de minion (30/07/2026,
pedido do usuário). Mesmo espírito de content/tower_definitions.py::
TOWER_TABLE — tabela SEPARADA de MOB_TABLE (minion não passa pelo
lookup de XP por nome em MOB_TABLE, ver server/server_death_handler.py)
porque a entidade é construída manualmente (entity_factory.py::
create_minion), sem passar por _build_combat_entity (que sempre anexa
AIControlled — minion usa MinionSystem próprio, engine/world_systems.py,
não EnemyAISystem).

Cada entrada define o TIPO de minion (aparência/atributos/sabor de
ataque/recompensa) — parâmetros de INSTÂNCIA (facção/lane/nível/rota)
NÃO entram aqui, vêm da colocação/wave no mapa (maps/{mapa}_
entities.json::"minion_lanes", ver engine/map_loader.py).

attributes: MESMO formato de MOB_TABLE["attributes"]/TOWER_TABLE
["attributes"] — health/armor/attack_min/attack_max/attack_power/
attack_speed/acerto/crit_chance. move_speed_pct é REAL aqui (diferente
de Torre, que nunca se move) — usado por create_minion pra calcular
TileMovement.speed = ENEMY_SPEED * move_speed_pct/100, igual
_build_combat_entity já faz pra mob comum.

"color"/"is_ranged": mesma razão de TOWER_TABLE — exigidos pelo lookup
em _build_combat_entity quando o CLIENTE reconstrói o espelho remoto do
minion (MOB_TABLE.get(race) or TOWER_TABLE.get(race) or
MINION_TABLE.get(race)).

entity_class: reaproveita PROJECTILE_BY_CLASS (mob_definitions.py) —
"Arqueiro" = flecha marrom. Zero asset novo.

attack_range_tiles: alcance de ATAQUE (adjacente pra melee, à distância
pra ranged).

aggro_range_tiles: raio de PERCEPÇÃO — separado de attack_range_tiles
(diferente de Torre, que usa o mesmo valor pros dois papéis) porque
minion precisa perceber um hostil de mais longe do que consegue atacar
(mesma distinção que AGGRO_RADIUS_TILES vs attack_range_tiles já fazem
no EnemyAISystem compartilhado, só que POR TIPO em vez de constante
global — mesma filosofia da correção de vision_radius_tiles da Torre,
ARQUITETURA_ONLINE.md §34.72.2).

tier: indexa ENEMY_TIER_CONFIGS (entity_factory.py) — "rare" já existe
lá (hp×3/dmg×2/xp×4) pro minion_ranged_raro, sem precisar inventar tier
novo.

xp_reward: recompensa FLAT ao matar o minion — usada diretamente por
server/server_death_handler.py, nunca cai no fallback de MOB_TABLE/
EnemyTier (minion não está cadastrado lá).

gold_min/gold_max (01/08/2026 — antes ficava de fora por pedido
explícito do usuário, "moeda pra outro momento"; agora implementado
como recompensa de OURO DE INSTÂNCIA): mesmo formato de TOWER_TABLE —
`random.randint(gold_min, gold_max)` concedido via
`server/instance_progression.py::grant_instance_gold()`, SÓ pro
`killer_eid` (quem dá o dano que MATA, não `first_attacker_eid`/dono de
loot) e SÓ se ele estiver em progressão normalizada de instância — não
tem relação com o `coins`/loot do mundo aberto (esse continua 0 pra
minion, `loot_items=[]` fixo, ver server_death_handler.py).
"""

MINION_TABLE: dict[str, dict] = {
    "minion_melee": {
        "display_name": "Minion",
        "entity_class": "Guerreiro",
        "race": "Humanoide",
        "tier": "normal",
        "color": (200, 60, 60),
        "is_ranged": False,
        "attributes": {
            "health": 175, 
            "armor": 5,
            "attack_min": 4, 
            "attack_max": 4, 
            "attack_power": 1,
            "move_speed_pct": 100,
            "attack_speed": 2.1,
            "acerto": 90, 
            "crit_chance": 0,
        },
        "attack_range_tiles": 1,
        "aggro_range_tiles": 4,
        "xp_reward": 45,
        "gold_min": 25, "gold_max": 25,
        "sounds": {"attack_melee": "hit_normal"},
    },
    "minion_ranged": {
        "display_name": "Minion Arqueiro",
        "entity_class": "Arqueiro",
        "race": "Humanoide",
        "tier": "normal",
        "color": (60, 140, 60),
        "is_ranged": True,
        "attributes": {
            "health": 100, 
            "armor": 2,
            "attack_min": 6, 
            "attack_max": 6, 
            "attack_power": 1,
            "move_speed_pct": 100,
            "attack_speed": 2.3,
            "acerto": 90, 
            "crit_chance": 0,
        },
        "attack_range_tiles": 4,
        "aggro_range_tiles": 4,
        "xp_reward": 40,
        "gold_min": 30, "gold_max": 30,
        "sounds": {"attack_ranged": "arrow_release", "attack_impact": "arrow_impact"},
    },
    "minion_ranged_raro": {
        "display_name": "Minion Arqueiro de Elite",
        "entity_class": "Arqueiro",
        "race": "Humanoide",
        "tier": "rare",
        "color": (220, 170, 40),
        "is_ranged": True,
        "attributes": {
            "health": 260, 
            "armor": 4,
            "attack_min": 8, 
            "attack_max": 8, 
            "attack_power": 8,
            "move_speed_pct": 100,
            "attack_speed": 1.3,
            "acerto": 92, 
            "crit_chance": 10,
        },
        "attack_range_tiles": 4,
        "aggro_range_tiles": 5,
        "xp_reward": 150,
        "gold_min": 80, "gold_max": 80,
        "sounds": {"attack_ranged": "arrow_release", "attack_impact": "arrow_impact"},
    },
}
