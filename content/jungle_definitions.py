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
        "display_name": "Lobo Alfa da Jungle",
        "entity_class": "Guerreiro",
        "race": "Fera",
        "color": (90, 70, 60),
        "is_ranged": False,
        "attributes": {
            "health": 800,
            "armor": 5,
            "attack_min": 20,
            "attack_max": 30,
            "attack_power": 15,
            "move_speed_pct": 100,
            "attack_speed": 2.0,
            "acerto": 95,
            "crit_chance": 10,
        },
        # Ponto de partida, não decisão final de balanceamento.
        "xp_reward": 40,
        "gold_min": 5,
        "gold_max": 10,
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
            "health": 4000,
            "armor": 15,
            "attack_min": 60,
            "attack_max": 90,
            "attack_power": 40,
            "move_speed_pct": 90,
            "attack_speed": 1.5,
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
}
