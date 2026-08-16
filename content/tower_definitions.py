"""
tower_definitions.py — Tabela central de TIPOS de torre (29/07/2026,
pedido do usuário). Mesmo espírito de content/mob_definitions.py::
MOB_TABLE, mas propositalmente SEPARADA dela — torre não é um "mob":
não passa pelo lookup de XP/ouro por nome em MOB_TABLE (ver
server/server_death_handler.py), tem tabela/valores de recompensa
próprios.

Cada entrada define o TIPO de torre (aparência/atributos/sabor de
ataque/recompensa) — parâmetros de INSTÂNCIA (facção, respawnável,
tempo de respawn, regenera vida, nível, posição) NÃO entram aqui, vêm
da colocação no mapa (maps/{mapa}_entities.json::"towers", ver
engine/map_loader.py) — a MESMA torre pode respawnar no mundo aberto e
não respawnar dentro de uma arena, por exemplo.

attributes: MESMO formato de MOB_TABLE["attributes"] (ver
content/mob_definitions.py, docstring do topo) — health/armor/
attack_min/attack_max/attack_power/attack_speed/acerto/crit_chance.
move_speed_pct sempre presente mas irrelevante (torre nunca se move de
verdade — TileMovement.speed sempre 0.0, entity_factory.create_tower);
só existe pra satisfazer o acesso obrigatório de
engine/entity_factory.py::_build_combat_entity (chamada pelo CLIENTE ao
reconstruir o espelho remoto da torre, ver comentário lá).

"color"/"is_ranged": mesma razão — exigidos pelo lookup em
_build_combat_entity, sem equivalente direto no resto desta definição.

entity_class: reaproveita o sabor de projétil já existente por classe
(PROJECTILE_BY_CLASS, mob_definitions.py) — "Mago" = bola de fogo
laranja, "Arqueiro" = flecha marrom. Zero asset novo.

attack_range_tiles: alcance de detecção/ataque da torre, em tiles.

vision_radius_tiles: raio de VISÃO COMPARTILHADA que a torre concede ao
time (30/07/2026, pedido do usuário — pediu explicitamente pra poder
ajustar por tipo de torre, em vez de uma constante global única) —
DIFERENTE de attack_range_tiles (esse é só pra decidir quem a torre
ataca). Lido por server/session.py::SessionManager.
_compute_ally_vision_centers() direto do componente `Tower`
(engine/entity_factory.py::create_tower grava aqui na criação). Default
(se omitido) é shared/constants.py::ALLY_VISION_RADIUS_TOWER.

xp_reward/gold_min/gold_max: recompensa FLAT ao destruir a torre —
usada diretamente por server/server_death_handler.py, nunca cai no
fallback de MOB_TABLE/EnemyTier (torre não está cadastrada lá).
"""

TOWER_TABLE: dict[str, dict] = {
    "torre_de_fogo": {
        "display_name": "Torre de Fogo",
        "entity_class": "Mago",
        "race": "Construcao",
        "tier": "elite",
        # "color"/"is_ranged": únicos campos SEM equivalente direto no
        # resto da definição, mas exigidos por engine/entity_factory.py::
        # _build_combat_entity (mesma função usada pra reconstruir a
        # torre no CLIENTE — ver comentário lá sobre o merge com
        # MOB_TABLE). Sem eles, o lookup quebra (KeyError) OU cai no
        # fallback genérico.
        "color": (120, 120, 130),
        # sprite_id (12/08/2026, pedido do usuário): tile "gcn_19" (`engine/
        # tileset.py`, seção "TX Grades2", prefixo "gcn" + sufixo "19") —
        # mesmo catálogo de sprite de objeto de mapa que harvestable já usa
        # via Renderable.sprite_id (ui/systems.py::RenderSystem.render()),
        # nunca reimplementar um mecanismo de sprite novo pra torre.
        "sprite_id": "gcn_19",
        # projectile_origin_offset (12/08/2026, pedido do usuário — "de
        # qual parte do sprite surge os projéteis"): pedido em pixel
        # LOCAL do sprite (x=31,y=56, origem top-left) — convertido aqui
        # pra deslocamento em pixels a partir da Position da torre
        # (formato que `_spawn_attack_projectile` consome direto), MESMA
        # âncora de base que o RenderSystem usa pra desenhar o sprite
        # (`ui/systems.py::RenderSystem.render`, branch `_sprite_rnd`):
        #   offset_x = local_x - TILE_SIZE/2      = 31 - 16 = 15
        #   offset_y = local_y - sprite_h + TILE_SIZE/2 = 56 - 128 + 16 = -56
        # (sprite_h=128 é a altura de "gcn_19" — se o sprite mudar, refazer a conta).
        "projectile_origin_offset": (15.0, -56.0),
        "is_ranged": True,
        "attributes": {
            "health": 2500,
            "armor": 20,
            "attack_min": 15,
            "attack_max": 25,
            "attack_power": 1,
            # move_speed_pct: torre nunca se move de verdade (TileMovement.
            # speed sempre 0.0 no servidor, create_tower) — só existe aqui
            # pra satisfazer o acesso `attrs["move_speed_pct"]` (bracket,
            # sem default) de _build_combat_entity, que o CLIENTE chama
            # ao reconstruir o espelho remoto da torre (ver comentário lá).
            # Nunca 0 (viraria divisão por zero em move_duration).
            "move_speed_pct": 1,
            "attack_speed": 1.8,
            "acerto": 100, 
            "crit_chance": 5,
        },
        "attack_range_tiles": 8,
        "vision_radius_tiles": 8,
        "xp_reward": 250,
        "gold_min": 20, 
        "gold_max": 40,
        # Mesmos arquivos REAIS já usados por "Mago (NPC)" (mob_
        # definitions.py) pra espelhar a Bola de Fogo do mago jogador —
        # nunca inventar nome de asset novo sem conferir se o .ogg existe
        # em assets/sounds/sfx/ (bug real: nomes fictícios aqui faziam a
        # torre de fogo não emitir NENHUM som).
        "sounds": {
            "attack_magic": "skill_bola_de_fogo_launch",
            "attack_impact": "skill_bola_de_fogo_impact",
        },
    },
    "torre_de_flechas": {
        "display_name": "Torre de Flechas",
        "entity_class": "Arqueiro",
        "race": "Construcao",
        "tier": "elite",
        "color": (120, 120, 130),
        # sprite_id/projectile_origin_offset: ver comentário na torre_de_fogo
        # acima — mesmo sprite, mesma conta.
        "sprite_id": "gcn_19",
        "projectile_origin_offset": (15.0, -56.0),
        "is_ranged": True,
        "attributes": {
            "health": 2500,
            "armor": 15,
            "attack_min": 15, 
            "attack_max": 20, 
            "attack_power": 1,
            "move_speed_pct": 1,   # ver comentário na torre_de_fogo acima
            "attack_speed": 1.8,
            "acerto": 100, 
            "crit_chance": 10,
        },
        "attack_range_tiles": 8,
        "vision_radius_tiles": 8,
        "xp_reward": 250,
        "gold_min": 18, 
        "gold_max": 36,
        # Mesmos arquivos REAIS já usados por "Arqueiro (NPC)" — ver
        # comentário acima sobre nunca inventar nome de asset.
        "sounds": {
            "attack_ranged": "arrow_release",
            "attack_impact": "arrow_impact",
        },
    },
}
