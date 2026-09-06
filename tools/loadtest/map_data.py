"""
map_data.py — dados reais de map_1 carregados OFFLINE, mesmo código que
o servidor usa (sem rede, sem client gráfico), pra dar ao bot
conhecimento de mundo que ele não tem sozinho (05/09/2026, ver
PROBLEMAS_ARQUITETURA.md §114/§115).

Achado que motiva este módulo: o servidor NUNCA calcula rota pro
player (`server/world_server.py::move_player` só valida passo-a-passo:
orçamento de velocidade + tile andável + linha reta sem parede) — quem
calcula o caminho inteiro é o CLIENT (`ui/systems.py::_process_ground_move`/
`_auto_move_step`, via `engine.world_systems.find_path`), mandando 1
`MOVE` por tile conforme anda. Pra o bot headless "andar até um ponto
distante" de verdade, ele precisa replicar esse cálculo — código
compartilhado, sem pygame, chamável direto aqui. Isso também responde
a preocupação de quem escreveu o bot: pathfinding do bot NUNCA consome
`MAX_PATHFINDS_PER_FRAME` do servidor (esse orçamento só existe pra IA
de mob/minion, `EnemyAISystem`/`MinionSystem`) — o bot roda A* no
PRÓPRIO processo do swarm, o servidor nem participa dessa conta.

Mesmo raciocínio pra "tile de arbusto real" (`server/bush_zone_processor.py::
_is_on_bush_tile` — fonte é `TileType.is_bush`, nunca zona sintética) e
pra "ponto de interesse conhecido" (zonas de spawn reais do mapa, nunca
coordenada aleatória crua — evita mirar num ponto inalcançável/dentro
de parede, mesma recomendação da pesquisa de bots de MMO real: manter
uma lista de pontos JÁ validados, não descobrir em runtime)."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

_MAP_FILE = "maps/map_1.csv"

_bush_tiles: "list[tuple[int, int]] | None" = None
_poi_points: "list[tuple[int, int]] | None" = None
_ready = False


def _ensure_loaded() -> None:
    global _bush_tiles, _poi_points, _ready
    if _ready:
        return
    from engine.world import World
    from engine.map_loader import load_map_csv
    from engine.entity_factory import create_tilemap
    from engine.components import Tilemap
    from engine.world_systems import PathfindingSystem, register_services

    terrain, objects, spawn_points, visual = load_map_csv(_MAP_FILE)
    world = World()
    tme = create_tilemap(world, terrain, objects, visual)
    tm = world.get_component(tme, Tilemap)

    bushes = []
    for ty, row in enumerate(tm.tile_matrix):
        for tx, tile in enumerate(row):
            if tile is not None and getattr(tile, "is_bush", False):
                bushes.append((tx, ty))
    _bush_tiles = bushes

    # Zonas de spawn reais — sempre em terreno andável (é onde o próprio
    # jogo spawna mob de verdade), único candidato aceito a "ponto de
    # interesse distante" pro bot mirar. Nunca coordenada aleatória crua.
    # `dict.fromkeys` deduplica preservando ordem (várias zonas de tipos
    # de inimigo diferentes podem compartilhar o mesmo centro x,y).
    _poi_points = list(dict.fromkeys(
        (int(z["x"]), int(z["y"])) for z in spawn_points.get("spawn_zones", [])
        if "x" in z and "y" in z))

    # Serviço global (mesmo módulo compartilhado client/server,
    # `engine.world_systems`) — registrado 1x pro processo do swarm
    # inteiro; todos os bots de mundo (mapa único, map_1) reusam a
    # MESMA instância, sem custo de reconstrução por bot.
    register_services(pathfinding=PathfindingSystem(world, tme))
    _ready = True


def bush_tiles() -> list:
    """Todo tile (tx, ty) de `map_1` que é um arbusto de verdade (mesma
    fonte que o servidor usa pra stealth, `TileType.is_bush`)."""
    _ensure_loaded()
    return _bush_tiles


def poi_points() -> list:
    """Centros de zona de spawn reais de `map_1` — pontos garantidamente
    andáveis, únicos aceitos como destino de movimento longo."""
    _ensure_loaded()
    return _poi_points


def find_path(start: tuple, end: tuple) -> "list | None":
    """A* real (mesmo algoritmo do client/servidor), rodando no PRÓPRIO
    processo do bot — nunca consome o orçamento de pathfinding do
    servidor (`MAX_PATHFINDS_PER_FRAME` só existe pra IA de mob/minion).
    `manhattan_limit=None` + `max_nodes` escalado pela distância — MESMA
    fórmula que o CLIENT DE VERDADE já usa pra clique-no-chão
    (`ui/systems.py::_process_ground_move`, linha ~1237:
    `min(30000, max(8000, dist*80))`), já validada em produção. O
    default de `max_nodes=300` (pensado pra IA de mob, raio curto)
    achava 'sem caminho' pra qualquer travessia real de mapa grande —
    não porque não existe caminho, só porque desistia cedo demais."""
    _ensure_loaded()
    from engine.world_systems import find_path as _fp
    dist = abs(end[0] - start[0]) + abs(end[1] - start[1])
    nodes_limit = min(30000, max(8000, dist * 80))
    return _fp(start, end, max_nodes=nodes_limit, manhattan_limit=None)
