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
from server.log import log
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

from engine.world import World
from shared.constants import TICK_RATE, TICK_INTERVAL, SNAPSHOT_HISTORY, TILE_SIZE, LAG_COMP_WINDOW_MS

from server.skill_processor import SkillProcessorMixin
from server.combat_processor import CombatProcessorMixin
from server.respawn_system import RespawnMixin
from server.loot_processor import LootProcessorMixin
from server.spell_completion_processor import SpellCompletionMixin
from server.trade_processor import TradeProcessorMixin
from server.duel_processor import DuelProcessorMixin
from server.party_processor import PartyProcessorMixin
from server.pvp_zone_processor import PvpZoneProcessorMixin
from server.match_processor import MatchProcessorMixin
from debug.mob_combat_debug import MCL

# move_player() faz snap instantâneo de tile (sem tween real) — esta janela é
# quanto tempo, após o último move aceito, o player ainda conta como "em
# movimento" pra mecânicas que dependem de TileMovement.is_moving no servidor
# (Calmo e Certeiro, regen de Concentração — ver
# ServerCombatStateSystem._tick_player_move_grace). Maior que o intervalo
# real entre moves consecutivos andando contínuo (~TILE_SIZE/PLAYER_SPEED ≈
# 0.29s) pra não "piscar" pra parado entre 2 tiles do mesmo movimento.
PLAYER_MOVE_GRACE_S = 0.4

# move_player(): validação de MOVE não exige mais adjacência exata (1 tile)
# com a última posição confirmada — modelo antigo travava PERMANENTEMENTE
# um player assim que UM move fosse rejeitado por qualquer motivo transitório
# (tile temporariamente ocupado por outro player/mob cruzando o caminho, por
# exemplo), porque todo MOVE seguinte era calculado relativo à posição LOCAL
# do cliente (que diverge cada vez mais da posição congelada do servidor) —
# ver ARQUITETURA_ONLINE.md, decisão sobre desync "player parado pros outros,
# não agra mob". Modelo novo (mesma família do que WoW faz de verdade,
# pesquisado: cliente é confiável pra posição, servidor só valida
# plausibilidade — não repetição exata): aceita qualquer destino alcançável
# dentro do orçamento tempo×velocidade desde a última posição de confiança, e
# sem tile sólido no caminho (linha reta, Bresenham) — pega speedhack/
# teleporte sem travar quem só teve um passo rejeitado por colisão dinâmica.
MOVE_SPEED_TOLERANCE = 2.0   # margem sobre a velocidade nominal (lag/jitter)
MOVE_ELAPSED_CAP_S    = 2.0  # teto de crédito acumulado (evita orçamento
                              # infinito pra quem ficou parado/AFK)
# Fallback só usado se tm.speed nunca foi setado (não deveria acontecer em
# uso normal — TileMovement de player sempre nasce com speed=PLAYER_SPEED,
# entity_factory.py). Mesmo valor (110 px/s), evita import cruzado só por isso.
PLAYER_SPEED_TILES_FALLBACK = 110 / TILE_SIZE


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
        from engine.core_systems import StatusEffectSystem as _Base

        class _Impl(_Base):
            def __init__(self, world, srv):
                super().__init__(world)
                self._srv = srv

            def _emit_damage(self, eid, amount, effect_type, pos, color):
                from engine.components import CombatStats as _CS
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
                from engine.components import CombatStats as _CS
                cs = self.world.get_component(eid, _CS)
                self._srv._combat_this_tick.append({
                    "attacker": -1,
                    "target":   eid,
                    "damage":   -amount,   # negativo = cura
                    "outcome":  "regen",
                    "hp_after": cs.current_hp if cs else 0,
                    "source":   effect_type,
                })

            def _on_resisted_dot(self, eid, school):
                from engine.stats_system import grant_resist_skill_xp
                grant_resist_skill_xp(self.world, eid, school)

        return _Impl(world, srv)


class _MapBundle:
    """Sistemas e dados de UM mapa no mundo compartilhado."""
    __slots__ = ("map_file", "tilemap_entity", "systems", "ai_systems",
                 "transitions", "tile_validation", "pathfinding")

    def __init__(self):
        self.map_file        = ""
        self.tilemap_entity  = -1
        self.systems         = []
        self.ai_systems      = set()  # subconjunto de systems a pular em mapas sem player
        self.transitions     = {}   # (tx,ty) -> {target_map, target_x, target_y}
        self.tile_validation = None
        self.pathfinding     = None


class WorldServer(SkillProcessorMixin, CombatProcessorMixin, RespawnMixin, LootProcessorMixin,
                   SpellCompletionMixin, TradeProcessorMixin, DuelProcessorMixin, PartyProcessorMixin,
                   PvpZoneProcessorMixin, MatchProcessorMixin):

    MAP_FILE = "maps/map_1.csv"   # mapa padrão carregado pelo servidor

    # DESCONTINUADO como "PvP de mundo aberto" (16/07/2026): players são
    # amigáveis por default e PvP só existe em contexto explícito (duelo/
    # zona/arena — ver _pvp_allowed_between). Mantido APENAS como
    # kill-switch de emergência: False desliga TODO contexto PvP de uma
    # vez (duelo incluso). Nunca mais significa "todo mundo pode brigar".
    pvp_enabled: bool = True

    def __init__(self, zone_id: str = "world_main", map_file: str = ""):
        self.zone_id    = zone_id
        self.world      = World()
        self.tick_count = 0
        self.running    = False

        # session_id → entity_id dos jogadores online
        self._player_eids: dict[str, int] = {}
        # Reverse map: eid → session_id (O(1) lookup em get_session_id_for_player)
        self._player_eid_to_sid: dict[int, str] = {}
        # Cache de HP para dirty-check automático a cada tick (eid → (current_hp, max_hp))
        self._player_hp_cache: dict[int, tuple[int, int, int]] = {}   # (hp, hp_max, level)
        # Cache de SkillLevels para dirty-check automático a cada tick (eid → snapshot
        # hashable de levels+xp) — ver _sync_player_skill_levels_dirty().
        self._player_skill_cache: dict[int, tuple] = {}
        self._skill_levels_broadcasts_this_tick: list[dict] = []
        # QuestLog: progresso/entrega de quest server-autoritativo — ver
        # _process_quest_events()/consume_quest_update_broadcasts().
        self._quest_update_broadcasts_this_tick: list[dict] = []

        # Eids de mobs gerenciados pelo servidor
        self._mob_eids: set[int] = set()

        # DEBUG Bug2 (regen/desaparecimento no golpe final): current_hp de cada
        # mob ao FINAL do tick anterior (pós death-sweep) — usado em _tick()
        # para detectar mutações de current_hp ocorridas ENTRE ticks (handlers
        # async de mensagem, ex: PROJECTILE_HIT_CS).
        self._mob_hp_prev: dict[int, float] = {}

        # Spells de projétil aguardando PROJECTILE_HIT_CS do cliente antes de aplicar dano
        self._spells_in_flight_queue: list[dict] = []

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
        # Mortes broadcast pra AOI (corpo visível p/ outros players): {eid, tx, ty}
        self._entity_deaths_this_tick: list[dict] = []
        # Revives (ghost→vivo): {session_id, player_eid, tx, ty, hp, hp_max, mana, max_mana}
        self._player_revives_this_tick: list[dict] = []
        # Atualizações de estado do espírito: {session_id, is_ghost, near_corpse, graveyard_timer}
        self._ghost_state_updates_this_tick: list[dict] = []
        # Marcadores de corpo de player: player_eid → (tx, ty)
        self._player_corpses: dict[int, tuple[int, int]] = {}
        # Dano de DoT/HoT por StatusEffectSystem neste tick: player_eid → total
        # Usado para corrigir o snapshot HP (evitar duplo COMBAT_RESULT)
        self._sfx_damage_players:      dict[int, int]   = {}

        # Rastreamento de dano por mob: mob_eid → {player_eid: total_damage}
        self._mob_damage_log: dict[int, dict[int, int]] = {}

        # XP a entregar aos jogadores (populado em _tick, consumido pelo SessionManager)
        self._pending_stats_updates: list[dict] = []

        # Corpses: corpse_id → {tx, ty, owner_eid, items, timer}
        self._corpses: dict[int, dict] = {}
        self._next_corpse_id: int = 1
        # Notificações de loot pendentes (consumidas pelo SessionManager no dispatch)
        self._pending_loot_notifications: list[dict] = []
        # Corpses que expiraram neste tick: list de {cid, tx, ty}
        self._expired_corpses_this_tick: list[dict] = []

        # Trade (player↔player) — ver server/trade_processor.py.
        # trade_id → TradeSession; player_eid → trade_id (O(1) "já está em
        # trade?"); target_eid → requester_eid (convite pendente, só 1 por vez
        # como alvo OU como requester — checado em request_trade).
        self._trade_sessions: dict[int, "object"] = {}
        self._player_trade: dict[int, int] = {}
        self._pending_trade_invites: dict[int, int] = {}
        self._next_trade_id: int = 1
        # Cancelamentos de trade por distância neste tick: {trade_id, player_a, player_b, reason}
        self._trade_cancellations_this_tick: list[dict] = []

        # Duelo (player↔player) — ver server/duel_processor.py e o contexto
        # PvP registrado em _load_all_maps. par (frozenset de 2 eids) →
        # info do duelo; target_eid → requester_eid (convite pendente).
        self._duel_pairs: dict[frozenset, dict] = {}
        self._pending_duel_invites: dict[int, int] = {}
        # DUEL_END do tick (win/distance/disconnect) — consumidos pelo
        # broadcast loop do SessionManager (consume_duel_end_events).
        self._duel_end_events_this_tick: list[dict] = []

        # Party/Grupo (ver server/party_processor.py) — party_id → info do
        # grupo; índice reverso pra achar rápido o grupo de um eid;
        # target_eid → requester_eid (convite pendente, sem checagem de
        # distância — ver decisão em ARQUITETURA_ONLINE.md §34.19).
        self._parties: dict[int, dict] = {}
        self._player_party_id: dict[int, int] = {}
        self._next_party_id: int = 1
        self._pending_party_invites: dict[int, int] = {}
        # Eventos de grupo do tick (party_id mudou, ou ("left", eid)) —
        # consumidos pelo broadcast loop do SessionManager.
        self._party_state_events_this_tick: list = []

        # Zona PvP (Fase F, ver server/pvp_zone_processor.py) — map_file →
        # lista de {"name","rect"}, populado por _load_map_for. Sem estado
        # de pares/tick-check: é um predicado stateless (ver docstring do
        # mixin).
        self._pvp_zones_by_map: dict[str, list[dict]] = {}

        # Arena 2x2 (Fase G leva 1, ver server/match_processor.py) — fila
        # FIFO de party_ids + partidas ativas (instância privada por
        # partida, time = componente Faction temporário no player).
        self._arena_queue_2v2: list[int] = []
        self._active_matches: dict[str, dict] = {}
        self._player_match_id: dict[int, str] = {}
        self._next_match_id: int = 1
        self._arena_match_start_events_this_tick: list[dict] = []
        self._arena_match_end_events_this_tick: list[dict] = []
        self._arena_match_result_events_this_tick: list[dict] = []
        # Aceite de partida (21/07/2026): eid → match_id enquanto aguarda
        # aceite/expira — NÃO é o mesmo que _player_match_id (que só passa a
        # existir pra um eid depois que ele de fato ACEITA e entra).
        self._pending_arena_invite: dict[int, str] = {}
        self._arena_match_found_events_this_tick: list[dict] = []
        # Portão físico de arena (22/07/2026): 1 entrada por eid quando o
        # portão da instância abre (fim do preparo) ou quando alguém aceita
        # tarde, já com o portão aberto — ver consume_arena_gate_open_events.
        self._arena_gate_open_events_this_tick: list[dict] = []

        # Timer de ataque por jogador: session_id → segundos até próximo hit
        self._attack_timers: dict[str, float] = {}

        # Dano PvP (player→player) neste tick: player_eid → total dano sofrido
        # Inicializado aqui e resetado em _tick para evitar getattr() lazy
        self._pvp_damage_this_tick: dict[int, int] = {}

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

        # Multi-map: bundles de sistemas e dados por mapa
        self._map_bundles: dict[str, _MapBundle] = {}
        self._player_maps: dict[str, str] = {}   # session_id → map_file

        self._load_all_maps()

        # Sistemas globais: rodam UMA vez por tick, após todos os bundles.
        # Se ficassem dentro de cada bundle (sem map_filter) rodariam N×/tick
        # (N = número de mapas carregados), causando timers/movimento N× rápidos.
        from engine.world_systems import TileMovementSystem as _TMS, ProjectileSystem as _ProjSys
        self._global_tms           = _TMS(self.world)
        self._global_proj_sys      = _ProjSys(self.world, screen=None)
        self._global_sfx_sys       = _ServerStatusEffectSystem.build(self.world, self)
        self._status_effect_system = self._global_sfx_sys  # alias de compat

        from server.server_death_handler import ServerDeathHandler
        self._death_handler = ServerDeathHandler(self.world, world_server=self)

        from engine.core_systems import ServerCombatStateSystem
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
        self._skill_last_used:    dict[tuple, float] = {}
        # CD efetivo por skill (com reduções de talento) — sincroniza com o que o cliente recebeu.
        self._skill_effective_cd: dict[tuple, float] = {}

        # Fila de skill requests recebidas dos clientes (processada em _tick)
        self._pending_skill_requests: list[dict] = []
        # Spells com cast_time pendentes de conclusão (gerenciadas por SpellCompletionMixin)
        self._pending_spell_completions: list[dict] = []
        # Pousos de knockback pendentes (stun + feedback de colisão só disparam
        # quando a tween de deslocamento termina, não no instante em que o
        # servidor resolve o empurrão — ver SpellCompletionMixin._process_knockback_landings)
        self._pending_knockback_landings: list[dict] = []
        # Resultados de skills processadas no tick (consumido pelo SessionManager)
        self._skill_results_this_tick: list[dict] = []
        # Efeitos de apresentação (som/VFX) broadcast AOI — separado do gameplay
        self._skill_effects_this_tick: list[dict] = []
        # Correções de posição por skill (Interceptar etc.) — enviadas direto ao caster
        self._skill_position_corrections: list[dict] = []
        # HP updates de players para broadcast AOI (ex: auto-cura de skill)
        self._player_hp_broadcasts_this_tick: list[dict] = []
        # Eventos de som posicionais (mob aggro, etc.) para broadcast AOI
        self._pending_sound_events: list[dict] = []
        # Eids cujo CombatState.is_visible mudou neste tick (ex: Camuflagem
        # ativa/expira) — força _build_update_for_session a reavaliar _can_see()
        # mesmo sem a entidade ter se movido (ver session.py)
        self._visibility_changed_this_tick: list[int] = []
        # Snapshot de estados de mob para detectar transições de aggro
        self._mob_states_prev: dict[int, str] = {}
        # Projéteis de mobs conhecidos (para detectar novos e removidos a cada tick)
        self._known_projectile_eids: set[int] = set()

        # SkillSystem instanciado AQUI mas NÃO adicionado a self._systems
        # (chamado manualmente em _process_skill_requests)
        from ui.systems import SkillSystem
        self._skill_system = SkillSystem(self.world, player_entity_id=-1)
        # Servidor usa melee range com lag tolerance (1 + MELEE_LAG_TOLERANCE tiles)
        self._skill_system._server_authoritative = True

        # ── Profiler de tick ─────────────────────────────────────────────────
        # Acumula tempo por seção; resumo impresso a cada _PERF_REPORT_TICKS ticks.
        self._perf_accum:       dict[str, float] = {}
        self._perf_count:       int = 0
        self._perf_map_active:  dict[str, int]   = {}  # map → ticks com ≥1 player
        self._perf_peak_maps:   int = 0                # pico de mapas simultâneos ativos
        self._perf_cpu_sum:     float = 0.0            # soma de % CPU por tick
        self._perf_cpu_peak:    float = 0.0            # pico de % CPU no período
        self._PERF_REPORT_TICKS = 300          # ~10s a 30 ticks/s
        self._PERF_BUDGET_MS    = 1000.0 / 30  # 33.3ms por tick
        # psutil: medição de CPU do processo. cpu_percent(interval=None) acumula desde
        # a última chamada — primeiro call inicializa o baseline, por isso chamamos aqui.
        try:
            import psutil as _psutil
            self._perf_proc = _psutil.Process()
            self._perf_proc.cpu_percent(interval=None)  # baseline
        except Exception:
            self._perf_proc = None
        # Log de perf gravado em arquivo — limpo a cada execução do servidor.
        import os as _os
        _log_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs")
        _os.makedirs(_log_dir, exist_ok=True)
        self._perf_log = open(_os.path.join(_log_dir, "server_perf.log"), "w",
                              encoding="utf-8", buffering=1)

    # ── Inicialização do mundo ────────────────────────────────────────────────

    def _load_all_maps(self) -> None:
        """Carrega mapa principal e todos os mapas de destino das transições (depth 1)."""
        main_bundle = self._load_map_for(self._map_file)
        self._map_bundles[self._map_file] = main_bundle

        self.tilemap_entity = main_bundle.tilemap_entity

        # Carrega mapas de destino das transições (depth 1)
        for trans in main_bundle.transitions.values():
            tgt = trans["target_map"]
            if tgt not in self._map_bundles:
                try:
                    self._map_bundles[tgt] = self._load_map_for(tgt)
                except Exception as e:
                    log.warning(f"[WorldServer] falha ao carregar mapa {tgt}: {e}")

        log.info(f"[WorldServer] mapas carregados: {list(self._map_bundles.keys())}")

        # Guarda AUTOMÁTICA contra "_svc apontando pro mapa errado" (item A3,
        # PROBLEMAS_ARQUITETURA.md §11): resolver por-entidade — funções de
        # módulo que recebem entity_id (is_tile_walkable) resolvem o bundle
        # do mapa da PRÓPRIA entidade por chamada, sem depender de
        # register_map_services_for() ter sido lembrado no entry point.
        # find_path()/get_tilemap() (sem eid na assinatura) continuam
        # cobertos só pela regra do register — ver CLAUDE.md.
        from engine.world_systems import register_service_resolver

        def _resolve_bundle_for_eid(eid: int):
            _m = self.get_entity_map(eid)
            return self._map_bundles.get(_m) if _m else None

        register_service_resolver(_resolve_bundle_for_eid)

        # Contexto PvP (engine/faction_system.py): entre dois PLAYERS de
        # facção amigável, quem decide se o dano é permitido é o CONTEXTO
        # (decisão do usuário 16/07/2026: players são TODOS amigáveis por
        # default — PvP só existe em contexto explícito: duelo aceito,
        # zona PvP, arena/campo de batalha). O "PvP de mundo aberto"
        # global (flag pvp_enabled liberando o mundo inteiro) foi
        # descontinuado de propósito — era o provisório da Fase 1.
        # Resolver COMPOSTO: cada contexto novo entra como mais uma
        # consulta aqui (duelo → par em _duel_pairs; futuro: zona PvP da
        # posição dos dois + exceção de party; times/MOBA nem passam por
        # aqui — Faction de time sobrescreve "jogadores" e a regra de
        # facção pura decide).
        from engine.faction_system import register_pvp_context
        register_pvp_context(self._pvp_allowed_between)

        # Golpe letal — hook plugável ÚNICO no ponto único de dano (engine/
        # core_systems.apply_damage_core), composto (duelo OU arena, mesmo
        # padrão do _pvp_allowed_between): duelo estilo WoW (golpe que
        # mataria encerra o duelo com o perdedor a 1 HP) e arena (Fase G —
        # golpe que mataria elimina o player da partida, 1 HP + imune, em
        # vez de matar/virar fantasma). Ver _lethal_interceptor_composite.
        from engine.core_systems import register_lethal_interceptor
        register_lethal_interceptor(self._lethal_interceptor_composite)

        # Rastreador de dano — slot ÚNICO (mesma composição de
        # _lethal_interceptor_composite): placar de fim de partida de Arena
        # (_track_arena_damage) + estatísticas acumuladas pro modal de
        # estatísticas (Fase E, _track_cumulative_damage). Ver
        # _damage_tracker_composite.
        from engine.core_systems import register_damage_tracker
        register_damage_tracker(self._damage_tracker_composite)

    def _load_map_for(self, map_file: str, instance_key: str = "") -> "_MapBundle":
        """
        Carrega um mapa e inicializa os sistemas headless para ele.
        Retorna um _MapBundle com os sistemas e dados deste mapa.

        `instance_key` (Fase G — instâncias de arena/campo de batalha,
        ver server/match_processor.py): quando fornecido, TODA a chave de
        isolamento (bundle, MapLocation das entidades, map_filter dos
        sistemas, pvp_zones_by_map) usa essa string sintética em vez do
        `map_file` real — permite carregar o MESMO arquivo de mapa várias
        vezes concorrentemente (uma instância por partida) sem colidir.
        `map_file` continua sendo SEMPRE o caminho real (só ele é lido do
        disco, e só ele é mandado pro cliente — ver _template_file_of).
        Vazio (default) = comportamento de sempre, chave == map_file."""
        from engine.map_loader import load_map_csv
        from engine.entity_factory import create_tilemap
        from engine.world_systems import (SpawnZoneSystem, EnemyAISystem, EnemyAbilitySystem,
                             TileValidationSystem, PathfindingSystem, CombatSystem,
                             ProjectileSystem, register_services, TauntSystem)
        from engine.components import MapLocation as _MLl

        key = instance_key or map_file
        log.info(f"[WorldServer] carregando mapa: {map_file}" +
                (f" (instância {key})" if instance_key else ""))
        terrain_matrix, object_matrix, spawn_points, terrain_visual = \
            load_map_csv(map_file)
        self._pvp_zones_by_map[key] = spawn_points.get("pvp_zones", [])

        # Snapshot de entidades ANTES de criar as do mapa
        _eids_before = set(self.world._components.keys())

        tilemap_entity = create_tilemap(
            self.world, terrain_matrix, object_matrix, terrain_visual)

        self._create_spawn_zones_for_map(spawn_points.get("spawn_zones", []), key)
        self._create_training_dummies(spawn_points.get("training_dummies", []))
        self._create_combat_npcs(spawn_points.get("combat_npcs", []), key)
        self._create_service_npcs(spawn_points)

        # Snapshot DEPOIS — todas as novas entidades ganham MapLocation
        _eids_after = set(self.world._components.keys())
        for _new_eid in (_eids_after - _eids_before):
            if _new_eid not in self.world._components:
                continue
            self.world.add_component(_new_eid, _MLl(key))

        # Sistemas específicos deste mapa
        tile_validation = TileValidationSystem(self.world, tilemap_entity=tilemap_entity,
                                               map_filter=key)
        pathfinding     = PathfindingSystem(self.world, tilemap_entity=tilemap_entity)
        combat          = CombatSystem(self.world, is_server=True,
                                       on_damage_dealt=self._log_mob_damage_hit)

        # Registra serviços globais (sobrescrito por _tick() antes de cada bundle)
        register_services(combat=combat, pathfinding=pathfinding,
                          tile_validation=tile_validation)

        # Callback de retaliation do Escudo de Fogo
        from engine.world_systems import _svc as _sys_svc
        _srv_ref = self
        def _on_retaliation(player_eid: int, mob_eid: int, damage: int, hp_after: int) -> None:
            from engine.components import CombatStats as _CSEF
            _cs_mob = _srv_ref.world.get_component(mob_eid, _CSEF)
            _srv_ref._combat_this_tick.append({
                "attacker": player_eid,
                "target":   mob_eid,
                "damage":   damage,
                "outcome":  "hit",
                "hp_after": hp_after,
                "source":   "skill",
            })
        _sys_svc["emit_retaliation"] = _on_retaliation

        # P4: injeção direta de serviços por bundle — elimina dependência no global _svc.
        enemy_ai_system = EnemyAISystem(self.world, map_filter=key,
                                        pathfinding=pathfinding, tile_validation=tile_validation)
        enemy_ab_system = EnemyAbilitySystem(self.world, map_filter=key,
                                             pathfinding=pathfinding)
        taunt_system = TauntSystem(self.world, map_filter=key,
                                   pathfinding=pathfinding, tile_validation=tile_validation,
                                   moved_this_tick=self._moved_this_tick)

        _spawn_sys = SpawnZoneSystem(self.world, map_filter=key, pathfinding=pathfinding)
        _spawn_sys.ACTIVATION_RADIUS = 999999

        systems = [
            tile_validation,  # 1. cache de tiles ocupados
            _spawn_sys,       # 2. spawn de mobs — raio ilimitado
            enemy_ai_system,  # 3. IA: aggro, pathfinding, ataque
            enemy_ab_system,  # 4. habilidades especiais (DoT, debuffs) — filtrado por mapa
            taunt_system,     # 5. movimento forçado de PLAYERS sob "taunted" (Brado Provocativo)
            # _ServerStatusEffectSystem, ProjectileSystem e TileMovementSystem removidos:
            # rodam GLOBALMENTE em _tick() após todos os bundles (self._global_*).
        ]

        # Lê transições do mapa
        transitions = {}
        for t in spawn_points.get("transitions", []):
            tkey = (int(t["x"]), int(t["y"]))
            transitions[tkey] = {
                "target_map": t["target_map"],
                "target_x":   int(t["target_x"]),
                "target_y":   int(t["target_y"]),
            }

        bundle = _MapBundle()
        bundle.map_file        = map_file
        bundle.tilemap_entity  = tilemap_entity
        bundle.systems         = systems
        bundle.ai_systems      = {enemy_ai_system, enemy_ab_system}
        bundle.transitions     = transitions
        bundle.tile_validation = tile_validation
        bundle.pathfinding     = pathfinding

        log.info(f"[WorldServer] mapa OK: {map_file} — {len(transitions)} transições")
        return bundle

    def _load_instance(self, template_file: str, instance_key: str) -> "_MapBundle":
        """Fase G — carrega uma cópia PRIVADA de `template_file` sob
        `instance_key` (registra em self._map_bundles) e retorna o bundle.
        Ver docstring de _load_map_for pro racional de instance_key."""
        bundle = self._load_map_for(template_file, instance_key=instance_key)
        self._map_bundles[instance_key] = bundle
        return bundle

    def _unload_instance(self, instance_key: str) -> None:
        """Fase G — desfaz uma instância criada por _load_instance: remove
        toda entidade com MapLocation(instance_key) (inclusive o tilemap),
        libera o bundle e os dicts auxiliares chaveados por instance_key.
        Chamar SÓ depois de já ter tirado os players de lá (match_processor
        teleporta de volta ANTES de desalocar)."""
        from engine.components import MapLocation as _MLu
        _to_remove = [eid for eid, ml in self.world.get_entities_with(_MLu)
                     if ml.map_file == instance_key]
        for eid in _to_remove:
            try:
                self.world.remove_entity(eid)
            except Exception:
                pass
        self._map_bundles.pop(instance_key, None)
        self._pvp_zones_by_map.pop(instance_key, None)

    def _template_file_of(self, map_or_instance_key: str) -> str:
        """Fase G — 'de-para' pro cliente: instance_key sintética
        (`f"{template}::{match_id}"`) → só a parte do template real (o
        arquivo que o cliente de fato carrega). Chave normal (mapa aberto)
        passa direto, sem separador."""
        return map_or_instance_key.split("::", 1)[0]

    def _create_training_dummies(self, dummies_data: list) -> None:
        from engine.entity_factory import create_training_dummy as _ctd
        for tx, ty in dummies_data:
            eid = _ctd(self.world, tx, ty)

    def _create_combat_npcs(self, combat_npcs_data: list, map_file: str) -> None:
        """Cria NPCs de combate (guarda, etc — Sistema de Facções, Fase 4) a
        partir de `{mapa}_entities.json::combat_npcs`. Diferente de
        `_create_npc_blockers` (entidade mínima, só pra pathfinding): NPC de
        combate já nasce com todos os componentes reais via
        `create_combat_npc()`/`_build_combat_entity` (TileMovement/
        CombatStats/AIControlled/Combatant) — não precisa de blocker
        separado, e sincroniza pro cliente pelo mesmo pipeline de mob
        (`_mob_eids`, gate `Combatant`, ver tick principal)."""
        from engine.entity_factory import create_combat_npc
        from engine.components import MapLocation as _MLcnpc
        for c in combat_npcs_data:
            eid = create_combat_npc(
                self.world, c["x"], c["y"],
                faction=c.get("faction", "guardas_vila"),
                name=c.get("name", ""),
                profession=c.get("profession", "Guarda"),
                race=c.get("race", "Humanoide"),
                entity_class=c.get("entity_class", ""),
                level=c.get("level", 1),
                tier=c.get("tier", "normal"),
                is_ranged=c.get("is_ranged", False),
            )
            self.world.add_component(eid, _MLcnpc(map_file))

    def _create_service_npcs(self, spawn_points: dict) -> None:
        """Cria mercador/ferreiro/dador-de-missão/treinador como NPCs de
        COMBATE DE VERDADE (Fase 1, 21/07/2026, pedido do usuário) — antes
        (`_create_npc_blockers`) só existia um bloqueador mínimo
        (TileMovement+NPC vazio) aqui, só pra pathfinding de mob não
        atravessar; a identidade de loja/treino/missão era 100%
        client-side (cada cliente lia o mesmo `_entities.json` e criava a
        própria cópia via `create_merchant`/etc., sem nenhum sync).

        Agora o SERVIDOR é quem cria a entidade de verdade (mesmas
        funções de `engine/entity_factory.py`, já com HP/IA de combate
        via `_build_combat_entity` — mesma infra do Guarda Real) e ela
        sincroniza pro cliente pelo pipeline normal de mob (`Combatant`,
        ver `_build_mob_spawn_payload`/`_spawn_remote_mob`) — `game.py`
        não cria mais essas 4 localmente em modo online (`_online` gate)."""
        from engine.entity_factory import (
            create_merchant as _csn_merchant, create_blacksmith as _csn_blacksmith,
            create_quest_giver as _csn_qg, create_trainer as _csn_trainer,
        )
        for col, row, name, shop_id, lvl, prof in spawn_points.get("merchants", []):
            _csn_merchant(self.world, col, row, name=name, shop_id=shop_id,
                         level=lvl, profession=prof)
        for col, row, name, quest_ids, turn_in_ids, lvl, prof in spawn_points.get("quest_givers", []):
            _csn_qg(self.world, col, row, name=name,
                   quest_ids=quest_ids, turn_in_ids=turn_in_ids,
                   level=lvl, profession=prof)
        for col, row, name, shop_id, lvl, prof in spawn_points.get("blacksmiths", []):
            _csn_blacksmith(self.world, col, row, name=name,
                           shop_id=shop_id, level=lvl, profession=prof)
        for col, row, name, class_id, quest_ids, turn_in_ids, lvl, prof in spawn_points.get("trainers", []):
            _csn_trainer(self.world, col, row, name=name,
                        class_id=class_id, quest_ids=quest_ids, turn_in_ids=turn_in_ids,
                        level=lvl, profession=prof)

    def _create_spawn_zones(self, zones_data: list) -> None:
        """Compat: cria SpawnZones sem MapLocation (usado antes do multi-map)."""
        self._create_spawn_zones_for_map(zones_data, "")

    def _create_spawn_zones_for_map(self, zones_data: list, map_file: str) -> None:
        """Cria entidades SpawnZone a partir dos dados já processados pelo map_loader.
        Cada entrada já é uma zona achatada com enemy_type, enemy_tier, count."""
        from engine.components import SpawnZone

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
                faction        = z.get("faction",       "monstros_hostis"),
            ))

    # ── API pública para SessionManager ──────────────────────────────────────

    def spawn_player(self, session_id: str, char_data: dict) -> int:
        """
        Cria entidade do jogador no ECS com todos os componentes necessários para skills.
        Abordagem cirúrgica: cria manualmente sem efeitos colaterais de create_player().
        """
        from engine.components import (Position, TileMovement, PlayerControlled, CombatState,
                                 CombatStats, CharacterStats, PermanentStats, Visible,
                                 Equipment, Wallet, Inventory, GhostState)
        from engine.stats_system import CLASS_BASE_STATS, apply_char_stats_to_combat, sync_attack_interval
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
        self.world.add_component(eid, GhostState())
        self.world.add_component(eid, Visible())

        # Reconstrói TODOS os slots de equipamento do equipment_json — não só a
        # aljava. Handlers de skill (ex: _skill_tiro_multiplo) checam
        # equip.slots["mainhand"].subtype == "Bow" no servidor; faltando isso,
        # "Precisa de um arco equipado." disparava mesmo com arco equipado.
        _eq_comp = Equipment()
        _eq_raw  = char_data.get("equipment_json") or char_data.get("equipment") or "{}"
        try:
            _eq_data = _json.loads(_eq_raw) if isinstance(_eq_raw, str) else (_eq_raw or {})
        except Exception:
            _eq_data = {}
        if isinstance(_eq_data, dict):
            for _slot, _item_d in _eq_data.items():
                if _slot not in _eq_comp.slots or not isinstance(_item_d, dict):
                    continue
                _item_d.setdefault("slot", _slot)
                _eq_comp.slots[_slot] = self._reconstruct_item(_item_d)
        self.world.add_component(eid, _eq_comp)
        self.world.add_component(eid, Wallet())

        # Inventário: reconstrói do inventory_json salvo. Necessário para que
        # handlers de skill (ex: _skill_recarregar) validem munição contra o
        # estado real — sem isso, "Inventory" fica None no servidor e Recarregar
        # sempre falha com "Não há flechas disponíveis", mesmo com flechas na bag.
        self.world.add_component(eid, Inventory())
        self.load_player_inventory(eid, char_data.get("inventory_json", "[]"))

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
            char.spirit           = int(_stats.get("spirit",       char.spirit))
        self.world.add_component(eid, char)

        perm = PermanentStats()
        self.world.add_component(eid, perm)

        # CombatStats derivado dos atributos base — equipamento e talentos são
        # aplicados abaixo a partir dos dados REAIS já carregados (Equipment,
        # TalentTree), nunca de um valor que o cliente diz que deveria ser
        # (antes: client_max_hp/client_ap, hints confiados sem validar — ver
        # arquitetura/PROBLEMAS_ARQUITETURA.md).
        cs = CombatStats()
        apply_char_stats_to_combat(char, cs, perm)
        self.world.add_component(eid, cs)
        self._apply_equipment_modifiers(eid)   # attack_power/crit/armor/spell_power/etc. reais
        sync_attack_interval(cs, self.world.get_component(eid, Equipment))
        # Restaura HP salvo; se não houver, usa max_hp (já com bônus de equip); nunca excede max_hp
        saved_hp = int(char_data.get("hp", 0))
        cs.current_hp = min(saved_hp, cs.max_hp) if saved_hp > 0 else cs.max_hp
        # Imunidade pós-login: 3s de proteção para mobs não agrirem durante loading.
        # Mesmo mecanismo do pós-revive (_revive_player) — is_visible restaurado
        # automaticamente por _tick_respawn_immunity quando o contador zera.
        _cst_login = self.world.get_component(eid, CombatState)
        if _cst_login:
            _cst_login.is_visible             = False
            _cst_login.respawn_immunity_ticks = 90  # 3s @ 30 ticks/s

        # Wallet: restaura gold salvo
        _gold = int(_stats.get("gold", 0))
        wall = self.world.get_component(eid, Wallet)
        if wall:
            wall.gold = _gold

        # PlayerSkills: restaura skills salvas ou usa iniciais da classe
        try:
            from engine.components import PlayerSkills as _PS
            from content.skill_config import SKILL_CATALOG, INITIAL_SKILLS_BY_CLASS
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
            log.warning(f"[World] aviso: PlayerSkills não criado — {_e}")

        # TalentTree — necessário para process_levelups() incrementar available_points
        _tal_alloc_spawn: dict = {}
        try:
            from engine.components import TalentTree as _TT
            _tt_comp = _TT()
            _tal_raw2 = char_data.get("talents_json") or "{}"
            _tal_d2   = _json.loads(_tal_raw2) if isinstance(_tal_raw2, str) else (_tal_raw2 or {})
            _tt_comp.chosen_build     = _tal_d2.get("chosen_build", _tt_comp.chosen_build)
            _tt_comp.available_points = int(_tal_d2.get("available_points", 0))
            _tt_comp.allocated        = dict(_tal_d2.get("allocated", {}))
            # Rede de segurança: o orçamento total (disponível + já gasto) nunca
            # pode ser menor do que 1 ponto por level-up que o personagem já
            # teria recebido (process_levelups concede +1 por nível, a partir
            # do nível 2). Sem isso, qualquer bug de persistência (ex: o de
            # chosen_build ausente que zerava talentos sem reembolso — ver
            # arquitetura/PROBLEMAS_ARQUITETURA.md) perde pontos do jogador
            # PERMANENTEMENTE — autocura aqui, todo login, em vez de só
            # corrigir a causa e deixar quem já foi afetado sem recuperação.
            _expected_budget = max(0, char.level - 1)
            _real_budget = _tt_comp.available_points + sum(_tt_comp.allocated.values())
            if _real_budget < _expected_budget:
                _tt_comp.available_points += (_expected_budget - _real_budget)
                # Propaga a correção pro char_data que vira o payload de
                # LOGIN_OK (char_data é mutado in-place — o caller usa o
                # mesmo dict) — sem isso, o cliente recebia de volta o
                # talents_json ANTIGO (sem o top-up) e desfazia a correção
                # na primeira sincronização.
                char_data["talents_json"] = _json.dumps({
                    "chosen_build":     _tt_comp.chosen_build,
                    "allocated":        _tt_comp.allocated,
                    "available_points": _tt_comp.available_points,
                })
            _tal_alloc_spawn          = _tt_comp.allocated
            self.world.add_component(eid, _tt_comp)
        except Exception as _te:
            log.warning(f"[World] aviso: TalentTree não criado — {_te}")

        # cs_flags (ex: impacto_maquina_matar) e modifiers de atributo (ex: parry_rating)
        # dos talentos salvos — antes só os cs_flags eram restaurados aqui (loop
        # duplicado); modifiers de atributo nunca eram reaplicados no login, ficavam
        # ausentes até o próximo TALENT_UPDATE/SAVE_STATE. _apply_talent_modifiers
        # já faz reset + as duas fases num só lugar (mesma lógica de
        # apply_talent_effects_to_player, sem duplicação).
        try:
            self._apply_talent_modifiers(eid, _tal_alloc_spawn)
        except Exception as _te:
            log.warning(f"[World] aviso: talent effects não aplicados no spawn — {_te}")

        # SkillLevels — progressão Tibia-like por uso (armas/escudo/defesa/
        # resistências/magic), server-autoritativo. Ver stats_system.py e
        # PROBLEMAS_ARQUITETURA.md, seção skill level.
        try:
            from engine.components import SkillLevels as _SKL
            from engine.stats_system import apply_skill_bonuses_to_combat as _apply_skl_bonuses
            _skl_comp = _SKL()
            _skl_raw  = char_data.get("skill_levels_json") or "{}"
            _skl_d    = _json.loads(_skl_raw) if isinstance(_skl_raw, str) else (_skl_raw or {})
            for _sid, _lvl in (_skl_d.get("levels") or {}).items():
                if _sid in _skl_comp.levels:
                    _skl_comp.levels[_sid] = int(_lvl)
            for _sid, _sxp in (_skl_d.get("xp") or {}).items():
                if _sid in _skl_comp.xp:
                    _skl_comp.xp[_sid] = int(_sxp)
            self.world.add_component(eid, _skl_comp)
            _apply_skl_bonuses(_skl_comp, cs)
        except Exception as _skl_err:
            log.warning(f"[World] aviso: SkillLevels não criado — {_skl_err}")

        # QuestLog — progresso/entrega de quest, server-autoritativo (ver
        # quest_logic.py e PROBLEMAS_ARQUITETURA.md, migração de quests).
        try:
            from engine.components import QuestLog as _QL
            _ql_comp  = _QL()
            _ql_raw   = char_data.get("quests_json") or "{}"
            _ql_d     = _json.loads(_ql_raw) if isinstance(_ql_raw, str) else (_ql_raw or {})
            for _qid, _prog in (_ql_d.get("active") or {}).items():
                _ql_comp.active[_qid] = list(_prog)
            _ql_comp.completed = set(_ql_d.get("completed") or [])
            self.world.add_component(eid, _ql_comp)
        except Exception as _ql_err:
            log.warning(f"[World] aviso: QuestLog não criado — {_ql_err}")

        # CharStatsTracker — estatísticas acumuladas pro modal de estatísticas
        # (Fase E, 23/07/2026), server-autoritativo (mesma regra de SkillLevels/
        # QuestLog acima).
        try:
            from engine.components import CharStatsTracker as _CST_trk
            _cst_comp = _CST_trk()
            _cst_raw  = char_data.get("char_stats_json") or "{}"
            _cst_d    = _json.loads(_cst_raw) if isinstance(_cst_raw, str) else (_cst_raw or {})
            for _f in ("pve_damage", "pvp_damage", "mobs_killed", "players_killed",
                       "duel_wins", "duel_losses"):
                if _f in _cst_d:
                    setattr(_cst_comp, _f, int(_cst_d[_f]))
            for _f in ("arena_wins", "arena_losses"):
                for _mode, _v in (_cst_d.get(_f) or {}).items():
                    getattr(_cst_comp, _f)[_mode] = int(_v)
            self.world.add_component(eid, _cst_comp)
        except Exception as _cst_err:
            log.warning(f"[World] aviso: CharStatsTracker não criado — {_cst_err}")

        self._player_eids[session_id]    = eid
        self._player_eid_to_sid[eid]     = session_id   # reverse map

        # Multi-map: registra mapa atual do player
        saved_map_id = char_data.get("map_id", self._map_file)
        if saved_map_id not in self._map_bundles:
            saved_map_id = self._map_file
        self._player_maps[session_id] = saved_map_id
        from engine.components import MapLocation as _MLp
        self.world.add_component(eid, _MLp(saved_map_id))

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

        log.info(f"[World] spawn player eid={eid}  tile=({tx},{ty})  "
              f"name={char_data.get('name', '?')}")
        return eid

    def get_player_map(self, session_id: str) -> str:
        """Retorna o mapa atual do player (padrão: mapa principal)."""
        return self._player_maps.get(session_id, self._map_file)

    def transfer_player(self, session_id: str, player_eid: int,
                        to_map: str, target_x: int, target_y: int) -> None:
        """Teleporta player para novo mapa + tile. Atualiza _player_maps e MapLocation."""
        self._player_maps[session_id] = to_map
        from engine.components import TileMovement, Position, MapLocation as _MLtp
        ml = self.world.get_component(player_eid, _MLtp)
        if ml is not None:
            ml.map_file = to_map
        # snap_to_tile: mesmo conjunto de escritas que o bloco manual antigo
        # fazia aqui — agora centralizado (única forma correta de teleportar).
        from engine.utils import snap_to_tile as _snap_xfer
        _snap_xfer(self.world, player_eid, target_x, target_y, carry_prev=False)

    def get_player_save_data(self, session_id: str) -> dict:
        """Coleta estado ECS completo do jogador para persistência."""
        import json as _json
        from engine.components import (TileMovement, CombatStats, CharacterStats,
                                 Wallet, PlayerSkills, SkillLevels, QuestLog, CharStatsTracker)
        eid = self._player_eids.get(session_id)
        if eid is None:
            return {}
        tm   = self.world.get_component(eid, TileMovement)
        cs   = self.world.get_component(eid, CombatStats)
        char = self.world.get_component(eid, CharacterStats)
        wall = self.world.get_component(eid, Wallet)
        ps   = self.world.get_component(eid, PlayerSkills)
        skl  = self.world.get_component(eid, SkillLevels)
        ql   = self.world.get_component(eid, QuestLog)
        cst  = self.world.get_component(eid, CharStatsTracker)

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
                "spirit":           char.spirit,
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

        # SkillLevels: sempre lido do componente vivo do servidor — nunca do
        # cliente (mesma regra de gold/talentos, ver _build_save_merge em session.py).
        skill_levels = None
        if skl:
            skill_levels = {"levels": dict(skl.levels), "xp": dict(skl.xp)}

        # QuestLog: sempre lido do componente vivo do servidor — nunca do
        # cliente (mesma regra de skill_levels/gold/talentos).
        quests = None
        if ql:
            quests = {"active": {q: list(p) for q, p in ql.active.items()},
                      "completed": list(ql.completed)}

        # CharStatsTracker: sempre lido do componente vivo do servidor — nunca
        # do cliente (mesma regra de skill_levels/quests acima).
        char_stats = None
        if cst:
            char_stats = {
                "pve_damage": cst.pve_damage, "pvp_damage": cst.pvp_damage,
                "mobs_killed": cst.mobs_killed, "players_killed": cst.players_killed,
                "duel_wins": cst.duel_wins, "duel_losses": cst.duel_losses,
                "arena_wins": dict(cst.arena_wins), "arena_losses": dict(cst.arena_losses),
            }

        return {
            "tile_x":       tm.current_tile_x if tm else 10,
            "tile_y":       tm.current_tile_y if tm else 10,
            "hp":           (max(1, cs.current_hp) if cs and cs.current_hp > 0
                            else (cs.max_hp if cs else 100)),
            "mp":           100,
            "stats":        stats,
            "skills":       skills,
            "skill_levels": skill_levels,
            "quests":       quests,
            "char_stats":   char_stats,
            "map_id":       self.get_player_map(session_id),
        }

    def despawn_player(self, session_id: str) -> None:
        """Remove entidade do jogador. Chamado no logout/disconnect."""
        eid = self._player_eids.pop(session_id, None)
        if eid is None:
            return
        self._player_eid_to_sid.pop(eid, None)
        self._player_maps.pop(session_id, None)
        self._despawned_this_tick.append({"eid": eid, "tx": None, "ty": None})
        self._pending_inv.pop(session_id, None)
        self._attack_timers.pop(session_id, None)
        # Cancela spells pendentes do player (evita completions após disconnect)
        self._pending_spell_completions = [
            e for e in self._pending_spell_completions
            if e.get("player_eid") != eid
        ]
        self._spells_in_flight_queue = [
            e for e in self._spells_in_flight_queue
            if e.get("player_eid") != eid
        ]
        self._pending_knockback_landings = [
            e for e in self._pending_knockback_landings
            if e.get("target_id") != eid and e.get("collided_eid") != eid
        ]
        self.world.remove_entity(eid)
        log.info(f"[World] despawn player eid={eid}  session={session_id}")

    def move_player(self, session_id: str, tx: int, ty: int) -> bool:
        """
        Valida e aplica movimento de 1 tile para o jogador.
        Retorna True se movimento aceito, False se rejeitado.
        """
        from engine.components import TileMovement, Position

        eid = self._player_eids.get(session_id)
        if eid is None:
            return False

        tm = self.world.get_component(eid, TileMovement)
        if tm is None:
            return False

        # Ghost (espírito liberado): intangível — sem CC check, sem walkable.
        # Só valida 1 tile de distância (anti-cheat básico).
        from engine.components import GhostState as _GState
        _gst = self.world.get_component(eid, _GState)
        if _gst and _gst.is_ghost:
            if abs(tx - tm.current_tile_x) > 1 or abs(ty - tm.current_tile_y) > 1:
                return False
            from_tx, from_ty = tm.current_tile_x, tm.current_tile_y
            tm.current_tile_x = tm.target_tile_x = tx
            tm.current_tile_y = tm.target_tile_y = ty
            tm.is_moving          = True
            tm._server_move_grace = PLAYER_MOVE_GRACE_S
            _px_center = tx * TILE_SIZE + TILE_SIZE // 2
            _py_center = ty * TILE_SIZE + TILE_SIZE // 2
            # Sincroniza campos de pixel do TileMovement com o snap instantâneo —
            # sem isso TileMovementSystem sobrescreve Position.x/y com valores
            # stale da última animação real, quebrando checks de pixel-range de
            # skills (ex: golpe_poderoso "Fora de alcance" mesmo adjacente ao alvo).
            tm.target_pixel_x = _px_center
            tm.target_pixel_y = _py_center
            tm.start_pixel_x  = _px_center
            tm.start_pixel_y  = _py_center
            tm.progress        = 0.0
            tm.move_duration   = PLAYER_MOVE_GRACE_S
            pos = self.world.get_component(eid, Position)
            if pos:
                pos.prev_x, pos.prev_y = pos.x, pos.y
                pos.x = _px_center
                pos.y = _py_center
            self._moved_this_tick.append({
                "eid":     eid,
                "tx":      tx, "ty":      ty,
                "from_tx": from_tx, "from_ty": from_ty,
            })
            return True

        # CC totalmente imobilizante (sleep/stun/root/fear) impede movimento —
        # igual ao bloqueio de IA de mobs (systems.py EnemyAISystem). Fear
        # entrou aqui 21/07/2026 (não tinha NENHUM bloqueio server-side —
        # player amedrontado conseguia mandar MOVE normalmente, só o cliente
        # respeitava is_action_locked por conta própria). Disoriented/
        # polymorph continuam de FORA de propósito: o wander aleatório é
        # decidido pelo cliente (CombatStateSystem) e enviado como MOVE
        # normal — bloquear aqui quebraria esse wander. Servidor nunca confia
        # no cliente para não enviar MOVE durante CC totalmente imobilizante.
        # "taunted" (Brado Provocativo, Fase D 23/07/2026) entra nesta MESMA
        # lista: o alvo taunted não controla o próprio movimento (é
        # conduzido à força pelo TauntMovementSystem, abaixo) — um MOVE
        # client-side durante o taunt é sempre recusado, igual sleep/stun.
        from engine.components import StatusEffects as _SFXmv
        _sfx_mv = self.world.get_component(eid, _SFXmv)
        if _sfx_mv and any(_sfx_mv.has(e) for e in ("sleep", "stun", "root", "fear", "taunted")):
            return False

        # Baseline anti-cheat: primeira vez que este player move desde o spawn
        # (ou componente recém-criado) — inicializa com a posição atual, sem
        # crédito de tempo acumulado (equivale ao comportamento antigo só
        # nesse instante específico).
        if tm._last_valid_ts <= 0.0:
            tm._last_valid_tile_x = tm.current_tile_x
            tm._last_valid_tile_y = tm.current_tile_y
            tm._last_valid_ts     = time.time()

        # Validação: destino precisa estar dentro do orçamento tempo×velocidade
        # desde a última posição de confiança (pega speedhack/teleporte), e o
        # caminho reto até lá não pode cruzar tile sólido (pega clip de
        # parede) — ver comentário de MOVE_SPEED_TOLERANCE acima pro porquê de
        # não exigir mais adjacência exata tile a tile.
        _elapsed   = min(time.time() - tm._last_valid_ts, MOVE_ELAPSED_CAP_S)
        _speed_tps = (tm.speed / TILE_SIZE) if tm.speed > 0 else (PLAYER_SPEED_TILES_FALLBACK)
        import math as _math_mv
        _budget = max(1, _math_mv.ceil(_elapsed * _speed_tps * MOVE_SPEED_TOLERANCE))
        dx = abs(tx - tm._last_valid_tile_x)
        dy = abs(ty - tm._last_valid_tile_y)
        if max(dx, dy) > _budget:
            return False

        # Validação: tile de destino deve ser walkable (sólido, fora do mapa, piso errado).
        # Usa o tile_validation do bundle do mapa atual do player — evita usar o serviço
        # global que pode apontar pro último mapa do loop de bundles (_tick) em vez do
        # mapa onde o player realmente está (bug: cave_west 80×60 tiles rejeitava y=316
        # de map_1, impedindo o player de chegar ao tile de transição para a caverna).
        from engine.components import CombatState as _CState
        _cst = self.world.get_component(eid, _CState)
        _pursuit_target = (_cst.target_entity_id
                           if _cst and _cst.is_pursuing and _cst.target_entity_id != -1
                           else -1)
        _player_bnd = self._map_bundles.get(self.get_player_map(session_id))
        _tile_val = _player_bnd.tile_validation if _player_bnd else None
        if _tile_val:
            if not _tile_val.is_tile_walkable(eid, tx, ty, tm._last_valid_tile_x, tm._last_valid_tile_y,
                                              ignore_eid=_pursuit_target):
                return False
        else:
            from engine.world_systems import is_tile_walkable as _walkable
            if not _walkable(eid, tx, ty, tm._last_valid_tile_x, tm._last_valid_tile_y,
                             ignore_eid=_pursuit_target):
                return False

        # Validação: caminho reto (última posição confirmada → destino) não
        # pode cruzar tile sólido — sem isto, um destino andável mas alcançado
        # "pulando por cima" de uma parede passaria só pelos 2 checks acima
        # (orçamento de distância + tile final andável).
        if max(dx, dy) > 1:
            from engine.components import Tilemap as _TMap_mv
            _tilemap_mv = (self.world.get_component(_player_bnd.tilemap_entity, _TMap_mv)
                          if _player_bnd is not None else None)
            if _tilemap_mv is not None:
                from engine.world_systems import EnemyAISystem as _EAIS_mv
                if not _EAIS_mv._has_line_of_sight(_tilemap_mv, tm._last_valid_tile_x,
                                                   tm._last_valid_tile_y, tx, ty):
                    return False

        from_tx, from_ty = tm.current_tile_x, tm.current_tile_y
        tm.current_tile_x = tx
        tm.current_tile_y = ty
        tm.target_tile_x  = tx
        tm.target_tile_y  = ty
        tm.is_moving          = True
        tm._server_move_grace = PLAYER_MOVE_GRACE_S
        tm._last_valid_tile_x = tx
        tm._last_valid_tile_y = ty
        tm._last_valid_ts     = time.time()
        _px_center = tx * TILE_SIZE + TILE_SIZE // 2
        _py_center = ty * TILE_SIZE + TILE_SIZE // 2
        # Sincroniza campos de pixel do TileMovement com o snap instantâneo —
        # sem isso TileMovementSystem sobrescreve Position.x/y com valores
        # stale da última animação real, quebrando checks de pixel-range de
        # skills (ex: golpe_poderoso "Fora de alcance" mesmo adjacente ao alvo,
        # funcionando só depois de um Interceptar que atualizava target_pixel_x
        # corretamente via start_tile_movement). Bug: ordem do tick é
        # _systems.update (TileMovementSystem) → _process_skill_requests, então
        # os valores stale são lidos pelos handlers antes de qualquer snap de lag).
        tm.target_pixel_x = _px_center
        tm.target_pixel_y = _py_center
        tm.start_pixel_x  = _px_center
        tm.start_pixel_y  = _py_center
        tm.progress        = 0.0
        tm.move_duration   = PLAYER_MOVE_GRACE_S

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

        from engine.quest_events import fire as _qfire_tile
        _qfire_tile("reach_tile", player_eid=eid, tx=tx, ty=ty,
                    map=self._player_maps.get(session_id, self._map_file))

        return True

    def get_players_in_aoi(self, center_session: str, radius: int) -> list[dict]:
        """
        Retorna lista de EntitySpawn para todos os jogadores dentro do raio
        ao redor do jogador `center_session`. Usado no WORLD_STATE inicial.
        """
        from engine.components import TileMovement
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

    def _pvp_allowed_between(self, world, attacker_id: int, target_id: int) -> bool:
        """Resolver de contexto PvP (registrado em _load_all_maps via
        engine/faction_system.register_pvp_context) — consultado SÓ quando
        a relação de facção é "amigavel" e ambos são players. Composto:
        cada contexto novo (zona PvP) entra como mais uma consulta aqui.
        pvp_enabled é só kill-switch de emergência (ver classe).

        Arena (Fase G) NÃO passa por aqui — times são resolvidos por
        FACÇÃO (Faction("arena_time_a"/"arena_time_b") no player, ver
        server/match_processor.py), então a relação já sai "hostil" (não
        "amigavel") e can_engage() libera sem nunca consultar este
        resolver — mesma ideia do comentário antigo "times/MOBA nem
        passam por aqui"."""
        if not self.pvp_enabled:
            return False
        # Duelo aceito: par hostil somente um ao outro.
        if frozenset((attacker_id, target_id)) in self._duel_pairs:
            return True
        # Zona PvP (Fase F): ambos dentro da mesma zona, exceto mesmo grupo
        # (server/pvp_zone_processor.py).
        return self._pvp_zone_allows(attacker_id, target_id)

    def _lethal_interceptor_composite(self, world, killer_eid: int, target_id: int) -> bool:
        """Registrado em _load_all_maps via engine/core_systems.
        register_lethal_interceptor — o slot é ÚNICO (não é uma lista
        componível como _pvp_allowed_between), então a composição
        duelo-ou-arena mora nesta função só. True = intercepta (alvo fica
        em 1 HP em vez de morrer)."""
        if self._duel_lethal_interceptor(world, killer_eid, target_id):
            return True
        return self._arena_lethal_interceptor(world, killer_eid, target_id)

    def _damage_tracker_composite(self, killer_eid: int, target_id: int, dmg: int) -> None:
        """Registrado em _load_all_maps via engine/core_systems.
        register_damage_tracker — o slot é ÚNICO (mesmo padrão de
        _lethal_interceptor_composite acima): placar de arena
        (_track_arena_damage, no-op fora de partida) + estatísticas
        acumuladas do personagem pro modal de estatísticas (Fase E,
        23/07/2026)."""
        self._track_arena_damage(killer_eid, target_id, dmg)
        from engine.components import PlayerControlled as _PC_dmgtrk
        from engine.utils import incr_char_stat
        field = ("pvp_damage" if self.world.get_component(target_id, _PC_dmgtrk) is not None
                 else "pve_damage")
        incr_char_stat(self.world, killer_eid, field, dmg)

    def set_player_target(self, session_id: str, target_eid: int) -> None:
        """Define o alvo de combate do jogador. target_eid=-1 para parar.

        Recusa alvos de facção "amigavel" (Sistema de Facções) — nem
        entra em combate: sem isso, `_process_player_attacks` ficava
        tentando atacar em loop (sempre bloqueado por `apply_damage_core`,
        dano sempre 0) e o alvo era arrastado pro estado de "combate" só
        por ter um `target_entity_id` setado contra ele (aggro visual
        incorreto). Pedido do usuário 15/07/2026, depois de tentar atacar
        o "Guarda Real" de teste."""
        from engine.components import CombatState
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        cs = self.world.get_component(eid, CombatState)
        if cs:
            if target_eid != -1:
                from engine.faction_system import can_engage
                if not can_engage(self.world, eid, target_eid):
                    cs.target_entity_id = -1
                    return
            cs.target_entity_id = target_eid

    def get_entity_spawn_data(self, eid: int) -> dict | None:
        """Retorna payload completo de ENTITY_SPAWN para qualquer entidade (mob ou player)."""
        from engine.components import TileMovement
        if eid not in self._mob_eids:
            return None   # jogadores são gerenciados separadamente
        tm = self.world.get_component(eid, TileMovement)
        if not tm:
            return None
        return self._build_mob_spawn_payload(eid, tm)

    def _build_mob_spawn_payload(self, eid: int, tm) -> dict:
        from engine.components import (CombatStats, AIControlled, Renderable, SpawnZoneOwner,
                                SpawnZone, EntityIdentity, TrainingDummy as _TDpay, Faction as _FacPay,
                                NPC as _NPCpay, Merchant as _Merchpay, Blacksmith as _Blackpay,
                                Trainer as _Trainpay, QuestGiver as _QGpay)
        cs    = self.world.get_component(eid, CombatStats)
        ai    = self.world.get_component(eid, AIControlled)
        ren   = self.world.get_component(eid, Renderable)
        szo   = self.world.get_component(eid, SpawnZoneOwner)
        ident = self.world.get_component(eid, EntityIdentity)
        fac   = self.world.get_component(eid, _FacPay)
        race="Humanoide"; entity_class="Warrior"; tier="normal"; is_ranged=False; zone=None
        if szo:
            zone = self.world.get_component(szo.zone_entity_id, SpawnZone)
            if zone:
                race         = zone.race
                entity_class = zone.entity_class or entity_class
                tier         = zone.enemy_tier
                is_ranged    = (zone.enemy_type == "ranged")
        elif ident:
            # Sem SpawnZone (ex: boneco de treino, Guarda Real, NPC de
            # serviço — entidades fixas, não nascem de zona) — usa a
            # identidade própria da entidade em vez do fallback genérico
            # acima. Sem isso, qualquer mob "solto" virava sempre
            # "Humanoide"/"Warrior" pro cliente, ignorando seu EntityIdentity
            # real. `mob_key` (chave EXATA de MOB_TABLE) tem prioridade sobre
            # `race` (categoria ampla, ex: "Humanoide", nunca bate uma
            # entrada de MOB_TABLE) — sem isso o cliente nunca reconstruía o
            # mob_def de verdade (sons/entity_class corretos) pra qualquer
            # entidade nesse ramo (bug real 21/07/2026: "Arqueiro (NPC)"
            # virava melee/mudo no cliente apesar de ranged de verdade no
            # servidor).
            race         = ident.mob_key or ident.race or race
            entity_class = ident.entity_class or entity_class
            tier         = ident.tier or tier
        # is_ranged: sempre prioriza o AIControlled DE VERDADE da própria
        # entidade (fonte única, nunca muda depois da criação) em vez de
        # derivar de SpawnZone/EntityIdentity — o ramo `elif ident` acima
        # nunca setava is_ranged (ficava preso no default False do topo da
        # função), então qualquer entidade ranged sem SpawnZone (Guarda Real
        # não expõe esse bug por ser melee; "Arqueiro (NPC)"/"Mago (NPC)"
        # são as primeiras ranged sem zona) mandava is_ranged=False pro
        # cliente mesmo lutando à distância de verdade no servidor.
        if ai is not None:
            is_ranged = ai.is_ranged
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
            # Facção do mob (content/faction_data.py) — cliente resolve a
            # disposição (hostil/neutro/amigavel) contra PLAYER_FACTION pra
            # colorir a barra de HP do nameplate (vermelho/amarelo claro/
            # verde). Pedido do usuário 15/07/2026. Default espelha o
            # mesmo default de Faction/create_enemy (retrocompatível: mob
            # sem componente — não deveria existir, mas nunca quebra).
            "faction":      fac.faction_id if fac else "monstros_hostis",
        }
        # Nome próprio (ex: "Boneco de treino") — sem isso o cliente deriva o
        # nome exibido a partir da raça (create_enemy: mob_display_name = race),
        # então qualquer entidade sem zona mostrava raça como nome também.
        if ident and ident.name:
            payload["name"] = ident.name
        # is_dummy: sinaliza ao cliente pra anexar TrainingDummy no proxy local —
        # sem isso, skills usadas no boneco nunca contavam pra objetivos de quest
        # com params={"on_dummy": True} (ver quest_system.py), porque o cliente
        # não tinha NENHUMA forma de saber que aquele mob remoto era um boneco.
        if self.world.get_component(eid, _TDpay) is not None:
            payload["is_dummy"] = True
        # NPC de serviço (mercador/ferreiro/treinador/dador-de-missão, Fase 1
        # de combate genérico, 21/07/2026) — campos condicionais, um por
        # componente de capacidade presente na entidade. Mob normal/Guarda
        # Real nunca têm esses componentes, então o payload deles fica
        # idêntico a antes. Cliente usa esses campos pra anexar o MESMO
        # componente de capacidade na entidade remota reconstruída
        # (client/remote_entity_handlers.py::_spawn_remote_mob) — Trainer e
        # QuestGiver podem coexistir na MESMA entidade (treinador com
        # quest), por isso são flags independentes, não um "kind" único.
        _npc = self.world.get_component(eid, _NPCpay)
        if _npc is not None:
            payload["profession"] = _npc.profession
        _merch = self.world.get_component(eid, _Merchpay)
        if _merch is not None:
            payload["shop_id"] = _merch.shop_id
        if self.world.get_component(eid, _Blackpay) is not None:
            payload["is_blacksmith"] = True
        _train = self.world.get_component(eid, _Trainpay)
        if _train is not None:
            payload["class_id"] = _train.class_id
        _qg = self.world.get_component(eid, _QGpay)
        if _qg is not None:
            payload["quest_ids"]    = list(_qg.quest_ids)
            payload["turn_in_ids"]  = list(_qg.turn_in_ids)
        # Se o mob já está em movimento no momento do spawn, inclui o destino.
        # O cliente inicia a animação imediatamente em vez de esperar o próximo evento.
        if tm.is_moving and (tm.target_tile_x != tm.current_tile_x or
                             tm.target_tile_y != tm.current_tile_y):
            payload["moving_to_tx"] = tm.target_tile_x
            payload["moving_to_ty"] = tm.target_tile_y
        return payload

    def get_mobs_in_aoi(self, center_tx: int, center_ty: int, radius: int,
                        map_file: str = "") -> list[dict]:
        """Retorna lista de mobs no AOI — para WORLD_STATE inicial."""
        from engine.components import TileMovement, MapLocation as _ML_gmai
        result = []
        for eid in self._mob_eids:
            if map_file:
                _ml = self.world.get_component(eid, _ML_gmai)
                if _ml and _ml.map_file != map_file:
                    continue
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
        from engine.components import CombatStats
        eid = self._player_eids.get(session_id)
        if eid is None:
            return (0, 0)
        cs = self.world.get_component(eid, CombatStats)
        return (cs.current_hp, cs.max_hp) if cs else (0, 0)

    def get_tile_pos(self, session_id: str) -> tuple[int, int]:
        from engine.components import TileMovement
        eid = self._player_eids.get(session_id)
        if eid is None:
            return (0, 0)
        tm = self.world.get_component(eid, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else (0, 0)

    def sync_player_resources(self, session_id: str, rage: int, mana: int) -> None:
        """NÃO é mais chamada pelo fluxo real (server/session.py) — só utilitário
        de setup pra testes/diagnósticos (ver tests/diag_skills*.py).

        Chegou a ser chamada em todo CAST_SKILL, adotando o rage/mana que o
        CLIENTE mandasse como verdade ("cliente é fonte de verdade... igual ao
        offline"). Bug real: o campo-espelho CombatStats.mana do cliente (default
        0) ainda não tinha sincronizado com CharacterStats.mana (cheio) no
        primeiro cast pós-login — o cliente mandava mana=0 e isso zerava a mana
        REAL do servidor, fazendo toda skill de Mago falhar por "Mana
        insuficiente" logo após logar. O servidor já rastreia rage/mana 100% por
        conta própria (ganho em combat_processor.py, custo deduzido nos próprios
        handlers de skill + _process_spell_cast_completions) — nunca precisou
        confiar no cliente pra isso. Ver arquitetura/PROBLEMAS_ARQUITETURA.md.
        """
        from engine.components import CharacterStats, CombatStats
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        char = self.world.get_component(eid, CharacterStats)
        cs   = self.world.get_component(eid, CombatStats)
        if char and rage >= 0:
            char.rage = rage
        if char and mana >= 0:
            char.mana = mana   # CharacterStats.mana — lido por _check_mana
        if cs and mana >= 0:
            cs.mana = mana     # CombatStats.mana — lido por _use_skill_visual_only

    def get_damage_log(self, mob_eid: int) -> dict[int, int]:
        """Retorna e remove o registro de dano acumulado para o mob. Chamado pelo death handler."""
        return self._mob_damage_log.pop(mob_eid, {})

    def _log_mob_damage_hit(self, attacker_eid: int, target_id: int, dmg: int) -> None:
        """Callback ÚNICO de log de dano por mob — injetado em CombatSystem
        (deal_damage, cobre auto-attack melee + TODA skill física) e em
        _apply_final_damage (spell_completion_processor.py, cobre auto-attack
        ranged + skills mágicas/ranged). `_mob_damage_log[mob][player] = soma`,
        em ordem de inserção — `next(iter(...))` no death handler assume o
        PRIMEIRO player a aparecer = quem atacou primeiro (dono do loot/quest
        kill). Antes desta centralização, só auto-attack melee/ranged loga(va)
        manualmente em 2 pontos (combat_processor.py, world_server.py —
        REMOVIDOS nesta mudança, teriam dano contado em dobro agora) e NENHUMA
        skill logava — um player que só usa skill (ex: mago) nunca aparecia
        no log, e quem chegasse depois com um auto-attack "roubava" o loot/XP
        mesmo tendo feito uma fração do trabalho. Ver PROBLEMAS_ARQUITETURA.md.
        """
        if dmg <= 0:
            return
        log = self._mob_damage_log.setdefault(target_id, {})
        log[attacker_eid] = log.get(attacker_eid, 0) + dmg

        # Dano mob-vs-mob/NPC (Sistema de Facções, Fase 5): broadcast pro
        # cliente via o MESMO canal de "mob atacou" (COMBAT_RESULT dentro do
        # AOI_UPDATE). Sem isto, o combate NPC-vs-NPC acontecia inteiro
        # server-side de forma INVISÍVEL — cliente via os dois parados com
        # HP cheio "sem desferir dano", e ao atacar o alvo já quase morto no
        # servidor, ele "morria instantaneamente" (bug real relatado pelo
        # usuário 15/07/2026 testando Guarda Real vs Bandido). O caminho
        # mob→PLAYER não passa por aqui de verdade (é detectado por snapshot
        # de HP de players em combat_processor.py) — só mob→mob entra.
        # Outcome sempre "hit": este hook roda depois da escrita de HP em
        # apply_damage_core e não conhece o outcome real (crit/block) — bom
        # o suficiente pra sincronizar HP + FLT; misses nem chegam aqui.
        if attacker_eid in self._mob_eids and target_id in self._mob_eids:
            from engine.components import CombatStats as _CS_mvm
            _cs_mvm = self.world.get_component(target_id, _CS_mvm)
            self._pending_mob_attacks.append({
                "attacker": attacker_eid,
                "target":   target_id,
                "damage":   dmg,
                "outcome":  "hit",
                "hp_after": max(0, _cs_mvm.current_hp) if _cs_mvm else 0,
                "source":   "auto",
            })

    def get_session_id_for_player(self, player_eid: int) -> str | None:
        """Retorna session_id do player dado seu entity_id — O(1) via reverse map."""
        return self._player_eid_to_sid.get(player_eid)

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

    def consume_skill_effects(self) -> list[dict]:
        """Retorna e limpa eventos de apresentação (som/VFX) do tick atual."""
        result = list(self._skill_effects_this_tick)
        self._skill_effects_this_tick.clear()
        return result

    # ── Corpse / Loot API ────────────────────────────────────────────────────
    # consume_loot_notifications, consume_expired_corpses → LootProcessorMixin
    # _process_skill_requests → SkillProcessorMixin

    def _apply_equipment_modifiers(self, eid: int) -> None:
        """Deriva os modificadores de equipamento (source="equipment") em
        CombatStats a partir dos itens REAIS equipados (Equipment, já validado
        contra catálogo — ver _reconstruct_item, Tier B).

        Substitui o antigo PLAYER_STAT_SYNC/_apply_stat_overrides — antes o
        servidor confiava direto no valor de attack_power/crit_rating/armor/
        spell_power/etc. que o CLIENTE calculava e mandava, sem validar contra
        nada (ver arquitetura/PROBLEMAS_ARQUITETURA.md). Agora o servidor
        deriva esses valores ele mesmo a partir dos modifiers reais de cada
        item, exatamente como já faz para talentos (apply_talent_effects_to_player).

        Chamar sempre que o Equipment mudar (spawn, EQUIP_SYNC). Modifiers de
        talento/buff (source != "equipment") nunca são tocados aqui."""
        from engine.components import CombatStats as _CSEq, Equipment as _EqEq, Modifier as _ModEq
        cs    = self.world.get_component(eid, _CSEq)
        equip = self.world.get_component(eid, _EqEq)
        if not cs or not equip:
            return
        cs.modifiers = [m for m in cs.modifiers if m.source != "equipment"]
        for item in equip.slots.values():
            if not item:
                continue
            for mod in item.modifiers:
                cs.modifiers.append(_ModEq(mod.attribute, mod.value, mod.type, source="equipment"))
        cs._recalculate_effective_stats()

    def process_shop_buy(self, session_id: str, shop_id: str,
                         item_name: str, quantity: int,
                         last_inventory: list | None = None,
                         current_gold: int | None = None) -> dict:
        """Processa compra em loja — autoritativo no servidor.

        Valida: shop_id existe no catálogo, item está no estoque,
        gold suficiente, espaço no inventário.
        Retorna dict {success, reason, item_data, new_gold}.
        """
        from engine.components import Wallet, Inventory

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

        # 2. Valida gold — sincroniza com o valor atual do cliente antes de processar.
        # O servidor pode ter gold desatualizado (ex: moedas de loot adicionadas localmente).
        wallet = self.world.get_component(eid, Wallet)
        if not wallet:
            return {"success": False, "reason": "no_wallet"}
        if current_gold is not None and current_gold >= 0:
            wallet.gold = int(current_gold)
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

        # 6. Atualiza o Inventory em memória do servidor — sem isso o item só
        #    aparece nessa cópia após o próximo SAVE_STATE/INV_SYNC, e handlers
        #    de skill (Recarregar) validam munição contra um Inventory
        #    desatualizado, recusando "Não há flechas disponíveis" até relogar.
        inv = self.world.get_component(eid, Inventory)
        factory = entry.get("factory")
        if inv is not None and callable(factory):
            qty       = max(1, quantity)
            preview   = factory()
            max_stack = getattr(preview, "max_stack", 1)
            remaining = qty
            if max_stack > 1:
                for existing in inv.items:
                    if existing is None or remaining <= 0:
                        continue
                    if existing.name == item_name and existing.stack < existing.max_stack:
                        can_add = min(remaining, existing.max_stack - existing.stack)
                        existing.stack += can_add
                        remaining -= can_add
            while remaining > 0:
                if len(inv.items) >= inv.max_slots:
                    break
                new_item = factory()
                take = min(remaining, getattr(new_item, "max_stack", 1))
                new_item.stack = take
                inv.items.append(new_item)
                remaining -= take

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
                          client_value: int, stack_sold: int = 1,
                          current_gold: int | None = None) -> dict:
        """Processa venda ao mercador — autoritativo no servidor.

        Calcula sell_price a partir do catálogo (loot_tables → merchant_data).
        Se o item não estiver no catálogo usa client_value com cap de 500g para
        evitar exploits.  Gold adicionado server-side na Wallet ECS.
        Retorna {success, item_name, sell_price, new_gold}.
        """
        from engine.components import Wallet
        eid = self._player_eids.get(session_id)
        if eid is None:
            return {"success": False, "reason": "not_logged_in"}
        wallet = self.world.get_component(eid, Wallet)
        if not wallet:
            return {"success": False, "reason": "no_wallet"}

        # Sincroniza gold com o cliente antes de processar (loot coins podem não ter chegado)
        if current_gold is not None and current_gold >= 0:
            wallet.gold = int(current_gold)

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
        """Serializa um item ECS para o dict que o cliente espera no BUY_RESULT
        (e, via get_player_equipment_data, pro cache de save de EQUIP_SYNC)."""
        data = {
            "name":      obj.name,
            "item_type": getattr(obj, "item_type", ""),
            "slot":      getattr(obj, "slot", ""),
            "rarity":    getattr(obj, "rarity", "common"),
            "value":     getattr(obj, "value", 0),
            "consumable": getattr(obj, "consumable", None),
            "max_stack":  getattr(obj, "max_stack", 1),
            "stack":      getattr(obj, "stack", 1),
            "modifiers": [{"attribute": m.attribute, "value": m.value, "type": m.type}
                          for m in getattr(obj, "modifiers", [])],
        }
        # Aljava: contagem de flechas — faltava aqui (bug real, 11/07/2026:
        # aljava sempre salvava cheia no relogin, mesmo com flechas gastas
        # em combate, porque este dict — usado por get_player_equipment_data
        # pro cache de save — nunca incluía o campo).
        if getattr(obj, "item_type", "") == "quiver":
            data["arrow_count"] = getattr(obj, "arrow_count", 0)
            data["max_arrows"]  = getattr(obj, "max_arrows",  0)
        for f in ("attack_power", "armor", "spell_power", "stamina",
                  "two_handed", "attack_speed", "damage_min", "damage_max",
                  "subtype", "cast_range",
                  "item_level", "level_requirement", "description"):
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
            from content.loot_tables import _T as _LT
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
            from content.merchant_data import SHOPS
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

        # crafting_data.RECIPES → _item_value_cache — SEM isso, todo item
        # FORJADO (Espada Afiada etc.) ficava fora do cache: process_shop_sell
        # caía no branch "desconhecido → client_value com teto de 500" (valor
        # de venda errado/manipulável) e sanitize_inventory_payload (item A4,
        # seção 11) descartaria item craftado legítimo como se fosse forjado
        # por cliente malicioso. _reconstruct_item sempre cobriu os 3
        # catálogos — o cache é que tinha ficado só com 2.
        try:
            from content.crafting_data import RECIPES
            for _rec in RECIPES.values():
                _f = _rec.get("result_factory")
                if callable(_f):
                    try:
                        _o = _f()
                        _n = getattr(_o, "name", None)
                        if _n and _n not in self._item_value_cache:
                            self._item_value_cache[_n] = int(getattr(_o, "value", 0))
                    except Exception:
                        pass
        except ImportError:
            pass

        # quests_data.QUEST_ITEMS — 4º catálogo autoritativo, faltando desde
        # sempre (item A4/Tier B só cobriam loot/loja/forja). Bug real
        # relatado pelo usuário 19/07/2026: progresso de "colete N itens"
        # nunca contava (mesmo com o item de verdade na mochila) porque
        # sanitize_inventory_payload descartava SILENCIOSAMENTE qualquer
        # item de quest — _lookup_item_value nunca achava "Presa de Lobo"
        # etc. (só existem em QUEST_ITEMS, nunca em loot_tables._T). O
        # Inventory AO VIVO do servidor nunca chegava a ter o item, então
        # sync_collect_progress nunca via nada pra contar.
        try:
            from content.quests_data import QUEST_ITEMS
            for _f in QUEST_ITEMS.values():
                if callable(_f):
                    try:
                        _o = _f()
                        _n = getattr(_o, "name", None)
                        if _n and _n not in self._item_value_cache:
                            self._item_value_cache[_n] = int(getattr(_o, "value", 0))
                    except Exception:
                        pass
        except ImportError:
            pass
        log.info(f"[WorldServer] caches: {len(self._item_value_cache)} itens, "
              f"{sum(len(v) for v in self._shop_item_cache.values())} entradas de loja")

    def _lookup_item_value(self, item_name: str) -> int | None:
        """Retorna o valor base de um item a partir do cache pré-construído.
        None se não encontrado (foi removido do catálogo após startup)."""
        return self._item_value_cache.get(item_name)

    def _reconstruct_item(self, d: dict):
        """Reconstrói um Item a partir de dict serializado (inventory_json / INV_SYNC).

        Tenta casar pelo nome em QUALQUER catálogo autoritativo do servidor —
        loot (`loot_tables._T`), loja (`merchant_data.SHOPS`), forja
        (`crafting_data.RECIPES[*]["result_factory"]`) e itens de quest
        (`quests_data.QUEST_ITEMS`) — e usa os stats REAIS do catálogo,
        ignorando `modifiers`/`attack_power`/etc. que o cliente mandou no
        payload. Só os campos puramente de bookkeeping (contagem de
        flecha/stack) vêm do cliente, nunca dano/armadura/atributo.

        Sem isso, qualquer item que NÃO esteja em loot_tables._T (ou seja,
        todo item comprado em loja ou forjado — confirmado: nenhum dos dois
        catálogos é um subconjunto de _T) caía no fallback abaixo, que monta
        o Item DIRETO dos campos do payload — um cliente malicioso podia
        equipar/inventariar um item fake com `modifiers: [{"attribute":
        "attack_power", "value": 99999}]` e o servidor aplicava sem checar
        nada (ver arquitetura/PROBLEMAS_ARQUITETURA.md, vulnerabilidade de
        forja de stats de equipamento).

        Necessário pra que handlers de skill validem o Inventory real do
        jogador no servidor (ex: Recarregar verificando munição "ammo" na bag).
        """
        if not d or not d.get("name"):
            return None
        from engine.components import Item as _Item
        name = d.get("name", "")

        def _apply_client_bookkeeping(candidate):
            """Campos que o cliente PODE reportar com segurança — nunca dano/
            stat, só estado descartável (contagem de flecha equipada, stack
            do slot), e sempre CLAMPADO contra a capacidade do CATÁLOGO
            (candidate.max_arrows/max_stack, vindos da factory). Antes o
            cliente também sobrescrevia max_arrows/max_stack — deixava
            forjar capacidade (aljava de 999999 flechas, stack ilimitado)
            mesmo com o item base validado. Nenhuma mecânica legítima muda
            capacidade em runtime (única mutação real é o fallback legado
            max_arrows==0→100 em _server_recarregar). Ver
            PROBLEMAS_ARQUITETURA.md seção 11 item A4."""
            if "arrow_count" in d:
                _cap_ar = candidate.max_arrows if candidate.max_arrows > 0 else 100
                candidate.arrow_count = max(0, min(int(d["arrow_count"]), _cap_ar))
            if "subtype" in d:
                candidate.subtype = str(d["subtype"])[:40]
            if "stack" in d:
                _cap_st = max(1, candidate.max_stack)
                candidate.stack = max(1, min(int(d["stack"]), _cap_st))
            return candidate

        # 1) Catálogo de loot
        from content.loot_tables import _T
        for _key, factory in _T.items():
            try:
                candidate = factory()
            except Exception:
                continue
            if getattr(candidate, "name", "") == name:
                return _apply_client_bookkeeping(candidate)

        # 2) Catálogo de loja — todo item comprado de um merchant
        from content.merchant_data import SHOPS
        for _shop in SHOPS.values():
            for _entry in _shop.get("stock", []):
                try:
                    candidate = _entry["factory"]()
                except Exception:
                    continue
                if getattr(candidate, "name", "") == name:
                    return _apply_client_bookkeeping(candidate)

        # 3) Catálogo de forja — resultado de receita (Espada Afiada, etc.)
        from content.crafting_data import RECIPES
        for _recipe in RECIPES.values():
            _factory = _recipe.get("result_factory")
            if not _factory:
                continue
            try:
                candidate = _factory()
            except Exception:
                continue
            if getattr(candidate, "name", "") == name:
                return _apply_client_bookkeeping(candidate)

        # 4) Catálogo de itens de quest (Presa de Lobo, Pelo de Urso, etc.) —
        # só existem em quests_data.QUEST_ITEMS, nunca em loot_tables._T
        # (drop condicional a quest ativa, ver engine/quest_logic.py). Faltava
        # aqui — bug real 19/07/2026, ver docstring de sanitize_inventory_payload.
        from content.quests_data import QUEST_ITEMS
        for _factory in QUEST_ITEMS.values():
            try:
                candidate = _factory()
            except Exception:
                continue
            if getattr(candidate, "name", "") == name:
                return _apply_client_bookkeeping(candidate)

        # Fallback: nome não bate com NENHUM catálogo conhecido (loot/loja/
        # forja/quest) — todo item de gameplay real vem de um desses, então isso
        # só acontece pra nome inválido/inventado. Por segurança, NUNCA
        # aplica modifiers/dano/atributo vindos do payload aqui — só os
        # campos puramente descritivos (nome, tipo, raridade, valor de
        # venda, consumível) e os mesmos campos de bookkeeping seguros de
        # cima. O item existe (não quebra render/inventário), mas é
        # mecanicamente inerte — não dá NENHUM bônus de combate, mesmo que o
        # payload peça (ver arquitetura/PROBLEMAS_ARQUITETURA.md,
        # vulnerabilidade de forja de stats de equipamento).
        item = _Item(
            name        = d["name"],
            item_type   = d.get("item_type", ""),
            slot        = d.get("slot", ""),
            rarity      = d.get("rarity", "common"),
            value       = int(d.get("value", 0)),
            consumable  = d.get("consumable"),
            max_stack   = int(d.get("max_stack", 1)),
            item_level        = int(d.get("item_level", 1)),
            level_requirement = int(d.get("level_requirement", 1)),
            description       = d.get("description", ""),
        )
        return _apply_client_bookkeeping(item)

    def load_player_inventory(self, eid: int, inventory_json) -> None:
        """Popula Inventory do jogador a partir do inventory_json salvo (spawn_player)."""
        from engine.components import Inventory
        import json as _json
        inv = self.world.get_component(eid, Inventory)
        if inv is None:
            return
        try:
            inv_list = _json.loads(inventory_json) if isinstance(inventory_json, str) else (inventory_json or [])
        except Exception:
            inv_list = []
        if not isinstance(inv_list, list):
            return
        inv.items.clear()
        for item_d in inv_list:
            item = self._reconstruct_item(item_d)
            if item:
                inv.items.append(item)

    def sanitize_inventory_payload(self, inventory_list) -> "list | None":
        """Round-trip de CADA item do payload pelo catálogo autoritativo
        (_reconstruct_item → _item_data_from_obj) antes de qualquer uso em
        persistência — o que sobrevive é o item do CATÁLOGO (stats/
        modifiers/valor reais) + bookkeeping clampado (stack/arrow_count),
        nunca o dict cru do cliente.

        Fecha o vetor de save-forging do item A4 (PROBLEMAS_ARQUITETURA.md
        seção 11): antes, SAVE_STATE/INV_SYNC cacheavam o payload cru em
        session.last_client_payload e _build_save_merge persistia
        `client_p["inventory"]` direto no banco — um cliente modificado
        gravava item com modifiers/valor inventados que voltavam como itens
        reais no próximo login (o load valida via _reconstruct_item, mas o
        fallback de nome desconhecido ainda preservava value/consumable
        arbitrários). Item de nome desconhecido é DESCARTADO aqui (todo
        item legítimo vem de loot/loja/forja/quest — os 4 catálogos
        cobertos, ver _reconstruct_item e _build_item_caches).

        None se o payload nem é uma lista (caller trata como "sem dado")."""
        if not isinstance(inventory_list, list):
            return None
        sanitized = []
        for item_d in inventory_list:
            if not isinstance(item_d, dict):
                continue
            obj = self._reconstruct_item(item_d)
            # Descarta itens fora de QUALQUER catálogo: _reconstruct_item
            # devolve um fallback "inerte" pra eles (compat de render), mas
            # persistir isso eterniza lixo forjado no banco. Detecção: o
            # fallback é o único caminho que preserva o item_type cru do
            # payload sem match de nome — re-checa contra o catálogo.
            if obj is None or self._lookup_item_value(getattr(obj, "name", "")) is None:
                continue
            data = self._item_data_from_obj(obj)
            if data:
                sanitized.append(data)
        return sanitized

    def sync_player_inventory(self, session_id: str, inventory_list: list) -> None:
        """Reconstrói Inventory do jogador a partir do payload INV_SYNC.

        O cliente é a fonte do estado de bag (loot/compra/venda já validados pelo
        servidor antes de alterá-la), mas o servidor precisa de uma cópia local
        para que handlers de skill (Recarregar, Tiro Múltiplo, etc.) validem
        munição contra o Inventory real — sem isso o componente fica vazio/None
        e a validação sempre falha ou é pulada.
        """
        from engine.components import Inventory
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        inv = self.world.get_component(eid, Inventory)
        if inv is None or not isinstance(inventory_list, list):
            return
        inv.items.clear()
        for item_d in inventory_list:
            item = self._reconstruct_item(item_d)
            if item:
                inv.items.append(item)

    def sync_player_skills(self, session_id: str, skills_data: dict) -> None:
        """Atualiza PlayerSkills.learned_skill_ids no ECS do servidor a partir
        dos dados enviados pelo cliente no SAVE_STATE.

        O aprendizado de skills acontece client-side (trainer_system._do_learn)
        e o servidor precisa de uma cópia viva para que sync_learn_skill_progress
        (em _process_quest_events) detecte skills recém-aprendidas e avance
        objetivos de quest do tipo learn_skill — sem isso o componente nunca é
        atualizado apos o login e o objetivo nunca conta (bug real: quest
        Prova de Valor / Iniciacao Arcana nao avancava ao aprender Golpe
        Poderoso / Bola de Fogo via treinador no modo online).
        """
        from engine.components import PlayerSkills as _PSSync
        learned = skills_data.get("learned")
        if not isinstance(learned, list):
            return
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        ps = self.world.get_component(eid, _PSSync)
        if ps is None:
            return
        ps.learned_skill_ids = set(learned)

    def apply_consumable(self, session_id: str, payload: dict) -> None:
        """Aplica efeitos de consumível no ECS do servidor (autoritativo).

        Suporta heal_instant, mana_restore, HoT de HP (ActiveRegen) e HoT de mana
        (ActiveManaRegen). Estrutura extensível via campo 'buffs'.

        SEMPRE responde ao CONSUMABLE_USE (aceito OU rejeitado) via
        queue_stats_update com `item_name` — o cliente NÃO consome o item
        localmente até essa confirmação chegar (ver ConsumableSystem/
        network_handlers.py). Antes, um bloqueio aqui (ex: HP já cheio no
        SERVIDOR, mesmo que o cliente ache que não está — drift natural
        entre os dois lados) retornava em silêncio: o cliente já tinha
        curado localmente (predição) e consumido o item ANTES de saber que
        o servidor não fez nada — item perdido, sem cura real, sem aviso
        nenhum (bug real reportado por testers: "consumível às vezes não
        regenera"). Ver PROBLEMAS_ARQUITETURA.md.
        """
        from engine.components import CombatStats, CombatState, ActiveRegen, CharacterStats, ActiveManaRegen
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        cs     = self.world.get_component(eid, CombatStats)
        cstate = self.world.get_component(eid, CombatState)
        char   = self.world.get_component(eid, CharacterStats)
        item_name = payload.get("item_name", "")

        def _reject(reason: str) -> None:
            if item_name:
                self.queue_stats_update({
                    "player_eid":          eid,
                    "item_name":           item_name,
                    "consumable_rejected": True,
                    "reason":              reason,
                })

        if not cs:
            _reject("no_stats")
            return

        if payload.get("ooc_only", False) and cstate and cstate.in_combat:
            _reject("in_combat")
            return

        _has_hp   = bool(payload.get("heal_instant", 0) or (isinstance(payload.get("hot"), dict)))
        _has_mana = bool(payload.get("mana_restore", 0) or (isinstance(payload.get("mana_hot"), dict)))

        # Bloqueia apenas se o recurso relevante estiver cheio
        if _has_hp and not _has_mana and cs.current_hp >= cs.max_hp:
            _reject("hp_full")
            return
        if _has_mana and not _has_hp and char and char.max_mana > 0 and char.mana >= char.max_mana:
            _reject("mana_full")
            return

        # Aceito — confirma ANTES de aplicar os efeitos: só agora o cliente
        # pode remover o item do Inventory local com segurança (o servidor
        # já garantiu que vai aplicar algo de verdade).
        if item_name:
            self.queue_stats_update({
                "player_eid":    eid,
                "item_name":     item_name,
                "consumable_ok": True,
            })

        # Evento de quest "use_consumable" — uso aceito (passou pelos blocks
        # acima). Server-autoritativo — ver quest_logic.py/PROBLEMAS_ARQUITETURA.md.
        if item_name:
            from engine.quest_events import fire as _qfire_cons
            _qfire_cons("use_consumable", player_eid=eid, item_name=item_name)

        # 1. Cura instantânea de HP
        heal_instant = int(payload.get("heal_instant", 0))
        if heal_instant > 0:
            healed = min(heal_instant, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + heal_instant)
            if healed > 0:
                self.queue_stats_update({
                    "player_eid":  eid,
                    "hp":          cs.current_hp,
                    "hp_max":      cs.max_hp,
                    "heal_amount": healed,
                    "heal_sid":    "consumable_instant",
                })

        # 2. Restauração instantânea de mana
        mana_restore = int(payload.get("mana_restore", 0))
        if mana_restore > 0 and char and char.max_mana > 0:
            restored = min(mana_restore, char.max_mana - char.mana)
            char.mana = min(char.max_mana, char.mana + mana_restore)
            if cs:
                cs.mana = char.mana
            if restored > 0:
                self.queue_stats_update({
                    "player_eid":   eid,
                    "mana":         char.mana,
                    "mana_amount":  restored,
                })

        # 3. HoT de HP (ActiveRegen)
        hot = payload.get("hot")
        if isinstance(hot, dict):
            heal_per_tick = int(hot.get("heal_per_tick", 0))
            interval      = float(hot.get("interval", 2.0))
            ticks         = int(hot.get("ticks", 0))
            if heal_per_tick > 0 and ticks > 0:
                try:
                    self.world.remove_component(eid, ActiveRegen)
                except Exception:
                    pass
                self.world.add_component(eid, ActiveRegen(
                    heal_per_tick=heal_per_tick,
                    interval=interval,
                    ticks_total=ticks,
                ))

        # 4. HoT de mana (ActiveManaRegen)
        mana_hot = payload.get("mana_hot")
        if isinstance(mana_hot, dict) and char and char.max_mana > 0:
            mana_per_tick = int(mana_hot.get("mana_per_tick", 0))
            interval      = float(mana_hot.get("interval", 5.0))
            ticks         = int(mana_hot.get("ticks", 0))
            if mana_per_tick > 0 and ticks > 0:
                try:
                    self.world.remove_component(eid, ActiveManaRegen)
                except Exception:
                    pass
                self.world.add_component(eid, ActiveManaRegen(
                    mana_per_tick=mana_per_tick,
                    interval=interval,
                    ticks_total=ticks,
                ))

    def get_entity_map(self, eid: int) -> "str | None":
        """Mapa (map_file) de qualquer entidade via MapLocation — fonte única
        entity→mapa (P3). None se a entidade não existe/não tem MapLocation
        (chamador decide o fallback; broadcasts usam None = sem filtro)."""
        from engine.components import MapLocation as _MLem
        ml = self.world.get_component(eid, _MLem)
        return ml.map_file if ml else None

    def register_map_services_for(self, eid: int) -> None:
        """Re-registra os serviços globais (_svc) com o bundle do MAPA da entidade.

        Handlers compartilhados (skill_handlers/spell_system) chamam
        is_tile_walkable/find_path/get_tilemap de módulo, que resolvem via _svc —
        e _svc fica apontando pro ÚLTIMO mapa carregado no startup (multi-map).
        Sem esta chamada, um player em map_1 tinha o caminho validado contra a
        matriz da CAVERNA (classe de bug: Interceptar "Caminho bloqueado"/"Sem
        espaço ao redor do alvo" em terreno aberto; Tiro Repulsivo stunando em
        parede fantasma). Mesmo problema já corrigido pontualmente em
        move_player() — TODO entry point que executa handler em nome de um
        player (skill request, spell completion, channeling) deve chamar isto
        ANTES do handler. Ver PROBLEMAS_ARQUITETURA.md.
        """
        from engine.world_systems import register_services
        _map = self.get_entity_map(eid) or self._map_file
        bundle = self._map_bundles.get(_map)
        if bundle is not None and bundle.tile_validation is not None:
            register_services(tile_validation=bundle.tile_validation,
                              pathfinding=bundle.pathfinding)

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

    def _sync_player_hp_dirty(self) -> None:
        """Detecta mudanças de HP/max_hp/level de qualquer player no tick e
        emite broadcast AOI. Chamado no início de _collect_deltas() — cobre
        qualquer fonte de mudança (level-up, consumíveis, skills, DoT, etc.)
        sem precisar de código por feature.

        `level` entrou aqui 17/07/2026 — bug real relatado pelo usuário:
        nameplate de player remoto travava no level de LOGIN pra sempre.
        Causa raiz: o payload de "player ficou visível" (server/session.py,
        AOI_UPDATE) usa `Session.char_data["level"]` — um snapshot cacheado
        no login, nunca atualizado — e nada mais notificava quem JÁ estava
        visível quando o personagem subia de nível de verdade (o
        `queue_stats_update` do level-up é privado, só o dono recebe).
        Level-up sempre muda max_hp (ganho de vitalidade), então o dirty-
        check de HP já disparava nesse momento — só faltava incluir o
        level no payload.

        Follow-up 18/07/2026 (usuário reportou que persistia — nameplate
        às vezes travava mesmo assim, coincidindo com duelo/combate):
        `_already` existia pra NÃO duplicar STATS_UPDATE quando outro
        sistema (`skill_processor.py`/`spell_completion_processor.py`,
        dano PvP) já tinha enfileirado uma entrada pro MESMO player NESTE
        tick — mas o guard antigo (`if peid not in _already: append`)
        pulava a entrada INTEIRA, level junto. Se o level-up acontecesse
        no MESMO tick de qualquer dano/cura do player (chance real e alta
        durante duelo, onde HP muda a cada golpe), o level nunca entrava
        em NENHUM broadcast daquele tick — silenciosamente perdido, sem
        outro tick pra tentar de novo (o cache já foi atualizado acima,
        então na próxima chamada `cur == cache` e nada dispara mais).
        Fix: em vez de pular, MESCLA o level na entrada já existente
        (mesmo dict, mutado in-place — `_already` guarda referências, não
        cópias).

        Follow-up 18/07/2026 (frame de grupo não atualizava o level mesmo
        quando o nameplate atualizava): PARTY_STATE só é reenviado em
        eventos de composição de grupo (entrar/sair/expulsar/promoção) —
        `consume_party_state_events()` nunca sabia que um MEMBRO subiu de
        nível, então o frame usava o snapshot antigo pra sempre. Fix: toda
        mudança de level detectada aqui também marca o grupo do player
        (se houver) como sujo em `_party_state_events_this_tick`, reusando
        o MESMO pipe que já reenvia PARTY_STATE pro grupo inteiro — sem
        precisar de um pipeline de replicação novo."""
        from engine.components import CombatStats as _CSD, CharacterStats as _CharD
        _already = {e["eid"]: e for e in self._player_hp_broadcasts_this_tick}
        for peid in list(self._player_eids.values()):
            cs = self.world.get_component(peid, _CSD)
            if cs is None:
                continue
            char  = self.world.get_component(peid, _CharD)
            level = char.level if char else 1
            _prev = self._player_hp_cache.get(peid)
            cur = (cs.current_hp, cs.max_hp, level)
            if cur != _prev:
                self._player_hp_cache[peid] = cur
                _existing = _already.get(peid)
                if _existing is not None:
                    _existing["level"] = level
                else:
                    self._player_hp_broadcasts_this_tick.append({
                        "eid": peid, "hp": cs.current_hp, "hp_max": cs.max_hp,
                        "level": level,
                    })
                if _prev is not None and _prev[2] != level:
                    _pid = self.get_party_id_of(peid)
                    if _pid != -1 and _pid not in self._party_state_events_this_tick:
                        self._party_state_events_this_tick.append(_pid)

    def consume_skill_levels_broadcasts(self) -> list[dict]:
        """Retorna e limpa updates de SkillLevels do tick atual (para o SessionManager)."""
        result = list(self._skill_levels_broadcasts_this_tick)
        self._skill_levels_broadcasts_this_tick.clear()
        return result

    def _sync_player_skill_levels_dirty(self) -> None:
        """Detecta mudança em SkillLevels.levels/xp de qualquer player no tick e
        enfileira snapshot completo pro dono (nunca broadcast AOI — é dado
        privado). Mesmo padrão de _sync_player_hp_dirty: cobre qualquer fonte
        de xp (cast de magia, auto-attack, arco, DoT resistido) sem precisar
        de código por feature — ver stats_system.grant_skill_xp.

        Também detecta level-ups (level novo > level antigo por skill_id) e
        inclui em "leveled_up" — cliente usa isso pra mostrar o feedback de
        "Parabéns, você subiu..." + som. Só reporta level-up quando já havia
        um snapshot anterior em cache (cache vazio = primeiro tick após
        login/spawn, não é um level-up real, é só o estado já salvo)."""
        from engine.components import SkillLevels as _SKLd
        for peid in list(self._player_eids.values()):
            skl = self.world.get_component(peid, _SKLd)
            if skl is None:
                continue
            snapshot = (tuple(sorted(skl.levels.items())), tuple(sorted(skl.xp.items())))
            cached = self._player_skill_cache.get(peid)
            if snapshot != cached:
                leveled_up = []
                if cached is not None:
                    _old_levels = dict(cached[0])
                    for sid, new_lvl in skl.levels.items():
                        if new_lvl > _old_levels.get(sid, 0):
                            leveled_up.append({"skill_id": sid, "level": new_lvl})
                self._player_skill_cache[peid] = snapshot
                _entry = {
                    "eid":    peid,
                    "levels": dict(skl.levels),
                    "xp":     dict(skl.xp),
                }
                if leveled_up:
                    _entry["leveled_up"] = leveled_up
                self._skill_levels_broadcasts_this_tick.append(_entry)

    def consume_quest_update_broadcasts(self) -> list[dict]:
        """Retorna e limpa updates de QuestLog do tick atual (para o SessionManager)."""
        result = list(self._quest_update_broadcasts_this_tick)
        self._quest_update_broadcasts_this_tick.clear()
        return result

    def _process_quest_events(self) -> None:
        """Drena QUEST_EVENTS (gatilhos server-side já autoritativos — kill,
        use_skill, use_consumable, equip_item, reach_tile, reach_level) e
        aplica progresso ao QuestLog do player_eid de cada evento. Também
        sincroniza, sem evento dedicado, objetivos collect_item (contra
        Inventory) e learn_skill (contra PlayerSkills.learned_skill_ids) —
        ambos já autoritativos no servidor. 1x por tick, mesmo padrão de
        _sync_player_skill_levels_dirty(). Ver quest_logic.py e
        arquitetura/PROBLEMAS_ARQUITETURA.md (migração de quests)."""
        from engine.quest_events import QUEST_EVENTS
        import engine.quest_logic as quest_logic
        from engine.components import QuestLog as _QL, Inventory as _Inv, PlayerSkills as _PS

        dirty_eids: set[int] = set()

        while QUEST_EVENTS:
            event_type, player_eid, data = QUEST_EVENTS.popleft()
            if player_eid == -1:
                continue  # evento sem dono explícito — não deveria ocorrer server-side
            ql = self.world.get_component(player_eid, _QL)
            if ql is None:
                continue
            if quest_logic.apply_event(ql, event_type, data):
                dirty_eids.add(player_eid)

        for peid in list(self._player_eids.values()):
            ql = self.world.get_component(peid, _QL)
            if ql is None or not ql.active:
                continue
            inv = self.world.get_component(peid, _Inv)
            if quest_logic.sync_collect_progress(ql, inv):
                dirty_eids.add(peid)
            ps = self.world.get_component(peid, _PS)
            learned = ps.learned_skill_ids if ps else set()
            if quest_logic.sync_learn_skill_progress(ql, learned):
                dirty_eids.add(peid)

        for peid in dirty_eids:
            ql = self.world.get_component(peid, _QL)
            if ql:
                self._quest_update_broadcasts_this_tick.append({
                    "eid":       peid,
                    "active":    {q: list(p) for q, p in ql.active.items()},
                    "completed": list(ql.completed),
                })

    def consume_skill_position_corrections(self) -> list[dict]:
        """Retorna e limpa correções de posição por skill (Interceptar etc.)."""
        result = list(self._skill_position_corrections)
        self._skill_position_corrections.clear()
        return result

    def queue_stats_update(self, entry: dict) -> None:
        """Enfileira um STATS_UPDATE privado para o dono de `player_eid`.

        ÚNICO ponto de entrada do canal (problema G, PROBLEMAS_ARQUITETURA.md
        — antes era o "bag" _pending_xp_deliveries com 16 produtores fazendo
        append direto e placeholders xp=0/mob_eid=-1 obrigatórios à mão).

        Schema — campos conhecidos (todos opcionais exceto player_eid; o
        SessionManager encaminha QUALQUER campo extra ao cliente, que lê
        com .get() e defaults):
          player_eid  int  OBRIGATÓRIO — destinatário (vira "eid" no wire)
          xp          int  XP ganho (vira "xp_gained"; ausente = 0)
          mob_eid     int  eid do mob que deu o XP (FLT posicional no cliente)
          hp/hp_max   int  sync de HP autoritativo
          mana        int  sync de mana | mana_amount: quanto restaurou
          rage        int  sync de fúria
          heal_amount int  quanto curou | heal_sid: origem da cura
          talent_points int  pontos de talento (level up)
          concentration  float  (mago)
          proj_incoming/proj_caster/proj_target  notificação de projétil
          applied_effects/effect_durations       efeitos aplicados (PvP)
        """
        if "player_eid" not in entry:
            raise ValueError(f"queue_stats_update sem player_eid: {entry!r}")
        self._pending_stats_updates.append(entry)

    def consume_stats_updates(self) -> list[dict]:
        """Retorna e limpa os STATS_UPDATE pendentes para o SessionManager."""
        result = list(self._pending_stats_updates)
        self._pending_stats_updates.clear()
        return result

    def validate_talent_allocation(self, session_id: str, claimed: dict) -> "dict | None":
        """Valida um payload `talents` (de TALENT_UPDATE/SAVE_STATE) contra o
        orçamento REAL de pontos do jogador antes de persistir.

        Sem isso, um cliente malicioso podia mandar `{"allocated":
        {"qualquer_talento": 999}, "available_points": 999}` e o servidor
        salvava/aplicava direto — `apply_talent_effects_to_player` faz
        `eff["value"] * points` sem checar limite (ver
        arquitetura/PROBLEMAS_ARQUITETURA.md, vulnerabilidade de orçamento
        de talento).

        `claimed` = {"allocated": {talent_id: pontos}, "available_points": N}
        (formato exato que o cliente manda). Retorna um dict no MESMO
        formato, mas corrigido pro orçamento real do servidor — nunca
        confia em `available_points` do payload, sempre recalcula a partir
        do total que o `TalentTree` do servidor já sabe que foi ganho
        (`tt.available_points` atual + pontos já alocados, que só cresce via
        `process_levelups()` no servidor, nunca por mensagem do cliente).
        Retorna `None` se `claimed` não for um dict com a forma esperada
        (chamador deve simplesmente não persistir/aplicar nesse caso)."""
        from engine.components import TalentTree as _TT_v
        from content.talent_data import TALENTS as _TAL_v

        if not isinstance(claimed, dict):
            return None
        new_allocated_raw = claimed.get("allocated")
        if not isinstance(new_allocated_raw, dict):
            return None

        eid = self._player_eids.get(session_id)
        tt  = self.world.get_component(eid, _TT_v) if eid is not None else None
        if tt is None:
            return None

        total_budget = int(tt.available_points) + sum(int(v) for v in tt.allocated.values())

        new_allocated: dict = {}
        spent = 0
        for talent_id, points in new_allocated_raw.items():
            t = _TAL_v.get(talent_id)
            if not t:
                continue   # talento inexistente — descartado silenciosamente
            try:
                points = int(points)
            except (TypeError, ValueError):
                continue
            points = max(0, min(points, t.get("max_points", 0)))
            if points <= 0:
                continue
            new_allocated[talent_id] = points
            spent += points

        if spent > total_budget:
            # Claim excede o orçamento real — rejeita a alocação inteira (não
            # tenta "corrigir" proporcionalmente, pra não mascarar bug/cheat).
            log.info(f"[TalentBudget] session={session_id} reivindicou {spent} pontos "
                  f"mas orçamento real é {total_budget} — alocação rejeitada.")
            return None

        # Persiste o resultado validado de volta no TalentTree AO VIVO da
        # entidade — sem isso, a próxima chamada calcularia total_budget a
        # partir do estado antigo (carregado no login), nunca refletindo
        # alocações feitas durante esta sessão.
        tt.allocated        = new_allocated
        tt.available_points = total_budget - spent

        # chosen_build NUNCA pode ser descartado aqui — bug real encontrado:
        # o retorno antigo só tinha allocated/available_points, e como esse
        # dict SUBSTITUI payload["talents"] inteiro antes de persistir
        # (_handle_save_state), talents_json perdia "chosen_build" no primeiro
        # save após qualquer alocação. Próximo login: cliente não achava
        # chosen_build salvo, caía no default "cavaleiro", isso nunca batia
        # com a build real da classe (ex: arqueiro=bardo) e
        # TalentSystem.apply_talent_effects() considerava TODOS os talentos
        # "de build errada" e limpava tt.allocated sem devolver os pontos —
        # perda silenciosa e permanente de pontos gastos (ver
        # arquitetura/PROBLEMAS_ARQUITETURA.md).
        chosen_build = claimed.get("chosen_build")
        if isinstance(chosen_build, str) and chosen_build:
            tt.chosen_build = chosen_build

        return {
            "chosen_build":      tt.chosen_build,
            "allocated":        new_allocated,
            "available_points": tt.available_points,
        }

    def apply_talent_effects_to_player(self, session_id: str, talent_allocated: dict) -> None:
        """Re-aplica efeitos de talentos ao CombatStats do servidor após salvar.

        Necessário pois o cliente aloca talentos e manda SAVE_STATE, mas o
        servidor precisa dos efeitos para processar skills corretamente
        (ex: impacto_maquina_matar, parry_rating, golpe_poderoso_rage_cost).
        """
        eid = self._player_eids.get(session_id)
        if eid is None:
            return
        self._apply_talent_modifiers(eid, talent_allocated)

    def _apply_talent_modifiers(self, eid: int, talent_allocated: dict) -> None:
        """Núcleo de apply_talent_effects_to_player, parametrizado por eid (em vez
        de session_id) para poder ser chamado em spawn_player antes de
        self._player_eids estar populado para essa sessão."""
        from engine.components import (CombatStats, CharacterStats, PermanentStats, Modifier)
        from content.talent_data import TALENTS as _TAL
        from engine.stats_system import apply_char_stats_to_combat, sync_attack_interval
        from engine.stat_fns import add_modifier
        from engine.components import Equipment

        cs   = self.world.get_component(eid, CombatStats)
        char = self.world.get_component(eid, CharacterStats)
        perm = self.world.get_component(eid, PermanentStats)
        equip = self.world.get_component(eid, Equipment)
        if not cs or not char:
            return

        # Limpa só modifiers de talento (acumulados de SAVE_STATEs anteriores) —
        # equipment/buff (source != "talent") nunca são tocados aqui, ver
        # _apply_equipment_modifiers. Antes isso era cs.modifiers.clear() (limpava
        # TUDO, inclusive bônus de equipamento) e dependia de _apply_stat_overrides
        # pra "restaurar" o que tinha acabado de apagar — root cause removido.
        cs.modifiers = [m for m in cs.modifiers if m.source != "talent"]

        # Recalcula base a partir dos atributos do personagem (modifiers de
        # equipamento/buff sobrevivem ao clear acima, então max_hp/etc. já saem
        # corretos daqui — sem necessidade de salvar/restaurar current_hp).
        apply_char_stats_to_combat(char, cs, perm)
        sync_attack_interval(cs, equip)

        # Reset de cs_flags pro valor padrão ANTES de aplicar a alocação atual —
        # sem isso, desalocar um talento (ex: trocar de build) deixava o cs_flag
        # dele travado no último valor calculado, já que o loop abaixo só visita
        # talentos alocados AGORA. Faltava em apply_talent_effects_to_player desde
        # sempre; só existia (duplicado) no spawn — unificado aqui pros dois lados.
        for _t_all in _TAL.values():
            for _flag in (_t_all.get("cs_flags") or []):
                _reset = _flag.get("reset")
                if _reset is not None:
                    try:
                        setattr(cs, _flag["field"], _reset)
                    except Exception:
                        pass

        # Re-aplica cs_flags e modifiers de cada talento alocado
        for talent_id, points in (talent_allocated or {}).items():
            if not points:
                continue
            t = _TAL.get(talent_id)
            if not t:
                continue
            # Defesa em profundidade — o orçamento real já devia ter sido
            # validado em validate_talent_allocation() antes disso ser salvo,
            # mas reforça aqui também: nunca aplica mais pontos que o teto do
            # próprio talento, mesmo que talent_allocated venha de uma fonte
            # que pulou a validação (ver PROBLEMAS_ARQUITETURA.md).
            points = min(int(points), t.get("max_points", int(points)))
            if points <= 0:
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
                mod = Modifier(eff["attribute"], eff["value"] * points, eff["type"], source="talent")
                add_modifier(cs, mod)

    def update_player_equipment(self, session_id: str, equipment: dict) -> list[dict]:
        """Reconstrói o componente Equipment do player a partir do payload EQUIP_SYNC.

        Chamado toda vez que o cliente equipa ou desequipa um item. Garante que
        validações server-side (quiver para auto-attack, bow para skills de flecha)
        usem o estado real do equipamento, não o estado congelado do login.

        Valida por slot, ANTES de aplicar: armor_class contra
        stats_system.CLASS_ARMOR_ALLOWED[classe] (antes só existia no cliente,
        client/inventory_handlers.py::_equip_item — um cliente malicioso podia
        equipar qualquer material em qualquer classe) e level_requirement
        contra CharacterStats.level. Slot que falha mantém o item anterior
        (nunca aplica o candidato) e entra na lista de retorno — o caller
        (server/session.py::_handle_equip_sync) manda EQUIP_REJECTED por
        rejeição pro cliente reverter a UI otimista e avisar o jogador.

        Retorna [{"slot":, "item_name":, "reason": "class"|"level"}, ...].
        """
        from engine.components import Equipment as _EqUpd, CharacterStats as _CSEquip
        eid = self._player_eids.get(session_id)
        if eid is None:
            return []
        eq_comp = self.world.get_component(eid, _EqUpd)
        if eq_comp is None:
            eq_comp = _EqUpd()
            self.world.add_component(eid, eq_comp)
        char = self.world.get_component(eid, _CSEquip)
        # Snapshot ANTES de sobrescrever — só dispara evento de quest pra item
        # que de fato passou a estar equipado agora (evita re-disparo a cada
        # EQUIP_SYNC redundante, ex: reconectar com o mesmo equipamento).
        _old_slot_names = {slot: (item.name if item else None) for slot, item in eq_comp.slots.items()}
        from engine.stats_system import CLASS_ARMOR_ALLOWED as _CAA_equip
        from engine.stats_system import is_weapon_allowed_for_class as _is_weapon_allowed_equip
        rejected: list[dict] = []
        for slot, item_d in equipment.items():
            if slot not in eq_comp.slots or not isinstance(item_d, dict):
                continue
            item_d.setdefault("slot", slot)
            candidate = self._reconstruct_item(item_d)
            if candidate is None:
                continue
            if char is not None:
                _mat = getattr(candidate, "armor_class", "")
                if (candidate.item_type == "armor" and _mat
                        and _mat not in _CAA_equip.get(char.class_id, frozenset())):
                    rejected.append({"slot": slot, "item_name": candidate.name, "reason": "class"})
                    continue
                if (candidate.item_type in ("weapon", "shield", "quiver")
                        and not _is_weapon_allowed_equip(candidate, char.class_id)):
                    rejected.append({"slot": slot, "item_name": candidate.name, "reason": "class"})
                    continue
                if char.level < getattr(candidate, "level_requirement", 1):
                    rejected.append({"slot": slot, "item_name": candidate.name, "reason": "level"})
                    continue
            eq_comp.slots[slot] = candidate
        # Slots ausentes no payload → desequipado
        for slot in list(eq_comp.slots.keys()):
            if slot not in equipment:
                eq_comp.slots[slot] = None

        # Evento de quest "equip_item" — só pra slots que mudaram de item.
        # Server-autoritativo — ver quest_logic.py/PROBLEMAS_ARQUITETURA.md.
        from engine.quest_events import fire as _qfire_equip
        for slot, item in eq_comp.slots.items():
            if item and item.name != _old_slot_names.get(slot):
                _qfire_equip("equip_item", player_eid=eid,
                             item_name=item.name, item_type=item.item_type)
        # Deriva attack_power/crit_rating/armor/spell_power/etc. dos itens REAIS
        # agora equipados (ver _apply_equipment_modifiers) — nunca confia em
        # nenhum valor calculado pelo cliente para isso.
        self._apply_equipment_modifiers(eid)
        # Atualiza attack_interval conforme arma equipada
        from engine.stats_system import sync_attack_interval as _sai
        from engine.components import CombatStats as _CSUpd
        cs = self.world.get_component(eid, _CSUpd)
        if cs:
            _sai(cs, eq_comp)
        return rejected

    def get_player_equipment_data(self, session_id: str) -> dict:
        """Serializa o Equipment ATUAL (pós-validação) do player pra cache de
        save — usado por _handle_equip_sync pra nunca persistir um slot que
        update_player_equipment rejeitou (classe/level), mesmo que o cliente
        tenha mandado no payload de EQUIP_SYNC."""
        from engine.components import Equipment as _EqData
        eid = self._player_eids.get(session_id)
        if eid is None:
            return {}
        eq_comp = self.world.get_component(eid, _EqData)
        if eq_comp is None:
            return {}
        return {slot: self._item_data_from_obj(item)
                for slot, item in eq_comp.slots.items() if item is not None}

    # request_loot → LootProcessorMixin

    # ── Alvo de combate unificado (ECS pattern: combatente = entidade com CombatStats) ──

    def _combat_targets(self, exclude_eid: int = -1) -> set[int]:
        """Retorna todos os eids que podem receber dano neste tick.

        Padrão ECS consolidado (Overwatch, Guild Wars 2): o sistema de combate
        não distingue mob de player — qualquer entidade com CombatStats é alvo,
        DESDE QUE can_engage(caster, alvo) — contexto (facção/duelo/zona/arena)
        decide, igual pro player quanto pro mob/NPC. exclude_eid é o CASTER
        (sempre um player — todo chamador é uma skill/spell de player), então
        dá pra resolver a relação por par.

        Bug real corrigido 16/07/2026: `_mob_eids` inclui QUALQUER Combatant,
        não só mob hostil — NPC de combate amigável (ex: Guarda Real) também
        entra ali (gate é Combatant, não Enemy, ver _tick comment). Nova
        Congelante (AoE) iterava _mob_eids sem filtro de hostilidade e
        enraizava/danificava o guarda. Antes só players passavam por
        can_engage aqui; agora mobs/NPCs passam pelo mesmo crivo.
        """
        from engine.faction_system import can_engage as _can_engage_ct
        targets = set()
        for m_eid in self._mob_eids:
            if exclude_eid != -1 and not _can_engage_ct(self.world, exclude_eid, m_eid):
                continue
            targets.add(m_eid)
        for p_eid in self._player_eids.values():
            if p_eid == exclude_eid:
                continue
            if exclude_eid != -1 and not _can_engage_ct(self.world, exclude_eid, p_eid):
                continue
            targets.add(p_eid)
        return targets

    def _snapshot_combat_targets(self, exclude_eid: int = -1) -> tuple[dict, dict]:
        """HP e StatusEffects antes de processar uma skill (para coletar diff depois).

        Retorna (hp_before, sfx_before) incluindo mobs + players PvP.
        """
        from engine.components import CombatStats as _CSsnap, StatusEffects as _SFXsnap
        hp_before: dict[int, int] = {}
        sfx_before: dict[int, set] = {}
        for eid in self._combat_targets(exclude_eid):
            cs = self.world.get_component(eid, _CSsnap)
            if cs:
                hp_before[eid] = cs.current_hp
            sfx = self.world.get_component(eid, _SFXsnap)
            sfx_before[eid] = set(sfx.effects.keys()) if sfx else set()
        return hp_before, sfx_before

    # ── Loop de ticks ─────────────────────────────────────────────────────────

    def register_on_tick(self, callback) -> None:
        self._on_tick_callbacks.append(callback)

    async def run(self) -> None:
        import gc as _gc_srv
        self.running = True
        log.info(f"[WorldServer] zona='{self.zone_id}' @ {TICK_RATE} ticks/s")

        next_tick  = time.perf_counter()
        _gc_ticks  = 0
        _GC_EVERY  = TICK_RATE * 10   # coleta manual a cada ~10s (evita pauses do GC automático)
        while self.running:
            now = time.perf_counter()
            if now >= next_tick:
                try:
                    self._tick(TICK_INTERVAL)
                except Exception as e:
                    import traceback
                    log.error(f"[WorldServer] ERRO no tick {self.tick_count}: {e}")
                    traceback.print_exc()
                    # Limpa deltas pendentes para não propagar estado corrompido
                    self._moved_this_tick.clear()
                    self._spawned_this_tick.clear()
                    self._despawned_this_tick.clear()
                    self._combat_this_tick.clear()
                    self._player_deaths_this_tick.clear()
                    self._entity_deaths_this_tick.clear()
                    self._player_revives_this_tick.clear()
                    self._ghost_state_updates_this_tick.clear()
                next_tick += TICK_INTERVAL
                if time.perf_counter() - next_tick > TICK_INTERVAL:
                    next_tick = time.perf_counter()
                _gc_ticks += 1
                if _gc_ticks >= _GC_EVERY:
                    _gc_ticks = 0
                    _gc_srv.collect()   # coleta manual entre ticks, nunca durante
            else:
                # Dorme até o próximo tick — elimina busy-spin com sleep(0).
                # Threshold 1ms: abaixo disso yield simples para não overshooting.
                _sleep = next_tick - time.perf_counter()
                if _sleep > 0.001:
                    await asyncio.sleep(_sleep)
                else:
                    await asyncio.sleep(0)
        self._perf_log.close()

    def _tick(self, dt: float) -> None:
        import time as _time_tick
        _t_tick_start = _time_tick.perf_counter()
        self.tick_count += 1
        # CPU % do processo neste tick (não-bloqueante: acumula desde a chamada anterior).
        if self._perf_proc is not None:
            try:
                _cpu_now = self._perf_proc.cpu_percent(interval=None)
                self._perf_cpu_sum  += _cpu_now
                if _cpu_now > self._perf_cpu_peak:
                    self._perf_cpu_peak = _cpu_now
            except Exception:
                pass

        # EnemyAISystem e EnemyAbilitySystem iteram todos os PlayerControlled internamente.
        from engine.components import TileMovement, Enemy, CombatStats

        # ── DEBUG Bug2: detecta mutações de current_hp em mobs ocorridas
        # ENTRE ticks (handlers async de mensagem, ex: PROJECTILE_HIT_CS),
        # comparando contra o snapshot salvo ao final do tick anterior
        # (após o death-sweep). Apenas log — não altera comportamento.
        if MCL.DBG_ENABLED:
            from engine.components import PendingDeath as _HPPD, EntityIdentity as _HPID
            for _hp_eid in list(self._mob_eids):
                _hp_cs = self.world.get_component(_hp_eid, CombatStats)
                if not _hp_cs:
                    continue
                _hp_now  = _hp_cs.current_hp
                _hp_prev = self._mob_hp_prev.get(_hp_eid)
                if _hp_prev is not None and _hp_now != _hp_prev:
                    _hp_pd = self.world.get_component(_hp_eid, _HPPD) is not None
                    _hp_id = self.world.get_component(_hp_eid, _HPID)
                    MCL.log("HP_DELTA", _hp_eid,
                            _hp_id.name if _hp_id else "?",
                            _hp_id.race if _hp_id else "?",
                            _hp_id.entity_class if _hp_id else "",
                            tick=self.tick_count, prev=_hp_prev, now=_hp_now,
                            pending_death=_hp_pd, where="between-ticks")

        self._tick_respawn_immunity()
        self._tick_ghost_states(dt)
        # Reseta rastreamento de dano PvP do tick anterior.
        # Populado por skill_processor e spell_completion_processor além de _process_pvp_attack.
        self._pvp_damage_this_tick = {}

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
        from engine.components import AIControlled as _AIC
        for _eid_sn in self._mob_eids:
            _ai_sn = self.world.get_component(_eid_sn, _AIC)
            if _ai_sn:
                self._mob_states_prev[_eid_sn] = _ai_sn.state

        # Roda sistemas offline reais por bundle de mapa.
        # P4: serviços já injetados diretamente nos sistemas em _load_map_for()
        # — register_services() não é mais necessário no loop de tick online.
        # Otimização: EnemyAISystem/EnemyAbilitySystem são pulados em mapas sem player —
        # mobs ficam parados (sem custo de pathfinding) até um player entrar no mapa.
        _maps_com_player = set(self._player_maps.values())
        # Profiler: rastreia ticks ativos por mapa e pico de mapas simultâneos.
        for _m in _maps_com_player:
            self._perf_map_active[_m] = self._perf_map_active.get(_m, 0) + 1
        if len(_maps_com_player) > self._perf_peak_maps:
            self._perf_peak_maps = len(_maps_com_player)
        _t0p = _time_tick.perf_counter()
        for _bnd_key, _bnd in self._map_bundles.items():
            _has_player = _bnd_key in _maps_com_player
            _t0bnd = _time_tick.perf_counter()
            for system in _bnd.systems:
                if not _has_player and system in _bnd.ai_systems:
                    continue  # sem player neste mapa: pula AI (mobs ficam parados)
                system.update(dt=dt)
            _bnd_label = "bnd:" + _bnd_key.split("/")[-1].replace(".csv", "")
            self._perf_accum[_bnd_label] = self._perf_accum.get(_bnd_label, 0.0) + (_time_tick.perf_counter() - _t0bnd)
        self._perf_accum["ai_bundles"] = self._perf_accum.get("ai_bundles", 0.0) + (_time_tick.perf_counter() - _t0p)

        # Sistemas globais: rodam UMA vez por tick, após todos os bundles de IA.
        _t0p = _time_tick.perf_counter()
        self._global_tms.update(dt=dt)       # movement: progress → current_tile
        self._global_sfx_sys.update(dt=dt)   # status effects: DoT/HoT timers
        self._global_proj_sys.update(dt=dt)  # projéteis de mobs: posição + hit
        self._perf_accum["global_systems"] = self._perf_accum.get("global_systems", 0.0) + (_time_tick.perf_counter() - _t0p)

        # Detecta mobs que aggraram neste tick (IDLE → CHASING/ATTACKING)
        from engine.components import EntityIdentity as _EIdent
        for _eid_ag in list(self._mob_eids):
            _ai_ag = self.world.get_component(_eid_ag, _AIC)
            _tm_ag = self.world.get_component(_eid_ag, TileMovement)
            if not _ai_ag or not _tm_ag:
                continue
            _prev_state = self._mob_states_prev.get(_eid_ag, "IDLE")
            # Aggro: detecta IDLE/RETURNING → AGGRO_DELAY ou CHASING (aggro por dano pula AGGRO_DELAY)
            if _prev_state in ("IDLE", "RETURNING") and _ai_ag.state in ("AGGRO_DELAY", "CHASING"):
                _ident_ag = self.world.get_component(_eid_ag, _EIdent)
                self._pending_sound_events.append({
                    "kind":     "mob_aggro",
                    "mob_eid":  _eid_ag,                          # server eid → cliente busca NpcSounds
                    "mob_name": _ident_ag.name if _ident_ag else "",
                    "tx":       _tm_ag.current_tile_x,
                    "ty":       _tm_ag.current_tile_y,
                })

        # CombatStateSystem headless (in_combat timer + rage decay + HP5/mana regen)
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
        # Regen de mana (Mago) — único produtor autoritativo; sincroniza via
        # STATS_UPDATE (mesmo canal de ActiveManaRegen/mana_restore). Cliente
        # só prediz offline (spell_system.ManaSystem) — ver PROBLEMAS_ARQUITETURA.md
        # (bug real: cliente regenerava mana sozinho sem o servidor saber).
        for _mana_ev in self._combat_state_sys.mana_events:
            self.queue_stats_update({
                "player_eid": _mana_ev["player_eid"],
                "mana":       _mana_ev["new_mana"],
            })
        # Decay de Raiva (Guerreiro) — único produtor autoritativo; sincroniza
        # via STATS_UPDATE. Cliente online NÃO gera/decai rage localmente (ver
        # PROBLEMAS_ARQUITETURA.md — hotbar acendia com rage local à frente do
        # servidor e a skill era rejeitada com "Raiva insuficiente").
        for _rage_ev in self._combat_state_sys.rage_events:
            self.queue_stats_update({
                "player_eid": _rage_ev["player_eid"],
                "rage":       _rage_ev["new_rage"],
            })
        # Procs de item rolados autoritativamente (core_systems.ServerCombatStateSystem.
        # _roll_procs) — qualquer mudança em current_hp/max_hp já é detectada e
        # propagada via _sync_player_hp_dirty() abaixo; aqui só log de auditoria.
        for _proc_ev in self._combat_state_sys.proc_events:
            log.info(f"[Proc] player_eid={_proc_ev['player_eid']} item={_proc_ev['item_name']!r} "
                  f"-> {_proc_ev['label']} (+{_proc_ev['value']} {_proc_ev['attribute']} "
                  f"por {_proc_ev['duration']:.0f}s)")

        # ── Regen do boneco de treino (hp5 = ~10% de max_hp a cada 5s) ──────────
        from engine.components import TrainingDummy as _TDtk
        for _td_eid, _td_cs, _ in self.world.get_entities_with(CombatStats, _TDtk):
            if _td_cs.current_hp < _td_cs.max_hp:
                _td_cs.hp5_timer += dt
                if _td_cs.hp5_timer >= 5.0:
                    _td_cs.hp5_timer -= 5.0
                    _old_td_hp = _td_cs.current_hp
                    _regen_td  = max(1, int(_td_cs.max_hp * _td_cs.hp5))
                    _td_cs.current_hp = min(_td_cs.max_hp, _old_td_hp + _regen_td)
                    self._combat_this_tick.append({
                        "attacker": -1, "target":  _td_eid,
                        "damage":   -(_td_cs.current_hp - _old_td_hp),
                        "outcome":  "regen", "hp_after": _td_cs.current_hp,
                        "source":   "regen",
                    })

        # ── Regen de mob fora de combate (1% de max_hp a cada 3s) ────────────
        # Substitui a cura instantânea que existia ao sair de RETURNING
        # (EnemyAISystem, Decisão 20 em ARQUITETURA_ONLINE.md) — o usuário
        # reportou que o client não via o HP atualizar (a cura instantânea
        # nunca passava pelo canal de broadcast). Reaproveita CombatStats.hp5
        # (já default 0.01 = 1% pra qualquer mob — nunca sobrescrito em
        # create_enemy, só o TrainingDummy customiza) com intervalo PRÓPRIO
        # de 3s (distinto do hp5 de player/dummy, que é 5s) — daí o timer
        # separado (_mob_regen_timer) em vez de reusar hp5_timer, que
        # assumiria sempre 5s se algum outro código também o lesse.
        # Só regenera fora de combate (IDLE/RETURNING, igual ao "in_combat"
        # do player) — mob CHASING/ATTACKING/AGGRO_DELAY/KITING não cura.
        # Broadcast via _combat_this_tick (mesmo canal do HP5 de player/
        # dummy acima) — chega a QUALQUER observador com o mob em
        # known_eids, não só o dono (mob não tem "dono").
        from engine.components import AIControlled as _AICregen
        for _mregen_eid in self._mob_eids:
            _mregen_cs = self.world.get_component(_mregen_eid, CombatStats)
            if not _mregen_cs or _mregen_cs.current_hp <= 0 or _mregen_cs.current_hp >= _mregen_cs.max_hp:
                continue
            _mregen_ai = self.world.get_component(_mregen_eid, _AICregen)
            if not _mregen_ai or _mregen_ai.state not in ("IDLE", "RETURNING"):
                continue
            _mregen_ai.regen_timer += dt
            if _mregen_ai.regen_timer >= 3.0:
                _mregen_ai.regen_timer -= 3.0
                _old_mregen_hp = _mregen_cs.current_hp
                _regen_amt     = max(1, round(_mregen_cs.max_hp * _mregen_cs.hp5))
                _mregen_cs.current_hp = min(_mregen_cs.max_hp, _old_mregen_hp + _regen_amt)
                self._combat_this_tick.append({
                    "attacker": -1, "target":  _mregen_eid,
                    "damage":   -(_mregen_cs.current_hp - _old_mregen_hp),
                    "outcome":  "regen", "hp_after": _mregen_cs.current_hp,
                    "source":   "regen",
                })

        # ── ActiveRegen (consumíveis HoT — HP) ───────────────────────────────
        from engine.components import ActiveRegen as _AR
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
                    self.queue_stats_update({
                        "player_eid":  _regen_eid,
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

        # ── ActiveManaRegen (consumíveis HoT — mana) ─────────────────────
        from engine.components import ActiveManaRegen as _AMR, CharacterStats as _CHSmr
        _mregen_remove = []
        for _mregen_eid, _mregen in list(self.world.get_entities_with(_AMR)):
            if _mregen_eid not in self._player_eids.values():
                continue
            _char_mr = self.world.get_component(_mregen_eid, _CHSmr)
            _cs_mr   = self.world.get_component(_mregen_eid, CombatStats)
            if not _char_mr or _char_mr.max_mana <= 0:
                _mregen_remove.append(_mregen_eid)
                continue
            _mregen.tick_timer -= dt
            if _mregen.tick_timer <= 0:
                _mregen.tick_timer += _mregen.interval
                _mregen.ticks_remaining -= 1
                _old_mana = _char_mr.mana
                _char_mr.mana = min(_char_mr.max_mana, _char_mr.mana + _mregen.mana_per_tick)
                if _cs_mr:
                    _cs_mr.mana = _char_mr.mana
                _restored = _char_mr.mana - _old_mana
                if _restored > 0:
                    self.queue_stats_update({
                        "player_eid":  _mregen_eid,
                        "mana":        _char_mr.mana,
                        "mana_amount": _restored,
                    })
                if _mregen.ticks_remaining <= 0:
                    _mregen_remove.append(_mregen_eid)
        for _mregen_eid in _mregen_remove:
            try:
                self.world.remove_component(_mregen_eid, _AMR)
            except Exception:
                pass

        # ── Arqueiro: timer de Camuflagem ─────────────────────────────────────
        # Regen de Concentração e concentration_free_timer (buff "Só um Gole")
        # JÁ são tratados por core_systems.ServerCombatStateSystem.update()
        # (chamado acima nesta mesma função) — este bloco antes DUPLICAVA os
        # dois com o mesmo dt/fórmula, então a Concentração regenerava 2x mais
        # rápido que o pretendido e concentration_free_timer (10s de "Só um
        # Gole") zerava em ~5s reais no servidor, sem nenhum aviso ao cliente.
        # Bug real reportado pelo usuário (13/07/2026): "às vezes" uma skill
        # de Concentração ainda cobrava custo com o buff supostamente ainda
        # ativo — o servidor (autoritativo, quem decide o desconto em
        # spell_completion_processor.py) já tinha voltado concentration_free
        # a False bem antes dos 10s aparentes. camouflage_timer NÃO é tratado
        # em nenhum outro lugar — continua só aqui.
        from engine.components import CombatStats as _ConcCSt, TileMovement as _ConcTM
        for _conc_eid in list(self._player_eids.values()):
            _cs = self.world.get_component(_conc_eid, _ConcCSt)
            if not _cs or _cs.camouflage_timer <= 0:
                continue
            _cs.camouflage_timer -= dt
            if _cs.camouflage_timer <= 0:
                _cs.camouflage_timer  = 0.0
                _cs.camouflage_object = ""
                _cst = self.world.get_component(_conc_eid, __import__("engine.components", fromlist=["CombatState"]).CombatState)
                if _cst:
                    _cst.is_visible    = True
                    _cst.is_immune     = False
                    _cst.is_camouflaged = False
                    self._visibility_changed_this_tick.append(_conc_eid)
                _tm_cam = self.world.get_component(_conc_eid, _ConcTM)
                if _tm_cam:
                    _tm_cam.speed = 110.0

        # ── FireShieldEffect: decrementa timer e remove quando expirar ──────
        from engine.components import FireShieldEffect as _FSE
        for _fse_eid, _fse in list(self.world.get_entities_with(_FSE)):
            _fse.elapsed += dt
            if _fse.elapsed >= _fse.duration:
                try:
                    self.world.remove_component(_fse_eid, _FSE)
                except Exception:
                    pass

        # ── Channeling de players (Calamidade Flamejante) ────────────────────
        self._process_player_channeling(dt)

        # Skills ANTES do auto-attack: skill dispara em mob vivo, depois auto-attack
        # (se ordem fosse invertida, auto-attack poderia matar o mob antes da skill checar HP)
        _t0p = _time_tick.perf_counter()
        self._process_skill_requests()
        self._perf_accum["skill_requests"] = self._perf_accum.get("skill_requests", 0.0) + (_time_tick.perf_counter() - _t0p)

        # Conclusão de spells com cast_time (Bola de Fogo, Nova Congelante, etc.)
        _t0p = _time_tick.perf_counter()
        self._process_spell_cast_completions(dt)
        self._perf_accum["spell_completions"] = self._perf_accum.get("spell_completions", 0.0) + (_time_tick.perf_counter() - _t0p)
        # Pousos de knockback (stun/feedback de colisão atrasados até a tween acabar)
        self._process_knockback_landings(dt)
        # Expira projéteis em voo que nunca receberam PROJECTILE_HIT_CS
        self._expire_spells_in_flight()
        # Bloco de Gelo: timer server-side (imunidade temporária)
        self._process_ice_blocks(dt)

        # Fatiador de Corpos: ticks subsequentes ao cast (tick 0 disparado pelo handler em
        # _process_skill_requests; PlayerInputSystem só roda no cliente, não no servidor).
        from engine.components import CharacterStats as _FatCS, TileMovement as _FatTM
        from engine.components import PlayerSkills as _FatPS, CombatStats as _FatCombatCS
        from content.skill_config import SKILL_CATALOG as _FatCat
        for _fat_peid in list(self._player_eids.values()):
            _fat_char = self.world.get_component(_fat_peid, _FatCS)
            if not _fat_char or _fat_char.fatiador_timer <= 0:
                continue
            _fat_char.fatiador_timer = max(0.0, _fat_char.fatiador_timer - dt)
            _fat_char.fatiador_tick  = max(0.0, _fat_char.fatiador_tick  - dt)
            if _fat_char.fatiador_tick <= 0 and _fat_char.fatiador_timer > 0:
                _fat_char.fatiador_tick = 1.0
                _fat_tm = self.world.get_component(_fat_peid, _FatTM)
                _fat_ps = self.world.get_component(_fat_peid, _FatPS)
                _fat_sk = _fat_ps.skill_by_id("fatiador_de_corpos") if _fat_ps else None
                if _fat_sk is None:
                    _fat_sk = _FatPS._make_skill("fatiador_de_corpos", _FatCat)
                if _fat_tm and _fat_sk:
                    _fat_hp_snap: dict[int, int] = {}
                    for _fat_meid in self._mob_eids:
                        _fat_mcs = self.world.get_component(_fat_meid, _FatCombatCS)
                        if _fat_mcs:
                            _fat_hp_snap[_fat_meid] = _fat_mcs.current_hp
                    self._skill_system.player_entity_id = _fat_peid
                    self._skill_system._fatiador_aoe_tick(_fat_sk, _fat_tm)
                    for _fat_meid, _fat_hp_pre in _fat_hp_snap.items():
                        _fat_mcs2 = self.world.get_component(_fat_meid, _FatCombatCS)
                        if not _fat_mcs2:
                            continue
                        _fat_dmg = max(0, _fat_hp_pre - _fat_mcs2.current_hp)
                        if _fat_dmg > 0:
                            # _mob_damage_log já populado centralizadamente por
                            # WorldServer._log_mob_damage_hit (injetado em
                            # CombatSystem — _fatiador_aoe_tick chama deal_damage
                            # internamente) — escrever aqui de novo contaria o
                            # mesmo tick em dobro. Ver PROBLEMAS_ARQUITETURA.md.
                            self._combat_this_tick.append({
                                "attacker": _fat_peid,
                                "target":   _fat_meid,
                                "damage":   _fat_dmg,
                                "outcome":  "hit",
                                "hp_after": max(0, _fat_mcs2.current_hp),
                                "source":   "skill",
                            })

        # Player→mob: usa deal_damage() offline; Mob→player: detectado por variação de HP
        self._process_player_attacks(dt, player_hp_snap)

        # Sweep: mobs com HP <= 0 sem PendingDeath (DoT, outros caminhos fora de deal_damage)
        from engine.components import Enemy as _Enemy, CombatStats as _CS2, PendingDeath as _PD
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
            self._mob_hp_prev.pop(eid, None)
            if not any(d["eid"] == eid for d in self._despawned_this_tick):
                self._despawned_this_tick.append(entry)
        for entry in self._death_handler.consume_xp():
            self.queue_stats_update(entry)
            # Aplica XP no ECS do servidor para manter level/xp sincronizados no save
            _xp_peid = entry["player_eid"]
            _xp_amt  = entry["xp"]
            from engine.components import CharacterStats as _CharXP, CombatStats as _CsXP, PermanentStats as _PermXP
            _char_xp = self.world.get_component(_xp_peid, _CharXP)
            _cs_xp   = self.world.get_component(_xp_peid, _CsXP)
            _perm_xp = self.world.get_component(_xp_peid, _PermXP)
            if _char_xp and _cs_xp:
                _level_before = _char_xp.level
                _char_xp.current_xp += _xp_amt
                from engine.stats_system import process_levelups as _pu
                _pu(self.world, _xp_peid, _char_xp, _cs_xp, _perm_xp)
                # Se subiu de nível: re-aplica talentos e notifica cliente.
                # Equipment não precisa ser reaplicado aqui — process_levelups()
                # (acima) chama apply_char_stats_to_combat, que nunca toca em
                # cs.modifiers, então os modifiers de equipamento (source="equipment")
                # sobrevivem ao recálculo intactos.
                if _char_xp.level > _level_before:
                    try:
                        from engine.components import TalentTree as _TTre
                        _tt_re = self.world.get_component(_xp_peid, _TTre)
                        if _tt_re and _tt_re.allocated:
                            _sid_re = self._player_eid_to_sid.get(_xp_peid, "")
                            if _sid_re:
                                self.apply_talent_effects_to_player(_sid_re, _tt_re.allocated)
                    except Exception as _lv_err:
                        log.warning(f"[LevelUp] aviso ao re-aplicar talentos: {_lv_err}")

                    # Garante HP cheio após qualquer recálculo acima
                    _cs_xp.current_hp = _cs_xp.max_hp
                    # Não é necessário broadcast manual aqui: _sync_player_hp_dirty()
                    # detecta a mudança de HP/max_hp automaticamente no fim do tick
                    # e a propaga via _player_hp_broadcasts_this_tick → STATS_UPDATE AOI.

                    from engine.components import TalentTree as _TTlv
                    _tt_lv = self.world.get_component(_xp_peid, _TTlv)
                    self.queue_stats_update({
                        "player_eid":    _xp_peid,
                        "hp":            _cs_xp.current_hp,
                        "hp_max":        _cs_xp.max_hp,
                        "talent_points": _tt_lv.available_points if _tt_lv else 0,
                    })
                    log.info(f"[LevelUp] player {_xp_peid} → nivel {_char_xp.level} "
                          f"hp={_cs_xp.current_hp}/{_cs_xp.max_hp} "
                          f"talentos={_tt_lv.available_points if _tt_lv else '?'}")
            # Salva XP/level imediatamente após cada kill — crash do servidor não perde progresso
            _sid_xp = self._player_eid_to_sid.get(_xp_peid, "")
            if _sid_xp and _char_xp and _cs_xp:
                import asyncio as _asyncio_xp
                from server.auth import save_character as _save_xp
                _mgr = getattr(self, "_session_manager", None)
                if _mgr:
                    _sess_xp = _mgr._sessions.get(_sid_xp)
                    if _sess_xp and _sess_xp.char_data.get("id"):
                        srv_data_xp = self.get_player_save_data(_sid_xp)
                        if srv_data_xp:
                            merged_xp = _mgr._build_save_merge(
                                srv_data_xp, _sess_xp.last_client_payload)
                            # Sobrescreve talentos com dados autoritativos do servidor ECS.
                            # last_client_payload ainda tem available_points antigo (notificação
                            # de level-up ainda não chegou ao cliente), então usar ECS evita
                            # salvar 0 pontos quando deveria salvar 1+.
                            from engine.components import TalentTree as _TTxp_save
                            _tt_xp = self.world.get_component(_xp_peid, _TTxp_save)
                            if _tt_xp and isinstance(merged_xp.get("talents"), dict):
                                merged_xp["talents"]["available_points"] = _tt_xp.available_points
                                merged_xp["talents"]["allocated"]        = dict(_tt_xp.allocated)
                            _asyncio_xp.ensure_future(_save_xp(_sess_xp.char_data["id"], merged_xp))
            log.info(f"[XP] player {_xp_peid} ganhou {_xp_amt} XP (mob {entry['mob_eid']})")

        self._process_loot_drops(dt)
        self._tick_trade_distance_check()
        self._tick_duel_distance_check()
        self._tick_arena_queue()
        self._tick_arena_pending()
        self._tick_arena_results_timeout()

        # Detecta novos mobs/NPCs de combate criados pelo SpawnZoneSystem
        # neste tick — gate é Combatant, não Enemy (Sistema de Facções,
        # Fase 4): Enemy sozinho implicaria "hostil ao player", que não é
        # verdade pra um NPC de combate amigável (ex: guarda). Combatant
        # cobre os dois (Enemy sempre implica Combatant, ver
        # engine/entity_factory.py::_build_combat_entity).
        from engine.components import Combatant as _Combatant_reg
        for eid, tm in self.world.get_entities_with(TileMovement):
            if self.world.get_component(eid, _Combatant_reg) and eid not in self._mob_eids \
                    and eid not in self._player_eids.values():
                self._mob_eids.add(eid)
                # CombatState: necessário para aggro do EnemyAISystem
                from engine.components import CombatState as _CS, Visible as _Vis
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
        from engine.components import Projectile as _Proj, Position as _ProjPos
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

        # ── DEBUG Bug2: snapshot de current_hp dos mobs ao final do tick
        # (pós death-sweep) — base de comparação para o próximo tick.
        if MCL.DBG_ENABLED:
            for _hp_eid3 in self._mob_eids:
                _hp_cs3 = self.world.get_component(_hp_eid3, CombatStats)
                if _hp_cs3:
                    self._mob_hp_prev[_hp_eid3] = _hp_cs3.current_hp

        # Limpa deltas de erro do try/except se necessário
        _t0p = _time_tick.perf_counter()
        deltas = self._collect_deltas()
        self._perf_accum["aoi_collect"] = self._perf_accum.get("aoi_collect", 0.0) + (_time_tick.perf_counter() - _t0p)
        self._store_snapshot()

        for cb in self._on_tick_callbacks:
            cb(self.tick_count, deltas)

        # ── Relatório de performance do tick ─────────────────────────────────
        _tick_ms = (_time_tick.perf_counter() - _t_tick_start) * 1000.0
        self._perf_accum["TOTAL"] = self._perf_accum.get("TOTAL", 0.0) + _tick_ms / 1000.0
        self._perf_count += 1
        if _tick_ms > self._PERF_BUDGET_MS:
            # print de propósito: escreve no ARQUIVO de perf (não no console/log)
            print(f"[PERF] tick lento: {_tick_ms:.1f}ms (budget={self._PERF_BUDGET_MS:.0f}ms) "
                  f"tick#{self.tick_count} players={len(self._player_eids)} "
                  f"mobs={len(self._mob_eids)}", file=self._perf_log)
        if self._perf_count >= self._PERF_REPORT_TICKS:
            n = self._perf_count
            total_avg = self._perf_accum.get("TOTAL", 0.0) / n * 1000
            _n_maps_total  = len(self._map_bundles)
            _cur_players_by_map: dict[str, int] = {}
            for _sm in self._player_maps.values():
                _cur_players_by_map[_sm] = _cur_players_by_map.get(_sm, 0) + 1
            _cpu_avg  = self._perf_cpu_sum  / n if n else 0.0
            _cpu_peak = self._perf_cpu_peak
            _f = self._perf_log
            # print de propósito: escreve no ARQUIVO de perf (não no console/log)
            print(f"\n[PERF SRV] {n} ticks | avg={total_avg:.2f}ms/tick | budget={self._PERF_BUDGET_MS:.0f}ms"
                  f" | players={len(self._player_eids)} | maps_ativos_peak={self._perf_peak_maps}/{_n_maps_total}"
                  f" | cpu_proc avg={_cpu_avg:.1f}% peak={_cpu_peak:.1f}%", file=_f)
            _rows = sorted(
                ((k, v) for k, v in self._perf_accum.items() if k != "TOTAL"),
                key=lambda x: -x[1]
            )
            for _lbl, _acc in _rows:
                _avg = _acc / n * 1000
                _pct = (_acc / max(self._perf_accum.get("TOTAL", 1), 1e-9)) * 100
                # Para bundles de mapa, mostra ticks ativos e players atuais
                _extra = ""
                for _bk in self._map_bundles:
                    _bshort = "bnd:" + _bk.split("/")[-1].replace(".csv", "")
                    if _lbl == _bshort:
                        _active_t = self._perf_map_active.get(_bk, 0)
                        _cur_p    = _cur_players_by_map.get(_bk, 0)
                        _extra = f"  [ativo {_active_t}/{n} ticks, {_cur_p}p agora]"
                        break
                print(f"  {_lbl:<22} avg={_avg:>7.3f}ms  {_pct:>5.1f}%{_extra}", file=_f)
            self._perf_accum    = {}
            self._perf_count    = 0
            self._perf_map_active = {}
            self._perf_peak_maps  = 0
            self._perf_cpu_sum    = 0.0
            self._perf_cpu_peak   = 0.0

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
        from engine.components import StatusEffects as _SE
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
        """Returns active status effects for all online players (for client sync).

        Always includes every player (even with empty effects) so the client can
        clear stale effects that were removed server-side (e.g. polymorph broken
        by damage). Without this, the client never calls _sync_player_effects when
        the server clears all effects, and CC icons/state persist indefinitely.
        """
        from engine.components import StatusEffects as _SE, PlayerControlled as _PC
        result = []
        for eid, sfx in self.world.get_entities_with(_SE):
            if not self.world.get_component(eid, _PC):
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
        # Detecta qualquer mudança de HP/max_hp de players antes de montar os deltas.
        # Deve rodar aqui (depois de todos os sistemas do tick) para capturar toda
        # fonte de mudança: skills, DoT, level-up, consumíveis, respawn, etc.
        self._sync_player_hp_dirty()
        self._sync_player_skill_levels_dirty()
        self._process_quest_events()

        # despawned: lista de eids (ints) para compatibilidade
        # despawned_pos: dict eid→(tx,ty) para AOI check de mobs mortos fora de known_eids
        _eids     = [d["eid"] for d in self._despawned_this_tick]
        _pos_map  = {d["eid"]: (d["tx"], d["ty"])
                     for d in self._despawned_this_tick
                     if d["tx"] is not None}

        # DEBUG Bug2: registra o(s) combat event(s) do golpe que despawnou o
        # mob neste mesmo tick — confirma que o servidor GERA o evento (a
        # questão é se _build_update_for_session o repassa ao cliente).
        if MCL.DBG_ENABLED and _eids:
            from engine.components import EntityIdentity as _FHID
            _all_combat = list(self._pending_mob_attacks) + list(self._combat_this_tick)
            for _fh_eid in _eids:
                _fh_evs = [c for c in _all_combat if c.get("target") == _fh_eid]
                _fh_id  = self.world.get_component(_fh_eid, _FHID)
                MCL.log("FATAL_HIT", _fh_eid,
                        _fh_id.name if _fh_id else "?",
                        _fh_id.race if _fh_id else "?",
                        _fh_id.entity_class if _fh_id else "",
                        tick=self.tick_count, combat_events=_fh_evs)

        deltas = {
            "moved":          list(self._moved_this_tick),
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
            "entity_deaths":  list(self._entity_deaths_this_tick),
            "player_revives": list(self._player_revives_this_tick),
            "ghost_states":   list(self._ghost_state_updates_this_tick),
            "visibility_changed": list(self._visibility_changed_this_tick),
        }
        self._moved_this_tick.clear()
        self._spawned_this_tick.clear()
        self._despawned_this_tick.clear()
        self._pending_mob_attacks.clear()
        self._combat_this_tick.clear()
        self._player_deaths_this_tick.clear()
        self._entity_deaths_this_tick.clear()
        self._player_revives_this_tick.clear()
        self._ghost_state_updates_this_tick.clear()
        self._sfx_damage_players.clear()
        self._visibility_changed_this_tick.clear()
        return deltas

    def _store_snapshot(self) -> None:
        """Grava posição (tile_x, tile_y) apenas dos mobs para lag compensation.
        Players são excluídos: lag comp só valida posição de alvo (mob), não do atacante."""
        from engine.components import TileMovement
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
        log.info(f"[WorldServer] encerrado no tick {self.tick_count}")
