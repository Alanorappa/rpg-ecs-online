"""
server/world_server.py
ECS headless — roda toda a lógica do jogo SEM Pygame.

Responsabilidades:
  - Manter o World ECS (entidades + componentes)
  - Criar/remover entidades de jogadores
  - Processar movimento com validação
  - Guardar histórico de snapshots para lag compensation
  - Notificar SessionManager sobre deltas a cada tick
"""
from __future__ import annotations
import asyncio
import sys
import os
import time
import random

# Pygame headless — servidor não tem display mas os sistemas usam pygame internamente
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world import World
from shared.constants import TICK_RATE, TICK_INTERVAL, SNAPSHOT_HISTORY, TILE_SIZE


class WorldServer:

    MAP_FILE = "maps/map_1.csv"   # mapa padrão carregado pelo servidor

    def __init__(self, zone_id: str = "world_main", map_file: str = ""):
        self.zone_id    = zone_id
        self.world      = World()
        self.tick_count = 0
        self.running    = False

        # session_id → entity_id dos jogadores online
        self._player_eids: dict[str, int] = {}

        # Eids de mobs gerenciados pelo servidor
        self._mob_eids: set[int] = set()

        # Deltas acumulados no tick atual (limpos ao fim de cada tick)
        self._moved_this_tick:    list[dict] = []
        self._spawned_this_tick:  list[dict] = []
        self._despawned_this_tick: list[int] = []

        # Histórico de snapshots
        self._snapshots: list[tuple[int, dict]] = []

        # Callbacks do SessionManager
        self._on_tick_callbacks: list = []
        self._systems: list = []

        self._map_file = map_file or self.MAP_FILE
        self._load_map()

    # ── Inicialização do mundo ────────────────────────────────────────────────

    def _load_map(self) -> None:
        """Carrega mapa, cria SpawnZones e inicializa sistemas de mob (headless)."""
        from map_loader import load_map_csv
        from entity_factory import create_tilemap
        from systems import SpawnZoneSystem, EnemyAISystem, EnemyAbilitySystem

        print(f"[WorldServer] carregando mapa: {self._map_file}")
        terrain_matrix, object_matrix, spawn_points, terrain_visual = \
            load_map_csv(self._map_file)

        self.tilemap_entity = create_tilemap(
            self.world, terrain_matrix, object_matrix, terrain_visual)

        self._create_spawn_zones(spawn_points.get("spawn_zones", []))

        # Sistemas de lógica rodando no servidor (sem render)
        self._enemy_ai_system = EnemyAISystem(self.world, player_entity_id=-1)
        self._enemy_ab_system = EnemyAbilitySystem(self.world, player_entity_id=-1)
        self._systems = [
            SpawnZoneSystem(self.world),
            self._enemy_ai_system,
            self._enemy_ab_system,
        ]
        print(f"[WorldServer] mapa OK — SpawnZoneSystem + EnemyAISystem ativos")

    def _create_spawn_zones(self, zones_data: list) -> None:
        """Cria entidades SpawnZone a partir dos dados já processados pelo map_loader.
        Cada entrada já é uma zona achatada com enemy_type, enemy_tier, count."""
        from components import SpawnZone

        for z in zones_data:
            count = z.get("count", 1)
            if count == 0:
                continue
            zone_eid = self.world.create_entity()
            self.world.add_component(zone_eid, SpawnZone(
                center_x       = z.get("x",  0),
                center_y       = z.get("y",  0),
                radius         = z.get("radius", 10),
                enemy_type     = z.get("enemy_type",  "melee"),
                enemy_tier     = z.get("enemy_tier",  "normal"),
                max_count      = count,
                respawn_cooldown = float(z.get("respawn_cooldown", 90.0)),
                level_min      = z.get("level_min",  1),
                level_max      = z.get("level_max",  5),
                race           = z.get("race",          "Humanoide"),
                entity_class   = z.get("entity_class",  ""),
            ))

    # ── API pública para SessionManager ──────────────────────────────────────

    def spawn_player(self, session_id: str, char_data: dict) -> int:
        """
        Cria entidade do jogador no ECS.
        Retorna entity_id. Chamado pelo SessionManager no login.
        """
        from components import Position, TileMovement, PlayerControlled

        tx = int(char_data.get("tile_x", 10))
        ty = int(char_data.get("tile_y", 10))
        px = tx * TILE_SIZE + TILE_SIZE // 2
        py = ty * TILE_SIZE + TILE_SIZE // 2

        eid = self.world.create_entity()
        self.world.add_component(eid, Position(x=px, y=py, prev_x=px, prev_y=py))
        self.world.add_component(eid, TileMovement(
            current_tile_x=tx, current_tile_y=ty,
            target_tile_x=tx,  target_tile_y=ty,
        ))
        # PlayerControlled permite que SpawnZoneSystem e EnemyAISystem encontrem o player
        self.world.add_component(eid, PlayerControlled())

        self._player_eids[session_id] = eid

        self._spawned_this_tick.append({
            "eid":      eid,
            "kind":     "player",
            "tx":       tx,
            "ty":       ty,
            "name":     char_data.get("name", session_id),
            "class_id": char_data.get("class_id", "guerreiro"),
            "hp":       char_data.get("hp",  100),
            "hp_max":   char_data.get("hp",  100),
            "level":    char_data.get("level", 1),
            "effects":  [],
        })

        print(f"[World] spawn player eid={eid}  tile=({tx},{ty})  "
              f"name={char_data.get('name', '?')}")
        return eid

    def despawn_player(self, session_id: str) -> None:
        """Remove entidade do jogador. Chamado no logout/disconnect."""
        from components import TileMovement, Position
        eid = self._player_eids.pop(session_id, None)
        if eid is None:
            return
        self._despawned_this_tick.append(eid)
        self.world.remove_entity(eid)
        print(f"[World] despawn player eid={eid}  session={session_id}")

    def move_player(self, session_id: str, tx: int, ty: int) -> bool:
        """
        Valida e aplica movimento de 1 tile para o jogador.
        Retorna True se movimento aceito, False se rejeitado.
        """
        from components import TileMovement, Position

        eid = self._player_eids.get(session_id)
        if eid is None:
            return False

        tm = self.world.get_component(eid, TileMovement)
        if tm is None:
            return False

        # Validação: máximo 1 tile de distância por move
        dx = abs(tx - tm.current_tile_x)
        dy = abs(ty - tm.current_tile_y)
        if dx > 1 or dy > 1:
            return False

        # TODO: validar walkability com tilemap

        from_tx, from_ty = tm.current_tile_x, tm.current_tile_y
        tm.current_tile_x = tx
        tm.current_tile_y = ty
        tm.target_tile_x  = tx
        tm.target_tile_y  = ty

        pos = self.world.get_component(eid, Position)
        if pos:
            pos.prev_x, pos.prev_y = pos.x, pos.y
            pos.x = tx * TILE_SIZE + TILE_SIZE // 2
            pos.y = ty * TILE_SIZE + TILE_SIZE // 2

        self._moved_this_tick.append({
            "eid":     eid,
            "tx":      tx, "ty":      ty,
            "from_tx": from_tx, "from_ty": from_ty,
        })
        return True

    def get_players_in_aoi(self, center_session: str, radius: int) -> list[dict]:
        """
        Retorna lista de EntitySpawn para todos os jogadores dentro do raio
        ao redor do jogador `center_session`. Usado no WORLD_STATE inicial.
        """
        from components import TileMovement
        from shared.constants import AOI_RADIUS

        r     = radius or AOI_RADIUS
        eid_c = self._player_eids.get(center_session)
        if eid_c is None:
            return []

        tm_c = self.world.get_component(eid_c, TileMovement)
        if tm_c is None:
            return []

        result = []
        for sid, eid in self._player_eids.items():
            if eid == eid_c:
                continue
            tm = self.world.get_component(eid, TileMovement)
            if tm is None:
                continue
            if (abs(tm.current_tile_x - tm_c.current_tile_x) <= r and
                    abs(tm.current_tile_y - tm_c.current_tile_y) <= r):
                result.append({
                    "eid": eid, "kind": "player",
                    "tx": tm.current_tile_x, "ty": tm.current_tile_y,
                    "session_id": sid,
                })
        return result

    def get_mobs_in_aoi(self, center_tx: int, center_ty: int, radius: int) -> list[dict]:
        """Retorna lista de mobs no AOI — para WORLD_STATE inicial."""
        from components import TileMovement, CombatStats, AIControlled, Renderable
        result = []
        for eid in self._mob_eids:
            tm = self.world.get_component(eid, TileMovement)
            if not tm:
                continue
            if abs(tm.current_tile_x - center_tx) <= radius and \
               abs(tm.current_tile_y - center_ty) <= radius:
                cs  = self.world.get_component(eid, CombatStats)
                ai  = self.world.get_component(eid, AIControlled)
                ren = self.world.get_component(eid, Renderable)
                result.append({
                    "eid":    eid,
                    "kind":   "enemy",
                    "tx":     tm.current_tile_x,
                    "ty":     tm.current_tile_y,
                    "mob_id": ai.entity_class if ai else "",
                    "color":  list(ren.color) if ren else [150, 60, 60],
                    "hp":     cs.current_hp if cs else 50,
                    "hp_max": cs.max_hp if cs else 50,
                    "level":  cs.level if cs else 1,
                    "effects": [],
                })
        return result

    def get_entity_id(self, session_id: str) -> int:
        return self._player_eids.get(session_id, -1)

    def get_tile_pos(self, session_id: str) -> tuple[int, int]:
        from components import TileMovement
        eid = self._player_eids.get(session_id)
        if eid is None:
            return (0, 0)
        tm = self.world.get_component(eid, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else (0, 0)

    # ── Loop de ticks ─────────────────────────────────────────────────────────

    def register_on_tick(self, callback) -> None:
        self._on_tick_callbacks.append(callback)

    async def run(self) -> None:
        self.running = True
        print(f"[WorldServer] zona='{self.zone_id}' @ {TICK_RATE} ticks/s")

        next_tick = time.perf_counter()
        while self.running:
            now = time.perf_counter()
            if now >= next_tick:
                self._tick(TICK_INTERVAL)
                next_tick += TICK_INTERVAL
                if time.perf_counter() - next_tick > TICK_INTERVAL:
                    next_tick = time.perf_counter()
            else:
                await asyncio.sleep(0)

    def _tick(self, dt: float) -> None:
        self.tick_count += 1

        # Atualiza player_entity_id da IA para o primeiro jogador online
        # (IA simples: todos os mobs agro o mesmo player; multi-player será melhorado)
        first_player_eid = next(iter(self._player_eids.values()), -1)
        if hasattr(self, "_enemy_ai_system"):
            self._enemy_ai_system.player_entity_id = first_player_eid
        if hasattr(self, "_enemy_ab_system"):
            self._enemy_ab_system.player_entity_id = first_player_eid

        # Snapshot de posições ANTES do tick (para detectar mobs que se moveram)
        from components import TileMovement, Enemy
        pre_mob_pos: dict[int, tuple[int, int]] = {}
        for eid, tm in self.world.get_entities_with(TileMovement):
            if eid in self._mob_eids:
                pre_mob_pos[eid] = (tm.current_tile_x, tm.current_tile_y)

        for system in self._systems:
            system.update(dt=dt)

        # Detecta novos mobs criados pelo SpawnZoneSystem neste tick
        for eid, tm in self.world.get_entities_with(TileMovement):
            if self.world.get_component(eid, Enemy) and eid not in self._mob_eids \
                    and eid not in self._player_eids.values():
                self._mob_eids.add(eid)
                self._emit_mob_spawn(eid, tm)

        # Detecta mobs que se moveram neste tick
        for eid in list(self._mob_eids):
            tm = self.world.get_component(eid, TileMovement)
            if tm is None:
                # Mob foi removido (morreu)
                self._mob_eids.discard(eid)
                self._despawned_this_tick.append(eid)
                continue
            old = pre_mob_pos.get(eid)
            new = (tm.current_tile_x, tm.current_tile_y)
            if old and old != new:
                self._moved_this_tick.append({
                    "eid": eid, "tx": new[0], "ty": new[1],
                    "from_tx": old[0], "from_ty": old[1],
                })

        deltas = self._collect_deltas()
        self._store_snapshot()

        for cb in self._on_tick_callbacks:
            cb(self.tick_count, deltas)

    def _emit_mob_spawn(self, eid: int, tm) -> None:
        """Monta payload de ENTITY_SPAWN para um novo mob e coloca em spawned_this_tick."""
        from components import CombatStats, AIControlled, Renderable, SpawnZoneOwner, SpawnZone
        cs   = self.world.get_component(eid, CombatStats)
        ai   = self.world.get_component(eid, AIControlled)
        ren  = self.world.get_component(eid, Renderable)
        szo  = self.world.get_component(eid, SpawnZoneOwner)
        # Pega metadados da zona de origem para o cliente recriar o mob
        race         = "Humanoide"
        entity_class = ai.entity_class if ai else "Warrior"
        tier         = "normal"
        is_ranged    = False
        if szo:
            zone = self.world.get_component(szo.zone_entity_id, SpawnZone)
            if zone:
                race         = zone.race
                entity_class = zone.entity_class or entity_class
                tier         = zone.enemy_tier
                is_ranged    = (zone.enemy_type == "ranged")

        self._spawned_this_tick.append({
            "eid":          eid,
            "kind":         "enemy",
            "tx":           tm.current_tile_x,
            "ty":           tm.current_tile_y,
            "race":         race,
            "entity_class": entity_class,
            "tier":         tier,
            "is_ranged":    is_ranged,
            "color":        list(ren.color) if ren else [150, 60, 60],
            "hp":           cs.current_hp if cs else 50,
            "hp_max":       cs.max_hp     if cs else 50,
            "level":        zone.level_min if szo and zone else 1,
            "effects":      [],
        })

    def _collect_deltas(self) -> dict:
        deltas = {
            "moved":     list(self._moved_this_tick),
            "stats":     [],
            "effects":   [],
            "spawned":   list(self._spawned_this_tick),
            "despawned": list(self._despawned_this_tick),
        }
        self._moved_this_tick.clear()
        self._spawned_this_tick.clear()
        self._despawned_this_tick.clear()
        return deltas

    def _store_snapshot(self) -> None:
        from components import TileMovement
        snapshot: dict[int, tuple[int, int]] = {}
        for eid, tm in self.world.get_entities_with(TileMovement):
            snapshot[eid] = (tm.current_tile_x, tm.current_tile_y)
        self._snapshots.append((self.tick_count, snapshot))
        if len(self._snapshots) > SNAPSHOT_HISTORY:
            self._snapshots.pop(0)

    def get_snapshot_at(self, tick: int) -> dict:
        if not self._snapshots:
            return {}
        best = self._snapshots[0][1]
        for t, snap in self._snapshots:
            if t <= tick:
                best = snap
            else:
                break
        return best

    def stop(self) -> None:
        self.running = False
        print(f"[WorldServer] encerrado no tick {self.tick_count}")
