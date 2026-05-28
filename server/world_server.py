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
from collections import deque

# Pygame headless — servidor não tem display mas os sistemas usam pygame internamente
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world import World
from shared.constants import TICK_RATE, TICK_INTERVAL, SNAPSHOT_HISTORY, TILE_SIZE, LAG_COMP_WINDOW_MS

from server.skill_processor import SkillProcessorMixin
from server.combat_processor import CombatProcessorMixin
from server.respawn_system import RespawnMixin
from server.loot_processor import LootProcessorMixin


# ── ServerStatusEffectSystem ──────────────────────────────────────────────────
# Subclasse headless de core_systems.StatusEffectSystem que emite eventos de
# dano/cura de DoT/HoT diretamente no _combat_this_tick do WorldServer.
# Definida aqui (módulo) em vez de dentro de _load_map() para evitar re-definição
# de classe a cada chamada e tornar o código mais navegável.

class _ServerStatusEffectSystem:
    """Instanciada em _load_map(); world_server passado como srv para callbacks."""

    @staticmethod
    def build(world, srv) -> "object":
        """Factory: retorna instância com os hooks de emit acoplados ao srv."""
        from core_systems import StatusEffectSystem as _Base

        class _Impl(_Base):
            def __init__(self, world, srv):
                super().__init__(world)
                self._srv = srv

            def _emit_damage(self, eid, amount, effect_type, pos, color):
                from components import CombatStats as _CS
                cs = self.world.get_component(eid, _CS)
                self._srv._combat_this_tick.append({
                    "attacker": -1,
                    "target":   eid,
                    "damage":   amount,
                    "outcome":  "hit",
                    "hp_after": cs.current_hp if cs else 0,
                    "source":   effect_type,
                })
                # Acumula dano DoT em players para corrigir snapshot HP
                # (evita duplo COMBAT_RESULT em _process_player_attacks)
                if eid in self._srv._player_eids.values():
                    prev = self._srv._sfx_damage_players.get(eid, 0)
                    self._srv._sfx_damage_players[eid] = prev + amount

            def _emit_heal(self, eid, amount, effect_type, pos, color):
                from components import CombatStats as _CS
                cs = self.world.get_component(eid, _CS)
                self._srv._combat_this_tick.append({
                    "attacker": -1,
                    "target":   eid,
                    "damage":   -amount,   # negativo = cura
                    "outcome":  "regen",
                    "hp_after": cs.current_hp if cs else 0,
                    "source":   effect_type,
                })

        return _Impl(world, srv)


class WorldServer(SkillProcessorMixin, CombatProcessorMixin, RespawnMixin, LootProcessorMixin):

    MAP_FILE = "maps/map_1.csv"   # mapa padrão carregado pelo servidor

    def __init__(self, zone_id: str = "world_main", map_file: str = ""):
        self.zone_id    = zone_id
        self.world      = World()
        self.tick_count = 0
        self.running    = False

        # session_id → entity_id dos jogadores online
        self._player_eids: dict[str, int] = {}
        # Reverse map: eid → session_id (O(1) lookup em get_session_id_for_player)
        self._player_eid_to_sid: dict[int, str] = {}

        # Eids de mobs gerenciados pelo servidor
        self._mob_eids: set[int] = set()

        # Deltas acumulados no tick atual (limpos ao fim de cada tick)
        self._moved_this_tick:    list[dict] = []
        self._spawned_this_tick:  list[dict] = []
        # Cada entrada: {"eid": int, "tx": int|None, "ty": int|None}
        # tx/ty = posição de morte do mob (None para players)
        self._despawned_this_tick: list[dict] = []
        self._combat_this_tick:        list[dict] = []
        # Mob→player attacks: emitidos ANTES de _combat_this_tick (DOT/HoT) para que o
        # cliente atualize HP em ordem correta (mob attack → DOT, não DOT → mob attack).
        self._pending_mob_attacks:     list[dict] = []
        self._player_deaths_this_tick: list[dict] = []   # mortes de players
        # Dano de DoT/HoT por StatusEffectSystem neste tick: player_eid → total
        # Usado para corrigir o snapshot HP (evitar duplo COMBAT_RESULT)
        self._sfx_damage_players:      dict[int, int]   = {}

        # Rastreamento de dano por mob: mob_eid → {player_eid: total_damage}
        self._mob_damage_log: dict[int, dict[int, int]] = {}

        # XP a entregar aos jogadores (populado em _tick, consumido pelo SessionManager)
        self._pending_xp_deliveries: list[dict] = []

        # Corpses: corpse_id → {tx, ty, owner_eid, items, timer}
        self._corpses: dict[int, dict] = {}
        self._next_corpse_id: int = 1
        # Notificações de loot pendentes (consumidas pelo SessionManager no dispatch)
        self._pending_loot_notifications: list[dict] = []
        # Corpses que expiraram neste tick: list de {cid, tx, ty}
        self._expired_corpses_this_tick: list[dict] = []

        # Timer de ataque por jogador: session_id → segundos até próximo hit
        self._attack_timers: dict[str, float] = {}

        # Itens comprados em loja mas ainda não confirmados por SAVE_STATE.
        # session_id → contagem de itens pendentes (limpo em confirm_inventory_save
        # e em despawn_player). Substitui o padrão setattr/getattr/delattr anterior.
        self._pending_inv: dict[str, int] = {}

        # Histórico de snapshots (deque com maxlen evita pop(0) O(n))
        self._snapshots: deque[tuple[int, dict]] = deque(maxlen=SNAPSHOT_HISTORY)

        # Callbacks do SessionManager
        self._on_tick_callbacks: list = []
        self._systems: list = []

        self._map_file = map_file or self.MAP_FILE
        self._load_map()

        from server.server_death_handler import ServerDeathHandler
        self._death_handler = ServerDeathHandler(self.world, world_server=self)

        from core_systems import ServerCombatStateSystem
        self._combat_state_sys = ServerCombatStateSystem(self.world)

        # Cache de valor por nome de item: {item_name: value} — evita instanciar
        # factories no hot path de venda (A5). Populado em _build_item_caches().
        self._item_value_cache: dict[str, int] = {}
        # Cache de itens de loja: {shop_id: {item_name: {entry, item_data}}}
        # Pré-compila item_data para evitar factory() duplicado em compras (A6).
        self._shop_item_cache: dict[str, dict] = {}
        self._build_item_caches()

        # Cooldown server-side: {(player_eid, sid) → unix_time do último uso}
        # Impede spam de skills mesmo que o cliente remova o cooldown local.
        self._skill_last_used: dict[tuple, float] = {}

        # Fila de skill requests recebidas dos clientes (processada em _tick)
        self._pending_skill_requests: list[dict] = []
        # Resultados de skills processadas no tick (consumido pelo SessionManager)
        self._skill_results_this_tick: list[dict] = []
        # Correções de posição por skill (Interceptar etc.) — enviadas direto ao caster
        self._skill_position_corrections: list[dict] = []
        # HP updates de players para broadcast AOI (ex: auto-cura de skill)
        self._player_hp_broadcasts_this_tick: list[dict] = []
        # Overrides de stats de combate por player (equipamento, buffs, consumíveis)
        # eid → {stat_name: valor} — aplicados após qualquer recalculo de CombatStats
        self._player_stat_overrides: dict[int, dict] = {}
        # Eventos de som posicionais (mob aggro, etc.) para broadcast AOI
        self._pending_sound_events: list[dict] = []
        # Snapshot de estados de mob para detectar transições de aggro
        self._mob_states_prev: dict[int, str] = {}
        # Projéteis de mobs conhecidos (para detectar novos e removidos a cada tick)
        self._known_projectile_eids: set[int] = set()

        # SkillSystem instanciado AQUI mas NÃO adicionado a self._systems
        # (chamado manualmente em _process_skill_requests)
        from systems import SkillSystem
        self._skill_system = SkillSystem(self.world, player_entity_id=-1)
        # Servidor usa melee range com lag tolerance (1 + MELEE_LAG_TOLERANCE tiles)
        self._skill_system._server_authoritative = True

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
                             TileMovementSystem, ProjectileSystem, register_services)
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

        # StatusEffectSystem do servidor — ver _ServerStatusEffectSystem (topo do módulo)
        self._status_effect_system = _ServerStatusEffectSystem.build(self.world, self)

        # SpawnZoneSystem: no servidor não há culling por distância de player.
        # O mundo deve existir independente de conexões — raio ilimitado.
        _spawn_sys = SpawnZoneSystem(self.world)
        _spawn_sys.ACTIVATION_RADIUS = 999999

        # ProjectileSystem headless: screen=None — render() nunca é chamado no servidor.
        # update() é pure ECS: move projétil → chama deal_damage() ao acertar.
        # HP diff detectado por _process_player_attacks → broadcast COMBAT_RESULT.
        _proj_sys = ProjectileSystem(self.world, screen=None)

        self._systems = [
            tile_validation,                          # 1. cache de tiles ocupados
            _spawn_sys,                               # 2. spawn de mobs — raio ilimitado
            self._enemy_ai_system,                    # 3. IA: aggro, pathfinding, ataque
            self._enemy_ab_system,                    # 4. habilidades especiais (DoT, debuffs)
            self._status_effect_system,               # 5. ticks de status effects (poison, bleed)
            _proj_sys,                                # 6. projéteis de mobs ranged → deal_damage
            TileMovementSystem(self.world),           # 7. avança progress→current_tile (headless)
        ]
        print(f"[WorldServer] mapa OK — EnemyAI + EnemyAbility + StatusEffect + Projectile")

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
        Cria entidade do jogador no ECS com todos os componentes necessários para skills.
        Abordagem cirúrgica: cria manualmente sem efeitos colaterais de create_player().
        """
        from components import (Position, TileMovement, PlayerControlled, CombatState,
                                 CombatStats, CharacterStats, PermanentStats, Visible,
                                 Equipment, Wallet)
        from stats_system import CLASS_BASE_STATS, apply_char_stats_to_combat, sync_attack_interval
        import json as _json

        tx = int(char_data.get("tile_x", 10))
        ty = int(char_data.get("tile_y", 10))
        px = tx * TILE_SIZE + TILE_SIZE // 2
        py = ty * TILE_SIZE + TILE_SIZE // 2
        _cls = char_data.get("class_id", "guerreiro")
        _stats_raw = char_data.get("stats_json") or char_data.get("stats", {})
        _stats     = _json.loads(_stats_raw) if isinstance(_stats_raw, str) else (_stats_raw or {})

        eid = self.world.create_entity()
        self.world.add_component(eid, Position(x=px, y=py, prev_x=px, prev_y=py))
        self.world.add_component(eid, TileMovement(
            current_tile_x=tx, current_tile_y=ty,
            target_tile_x=tx,  target_tile_y=ty,
        ))
        self.world.add_component(eid, PlayerControlled())
        self.world.add_component(eid, CombatState())
        self.world.add_component(eid, Visible())
        self.world.add_component(eid, Equipment())
        self.world.add_component(eid, Wallet())

        # CharacterStats — restaura stats salvas; usa base da classe para personagens novos
        char = CharacterStats()
        char.class_id     = _cls
        char.name         = char_data.get("name", session_id)
        char.spawn_map    = self._map_file
        char.spawn_tile_x = tx
        char.spawn_tile_y = ty
        _base = CLASS_BASE_STATS.get(_cls, CLASS_BASE_STATS["guerreiro"])
        # Sempre começa com atributos base da classe
        char.level        = int(char_data.get("level", 1))
        char.strength     = _base["strength"]
        char.intelligence = _base["intelligence"]
        char.agility      = _base["agility"]
        char.vitality     = _base["vitality"]
        char.defense      = _base["defense"]
        # Se há stats_json salvo (qualquer campo), sobrescreve com valores salvos
        if _stats:
            char.level            = int(_stats.get("level",         char.level))
            char.current_xp       = int(_stats.get("current_xp",   0))
            char.xp_to_next_level = CharacterStats.xp_for_level(char.level)
            char.strength         = int(_stats.get("strength",     char.strength))
            char.intelligence     = int(_stats.get("intelligence", char.intelligence))
            char.agility          = int(_stats.get("agility",      char.agility))
            char.vitality         = int(_stats.get("vitality",     char.vitality))
            char.defense          = int(_stats.get("defense",      char.defense))
        self.world.add_component(eid, char)

        perm = PermanentStats()
        self.world.add_component(eid, perm)

        # CombatStats derivado dos atributos base + prioridade client_max_hp
        cs = CombatStats()
        apply_char_stats_to_combat(char, cs, perm)
        sync_attack_interval(cs, self.world.get_component(eid, Equipment))
        # max_hp salvo no stats_json inclui bônus de equipamento (cliente é autoritativo).
        # Aplica sobre o valor calculado para que curas e caps usem o HP real do player.
        _saved_max_hp = int(_stats.get("max_hp", 0))
        if _saved_max_hp > cs.max_hp:
            cs.max_hp = _saved_max_hp
        # client_max_hp/ap só se usa para personagens sem save (stats_json vazio).
        # São hints do cliente para equipamento ainda não sincronizado via PLAYER_STAT_SYNC.
        # Cap: máximo 10× o valor calculado pelo servidor — bloqueia exploits sem
        # afetar equipamentos legítimos. PLAYER_STAT_SYNC corrige o valor real logo após login.
        _client_hp = int(char_data.get("client_max_hp", 0))
        _client_ap = float(char_data.get("client_ap", 0))
        if not _stats:
            _cap_hp = cs.max_hp * 10
            _cap_ap = max(1, cs.base_attack_power) * 10
            if _client_ap > 0:
                cs.base_attack_power = int(min(_client_ap, _cap_ap))
                cs._recalculate_effective_stats()   # sincroniza attack_power a partir do novo base
            # Aplica max_hp APÓS recalculate — _recalculate_effective_stats sobrescreve max_hp
            # com base_stamina, então o override do cliente deve ser o último passo.
            if _client_hp > 0:
                cs.max_hp = int(min(_client_hp, _cap_hp))
        # Restaura HP salvo; se não houver, usa max_hp; nunca excede max_hp
        saved_hp = int(char_data.get("hp", 0))
        cs.current_hp = min(saved_hp, cs.max_hp) if saved_hp > 0 else cs.max_hp
        self.world.add_component(eid, cs)

        # Wallet: restaura gold salvo
        _gold = int(_stats.get("gold", 0))
        wall = self.world.get_component(eid, Wallet)
        if wall:
            wall.gold = _gold

        # Aplica cs_flags dos talentos (ex: impacto_maquina_matar) no servidor
        try:
            import json as _tjson
            _tal_raw  = char_data.get("talents_json") or "{}"
            _tal_data = _tjson.loads(_tal_raw) if isinstance(_tal_raw, str) else {}
            _tal_alloc = _tal_data.get("allocated", {})
            if _tal_alloc:
                from talent_data import TALENTS as _TALMAP
                for _tid, _pts in _tal_alloc.items():
                    _t = _TALMAP.get(_tid)
                    if not _t:
                        continue
                    for _flag in (_t.get("cs_flags") or []):
                        _formula = _flag.get("formula")
                        if _formula:
                            try:
                                setattr(cs, _flag["field"], _formula(_pts))
                            except Exception:
                                pass
        except Exception as _te:
            print(f"[World] aviso: talent cs_flags não aplicados — {_te}")

        # PlayerSkills: restaura skills salvas ou usa iniciais da classe
        try:
            from components import PlayerSkills as _PS
            from skill_config import SKILL_CATALOG, INITIAL_SKILLS_BY_CLASS
            ps = _PS()
            _saved_skills = _stats_raw if isinstance(_stats_raw, str) else ""
            # Tenta restaurar do skills_json salvo
            _skills_data = {}
            try:
                import json as _sj
                _skills_raw2 = char_data.get("skills_json") or "{}"
                _skills_data = _sj.loads(_skills_raw2) if isinstance(_skills_raw2, str) else {}
            except Exception:
                pass

            _learned_ids = _skills_data.get("learned") or []
            _hotbar_ids  = _skills_data.get("hotbar")  or []

            # Skills aprendidas (salvas ou iniciais da classe)
            if _learned_ids:
                for _sid in _learned_ids:
                    ps.learned_skill_ids.add(_sid)
            else:
                for _sid in INITIAL_SKILLS_BY_CLASS.get(_cls, []):
                    ps.learned_skill_ids.add(_sid)

            # Hotbar (salva ou constrói a partir dos IDs aprendidos)
            if _hotbar_ids:
                for _i, _sid in enumerate(_hotbar_ids):
                    if _sid and _i < len(ps.skills):
                        sk = _PS._make_skill(_sid, SKILL_CATALOG)
                        if sk:
                            ps.skills[_i] = sk
            else:
                for _sid in ps.learned_skill_ids:
                    if ps.skill_by_id(_sid) is None:
                        sk = _PS._make_skill(_sid, SKILL_CATALOG)
                        if sk:
                            try:
                                idx = ps.skills.index(None)
                                ps.skills[idx] = sk
                            except ValueError:
                                ps.skills.append(sk)
            self.world.add_component(eid, ps)
        except Exception as _e:
            print(f"[World] aviso: PlayerSkills não criado — {_e}")

        self._player_eids[session_id]    = eid
        self._player_eid_to_sid[eid]     = session_id   # reverse map

        _srv_hp, _srv_hp_max = self.get_player_hp(session_id)
        self._spawned_this_tick.append({
            "eid":      eid,
            "kind":     "player",
            "tx":       tx,
            "ty":       ty,
            "name":     char_data.get("name", session_id),
            "class_id": char_data.get("class_id", "guerreiro"),
            "hp":       _srv_hp,
            "hp_max":   _srv_hp_max,
            "level":    char_data.get("level", 1),
            "effects":  [],
        })

        print(f"[World] spawn player eid={eid}  tile=({tx},{ty})  "
              f"name={char_data.get('name', '?')}")
        return eid

    def get_player_save_data(self, session_id: str) -> dict:
        """Coleta estado ECS completo do jogador para persistência."""
        import json as _json
        from components import (TileMovement, CombatStats, CharacterStats,
                                 Wallet, PlayerSkills)
        eid = self._player_eids.get(session_id)
        if eid is None:
            return {}
        tm   = self.world.get_component(eid, TileMovement)
        cs   = self.world.get_component(eid, CombatStats)
        char = self.world.get_component(eid, CharacterStats)
        wall = self.world.get_component(eid, Wallet)
        ps   = self.world.get_component(eid, PlayerSkills)

        # Stats: level, xp, atributos base
        stats = {}
        if char:
            stats = {
                "level":            char.level,
                "current_xp":       char.current_xp,
                "xp_to_next_level": char.xp_to_next_level,
                "strength":         char.strength,
                "intelligence":     char.intelligence,
                "agility":          char.agility,
                "vitality":         char.vitality,
                "defense":          char.defense,
                "max_hp":           cs.max_hp if cs else 200,
            }
        if wall:
            stats["gold"] = wall.gold

        # Skills: IDs aprendidos + posições na hotbar
        skills = {}
        if ps:
            skills = {
                "learned": list(ps.learned_skill_ids),
                "hotbar":  [sk.skill_id if sk else None for sk in ps.skills],
            }

        return {
            "tile_x":     tm.current_tile_x if tm else 10,
            "tile_y":     tm.current_tile_y if tm else 10,
            "hp":         cs.current_hp     if cs else 100,
            "mp":         100,
            "stats":      stats,
            "skills":     skills,
        }

    def despawn_player(self, session_id: str) -> None:
        """Remove entidade do jogador. Chamado no logout/disconnect."""
        from components import TileMovement, Position
        eid = self._player_eids.pop(session_id, None)
        if eid is None:
            return
        self._player_eid_to_sid.pop(eid, None)   # limpa reverse map
        self._despawned_this_tick.append({"eid": eid, "tx": None, "ty": None})
        self._pending_inv.pop(session_id, None)  # limpa itens pendentes de loja
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

        # Validação: tile de destino deve ser walkable (sólido, fora do mapa, piso errado)
        # Usa is_tile_walkable do offline — mesma lógica de colisão + elevação.
        # _svc['tile_validation'] é registrado em _load_map() antes de qualquer MOVE chegar.
        from systems import is_tile_walkable as _walkable
        from components import CombatState as _CState
        _cst = self.world.get_component(eid, _CState)
        _pursuit_target = (_cst.target_entity_id
                           if _cst and _cst.is_pursuing and _cst.target_entity_id != -1
                           else -1)
        if not _walkable(eid, tx, ty, tm.current_tile_x, tm.current_tile_y,
                         ignore_eid=_pursuit_target):
            return False

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
        from components import CombatStats, AIControlled, Renderable, SpawnZoneOwner, SpawnZone, EntityIdentity
        cs    = self.world.get_component(eid, CombatStats)
        ai    = self.world.get_component(eid, AIControlled)
        ren   = self.world.get_component(eid, Renderable)
        szo   = self.world.get_component(eid, SpawnZoneOwner)
        ident = self.world.get_component(eid, EntityIdentity)
        race="Humanoide"; entity_class="Warrior"; tier="normal"; is_ranged=False; zone=None
        if szo:
            zone = self.world.get_component(szo.zone_entity_id, SpawnZone)
            if zone:
                race         = zone.race
                entity_class = zone.entity_class or entity_class
                tier         = zone.enemy_tier
                is_ranged    = (zone.enemy_type == "ranged")
        mob_level = ident.level if ident else (zone.level_min if szo and zone else 1)
        payload = {
            "eid":          eid, "kind":         "enemy",
            "tx":           tm.current_tile_x,
            "ty":           tm.current_tile_y,
            "race":         race, "entity_class": entity_class,
            "tier":         tier, "is_ranged":    is_ranged,
            "color":        list(ren.color) if ren else [150, 60, 60],
            "hp":           cs.current_hp if cs else 50,
            "hp_max":       cs.max_hp     if cs else 50,
            "level":        mob_level,
            "effects":      [],
        }
        # Se o mob já está em movimento no momento do spawn, inclui o destino.
        # O cliente inicia a animação imediatamente em vez de esperar o próximo evento.
        if tm.is_moving and (tm.target_tile_x != tm.current_tile_x or
                             tm.target_tile_y != tm.current_tile_y):
            payload["moving_to_tx"] = tm.target_tile_x
            payload["moving_to_ty"] = tm.target_tile_y
        return payload

    def get_mobs_in_aoi(self, center_tx: int, center_ty: int, radius: int) -> list[dict]:
        """Retorna lista de mobs no AOI — para WORLD_STATE inicial."""
        from components import TileMovement
        result = []
        for eid in self._mob_eids:
            tm = self.world.get_component(eid, TileMovement)
            if not tm:
                continue
            if abs(tm.current_tile_x - center_tx) <= radius and \
               abs(tm.current_tile_y - center_ty) <= radius:
                payload = self._build_mob_spawn_payload(eid, tm)
                result.append(payload)
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

    def sync_player_resources(self, session_id: str, rage: int, mana: int) -> None:
        """Sincroniza rage e mana do cliente no ECS do servidor antes de executar skills.
        O cliente é fonte de verdade para recursos de combate (rage, mana) pois
        os gera localmente via PlayerInputSystem — igual ao offline.
        """
        from components import CharacterStats, CombatStats
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        char = self.world.get_component(eid, CharacterStats)
        cs   = self.world.get_component(eid, CombatStats)
        if char and rage >= 0:
            char.rage = rage
        if cs and mana >= 0:
            cs.mana = mana

    def get_damage_log(self, mob_eid: int) -> dict[int, int]:
        """Retorna e remove o registro de dano acumulado para o mob. Chamado pelo death handler."""
        return self._mob_damage_log.pop(mob_eid, {})

    def get_session_id_for_player(self, player_eid: int) -> str | None:
        """Retorna session_id do player dado seu entity_id — O(1) via reverse map."""
        return self._player_eid_to_sid.get(player_eid)
        return None

    # ── Skill API ────────────────────────────────────────────────────────────

    def queue_skill(self, session_id: str, sid: str, tid: int,
                    dir_x: float, dir_y: float, ts: int,
                    dbg_seq: int = 0) -> None:
        """Enfileira um request de skill para processar no próximo tick."""
        player_eid = self._player_eids.get(session_id, -1)
        if player_eid == -1:
            return
        self._pending_skill_requests.append({
            "player_eid": player_eid,
            "sid":        sid,
            "tid":        tid,
            "dir_x":      dir_x,
            "dir_y":      dir_y,
            "ts":         ts,
            "dbg_seq":    dbg_seq,
        })

    def consume_skill_results(self) -> list[dict]:
        """Retorna e limpa resultados de skills do tick atual (para o SessionManager)."""
        result = list(self._skill_results_this_tick)
        self._skill_results_this_tick.clear()
        return result

    # ── Corpse / Loot API ────────────────────────────────────────────────────
    # consume_loot_notifications, consume_expired_corpses → LootProcessorMixin
    # _process_skill_requests → SkillProcessorMixin

    def sync_player_combat_stats(self, session_id: str, stats: dict) -> None:
        """Recebe stats efetivos do cliente e aplica ao CombatStats do servidor.

        Armazena os overrides para reaplicação após qualquer recalculo
        (level up, re-login, troca de talentos). Modular: adicionar nova
        stat = apenas inserir em COMBAT_SYNC_STATS em shared/constants.py.
        """
        from shared.constants import COMBAT_SYNC_STATS
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        # Filtra apenas as stats conhecidas (evita poluição de dados)
        overrides = {k: float(v) for k, v in stats.items()
                     if k in COMBAT_SYNC_STATS and isinstance(v, (int, float))}
        if not overrides:
            return
        self._player_stat_overrides[eid] = overrides
        self._apply_stat_overrides(eid)

    def _apply_stat_overrides(self, eid: int) -> None:
        """Aplica overrides de stats ao CombatStats do player.

        Chamado após qualquer operação que recalcule o CombatStats
        (spawn, level up, apply_talent_effects). Sem isso, os bônus de
        equipamento seriam perdidos após cada recalculo.
        """
        overrides = self._player_stat_overrides.get(eid)
        if not overrides:
            return
        from components import CombatStats as _CS
        from shared.constants import COMBAT_SYNC_STATS
        cs = self.world.get_component(eid, _CS)
        if not cs:
            return
        changed = False
        for eff_attr, base_attr in COMBAT_SYNC_STATS.items():
            if eff_attr in overrides:
                new_val = overrides[eff_attr]
                old_val = getattr(cs, base_attr, None)
                if old_val is None or abs(float(old_val) - new_val) > 0.01:
                    setattr(cs, base_attr, type(old_val)(new_val) if old_val is not None else new_val)
                    changed = True
        if changed:
            cs._recalculate_effective_stats()

    def process_shop_buy(self, session_id: str, shop_id: str,
                         item_name: str, quantity: int,
                         last_inventory: list | None = None) -> dict:
        """Processa compra em loja — autoritativo no servidor.

        Valida: shop_id existe no catálogo, item está no estoque,
        gold suficiente, espaço no inventário.
        Retorna dict {success, reason, item_data, new_gold}.
        """
        from components import Wallet, Inventory

        eid = self._player_eids.get(session_id)
        if eid is None:
            return {"success": False, "reason": "not_logged_in"}

        # 1. Valida catálogo — usa cache pré-construído (sem instanciar factories)
        shop_idx = self._shop_item_cache.get(shop_id)
        if shop_idx is None:
            return {"success": False, "reason": "invalid_shop"}
        cached = shop_idx.get(item_name)
        if not cached:
            return {"success": False, "reason": "item_not_in_stock"}

        entry      = cached["entry"]
        price      = int(entry["price"])
        total_cost = price * max(1, quantity)

        # 2. Valida gold (Wallet é server-autoritativo)
        wallet = self.world.get_component(eid, Wallet)
        if not wallet:
            return {"success": False, "reason": "no_wallet"}
        if wallet.gold < total_cost:
            return {"success": False, "reason": "insufficient_gold",
                    "required": total_cost, "available": wallet.gold}

        # 3. Valida espaço no inventário via last_client_payload
        #    (inventário real mantido pelo cliente; servidor usa payload para contagem)
        inv_count = len(last_inventory) if last_inventory is not None else 0
        # Soma itens pendentes desta sessão ainda não confirmados por SAVE_STATE
        pending = self._pending_inv.get(session_id, 0)
        if inv_count + pending + 1 > 24:  # max_slots default = 24
            return {"success": False, "reason": "inventory_full"}

        # 4. Aplica a compra — gold deduzido server-side
        wallet.gold -= total_cost
        # Rastreia item pendente até próximo SAVE_STATE
        self._pending_inv[session_id] = pending + 1

        # 5. item_data pré-compilado no cache (sem factory() adicional)
        item_data = cached["item_data"]

        return {
            "success":  True,
            "item":     item_data,
            "quantity": max(1, quantity),
            "new_gold": wallet.gold,
            "price":    total_cost,
        }

    def confirm_inventory_save(self, session_id: str) -> None:
        """Chamado quando SAVE_STATE chega — zera contador de itens pendentes."""
        self._pending_inv.pop(session_id, None)

    # ---- sell ratio igual ao ShopSystem do cliente ----
    _SELL_RATIO = 0.4

    def process_shop_sell(self, session_id: str, item_name: str,
                          client_value: int, stack_sold: int = 1) -> dict:
        """Processa venda ao mercador — autoritativo no servidor.

        Calcula sell_price a partir do catálogo (loot_tables → merchant_data).
        Se o item não estiver no catálogo usa client_value com cap de 500g para
        evitar exploits.  Gold adicionado server-side na Wallet ECS.
        Retorna {success, item_name, sell_price, new_gold}.
        """
        from components import Wallet
        eid = self._player_eids.get(session_id)
        if eid is None:
            return {"success": False, "reason": "not_logged_in"}
        wallet = self.world.get_component(eid, Wallet)
        if not wallet:
            return {"success": False, "reason": "no_wallet"}

        # Tenta encontrar o item no catálogo para validar value
        canonical_value = self._lookup_item_value(item_name)
        if canonical_value is None:
            # Desconhecido: usa valor informado pelo cliente com teto conservador
            canonical_value = min(int(client_value), 500)

        sell_price = max(1, int(canonical_value * self._SELL_RATIO)) * max(1, stack_sold)
        wallet.gold += sell_price
        return {
            "success":    True,
            "item_name":  item_name,
            "sell_price": sell_price,
            "new_gold":   wallet.gold,
        }

    @staticmethod
    def _item_data_from_obj(obj) -> dict:
        """Serializa um item ECS para o dict que o cliente espera no BUY_RESULT."""
        data = {
            "name":      obj.name,
            "item_type": getattr(obj, "item_type", ""),
            "slot":      getattr(obj, "slot", ""),
            "rarity":    getattr(obj, "rarity", "common"),
            "value":     getattr(obj, "value", 0),
            "consumable": getattr(obj, "consumable", None),
            "max_stack":  getattr(obj, "max_stack", 1),
            "modifiers": [{"attribute": m.attribute, "value": m.value, "type": m.type}
                          for m in getattr(obj, "modifiers", [])],
        }
        for f in ("attack_power", "armor", "spell_power", "stamina",
                  "two_handed", "attack_speed", "damage_min", "damage_max"):
            v = getattr(obj, f, None)
            if v is not None:
                data[f] = v
        return data

    def _build_item_caches(self) -> None:
        """Constrói _item_value_cache e _shop_item_cache na inicialização.

        Instancia cada factory UMA VEZ (startup) em vez de a cada venda/compra.
        _item_value_cache: {nome → valor} para validação de venda (A5).
        _shop_item_cache:  {shop_id → {nome → {entry, item_data}}} para compras (A6).
        """
        # loot_tables._T: {key: factory_fn} — valores são callables diretamente
        try:
            from loot_tables import _T as _LT
            for _f in _LT.values():
                if callable(_f):
                    try:
                        _o = _f()
                        _n = getattr(_o, "name", None)
                        if _n:
                            self._item_value_cache[_n] = int(getattr(_o, "value", 0))
                    except Exception:
                        pass
        except ImportError:
            pass

        # merchant_data → _shop_item_cache + _item_value_cache
        try:
            from merchant_data import SHOPS
            for _sid, _shop in SHOPS.items():
                _idx: dict[str, dict] = {}
                for _e in _shop.get("stock", []):
                    _f = _e.get("factory")
                    if callable(_f):
                        try:
                            _o = _f()
                            _n = getattr(_o, "name", None)
                            if _n:
                                self._item_value_cache[_n] = int(getattr(_o, "value", 0))
                                _idx[_n] = {
                                    "entry":     _e,
                                    "item_data": self._item_data_from_obj(_o),
                                }
                        except Exception:
                            pass
                self._shop_item_cache[_sid] = _idx
        except ImportError:
            pass
        print(f"[WorldServer] caches: {len(self._item_value_cache)} itens, "
              f"{sum(len(v) for v in self._shop_item_cache.values())} entradas de loja")

    def _lookup_item_value(self, item_name: str) -> int | None:
        """Retorna o valor base de um item a partir do cache pré-construído.
        None se não encontrado (foi removido do catálogo após startup)."""
        return self._item_value_cache.get(item_name)

    def apply_consumable(self, session_id: str, payload: dict) -> None:
        """Aplica efeitos de consumível no ECS do servidor (autoritativo).

        Suporta heal_instant e HoT (ActiveRegen). Estrutura extensível:
        o campo 'buffs' (list) é reservado para efeitos futuros de stat boost.
        """
        from components import CombatStats, CombatState, ActiveRegen
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        cs     = self.world.get_component(eid, CombatStats)
        cstate = self.world.get_component(eid, CombatState)
        if not cs:
            return

        if payload.get("ooc_only", False) and cstate and cstate.in_combat:
            return

        if cs.current_hp >= cs.max_hp:
            return

        # 1. Cura instantânea
        heal_instant = int(payload.get("heal_instant", 0))
        if heal_instant > 0:
            healed = min(heal_instant, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + heal_instant)
            if healed > 0:
                self._pending_xp_deliveries.append({
                    "player_eid":  eid,
                    "xp":          0,
                    "mob_eid":     -1,
                    "hp":          cs.current_hp,
                    "hp_max":      cs.max_hp,
                    "heal_amount": healed,
                    "heal_sid":    "consumable_instant",
                })

        # 2. HoT (Heal over Time) — adiciona ActiveRegen ao ECS
        hot = payload.get("hot")
        if isinstance(hot, dict):
            heal_per_tick = int(hot.get("heal_per_tick", 0))
            interval      = float(hot.get("interval", 2.0))
            ticks         = int(hot.get("ticks", 0))
            if heal_per_tick > 0 and ticks > 0:
                # Substitui regen ativa anterior (evita stack de consumíveis)
                try:
                    self.world.remove_component(eid, ActiveRegen)
                except Exception:
                    pass
                self.world.add_component(eid, ActiveRegen(
                    heal_per_tick=heal_per_tick,
                    interval=interval,
                    ticks_total=ticks,
                ))

        # 3. Buffs futuros (stat boosts, etc.) — reservado, não implementado ainda
        # for buff in payload.get("buffs", []):
        #     _apply_buff(eid, buff)

    def consume_sound_events(self) -> list[dict]:
        """Retorna e limpa eventos de som posicionais do tick."""
        result = list(self._pending_sound_events)
        self._pending_sound_events.clear()
        return result

    def consume_player_hp_broadcasts(self) -> list[dict]:
        """Retorna e limpa HP broadcasts de players do tick atual (para o SessionManager)."""
        result = list(self._player_hp_broadcasts_this_tick)
        self._player_hp_broadcasts_this_tick.clear()
        return result

    def consume_skill_position_corrections(self) -> list[dict]:
        """Retorna e limpa correções de posição por skill (Interceptar etc.)."""
        result = list(self._skill_position_corrections)
        self._skill_position_corrections.clear()
        return result

    def consume_xp_deliveries(self) -> list[dict]:
        """Retorna e limpa entregas de XP pendentes para o SessionManager."""
        result = list(self._pending_xp_deliveries)
        self._pending_xp_deliveries.clear()
        return result

    def apply_talent_effects_to_player(self, session_id: str, talent_allocated: dict) -> None:
        """Re-aplica efeitos de talentos ao CombatStats do servidor após salvar.

        Necessário pois o cliente aloca talentos e manda SAVE_STATE, mas o
        servidor precisa dos efeitos para processar skills corretamente
        (ex: impacto_maquina_matar, parry_rating, golpe_poderoso_rage_cost).
        """
        from components import (CombatStats, CharacterStats, PermanentStats, Modifier)
        from talent_data import TALENTS as _TAL
        from stats_system import apply_char_stats_to_combat, sync_attack_interval
        from stat_fns import add_modifier
        from components import Equipment

        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        cs   = self.world.get_component(eid, CombatStats)
        char = self.world.get_component(eid, CharacterStats)
        perm = self.world.get_component(eid, PermanentStats)
        equip = self.world.get_component(eid, Equipment)
        if not cs or not char:
            return

        # Preserva current_hp: apply_char_stats_to_combat recalcula max_hp a partir
        # de base_stamina (sem equipamento) e clamparia current_hp para esse valor menor.
        # Após esta função, _apply_stat_overrides restaura max_hp com bônus de equip.
        _saved_current_hp = cs.current_hp

        # Recalcula base — limpa modificadores anteriores de talentos
        apply_char_stats_to_combat(char, cs, perm)
        sync_attack_interval(cs, equip)

        # Restaura current_hp para o valor antes do reset
        # (_recalculate_effective_stats no final desta função irá clampá-lo corretamente)
        cs.current_hp = _saved_current_hp

        # Re-aplica cs_flags e modifiers de cada talento alocado
        for talent_id, points in (talent_allocated or {}).items():
            if not points:
                continue
            t = _TAL.get(talent_id)
            if not t:
                continue
            # cs_flags comportamentais (ex: impacto_maquina_matar, interceptar_rage_bonus)
            for flag in (t.get("cs_flags") or []):
                formula = flag.get("formula")
                if formula:
                    try:
                        setattr(cs, flag["field"], formula(points))
                    except Exception:
                        pass
            # Modifiers de atributo (ex: parry_rating +20 por ponto de Reflexos Apurados)
            for eff in (t.get("effects") or []):
                mod = Modifier(eff["attribute"], eff["value"] * points, eff["type"])
                add_modifier(cs, mod)

    # request_loot → LootProcessorMixin

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
                # Dorme até o próximo tick — elimina busy-spin com sleep(0).
                # Threshold 1ms: abaixo disso yield simples para não overshooting.
                _sleep = next_tick - time.perf_counter()
                if _sleep > 0.001:
                    await asyncio.sleep(_sleep)
                else:
                    await asyncio.sleep(0)

    def _tick(self, dt: float) -> None:
        self.tick_count += 1

        # EnemyAISystem e EnemyAbilitySystem iteram todos os PlayerControlled internamente.
        from components import TileMovement, Enemy, CombatStats

        self._tick_respawn_immunity()

        # Snapshot ANTES dos sistemas:
        #   pre_mob_target: rastreia target_tile (destino do movimento).
        #   Usar target em vez de current elimina o lag estrutural de 1 animação:
        #   o servidor emite o evento quando o movimento COMEÇA (target muda),
        #   não quando conclui (current muda). Cliente e servidor animam em paralelo.
        pre_mob_target:  dict[int, tuple[int, int]] = {}
        player_hp_snap:  dict[int, int]             = {}
        for eid, tm in self.world.get_entities_with(TileMovement):
            if eid in self._mob_eids:
                pre_mob_target[eid] = (tm.target_tile_x, tm.target_tile_y)
        for peid in self._player_eids.values():
            pcs = self.world.get_component(peid, CombatStats)
            player_hp_snap[peid] = pcs.current_hp if pcs else 0

        # Snapshot de estados de mob antes do AI update (para detectar aggro)
        from components import AIControlled as _AIC
        for _eid_sn in self._mob_eids:
            _ai_sn = self.world.get_component(_eid_sn, _AIC)
            if _ai_sn:
                self._mob_states_prev[_eid_sn] = _ai_sn.state

        # Roda sistemas offline reais (EnemyAISystem inclui mob→player via deal_damage)
        for system in self._systems:
            system.update(dt=dt)

        # Detecta mobs que aggraram neste tick (IDLE → CHASING/ATTACKING)
        from components import EntityIdentity as _EIdent
        for _eid_ag in list(self._mob_eids):
            _ai_ag = self.world.get_component(_eid_ag, _AIC)
            _tm_ag = self.world.get_component(_eid_ag, TileMovement)
            if not _ai_ag or not _tm_ag:
                continue
            _prev_state = self._mob_states_prev.get(_eid_ag, "IDLE")
            # Aggro: detecta IDLE → AGGRO_DELAY (é quando EnemyAISystem emite o som localmente)
            if _prev_state == "IDLE" and _ai_ag.state == "AGGRO_DELAY":
                _ident_ag = self.world.get_component(_eid_ag, _EIdent)
                self._pending_sound_events.append({
                    "kind":     "mob_aggro",
                    "mob_eid":  _eid_ag,                          # server eid → cliente busca MobSounds
                    "mob_name": _ident_ag.name if _ident_ag else "",
                    "tx":       _tm_ag.current_tile_x,
                    "ty":       _tm_ag.current_tile_y,
                })

        # CombatStateSystem headless (in_combat timer + rage decay + HP5 regen)
        # Lógica em core_systems.ServerCombatStateSystem — sem duplicação vs offline.
        self._combat_state_sys.update(self._player_eids, dt)
        for _hp5_ev in self._combat_state_sys.hp5_events:
            self._combat_this_tick.append({
                "attacker": -1,
                "target":   _hp5_ev["player_eid"],
                "damage":   -(_hp5_ev["new_hp"] - _hp5_ev["old_hp"]),  # negativo = cura
                "outcome":  "regen",
                "hp_after": _hp5_ev["new_hp"],
                "source":   "regen",
            })

        # ── ActiveRegen (consumíveis HoT) ─────────────────────────────────────
        # Processa ticks de regeneração de HP dos consumíveis.
        # Mesmo padrão do ConsumableSystem offline (systems.py:4533-4555).
        from components import ActiveRegen as _AR
        _regen_remove = []
        _all_regen = list(self.world.get_entities_with(_AR))
        for _regen_eid, _regen in _all_regen:
            if _regen_eid not in self._player_eids.values():
                continue
            _rcst = self.world.get_component(_regen_eid, CombatStats)
            if not _rcst:
                _regen_remove.append(_regen_eid)
                continue
            _regen.tick_timer -= dt
            if _regen.tick_timer <= 0:
                _regen.tick_timer += _regen.interval
                _regen.ticks_remaining -= 1
                _old_hp = _rcst.current_hp
                _rcst.current_hp = min(_rcst.max_hp, _rcst.current_hp + _regen.heal_per_tick)
                _healed = _rcst.current_hp - _old_hp
                if _healed > 0:
                    # Envia STATS_UPDATE com heal_amount para o cliente
                    self._pending_xp_deliveries.append({
                        "player_eid":  _regen_eid,
                        "xp":          0,
                        "mob_eid":     -1,
                        "hp":          _rcst.current_hp,
                        "hp_max":      _rcst.max_hp,
                        "heal_amount": _healed,
                        "heal_sid":    "consumable_hot",
                    })
                if _regen.ticks_remaining <= 0:
                    _regen_remove.append(_regen_eid)
        for _regen_eid in _regen_remove:
            try:
                self.world.remove_component(_regen_eid, _AR)
            except Exception:
                pass

        # Skills ANTES do auto-attack: skill dispara em mob vivo, depois auto-attack
        # (se ordem fosse invertida, auto-attack poderia matar o mob antes da skill checar HP)
        self._process_skill_requests()

        # Player→mob: usa deal_damage() offline; Mob→player: detectado por variação de HP
        self._process_player_attacks(dt, player_hp_snap)

        # Sweep: mobs com HP <= 0 sem PendingDeath (DoT, outros caminhos fora de deal_damage)
        from components import Enemy as _Enemy, CombatStats as _CS2, PendingDeath as _PD
        for eid in list(self._mob_eids):
            _cs = self.world.get_component(eid, _CS2)
            _pd = self.world.get_component(eid, _PD)
            if _cs and _cs.current_hp <= 0 and _pd is None:
                self.world.add_component(eid, _PD(killer_entity_id=-1))

        # Processa mortes (PendingDeath) — XP, SpawnZone, despawn, remove_entity
        self._death_handler.update()
        for entry in self._death_handler.consume_despawns():
            eid = entry["eid"]
            self._mob_eids.discard(eid)
            self._mob_damage_log.pop(eid, None)  # limpa entradas stale
            if not any(d["eid"] == eid for d in self._despawned_this_tick):
                self._despawned_this_tick.append(entry)
        for entry in self._death_handler.consume_xp():
            self._pending_xp_deliveries.append(entry)
            # Aplica XP no ECS do servidor para manter level/xp sincronizados no save
            _xp_peid = entry["player_eid"]
            _xp_amt  = entry["xp"]
            from components import CharacterStats as _CharXP, CombatStats as _CsXP, PermanentStats as _PermXP
            _char_xp = self.world.get_component(_xp_peid, _CharXP)
            _cs_xp   = self.world.get_component(_xp_peid, _CsXP)
            _perm_xp = self.world.get_component(_xp_peid, _PermXP)
            if _char_xp and _cs_xp:
                _level_before = _char_xp.level
                _char_xp.current_xp += _xp_amt
                from stats_system import process_levelups as _pu
                _pu(self.world, _xp_peid, _char_xp, _cs_xp, _perm_xp)
                # Se subiu de nível, notifica cliente do novo HP (cheio após level up)
                if _char_xp.level > _level_before:
                    # process_levelups recalculou CombatStats — re-aplica overrides de equip
                    self._apply_stat_overrides(_xp_peid)
                    self._pending_xp_deliveries.append({
                        "player_eid": _xp_peid,
                        "xp":         0,
                        "mob_eid":    -1,
                        "hp":         _cs_xp.current_hp,
                        "hp_max":     _cs_xp.max_hp,
                    })
            print(f"[XP] player {_xp_peid} ganhou {_xp_amt} XP (mob {entry['mob_eid']})")

        self._process_loot_drops(dt)

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

        # Detecta mobs que iniciaram movimento neste tick (target_tile mudou).
        # Emite ao INÍCIO da animação (não ao fim): cliente e servidor animam em paralelo,
        # eliminando o lag estrutural de 1 animação (~376ms) que havia com current_tile.
        for eid in list(self._mob_eids):
            tm = self.world.get_component(eid, TileMovement)
            if tm is None:
                self._mob_eids.discard(eid)
                if not any(d["eid"] == eid for d in self._despawned_this_tick):
                    self._despawned_this_tick.append({"eid": eid, "tx": None, "ty": None})
                continue
            old_target = pre_mob_target.get(eid)
            new_target = (tm.target_tile_x, tm.target_tile_y)
            if old_target and old_target != new_target:
                self._moved_this_tick.append({
                    "eid":     eid,
                    "tx":      new_target[0], "ty":      new_target[1],
                    "from_tx": old_target[0], "from_ty": old_target[1],
                })

        # Detecta novos projéteis de mobs (criados por EnemyAISystem._spawn_projectile)
        # e envia como ENTITY_SPAWN kind="mob_projectile" para clientes renderizarem.
        # Detecta também projéteis removidos (hit ou alvo morto) para cleanup no cliente.
        from components import Projectile as _Proj, Position as _ProjPos
        _current_proj_eids: set[int] = set()
        for _peid, _ppos, _prj in self.world.get_entities_with(_ProjPos, _Proj):
            _current_proj_eids.add(_peid)
            if _peid not in self._known_projectile_eids:
                # Novo projétil: broadcast spawn com posição + cor + direção
                # tx/ty necessários para o filtro AOI em _build_aoi_update
                _proj_tx = int(_ppos.x / TILE_SIZE)
                _proj_ty = int(_ppos.y / TILE_SIZE)
                self._spawned_this_tick.append({
                    "eid":            _peid,
                    "kind":           "mob_projectile",
                    "tx":             _proj_tx,
                    "ty":             _proj_ty,
                    "x":              _ppos.x,
                    "y":              _ppos.y,
                    "attacker_seid":  self._remote_mobs_reverse_srv(_prj.attacker_id),
                    "target_seid":    self._player_seid_by_eid(_prj.target_id),
                    "color":          list(getattr(_prj, "color", (220, 160, 60))),
                    "is_arrow":       bool(getattr(_prj, "is_arrow", True)),
                    "dir_x":          float(getattr(_prj, "dir_x", 1.0)),
                    "dir_y":          float(getattr(_prj, "dir_y", 0.0)),
                    "speed":          float(getattr(_prj, "speed", 380.0)),
                })
        # Projéteis que foram removidos neste tick → despawn no cliente
        for _gone_peid in self._known_projectile_eids - _current_proj_eids:
            self._despawned_this_tick.append({"eid": _gone_peid, "tx": None, "ty": None})
        self._known_projectile_eids = _current_proj_eids

        # Limpa deltas de erro do try/except se necessário
        deltas = self._collect_deltas()
        self._store_snapshot()

        for cb in self._on_tick_callbacks:
            cb(self.tick_count, deltas)

    def _remote_mobs_reverse_srv(self, mob_eid: int) -> int:
        """Retorna o eid canônico de um mob para envio ao cliente.

        Existe por simetria com a API futura do cliente, onde o mapeamento
        entre eid ECS interno e eid exposto na rede pode ser diferente
        (ex: instâncias separadas, remapeamento de entidades remotas).
        No servidor headless: eid ECS == eid de rede → retorna direto.
        """
        return mob_eid

    def _player_seid_by_eid(self, player_eid: int) -> int:
        """Retorna o eid exposto na rede de um player a partir do eid ECS.

        Mesmo padrão que _remote_mobs_reverse_srv: hook para futura separação
        entre eid ECS interno e eid transmitido na rede.
        No servidor headless: eid ECS == eid de rede → retorna direto.
        """
        return player_eid

    def _emit_mob_spawn(self, eid: int, tm) -> None:
        """Adiciona payload de spawn do mob aos deltas do tick atual."""
        payload = self._build_mob_spawn_payload(eid, tm)
        if payload:
            self._spawned_this_tick.append(payload)

    def _collect_mob_effects(self) -> dict:
        """Retorna efeitos ativos em mobs — {str(mob_eid): [effects_list]}.
        Inclui apenas mobs com pelo menos um efeito ativo.
        Chaves são str para compatibilidade JSON."""
        from components import StatusEffects as _SE
        result = {}
        for mob_eid in self._mob_eids:
            sfx = self.world.get_component(mob_eid, _SE)
            if sfx and sfx.effects:
                result[str(mob_eid)] = [
                    {"type": k, "duration": round(v.duration, 2)}
                    for k, v in sfx.effects.items()
                ]
        return result

    def _collect_player_effects(self) -> list[dict]:
        """Returns active status effects for all online players (for client sync)."""
        from components import StatusEffects as _SE, PlayerControlled as _PC
        result = []
        for eid, sfx in self.world.get_entities_with(_SE):
            if not self.world.get_component(eid, _PC):
                continue
            if not sfx.effects:
                continue
            result.append({
                "eid": eid,
                "effects": [
                    {"type": k, "duration": round(v.duration, 2)}
                    for k, v in sfx.effects.items()
                ],
            })
        return result

    def _collect_deltas(self) -> dict:
        # despawned: lista de eids (ints) para compatibilidade
        # despawned_pos: dict eid→(tx,ty) para AOI check de mobs mortos fora de known_eids
        _eids     = [d["eid"] for d in self._despawned_this_tick]
        _pos_map  = {d["eid"]: (d["tx"], d["ty"])
                     for d in self._despawned_this_tick
                     if d["tx"] is not None}
        deltas = {
            "moved":          list(self._moved_this_tick),
            "stats":          [],
            "effects":        self._collect_player_effects(),
            "mob_effects":    self._collect_mob_effects(),
            "spawned":        list(self._spawned_this_tick),
            "despawned":      _eids,
            "despawned_pos":  _pos_map,   # eid→(tx,ty) — mobs com posição conhecida
            # _pending_mob_attacks emitido ANTES de _combat_this_tick (DOT/HoT):
            # garante que cliente aplique mob_attack (HP = X) → DOT (HP = X-N)
            # em vez de DOT (HP = X-N) → mob_attack (HP = X-N, sem mudança visível).
            "combat":         list(self._pending_mob_attacks) + list(self._combat_this_tick),
            "player_deaths":  list(self._player_deaths_this_tick),
        }
        self._moved_this_tick.clear()
        self._spawned_this_tick.clear()
        self._despawned_this_tick.clear()
        self._pending_mob_attacks.clear()
        self._combat_this_tick.clear()
        self._player_deaths_this_tick.clear()
        self._sfx_damage_players.clear()
        return deltas

    def _store_snapshot(self) -> None:
        """Grava posição (tile_x, tile_y) apenas dos mobs para lag compensation.
        Players são excluídos: lag comp só valida posição de alvo (mob), não do atacante."""
        from components import TileMovement
        snapshot: dict[int, tuple[int, int]] = {}
        for eid in self._mob_eids:
            tm = self.world.get_component(eid, TileMovement)
            if tm is not None:
                snapshot[eid] = (tm.current_tile_x, tm.current_tile_y)
        self._snapshots.append((self.tick_count, snapshot))
        # deque(maxlen=SNAPSHOT_HISTORY) descarta o elemento mais antigo automaticamente

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
