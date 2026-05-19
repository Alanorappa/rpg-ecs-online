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
        self._despawned_this_tick: list[int]  = []
        self._combat_this_tick:        list[dict] = []
        self._player_deaths_this_tick: list[dict] = []   # mortes de players

        # Timer de ataque por jogador: session_id → segundos até próximo hit
        self._attack_timers: dict[str, float] = {}

        # Histórico de snapshots
        self._snapshots: list[tuple[int, dict]] = []

        # Callbacks do SessionManager
        self._on_tick_callbacks: list = []
        self._systems: list = []

        self._map_file = map_file or self.MAP_FILE
        self._load_map()

    # ── Inicialização do mundo ────────────────────────────────────────────────

    def _load_map(self) -> None:
        """
        Carrega mapa e inicializa os MESMOS sistemas do jogo offline (headless).
        Usa register_services() para habilitar deal_damage() e EnemyAISystem.
        """
        from map_loader import load_map_csv
        from entity_factory import create_tilemap
        from systems import (SpawnZoneSystem, EnemyAISystem, EnemyAbilitySystem,
                             TileValidationSystem, PathfindingSystem, CombatSystem,
                             TileMovementSystem, register_services)

        print(f"[WorldServer] carregando mapa: {self._map_file}")
        terrain_matrix, object_matrix, spawn_points, terrain_visual = \
            load_map_csv(self._map_file)

        self.tilemap_entity = create_tilemap(
            self.world, terrain_matrix, object_matrix, terrain_visual)

        self._create_spawn_zones(spawn_points.get("spawn_zones", []))

        # Cria os mesmos sistemas de serviço do game.py offline
        tile_validation = TileValidationSystem(self.world)
        pathfinding     = PathfindingSystem(self.world)
        combat          = CombatSystem(self.world)

        # Registra serviços — habilita deal_damage() e EnemyAISystem.pathfinding
        register_services(combat=combat, pathfinding=pathfinding,
                          tile_validation=tile_validation)

        # Sistemas de lógica idênticos ao offline — sem render, sem input
        self._enemy_ai_system = EnemyAISystem(self.world, player_entity_id=-1)
        self._enemy_ab_system = EnemyAbilitySystem(self.world, player_entity_id=-1)

        self._systems = [
            tile_validation,                          # cache de tiles ocupados
            SpawnZoneSystem(self.world),              # spawn de mobs (igual offline)
            self._enemy_ai_system,                    # IA: aggro, pathfinding, ataque
            self._enemy_ab_system,                    # habilidades especiais de mobs
            TileMovementSystem(self.world),           # avança progress→current_tile (headless)
        ]
        print(f"[WorldServer] mapa OK — EnemyAISystem real + register_services ativos")

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
        from components import Position, TileMovement, PlayerControlled, CombatState, CombatStats

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
        # PlayerControlled: SpawnZoneSystem e EnemyAISystem encontram o player
        self.world.add_component(eid, PlayerControlled())
        # CombatState: aggro e estado de combate
        self.world.add_component(eid, CombatState())
        # Visible: EnemyAISystem verifica se o player está "visível" para mobs
        from components import Visible as _Vis2
        self.world.add_component(eid, _Vis2())
        # CombatStats: prioridade — stats enviados pelo cliente > stats_json > fallback por classe
        import json as _json
        _cls          = char_data.get("class_id", "guerreiro")
        _stats_raw    = char_data.get("stats_json") or char_data.get("stats", {})
        _stats        = _json.loads(_stats_raw) if isinstance(_stats_raw, str) else (_stats_raw or {})
        _class_ap     = {"guerreiro": 20, "mago": 10, "arqueiro": 18}
        _class_hp     = {"guerreiro": 180, "mago": 100, "arqueiro": 120}
        _class_int    = {"guerreiro": 2.0, "mago": 2.5, "arqueiro": 1.8}
        # Stats do cliente têm maior prioridade (enviados no LOGIN)
        _client_ap    = float(char_data.get("client_ap",     0))
        _client_hp    = int(char_data.get("client_max_hp", 0))
        _ap = _client_ap  if _client_ap  > 0 else _stats.get("attack_power", _class_ap.get(_cls, 20))
        _hp = _client_hp  if _client_hp  > 0 else _stats.get("max_hp",       _class_hp.get(_cls, 150))
        cs_player     = CombatStats(base_attack_power=int(_ap))
        cs_player.max_hp      = int(_hp)
        cs_player.current_hp  = int(_hp)
        cs_player.attack_interval = _class_int.get(_cls, 2.0)
        self.world.add_component(eid, cs_player)

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

    def set_player_target(self, session_id: str, target_eid: int) -> None:
        """Define o alvo de combate do jogador. target_eid=-1 para parar."""
        from components import CombatState
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        cs = self.world.get_component(eid, CombatState)
        if cs:
            cs.target_entity_id = target_eid

    def _process_player_attacks(self, dt: float,
                               player_hp_snapshot: dict[int, int]) -> None:
        """
        Processa auto-attacks dos jogadores em mobs.

        Usa deal_damage() do offline — mesma fórmula de dano, armor reduction,
        crit, dodge, parry. Mob→player é tratado detectando a variação de HP
        após EnemyAISystem rodar (EnemyAISystem chama deal_damage internamente).
        """
        from systems import deal_damage
        from components import CombatState, CombatStats, TileMovement, Enemy, PendingDeath
        from utils import chebyshev

        # ── Player → Mob ───────────────────────────────────────────────────
        for session_id, player_eid in list(self._player_eids.items()):
            cs = self.world.get_component(player_eid, CombatState)
            if not cs or cs.target_entity_id == -1:
                continue

            target_eid = cs.target_entity_id
            if target_eid not in self._mob_eids:
                cs.target_entity_id = -1
                continue

            target_cs = self.world.get_component(target_eid, CombatStats)
            if not target_cs or target_cs.current_hp <= 0:
                cs.target_entity_id = -1
                continue

            # Valida range
            player_tm = self.world.get_component(player_eid, TileMovement)
            target_tm = self.world.get_component(target_eid, TileMovement)
            if not player_tm or not target_tm:
                continue
            player_cs    = self.world.get_component(player_eid, CombatStats)
            attack_range = 7 if getattr(player_cs, "is_ranged", False) else 1
            if chebyshev(player_tm.current_tile_x, player_tm.current_tile_y,
                         target_tm.current_tile_x, target_tm.current_tile_y) > attack_range:
                continue

            # Cooldown de ataque (inicializa em 0 para atacar imediatamente no primeiro range)
            timer = self._attack_timers.get(session_id, 0.0) - dt
            if timer > 0:
                self._attack_timers[session_id] = timer
                continue
            interval = player_cs.attack_interval if player_cs else 2.0
            self._attack_timers[session_id] = interval

            # ── deal_damage() do offline: mesma fórmula, armor, crit, dodge ──
            hp_before = target_cs.current_hp
            dead      = deal_damage(player_eid, target_eid, "physical")
            hp_after  = 0 if dead else target_cs.current_hp
            damage    = max(0, hp_before - hp_after)

            self._combat_this_tick.append({
                "attacker": player_eid,
                "target":   target_eid,
                "damage":   damage,
                "outcome":  "crit" if damage > int(hp_before * 0.15) else "hit",
                "hp_after": hp_after,
                "source":   "auto",
            })

            if dead:
                # deal_damage adicionou PendingDeath; removemos manualmente
                # (sem DeathHandlerSystem, para evitar loot/corpse no servidor por enquanto)
                self._mob_eids.discard(target_eid)
                if target_eid not in self._despawned_this_tick:
                    self._despawned_this_tick.append(target_eid)
                try:
                    self.world.remove_entity(target_eid)
                except Exception:
                    pass
                cs.target_entity_id = -1
                self._attack_timers.pop(session_id, None)
                print(f"[Combat] mob {target_eid} morto por player {player_eid}")

        # ── Mob → Player: detectado via variação de HP após EnemyAISystem ──
        # EnemyAISystem já chamou deal_damage() nos players. Basta comparar
        # o snapshot de HP capturado antes dos sistemas rodarem.
        for peid, hp_before in player_hp_snapshot.items():
            pcs = self.world.get_component(peid, CombatStats)
            if not pcs:
                continue
            hp_now = pcs.current_hp
            if hp_now < hp_before:
                damage = hp_before - hp_now
                self._combat_this_tick.append({
                    "attacker": -1,          # EnemyAISystem não expõe atacante
                    "target":   peid,
                    "damage":   damage,
                    "outcome":  "hit",
                    "hp_after": max(0, hp_now),
                    "source":   "auto",
                })
                if hp_now <= 0:
                    # Remove PendingDeath adicionado pelo deal_damage do EnemyAI
                    from components import PendingDeath as _PD
                    try:
                        self.world.remove_component(peid, _PD)
                    except Exception:
                        pass
                    self._handle_player_death(peid)

    # Tile de respawn padrão do mapa — deve coincidir com spawn do mapa offline
    RESPAWN_TILE = (115, 389)

    def _handle_player_death(self, player_eid: int) -> None:
        """Player morreu: reseta HP, teleporta para respawn, mobs param de atacar."""
        from components import CombatStats, CombatState, TileMovement, Position
        player_cs = self.world.get_component(player_eid, CombatStats)
        if player_cs:
            player_cs.current_hp = player_cs.max_hp

        # Teleporta o player para o spawn no servidor ANTES de limpar aggro.
        # Isso garante que ServerMobSystem._try_aggro não re-agre imediatamente
        # porque o player está fora do AGGRO_RANGE após o teleporte.
        rx, ry = self.RESPAWN_TILE
        ptm = self.world.get_component(player_eid, TileMovement)
        pos = self.world.get_component(player_eid, Position)
        if ptm:
            ptm.current_tile_x = rx;  ptm.current_tile_y = ry
            ptm.target_tile_x  = rx;  ptm.target_tile_y  = ry
        if pos:
            pos.x = rx * TILE_SIZE + TILE_SIZE // 2
            pos.y = ry * TILE_SIZE + TILE_SIZE // 2

        # Limpa alvo de todos os mobs
        for mob_eid in self._mob_eids:
            mob_state = self.world.get_component(mob_eid, CombatState)
            if mob_state and mob_state.target_entity_id == player_eid:
                mob_state.target_entity_id = -1

        # Encontra session_id para notificar o cliente
        session_id = None
        for sid, eid in self._player_eids.items():
            if eid == player_eid:
                session_id = sid
                break

        self._player_deaths_this_tick.append({
            "session_id": session_id,
            "player_eid": player_eid,
            "respawn_tx": rx,
            "respawn_ty": ry,
        })

    def get_entity_spawn_data(self, eid: int) -> dict | None:
        """Retorna payload completo de ENTITY_SPAWN para qualquer entidade (mob ou player)."""
        from components import TileMovement
        if eid not in self._mob_eids:
            return None   # jogadores são gerenciados separadamente
        tm = self.world.get_component(eid, TileMovement)
        if not tm:
            return None
        return self._build_mob_spawn_payload(eid, tm)

    def _build_mob_spawn_payload(self, eid: int, tm) -> dict:
        from components import CombatStats, AIControlled, Renderable, SpawnZoneOwner, SpawnZone
        cs  = self.world.get_component(eid, CombatStats)
        ai  = self.world.get_component(eid, AIControlled)
        ren = self.world.get_component(eid, Renderable)
        szo = self.world.get_component(eid, SpawnZoneOwner)
        race="Humanoide"; entity_class="Warrior"; tier="normal"; is_ranged=False
        if szo:
            zone = self.world.get_component(szo.zone_entity_id, SpawnZone)
            if zone:
                race         = zone.race
                entity_class = zone.entity_class or entity_class
                tier         = zone.enemy_tier
                is_ranged    = (zone.enemy_type == "ranged")
        return {
            "eid":          eid, "kind":         "enemy",
            "tx":           tm.current_tile_x,
            "ty":           tm.current_tile_y,
            "race":         race, "entity_class": entity_class,
            "tier":         tier, "is_ranged":    is_ranged,
            "color":        list(ren.color) if ren else [150, 60, 60],
            "hp":           cs.current_hp if cs else 50,
            "hp_max":       cs.max_hp     if cs else 50,
            "level":        zone.level_min if szo and zone else 1,
            "effects":      [],
        }

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
                    "level":  1,
                    "effects": [],
                })
        return result

    def get_entity_id(self, session_id: str) -> int:
        return self._player_eids.get(session_id, -1)

    def get_player_hp(self, session_id: str) -> tuple[int, int]:
        """Retorna (current_hp, max_hp) do player. Usado no LOGIN_OK."""
        from components import CombatStats
        eid = self._player_eids.get(session_id)
        if eid is None:
            return (0, 0)
        cs = self.world.get_component(eid, CombatStats)
        return (cs.current_hp, cs.max_hp) if cs else (0, 0)

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
                try:
                    self._tick(TICK_INTERVAL)
                except Exception as e:
                    import traceback
                    print(f"[WorldServer] ERRO no tick {self.tick_count}: {e}")
                    traceback.print_exc()
                    # Limpa deltas pendentes para não propagar estado corrompido
                    self._moved_this_tick.clear()
                    self._spawned_this_tick.clear()
                    self._despawned_this_tick.clear()
                    self._combat_this_tick.clear()
                    self._player_deaths_this_tick.clear()
                next_tick += TICK_INTERVAL
                if time.perf_counter() - next_tick > TICK_INTERVAL:
                    next_tick = time.perf_counter()
            else:
                await asyncio.sleep(0)

    def _tick(self, dt: float) -> None:
        self.tick_count += 1

        # Atualiza player_entity_id da IA para o primeiro jogador online
        first_player_eid = next(iter(self._player_eids.values()), -1)
        if hasattr(self, "_enemy_ai_system"):
            self._enemy_ai_system.player_entity_id = first_player_eid
        if hasattr(self, "_enemy_ab_system"):
            self._enemy_ab_system.player_entity_id = first_player_eid

        from components import TileMovement, Enemy, CombatStats

        # Snapshot de posições dos mobs e HP dos players ANTES dos sistemas
        pre_mob_pos:     dict[int, tuple[int, int]] = {}
        player_hp_snap:  dict[int, int]             = {}
        for eid, tm in self.world.get_entities_with(TileMovement):
            if eid in self._mob_eids:
                pre_mob_pos[eid] = (tm.current_tile_x, tm.current_tile_y)
        for peid in self._player_eids.values():
            pcs = self.world.get_component(peid, CombatStats)
            player_hp_snap[peid] = pcs.current_hp if pcs else 0

        # Roda sistemas offline reais (EnemyAISystem inclui mob→player via deal_damage)
        for system in self._systems:
            system.update(dt=dt)

        # Player→mob: usa deal_damage() offline; Mob→player: detectado por variação de HP
        self._process_player_attacks(dt, player_hp_snap)

        # Detecta novos mobs criados pelo SpawnZoneSystem neste tick
        for eid, tm in self.world.get_entities_with(TileMovement):
            if self.world.get_component(eid, Enemy) and eid not in self._mob_eids \
                    and eid not in self._player_eids.values():
                self._mob_eids.add(eid)
                # CombatState: necessário para aggro do EnemyAISystem
                from components import CombatState as _CS, Visible as _Vis
                if not self.world.get_component(eid, _CS):
                    self.world.add_component(eid, _CS())
                # Visible: EnemyAISystem filtra por Visible (FogSystem não roda no servidor)
                if not self.world.get_component(eid, _Vis):
                    self.world.add_component(eid, _Vis())
                self._emit_mob_spawn(eid, tm)

        # Detecta mobs que se moveram neste tick
        for eid in list(self._mob_eids):
            tm = self.world.get_component(eid, TileMovement)
            if tm is None:
                # Mob foi removido (morreu via _process_combat ou outro meio)
                self._mob_eids.discard(eid)
                # Evita duplicata: _process_combat já pode ter adicionado
                if eid not in self._despawned_this_tick:
                    self._despawned_this_tick.append(eid)
                continue
            old = pre_mob_pos.get(eid)
            new = (tm.current_tile_x, tm.current_tile_y)
            if old and old != new:
                self._moved_this_tick.append({
                    "eid": eid, "tx": new[0], "ty": new[1],
                    "from_tx": old[0], "from_ty": old[1],
                })

        # Limpa deltas de erro do try/except se necessário
        deltas = self._collect_deltas()
        self._store_snapshot()

        for cb in self._on_tick_callbacks:
            cb(self.tick_count, deltas)

    def _emit_mob_spawn(self, eid: int, tm) -> None:
        """Adiciona payload de spawn do mob aos deltas do tick atual."""
        payload = self._build_mob_spawn_payload(eid, tm)
        if payload:
            self._spawned_this_tick.append(payload)

    def _collect_deltas(self) -> dict:
        deltas = {
            "moved":         list(self._moved_this_tick),
            "stats":         [],
            "effects":       [],
            "spawned":       list(self._spawned_this_tick),
            "despawned":     list(self._despawned_this_tick),
            "combat":        list(self._combat_this_tick),
            "player_deaths": list(self._player_deaths_this_tick),
        }
        self._moved_this_tick.clear()
        self._spawned_this_tick.clear()
        self._despawned_this_tick.clear()
        self._combat_this_tick.clear()
        self._player_deaths_this_tick.clear()
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
