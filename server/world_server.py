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
from server.bg_queue_processor import BgQueueProcessorMixin
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
                 "transitions", "tile_validation", "pathfinding",
                 "proximity_systems")

    def __init__(self):
        self.map_file        = ""
        self.tilemap_entity  = -1
        self.systems         = []
        self.ai_systems      = set()  # subconjunto de systems a pular em mapas sem player
        self.transitions     = {}   # (tx,ty) -> {target_map, target_x, target_y}
        self.tile_validation = None
        self.pathfinding     = None
        # Subconjunto de systems que aceita o índice canônico de players
        # por tick (05/08/2026 — ver `players_by_map` em `_tick()`) — cada
        # um fazia seu PRÓPRIO scan de `get_entities_with(...
        # PlayerControlled...)`, alguns por MOB (EnemyAISystem, o mais
        # caro), nunca compartilhando. Mesmo padrão de `ai_systems` (set
        # de instâncias construído em `_load_map_for`).
        self.proximity_systems = set()


class WorldServer(SkillProcessorMixin, CombatProcessorMixin, RespawnMixin, LootProcessorMixin,
                   SpellCompletionMixin, TradeProcessorMixin, DuelProcessorMixin, PartyProcessorMixin,
                   PvpZoneProcessorMixin, MatchProcessorMixin, BgQueueProcessorMixin):

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
        # Harvestable (Fase M1, revisão 2, 25/07/2026) — entidades reais,
        # paradas, SEM Combatant (por isso um set PRÓPRIO, não misturado
        # em _mob_eids — evita quebrar os ~12 outros usos de _mob_eids
        # que assumem CombatStats/Combatant presentes). Loot em si
        # continua em self._corpses (ver Harvestable.corpse_id).
        self._harvestable_eids: set[int] = set()
        # corpse_id → entity ID da entidade real correspondente (usado pra
        # remover a entidade quando um nó de ZONA se esgota — Fase "Zona de
        # itens", 25/07/2026; harvestable de posição fixa nunca é removido,
        # então nunca precisa disso, mas fica preenchido igual por
        # simplicidade/consistência).
        self._harvestable_hid_to_eid: dict[int, int] = {}

        # Zona de itens (25/07/2026, pedido do usuário — decisões
        # confirmadas via AskUserQuestion): mistura de sub-tipos na mesma
        # zona, 1 cooldown pra zona inteira, nó esgotado SOME e um novo
        # nasce em posição ALEATÓRIA dentro do raio (diferente do
        # harvestable de posição fixa, que fica visível vazio — mais
        # parecido com nó de recurso "migrando" tipo SpawnZone de mob).
        # zone_id → {x, y, radius, respawn_cooldown, requires_quest, map,
        #            spawns: [{name, sprite, items, coins, count}, ...]}
        self._harvestable_zones: dict[int, dict] = {}
        # zone_id → {hid: subtype_index} — nós vivos desta zona agora
        self._harvestable_zone_active: dict[int, dict] = {}
        # zone_id → list [subtype_index, timer_restante] — um timer por
        # slot vago (inicial ou reabertura após esgotar)
        self._harvestable_zone_timers: dict[int, list] = {}
        self._next_harvestable_zone_id: int = 1

        # Torre (29/07/2026, pedido do usuário) — respawn exato (NUNCA via
        # SpawnZone, que sempre sorteia um tile aleatório dentro de um
        # raio — errado pra uma estrutura que precisa nascer sempre no
        # MESMO tile). Chave = (map_file, spawn_tile_x, spawn_tile_y),
        # única por torre colocada no mapa. Valor: {tower_key, faction_id,
        # regen_enabled, respawn_s, level, timer}. Ver register_tower_
        # respawn()/_tick_tower_respawns().
        self._tower_respawn_timers: dict[tuple, dict] = {}

        # Minion de lane estilo MOBA (30/07/2026, pedido do usuário).
        # _minion_lanes: map_file -> lista de configs de lane (registradas
        # em _create_minion_lanes, chamado de _load_map_for). Só CONFIG —
        # não cria minion na hora. _minion_wave_timers: (map_file,faction,
        # lane_id) -> segundos acumulados; a CHAVE PRESENTE = lane ATIVA (só
        # existe depois que _activate_minion_lanes é chamado — combate de
        # arena de verdade liberado, match_processor.py::_tick_arena_pending
        # — nunca durante o preparo). Ver _tick_minion_waves().
        self._minion_lanes: dict[str, list[dict]] = {}
        self._minion_wave_timers: dict[tuple, float] = {}
        # Fila de spawn escalonado da wave (01/08/2026, bug real relatado
        # pelo usuário no primeiro playtest de verdade num mapa MOBA: os 7
        # minions nascendo no MESMO tick, empilhados nos offsets de
        # _MINION_WAVE_OFFSETS, travavam uns aos outros — cada um via os
        # outros como dynamic_obstacle antes de ter espaço pra se afastar).
        # _tick_minion_waves só ENFILEIRA aqui (rota já calculada); quem
        # cria a entidade de fato é _tick_minion_spawn_queue, um por vez,
        # a cada MINION_SPAWN_STAGGER_S.
        self._minion_spawn_queue: list[dict] = []

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
        # Fase M2 (25/07/2026): harvestable de posição fixa reabastecido
        # (respawn_s>0) — consumido pelo SessionManager (consume_
        # harvestable_refills, mesmo padrão de consume_loot_notifications)
        # pra mandar LOOT_AVAILABLE pra quem já conhece a entidade.
        self._pending_harvestable_refill: list[dict] = []
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

        # Arena 1x1/2x2/3x3 (Fase G leva 1 + Fase H, ver
        # server/match_processor.py::ARENA_MODES) — 1 fila FIFO por modo
        # (party_ids nos modos de time, o próprio eid no modo solo 1x1) +
        # partidas ativas (instância privada por partida, time = componente
        # Faction temporário no player).
        from server.match_processor import ARENA_MODES as _ARENA_MODES_INIT
        self._arena_queues: dict[str, list] = {mid: [] for mid in _ARENA_MODES_INIT}
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

        # Fila REAL de matchmaking da BG estilo MOBA (04/08/2026, ver
        # server/bg_queue_processor.py) — fila ÚNICA (sem chave por modo,
        # diferente da Arena: token = ("solo", eid) ou ("party", party_id),
        # a fila decide o tamanho do time sozinha) + partidas ativas
        # (instância privada POR PARTIDA, `template::match_id`).
        self._bg_queue: list[tuple] = []
        self._bg_active_matches: dict[str, dict] = {}
        self._player_bg_match_id: dict[int, str] = {}
        self._next_bg_match_id: int = 1
        self._pending_bg_invite: dict[int, str] = {}
        self._bg_match_found_events_this_tick: list[dict] = []
        self._bg_match_start_events_this_tick: list[dict] = []
        self._bg_gate_open_events_this_tick: list[dict] = []
        self._bg_match_leave_events_this_tick: list[dict] = []
        self._bg_match_result_events_this_tick: list[tuple] = []

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
        from engine.world_systems import TileMovementSystem as _TMS, ProjectileSystem as _ProjSys, TowerSystem as _TowerSys
        self._global_tms           = _TMS(self.world)
        self._global_proj_sys      = _ProjSys(self.world, screen=None)
        self._global_sfx_sys       = _ServerStatusEffectSystem.build(self.world, self)
        self._status_effect_system = self._global_sfx_sys  # alias de compat
        # Torre (29/07/2026) — sweep global, não por-bundle (torre não
        # precisa de map_filter — mesmo princípio de _tick_harvestable_
        # respawn/_tick_harvestable_zones, um sweep só filtrando por
        # MapLocation internamente onde precisa). get_tilemap_for_map
        # injeta a resolução de bundle→tilemap (só o WorldServer conhece
        # _map_bundles — engine/world_systems.py é headless/compartilhado).
        self._tower_system = _TowerSys(
            self.world, get_tilemap_for_map=self._get_tilemap_for_map_file)

        # Minion de lane estilo MOBA (30/07/2026, pedido do usuário) — mesmo
        # princípio de Torre acima (sweep global, não por-bundle). Precisa
        # TAMBÉM de get_pathfinding_for_map (Torre nunca se move; minion
        # sim, repathing tile-a-tile ao perseguir/retornar/desviar de
        # obstáculo — ver _walk_toward, engine/world_systems.py).
        from engine.world_systems import MinionSystem as _MinionSys
        self._minion_system = _MinionSys(
            self.world, get_tilemap_for_map=self._get_tilemap_for_map_file,
            get_pathfinding_for_map=self._get_pathfinding_for_map_file)

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

        # ── Profiler de tick ─────────────────────────────────────────────────
        # Acumula tempo por seção; resumo impresso a cada _PERF_REPORT_TICKS ticks.
        self._perf_accum:       dict[str, float] = {}
        # Breakdown SÓ do tick atual (04/08/2026, pedido do usuário — ver
        # ARQUITETURA_ONLINE.md §34.74.29: precisa saber qual sistema causou
        # UM pico específico de latência, não só a média diluída num
        # relatório de ~10s. Resetado no início de CADA tick por _tick();
        # `_perf_mark` grava em `_perf_accum` (média) E aqui (pico) ao mesmo
        # tempo — chokepoint único, nunca duplicar a leitura de perf_counter
        # por seção nova.
        self._perf_tick_now:    dict[str, float] = {}
        self._perf_count:       int = 0
        self._perf_map_active:  dict[str, int]   = {}  # map → ticks com ≥1 player
        self._perf_peak_maps:   int = 0                # pico de mapas simultâneos ativos
        self._perf_cpu_sum:     float = 0.0            # soma de % CPU por tick
        self._perf_cpu_peak:    float = 0.0            # pico de % CPU no período
        # RSS (memória residente) — instrumentação 05/08/2026 pra investigar
        # picos isolados de 75-190ms concentrados SÓ em ai_bundles/bnd:map_1,
        # crescendo com a duração da sessão mesmo com players/mobs estáveis
        # (log real analisado com o usuário). Hipótese: acúmulo de memória
        # (garbage cíclico, já que `gc.disable()` em main.py só coleta via
        # `_gc_srv.collect()` manual a cada ~10s em run()) causando pausa de
        # alocador/SO bem no meio do trabalho mais pesado do tick. RSS
        # amostrado 1x/tick (barato, só um syscall), reportado no resumo
        # periódico pra ver se cresce de forma anormal entre relatórios.
        self._perf_rss_sum:     float = 0.0            # soma de RSS (bytes) por tick
        self._perf_rss_peak:    float = 0.0            # pico de RSS (bytes) no período
        # Contador de mobs "ativos" (CHASING/ATTACKING/AGGRO_DELAY) —
        # instrumentação 05/08/2026 pra confirmar a hipótese de que o custo
        # crescente de sys:EnemyAISystem ao longo de uma MESMA sessão (RSS
        # estável, população de mob estável) vem de mobs progressivamente
        # saindo do caminho barato (IDLE/sleep-check) e entrando no caminho
        # caro (leash/tiles de ataque candidatos/orçamento de pathfinding)
        # conforme agroam, não de vazamento. Contado de graça no loop que já
        # existe (snapshot de estado pré-update) — nunca um scan novo.
        self._perf_active_mobs_sum:  float = 0.0
        self._perf_active_mobs_peak: int   = 0
        self._perf_active_now:       int   = 0
        # Mobs que sobraram do pré-filtro do Achado 5 (§34.74.40) por tick
        # — soma de `EnemyAISystem._last_active_mob_count` de todos os
        # bundles ativos. Diferente de `_perf_active_mobs_sum` acima (que
        # só conta mob em CHASING/ATTACKING/AGGRO_DELAY): este conta TODO
        # mob que passou pro corpo do loop, incluindo os IDLE que só
        # acordaram por estar no raio.
        self._perf_ai_active_mobs_sum:  float = 0.0
        self._perf_ai_active_mobs_peak: int   = 0
        self._PERF_REPORT_TICKS = 300          # ~10s a 30 ticks/s
        self._PERF_BUDGET_MS    = 1000.0 / 30  # 33.3ms por tick
        # Threshold pra imprimir o BREAKDOWN por seção na linha de "tick
        # lento" (04/08/2026, pedido do usuário — travadas percebidas em
        # jogo, ping >600ms na HUD). Bem acima de _PERF_BUDGET_MS de
        # propósito: qualquer tick real sob carga passa um pouco de
        # 33.3ms (isso sozinho não é o "freeze" que o usuário sente) — o
        # breakdown só vale a pena pra picos de verdade, senão o arquivo
        # de log vira ruído a cada tick um pouco mais pesado.
        self._PERF_BREAKDOWN_MS = 100.0
        # Fase 5 de escala (06/08/2026, ver ARQUITETURA_ONLINE.md
        # Sec.34.74.44) - streak de ticks CONSECUTIVOS acima do budget.
        # Diferente de "tick lento" (incidente isolado, ja logado acima):
        # isso detecta sobrecarga SUSTENTADA - estado em que o loop de
        # run() nunca cai no branch que faz `await asyncio.sleep()`
        # (nenhum yield pro event loop, WebSocket para de responder).
        # So observabilidade - nao muda comportamento/timing nenhum.
        self._perf_overbudget_streak: int = 0
        self._perf_degraded: bool = False
        self._PERF_DEGRADED_STREAK_THRESHOLD = 10  # ~0.33s a 30 ticks/s
        # Estado do gc.collect() manual periódico - movido de local de
        # run() pra campo de instância (Fase 5, 06/08/2026) porque cada
        # iteração do loop virou uma chamada própria de
        # _run_tick_or_sleep() (extraído pra ser testável isoladamente),
        # sem estado de loop sobrevivendo entre chamadas.
        self._gc_ticks_since_collect = 0
        self._GC_EVERY_TICKS = TICK_RATE * 10  # coleta manual a cada ~10s
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
        self._create_harvestables_for_map(spawn_points, key)
        self._create_harvestable_zones_for_map(spawn_points.get("harvestable_zones", []), key)
        self._create_towers(spawn_points.get("towers", []), key)
        self._create_minion_lanes(spawn_points.get("minion_lanes", []), key)

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
        # tile_validation entra aqui também (04/08/2026, pedido do usuário —
        # "não tem como pular se não precisa?"): seu cache (_occupied) só é
        # consultado por pathfinding/AI (EnemyAISystem/TauntSystem), que já
        # ficam fora do ar sem player neste mapa — sem gate aqui ele
        # reconstruía um cache global (get_entities_with(TileMovement) de
        # TODO o mundo) que ninguém lia, todo tick, em mapa vazio.
        bundle.ai_systems      = {enemy_ai_system, enemy_ab_system, tile_validation}
        bundle.proximity_systems = {enemy_ai_system, enemy_ab_system, _spawn_sys}
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

    def _create_harvestables_for_map(self, spawn_points: dict, map_key: str) -> None:
        """Cria itens de mapa saqueáveis (planta/pergaminho/ferramenta —
        Fase M1, revisão 2, 25/07/2026) a partir de
        spawn_points["harvestables"]. Loot em si continua em
        self._corpses[hid] (dict simples, owner_eid=-1 público,
        no_decay=True permanente até M2 trazer respawn — ver
        loot_processor.py) — ISSO NÃO MUDOU. O que mudou: cada
        harvestable agora TAMBÉM ganha uma entidade ECS real
        (create_harvestable_entity — Position+TileMovement+Renderable+
        Harvestable, sem Combatant/AIControlled) pra ganhar colisão real
        (TileValidationSystem, automático via TileMovement) e Y-sort
        real (RenderSystem, automático via Position+Renderable) — sem
        isso, o jogador atravessava por cima e o desenho ficava sempre
        atrás do player (bug relatado pelo usuário na 1a versão). Formato
        de item idêntico a QuestReward.items ("item_key" ou (item_key,
        stack)), resolvido via engine.quest_logic (mesmas funções da
        recompensa de quest)."""
        from engine.entity_factory import create_harvestable_entity

        for h in spawn_points.get("harvestables", []):
            hid = self._next_corpse_id
            self._next_corpse_id += 1
            name = h.get("name", "Objeto")
            template_items = h.get("items", [])
            template_coins = h.get("coins", 0)
            granted = self._resolve_harvestable_items(template_items, name)
            self._corpses[hid] = {
                "tx": h["x"], "ty": h["y"], "owner_eid": -1,
                "items": granted, "coins": template_coins,
                "timer": float("inf"), "map": map_key,
                "mob_name": "", "mob_race": "", "quest_rolls": {},
                "no_decay": True, "name": name,
                # Fase M2 (25/07/2026) — respawn_s=0/ausente = nunca
                # reabastece (comportamento original da Fase M1). O
                # template (item_key cru, não resolvido) é guardado à
                # parte pra _tick_harvestable_respawn conseguir re-rolar
                # os itens do zero ao reabastecer, sem duplicar a lógica
                # de resolução de fábrica.
                "respawn_s": float(h.get("respawn_s", 0)),
                "empty_timer": 0.0,
                "_template_items": template_items,
                "_template_coins": template_coins,
            }
            eid = create_harvestable_entity(
                self.world, h["x"], h["y"], corpse_id=hid,
                sprite_id=h.get("sprite", ""), name=name,
                requires_quest=h.get("requires_quest", ""))
            self._harvestable_eids.add(eid)
            self._harvestable_hid_to_eid[hid] = eid

    def _resolve_harvestable_items(self, template_items: list, context_name: str = "Objeto") -> list:
        """Resolve uma lista de item_key (ou (item_key, stack)) em itens
        serializados prontos pra `self._corpses[hid]["items"]` — extraído
        de `_create_harvestables_for_map` pra ser reaproveitado por
        `_tick_harvestable_respawn` (Fase M2) sem duplicar a resolução
        de fábrica."""
        import engine.quest_logic as _hq_logic
        from server.server_death_handler import _serialize_item as _hq_serialize
        granted = []
        for entry in template_items:
            item_key, stack = _hq_logic.normalize_reward_entry(entry)
            factory = _hq_logic.resolve_reward_item_factory(item_key)
            if factory is None:
                log.warning(f"[Harvestable] item_key '{item_key}' de "
                           f"'{context_name}' não existe em nenhum "
                           f"catálogo — ignorado")
                continue
            item = factory()
            item.stack = max(1, min(stack, item.max_stack))
            granted.append(_hq_serialize(item))
        return granted

    def _tick_harvestable_respawn(self, dt: float) -> None:
        """Fase M2 (25/07/2026): harvestable de posição fixa com
        `respawn_s > 0` reabastece sozinho depois de esvaziar POR
        COMPLETO (itens E moedas — decisão do usuário: saque parcial não
        conta, só reseta o timer). Reset TOTAL ao reabastecer: comuns E
        `quest_rolls` (decisão do usuário: todo mundo ganha uma chance
        nova, mesmo quem já tinha resolvido o sorteio condicional antes).
        A entidade NUNCA é removida — fica visível vazia até reabastecer
        (bug corrigido antes desta fase, a pedido do usuário)."""
        for hid, corpse in self._corpses.items():
            respawn_s = corpse.get("respawn_s", 0)
            if respawn_s <= 0:
                continue
            is_empty = not corpse.get("items") and corpse.get("coins", 0) <= 0
            if not is_empty:
                corpse["empty_timer"] = 0.0
                continue
            corpse["empty_timer"] = corpse.get("empty_timer", 0.0) + dt
            if corpse["empty_timer"] >= respawn_s:
                corpse["items"] = self._resolve_harvestable_items(
                    corpse.get("_template_items", []), corpse.get("name", "Objeto"))
                corpse["coins"] = corpse.get("_template_coins", 0)
                corpse["quest_rolls"] = {}
                corpse["empty_timer"] = 0.0
                self._pending_harvestable_refill.append({
                    "hid": hid, "tx": corpse["tx"], "ty": corpse["ty"],
                    "map": corpse.get("map"),
                })

    def _create_harvestable_zones_for_map(self, zones_data: list, map_key: str) -> None:
        """Zona de itens (25/07/2026) — a partir de
        spawn_points["harvestable_zones"] (já processado por map_loader.py,
        cada entrada com uma lista `spawns` de sub-tipos misturados, mesmo
        padrão de `spawn_zones` de mob). Registra a zona (metadados +
        templates dos sub-tipos) e enfileira um timer de preenchimento
        inicial por slot (escalonado, evita spike de criação — mesmo
        princípio do preenchimento inicial de `SpawnZoneSystem`). Os nós em
        si só nascem em `_tick_harvestable_zones`, não aqui."""
        for z in zones_data:
            zone_id = self._next_harvestable_zone_id
            self._next_harvestable_zone_id += 1
            self._harvestable_zones[zone_id] = {
                "x": z.get("x", 0), "y": z.get("y", 0),
                "radius": z.get("radius", 10),
                "respawn_cooldown": float(z.get("respawn_cooldown", 60.0)),
                "requires_quest": z.get("requires_quest", ""),
                "map": map_key,
                "spawns": z.get("spawns", []),
            }
            self._harvestable_zone_active[zone_id] = {}
            timers = []
            for idx, sub in enumerate(z.get("spawns", [])):
                for i in range(sub.get("count", 1)):
                    timers.append([idx, i * 0.15 + random.uniform(0.0, 0.05)])
            self._harvestable_zone_timers[zone_id] = timers

    def _get_tilemap_for_map_file(self, map_file: str):
        """Resolve o tilemap do MAPA pedido via `_map_bundles` — injetado
        no `TowerSystem` (`engine/world_systems.py`) como callback, já
        que aquele módulo é headless/compartilhado e não conhece
        `_map_bundles` (server-only). Mesmo princípio de
        `_pick_harvestable_zone_tile` abaixo."""
        bundle = self._map_bundles.get(map_file)
        return bundle.pathfinding._get_tilemap_component() if bundle else None

    def _get_pathfinding_for_map_file(self, map_file: str):
        """Resolve o `PathfindingSystem` do MAPA pedido via `_map_bundles`
        — injetado no `MinionSystem` (engine/world_systems.py) como
        callback, mesmo princípio de `_get_tilemap_for_map_file` acima
        (Torre não precisa disso — nunca se move; minion sim, pra
        repathing tile-a-tile ao perseguir/retornar)."""
        bundle = self._map_bundles.get(map_file)
        return bundle.pathfinding if bundle else None

    def _get_tower_tiles_for_map(self, map_file: str) -> set:
        """Tiles ocupados por torres VIVAS no mapa/instância — usado como
        `dynamic_obstacles` no cálculo de rota de minion (01/08/2026, ver
        comentário em `_tick_minion_waves`). Torre morta some do world
        (respawn é entidade NOVA, `_tick_tower_respawns`), então já sai
        sozinha do resultado sem checagem extra de HP aqui."""
        from engine.components import Tower as _TwrTiles, Position as _PosTiles, \
            MapLocation as _MLTiles
        tiles: set = set()
        for eid, _twr, pos, ml in self.world.get_entities_with(_TwrTiles, _PosTiles, _MLTiles):
            if ml.map_file != map_file:
                continue
            tiles.add((int(pos.x // TILE_SIZE), int(pos.y // TILE_SIZE)))
        return tiles

    def _create_towers(self, towers_data: list, map_file: str) -> None:
        """Cria torres estáticas (29/07/2026, pedido do usuário) a partir
        de `{mapa}_entities.json::towers` — mesmo padrão de
        `_create_combat_npcs`, mas via `create_tower()` (sem
        `AIControlled` — ver docstring de `engine/components.py::Tower`).
        Sincroniza pro cliente pelo MESMO pipeline genérico de mob
        (`Combatant`+`TileMovement` → `_mob_eids`, ver sweep de
        registro em `_tick()`) — nenhum protocolo novo."""
        from engine.entity_factory import create_tower
        for t in towers_data:
            create_tower(
                self.world, t["x"], t["y"], t["tower_key"],
                faction_id=t.get("faction", "monstros_hostis"),
                respawnable=t.get("respawnable", False),
                respawn_s=float(t.get("respawn_s", 0)),
                regen_enabled=t.get("regen_enabled", False),
                level=t.get("level", 1),
                is_nexus=t.get("is_nexus", False),
            )
            # MapLocation é anexado automaticamente pelo diff antes/depois
            # de entidades em _load_map_for (mesmo mecanismo de
            # _create_combat_npcs) — nenhuma linha extra necessária aqui.

    def _create_minion_lanes(self, lanes_data: list, map_file: str) -> None:
        """Registra as lanes de `{mapa}_entities.json::minion_lanes` — só
        CONFIG (spawn/alvo/intervalo/facção/lane_id), não cria nenhum
        minion agora. Ativação (início da contagem de wave) é separada,
        depois que o combate libera de verdade (`_activate_minion_lanes`,
        chamado por `match_processor.py::_tick_arena_pending` no mesmo
        momento em que o portão físico abre — nunca durante o preparo)."""
        if lanes_data:
            self._minion_lanes[map_file] = list(lanes_data)

    # Ordem de disparo das lanes por GRUPO (03/08/2026, pedido do usuário,
    # ao revisar o fix de travamento/keepalive-timeout de _tick_minion_waves
    # abaixo): top dos 2 times juntos, DEPOIS bot dos 2 times, DEPOIS mid dos
    # 2 times — nunca um time isolado (senão o outro time daquela MESMA lane
    # ganharia alguns segundos de vantagem de timing, injusto). Cada grupo
    # atrasa `_LANE_GROUP_STAGGER_S` a mais que o anterior — espalha o custo
    # (mesmo já reduzido pelo cache de rota por lane) em vez de tudo disparar
    # no MESMO tick pra sempre (era o caso antes: todas as lanes começavam
    # com o MESMO timer 0.0, então SEMPRE coincidiam, toda wave).
    _LANE_GROUP_ORDER: tuple = ("top", "bot", "mid")
    _LANE_GROUP_STAGGER_S: float = 1.5

    def _activate_minion_lanes(self, map_file: str) -> None:
        """Inicia o timer de wave de cada lane registrada pra este
        mapa/instância — chamado 1x quando o combate libera de verdade.
        Chave = (map_file, faction, lane_id) — lane_id distingue rotas do
        MESMO time no MESMO mapa (top/mid/bot), senão colidiriam na mesma
        chave e só a primeira jamais dispararia. Timer inicial NEGATIVO
        (não 0.0) pro grupo da lane — ver `_LANE_GROUP_ORDER`/
        `_LANE_GROUP_STAGGER_S` — atrasa o 1º disparo desse grupo, e o
        atraso se PROPAGA pra sempre (o `elapsed - wave_interval_s` do
        reset em `_tick_minion_waves` carrega o resto adiante), sem
        precisar tocar na lógica de disparo em si."""
        for lane in self._minion_lanes.get(map_file, []):
            key = (map_file, lane["faction"], lane.get("lane_id", "default"))
            if key not in self._minion_wave_timers:
                try:
                    _group_idx = self._LANE_GROUP_ORDER.index(lane.get("lane_id", "default"))
                except ValueError:
                    _group_idx = 0
                self._minion_wave_timers[key] = -(_group_idx * self._LANE_GROUP_STAGGER_S)

    def register_tower_respawn(self, tower, faction_id: str, map_file: str, level: int) -> None:
        """Chamado por `ServerDeathHandler.update()` quando uma entidade
        com componente `Tower` morre, ANTES de ser removida do world —
        único momento em que dá pra capturar `faction_id`/`level` (o
        componente `Tower` em si não guarda esses dois, eles vivem em
        `Faction`/`EntityIdentity`). Se `tower.respawnable`, agenda um
        timer exato (NUNCA via SpawnZone — ver comentário do dict no
        __init__) pra recriar a MESMA torre no MESMO tile depois de
        `tower.respawn_s`."""
        if not tower.respawnable:
            return
        key = (map_file, tower.spawn_tile_x, tower.spawn_tile_y)
        self._tower_respawn_timers[key] = {
            "tower_key":     tower.tower_key,
            "faction_id":    faction_id,
            "regen_enabled": tower.regen_enabled,
            "respawn_s":     tower.respawn_s,
            "level":         level,
            "timer":         0.0,
        }

    def _tick_tower_respawns(self, dt: float) -> None:
        """Decrementa os timers de `_tower_respawn_timers` e recria a
        torre (entidade NOVA, não reaproveita o eid morto — mesmo
        princípio do harvestable de zona, `_tick_harvestable_zones`)
        exatamente no tile original ao completar. `MapLocation` precisa
        ser anexado manualmente aqui (diferente de `_create_towers`,
        chamado durante `_load_map_for` — o diff antes/depois de
        entidades só cobre a CARGA do mapa, não criação em runtime;
        mesmo detalhe já resolvido em `_spawn_harvestable_zone_node`)."""
        if not self._tower_respawn_timers:
            return
        from engine.entity_factory import create_tower
        from engine.components import MapLocation as _MLtw
        done_keys = []
        for key, info in self._tower_respawn_timers.items():
            info["timer"] += dt
            if info["timer"] < info["respawn_s"]:
                continue
            map_file, tx, ty = key
            eid = create_tower(
                self.world, tx, ty, info["tower_key"],
                faction_id=info["faction_id"],
                respawnable=True, respawn_s=info["respawn_s"],
                regen_enabled=info["regen_enabled"], level=info["level"],
            )
            self.world.add_component(eid, _MLtw(map_file))
            done_keys.append(key)
        for key in done_keys:
            del self._tower_respawn_timers[key]

    # Composição fixa de uma wave (30/07/2026, pedido do usuário; reduzida
    # 04/08/2026, mesmo pedido — 7→4 por lane, 42→24 minions simultâneos
    # com as 6 lanes ativas, pra baixar a carga de combate quando 2 waves
    # se cruzam no meio da lane): 1 melee + 2 à distância + 1 à distância
    # tier raro. Offsets evitam empilhar os minions no MESMO tile ao
    # nascer (não é posição real de formação, só um espalhamento pequeno
    # ao redor do spawn_tile).
    _MINION_WAVE_COMPOSITION = (
        ("minion_melee", 1), ("minion_ranged", 2), ("minion_ranged_raro", 1),
    )
    _MINION_WAVE_OFFSETS = (
        (0, 0), (1, 0), (-1, 0), (0, 1), (1, 1), (-1, 1), (0, 2),
    )
    # Atraso entre a criação de cada minion da MESMA wave (01/08/2026,
    # pedido do usuário — ver _minion_spawn_queue/_tick_minion_spawn_queue).
    MINION_SPAWN_STAGGER_S = 0.5

    def _compute_team_avg_level(self, map_file: str, faction_id: str, default_level: int = 1) -> int:
        """Level médio (arredondado) dos players REAIS do time `faction_id`
        presentes em `map_file` agora — recalculado a CADA wave (pedido do
        usuário, 30/07/2026), não fixado 1x no início do combate. Sem
        nenhum player do time na instância (ex: lane ainda sem ninguém
        perto, ou fallback de teste), usa `default_level` (o valor
        estático da própria lane). Generaliza pra battlefield/dungeon
        futuro de propósito — não depende de bookkeeping específico da
        arena (`_player_match_id`/`match['team_a']`), só de
        `Faction`+`MapLocation`+`CharacterStats` já anexados ao player."""
        from engine.components import Faction as _FacAvg, CharacterStats as _CSAvg, \
            MapLocation as _MLAvg, PlayerControlled as _PCAvg
        levels = []
        for eid, _pc, fac, ml, char in self.world.get_entities_with(
                _PCAvg, _FacAvg, _MLAvg, _CSAvg):
            if ml.map_file != map_file or fac.faction_id != faction_id:
                continue
            levels.append(char.level)
        if not levels:
            return default_level
        return round(sum(levels) / len(levels))

    def _tick_minion_waves(self, dt: float) -> None:
        """Pra cada lane ATIVA (chave em `_minion_wave_timers` — só
        existe depois de `_activate_minion_lanes`), acumula `dt`; ao
        passar de `wave_interval_s`, CALCULA a rota dos minions da leva
        (composição fixa acima) e ENFILEIRA a criação de cada um em
        `_minion_spawn_queue` (quem cria de fato é
        `_tick_minion_spawn_queue`, escalonado por `MINION_SPAWN_STAGGER_S`
        — bug real relatado pelo usuário, 01/08/2026: os 7 nascendo no
        MESMO tick, mesmo com offsets de posição, travavam uns aos
        outros — MinionSystem via os vizinhos recém-nascidos como
        dynamic_obstacle antes de terem espaço pra sair do aglomerado).
        DIFERENTE do respawn de torre (`_tick_tower_respawns`, 1 morte→1
        timer→1 respawn no MESMO tile): aqui é um timer POR LANE,
        incondicional (dispara sozinho no relógio, não em resposta a
        morte) — minion morto NÃO agenda respawn individual, só a
        próxima wave programada cria minions novos."""
        if not self._minion_wave_timers:
            return
        for key, elapsed in list(self._minion_wave_timers.items()):
            map_file, faction, lane_id = key
            lane = next((l for l in self._minion_lanes.get(map_file, [])
                        if l["faction"] == faction
                        and l.get("lane_id", "default") == lane_id), None)
            if lane is None:
                del self._minion_wave_timers[key]
                continue
            elapsed += dt
            wave_interval_s = float(lane.get("wave_interval_s", 45.0))
            if elapsed < wave_interval_s:
                self._minion_wave_timers[key] = elapsed
                continue
            self._minion_wave_timers[key] = elapsed - wave_interval_s

            pathfinding = self._get_pathfinding_for_map_file(map_file)
            if pathfinding is None:
                continue
            # Level médio do time, recalculado a CADA wave (pedido do
            # usuário, 30/07/2026) — nunca o valor estático da lane
            # quando há player de verdade pra medir.
            wave_level = self._compute_team_avg_level(
                map_file, faction, default_level=lane.get("level", 1))
            sx, sy = lane["spawn_tile"]
            waypoints = lane["target_tile"]  # sempre lista de (x,y), ver map_loader.py
            # Torres como obstáculo da ROTA (01/08/2026, bug real relatado
            # pelo usuário: minion travava "atrás da torre" sem NUNCA
            # desviar). Raiz: a rota é calculada 1x aqui, sem saber onde
            # as torres estão — se o A* (livre, só olhando terreno) cruza
            # o tile exato de uma torre, o minion recebe esse tile como
            # PRÓXIMO PASSO da rota em ADVANCING; `_walk_toward` só pede
            # ao pathfinder "chegar no PRÓXIMO tile" (distância 1) — sem
            # espaço nenhum pra desviar quando esse único tile está
            # ocupado (torre nunca sai do lugar, então nunca destrava
            # sozinho). Corrigido na ORIGEM: passa as torres do mapa como
            # `dynamic_obstacles` pro cálculo da rota, então ela nunca
            # atravessa o tile de uma torre pra começo de conversa — perto
            # de uma torre (pra brigar de verdade) continua funcionando
            # normal via FIGHTING (que persegue por chebyshev, nunca tenta
            # pisar no tile exato do alvo, ver _walk_toward na FIGHTING).
            tower_tiles = self._get_tower_tiles_for_map(map_file)
            # tile_validation do MESMO bundle do pathfinding acima — usado só
            # pra validar os offsets de spawn abaixo (03/08/2026, bug real:
            # 2 dos 7 offsets de _MINION_WAVE_OFFSETS caem em parede em
            # lanes com base "estreita" tipo top/bot — mid nunca pegava
            # porque o spawn ali tem mais espaço livre ao redor). Nunca
            # `None` se `pathfinding` também não é (mesmo bundle).
            _bundle_mw = self._map_bundles.get(map_file)
            tile_validation = _bundle_mw.tile_validation if _bundle_mw else None

            # 1 find_path por PERNA, calculado UMA VEZ POR LANE (não por
            # minion) — pedido do usuário, 30/07/2026: lane com curva
            # (top/bot) precisa de waypoints intermediários; mid (reta) usa
            # só 1, que vira o comportamento de sempre. `manhattan_limit=
            # None`: o default (60) rejeitaria silenciosamente uma perna
            # longa num mapa MOBA de verdade. `max_nodes=4000` (03/08/2026,
            # bug real relatado pelo usuário — "só testei a rota do mid,
            # quero liberar todas"): o default (300, engine/world_systems.py)
            # é MUITO baixo pras lanes com curva (top/bot) deste mapa
            # 100×100 — cada perna precisa de ~450-590 nós explorados; mid é
            # reta e cabia em ~100-270, por isso era a ÚNICA que sempre
            # funcionava. Falha vira log (nunca mais silenciosa).
            #
            # CACHE POR LANE (03/08/2026, mesmo pedido — travamento real: os
            # 7 minions de uma wave têm offsets de ±1-2 tiles só, TODOS
            # convergindo pro MESMO destino pelos MESMOS waypoints — calcular
            # o A* individualmente pra cada um (até 7×2=14 buscas por lane,
            # 84 por wave com as 6 lanes disparando no mesmo tick) era
            # trabalho redundante síncrono suficiente pra travar o loop do
            # asyncio por tempo maior que o timeout de keepalive ping da lib
            # `websockets` — conexão caía sozinha achando que o servidor
            # sumiu. Agora só 1 rota "tronco" por lane (a partir do tile
            # PURO da lane, sem offset); cada minion prepende só o próprio
            # tile de nascimento (com offset) na frente do tronco — visual
            # idêntico (offsets são pequenos), 1/7 do custo total.
            lane_route_tail: list = []
            leg_start = (sx, sy)
            route_ok = True
            for wp in waypoints:
                leg_path = pathfinding.find_path(
                    leg_start, wp, dynamic_obstacles=tower_tiles,
                    manhattan_limit=None, max_nodes=4000)
                if not leg_path:
                    route_ok = False
                    break
                lane_route_tail.extend(leg_path)
                leg_start = wp
            if not route_ok:
                log.warning(
                    f"[MinionWave] rota falhou: {map_file} {faction}/{lane_id} "
                    f"perna {leg_start}->{wp} sem caminho (max_nodes=4000)")
                continue

            offset_idx = 0
            for minion_key, count in self._MINION_WAVE_COMPOSITION:
                for _ in range(count):
                    dx, dy = self._MINION_WAVE_OFFSETS[offset_idx % len(self._MINION_WAVE_OFFSETS)]
                    offset_idx += 1
                    spawn_x, spawn_y = sx + dx, sy + dy
                    # Offset cai em parede (base estreita) → volta pro tile
                    # PURO da lane (sempre validado por construção, é onde
                    # o minion nasceria sem offset nenhum) em vez de deixar
                    # o minion nascer preso dentro de uma parede.
                    if (tile_validation is not None
                            and not tile_validation.is_tile_walkable(-1, spawn_x, spawn_y)):
                        spawn_x, spawn_y = sx, sy
                    route = [(spawn_x, spawn_y)] + lane_route_tail
                    self._minion_spawn_queue.append({
                        "map_file": map_file, "faction": faction,
                        "minion_key": minion_key, "level": wave_level,
                        "spawn_x": spawn_x, "spawn_y": spawn_y, "route": route,
                        "delay": (offset_idx - 1) * self.MINION_SPAWN_STAGGER_S,
                    })

    def _tick_minion_spawn_queue(self, dt: float) -> None:
        """Drena `_minion_spawn_queue` (populada por `_tick_minion_waves`)
        um minion por vez — cada entrada só vira entidade de verdade
        quando seu `delay` zera, dando `MINION_SPAWN_STAGGER_S` de
        intervalo real entre nascimentos da MESMA wave (rota já foi
        calculada em `_tick_minion_waves`, aqui só materializa)."""
        if not self._minion_spawn_queue:
            return
        from engine.entity_factory import create_minion
        from engine.components import MapLocation as _MLmnq
        still_pending = []
        for entry in self._minion_spawn_queue:
            entry["delay"] -= dt
            if entry["delay"] > 0:
                still_pending.append(entry)
                continue
            eid = create_minion(
                self.world, entry["spawn_x"], entry["spawn_y"], entry["minion_key"],
                faction_id=entry["faction"], route=entry["route"], level=entry["level"],
            )
            self.world.add_component(eid, _MLmnq(entry["map_file"]))
        self._minion_spawn_queue = still_pending

    def _pick_harvestable_zone_tile(self, zone: dict, occupied: set):
        """Tile (x, y) caminhável aleatório dentro do raio da zona, ou None
        — adaptação de `SpawnZoneSystem._pick_tile` (mesma amostragem O(até
        40 tentativas), agora resolvendo o tilemap do MAPA da zona via
        `_map_bundles` em vez de depender de um `_svc` de sistema já
        registrado (esta chamada acontece fora de qualquer System)."""
        bundle = self._map_bundles.get(zone["map"])
        tilemap_comp = bundle.pathfinding._get_tilemap_component() if bundle else None
        rows = tilemap_comp.tile_matrix if tilemap_comp else None
        map_h = len(rows) if rows else 0
        map_w = len(rows[0]) if (rows and map_h) else 0
        r = zone["radius"]
        for _ in range(40):
            tx = zone["x"] + random.randint(-r, r)
            ty = zone["y"] + random.randint(-r, r)
            if (tx, ty) in occupied:
                continue
            if rows is not None:
                if not (0 <= tx < map_w and 0 <= ty < map_h):
                    continue
                if rows[ty][tx].is_solid:
                    continue
            return (tx, ty)
        return None

    def _spawn_harvestable_zone_node(self, zone_id: int, zone: dict,
                                     subtype_idx: int, dest: tuple) -> int:
        """Cria UM nó de zona (corpse + entidade real) do sub-tipo
        `subtype_idx` em `dest` — mesmo esqueleto de
        `_create_harvestables_for_map`, mas pra um nó só, e com `zone_id`
        gravado no corpse (usado por `_tick_harvestable_zones` pra saber
        que esgotar este corpse deve REMOVER a entidade, não reabastecer no
        lugar como o harvestable de posição fixa)."""
        from engine.entity_factory import create_harvestable_entity
        from engine.components import MapLocation as _MLhz
        tx, ty = dest
        sub = zone["spawns"][subtype_idx]
        hid = self._next_corpse_id
        self._next_corpse_id += 1
        name = sub.get("name", "Objeto")
        template_items = sub.get("items", [])
        template_coins = sub.get("coins", 0)
        granted = self._resolve_harvestable_items(template_items, name)
        self._corpses[hid] = {
            "tx": tx, "ty": ty, "owner_eid": -1,
            "items": granted, "coins": template_coins,
            "timer": float("inf"), "map": zone["map"],
            "mob_name": "", "mob_race": "", "quest_rolls": {},
            "no_decay": True, "name": name,
            "respawn_s": 0, "empty_timer": 0.0,
            "_template_items": template_items, "_template_coins": template_coins,
            "zone_id": zone_id,
        }
        eid = create_harvestable_entity(
            self.world, tx, ty, corpse_id=hid,
            sprite_id=sub.get("sprite", ""), name=name,
            requires_quest=zone.get("requires_quest", ""))
        # MapLocation manual (fora do diff antes/depois de _load_map_for,
        # que só cobre entidades criadas na carga do mapa — mesmo padrão
        # de SpawnZoneSystem._spawn_one pra mob criado em runtime).
        self.world.add_component(eid, _MLhz(zone["map"]))
        self._harvestable_eids.add(eid)
        self._harvestable_hid_to_eid[hid] = eid
        self._harvestable_zone_active.setdefault(zone_id, {})[hid] = subtype_idx
        return hid

    def _tick_harvestable_zones(self, dt: float) -> None:
        """Zona de itens (25/07/2026) — chamado do MESMO lugar que já chama
        `_process_loot_drops`/`_tick_harvestable_respawn` (nenhuma `System`
        ECS nova, mesmo princípio de M2: só bookkeeping de timer). Pra cada
        zona: (1) detecta nós ativos totalmente esgotados (itens E moedas)
        e os REMOVE (diferente do harvestable de posição fixa — decisão do
        usuário: nó de zona some, não fica visível vazio), reaproveitando o
        pipeline genérico de despawn (`self._despawned_this_tick`, já
        despachado como ENTITY_DESPAWN pra quem conhece o eid — nenhum
        broadcast novo necessário); (2) decrementa timers de slots vagos e
        spawna um nó novo em posição ALEATÓRIA dentro do raio ao zerar
        (nascimento também não precisa de notificação dedicada — o sweep
        genérico, que já cobre `_mob_eids | _harvestable_eids` desde a Fase
        M1, descobre sozinho no próximo tick)."""
        if not self._harvestable_zones:
            return
        from engine.components import TileMovement as _TMhz, MapLocation as _MLhz2
        occupied_by_map: dict = {}

        def _occupied_for(map_file):
            if map_file not in occupied_by_map:
                occ = set()
                for occ_eid, tm in self.world.get_entities_with(_TMhz):
                    ml = self.world.get_component(occ_eid, _MLhz2)
                    if ml is None or ml.map_file != map_file:
                        continue
                    occ.add((tm.current_tile_x, tm.current_tile_y))
                    if tm.is_moving:
                        occ.add((tm.target_tile_x, tm.target_tile_y))
                occupied_by_map[map_file] = occ
            return occupied_by_map[map_file]

        for zone_id, zone in self._harvestable_zones.items():
            active = self._harvestable_zone_active.setdefault(zone_id, {})

            exhausted = [hid for hid in active
                        if hid not in self._corpses or
                           (not self._corpses[hid].get("items")
                            and self._corpses[hid].get("coins", 0) <= 0)]
            for hid in exhausted:
                subtype_idx = active.pop(hid)
                eid = self._harvestable_hid_to_eid.pop(hid, None)
                if eid is not None:
                    self._harvestable_eids.discard(eid)
                    try:
                        self.world.remove_entity(eid)
                    except Exception:
                        pass
                    self._despawned_this_tick.append({"eid": eid, "tx": None, "ty": None})
                self._corpses.pop(hid, None)
                self._harvestable_zone_timers.setdefault(zone_id, []).append(
                    [subtype_idx, zone["respawn_cooldown"]])

            timers = self._harvestable_zone_timers.get(zone_id, [])
            still_waiting = []
            for subtype_idx, t in timers:
                t -= dt
                if t <= 0:
                    occ = _occupied_for(zone["map"])
                    dest = self._pick_harvestable_zone_tile(zone, occ)
                    if dest is None:
                        still_waiting.append([subtype_idx, 2.0])  # reagenda em 2s
                        continue
                    occ.add(dest)  # reserva o tile imediatamente
                    self._spawn_harvestable_zone_node(zone_id, zone, subtype_idx, dest)
                else:
                    still_waiting.append([subtype_idx, t])
            self._harvestable_zone_timers[zone_id] = still_waiting

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
                       "duel_wins", "duel_losses", "deaths", "minions_killed"):
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
                "deaths": cst.deaths, "minions_killed": cst.minions_killed,
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
        23/07/2026) + registro em _pvp_damage_this_tick (Fase C,
        23/07/2026 — ver comentário em _pvp_damage_this_tick no __init__).

        Fase C (FLT duplicado): dano PLAYER→PLAYER (killer E target são
        players) que passa por apply_damage_core (killer_eid != -1, único
        jeito deste hook disparar) precisa se excluir de
        _pvp_damage_this_tick, senão `_process_player_attacks`
        (server/combat_processor.py) o contabiliza DE NOVO como "dano de
        mob não rastreado" (mob_delta), gerando um segundo combat_this_tick
        pro MESMO golpe — 1 real (daqui ou do call site) + 1 fantasma
        (mob_delta, attacker=-1/mob-errado). Bug real relatado pelo usuário
        23/07/2026 em 2 skills SEM relação nenhuma entre si (retaliation do
        Escudo de Fogo — killer=dono do escudo, target=quem atacou o dono;
        tick de canalização de Calamidade Flamejante — killer=mago,
        target=vítima), ambas PvP (killer sempre player nos dois casos) e
        ambas SEM esse rastreio manual — prova que é um buraco SISTÊMICO
        (qualquer call site novo que esqueça de rastrear cai nele), não um
        bug específico de uma skill. Fix: centralizar aqui, no ÚNICO ponto
        que roda pra qualquer dano com killer_eid válido — os 4 call sites
        que faziam esse rastreio à mão (combat_processor.py::
        _process_pvp_attack, skill_processor.py, spell_completion_processor.py
        ×2) tiveram a linha removida, pra não contar 2x o mesmo dano no dict.

        CUIDADO: só quando o KILLER também é player (PvP de verdade) — dano
        de MOB contra player (auto-attack normal via EnemyAISystem, sem
        nenhum combat_this_tick próprio) depende INTEIRAMENTE do mob_delta
        pra ser detectado; excluir esse caso aqui também zeraria o
        combat_this_tick de todo ataque de mob contra player."""
        self._track_arena_damage(killer_eid, target_id, dmg)
        from engine.components import PlayerControlled as _PC_dmgtrk
        from engine.utils import incr_char_stat
        _target_is_player = self.world.get_component(target_id, _PC_dmgtrk) is not None
        _killer_is_player = self.world.get_component(killer_eid, _PC_dmgtrk) is not None
        field = "pvp_damage" if _target_is_player else "pve_damage"
        incr_char_stat(self.world, killer_eid, field, dmg)
        if _target_is_player and _killer_is_player and dmg > 0:
            self._pvp_damage_this_tick[target_id] = (
                self._pvp_damage_this_tick.get(target_id, 0) + dmg)

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
        """Retorna payload completo de ENTITY_SPAWN para qualquer entidade
        (mob, harvestable ou player)."""
        from engine.components import TileMovement
        if eid not in self._mob_eids and eid not in self._harvestable_eids:
            return None   # jogadores são gerenciados separadamente
        tm = self.world.get_component(eid, TileMovement)
        if not tm:
            return None
        return self._build_mob_spawn_payload(eid, tm)

    def _build_mob_spawn_payload(self, eid: int, tm) -> dict:
        from engine.components import Harvestable as _HvBuild
        _hv = self.world.get_component(eid, _HvBuild)
        if _hv is not None:
            # Harvestable (Fase M1, revisão 2) — payload SIMPLES, sem
            # nenhum campo de combate (não é um mob). Loot em si mora em
            # self._corpses[_hv.corpse_id], não aqui — este payload só dá
            # posição/nome/sprite pro cliente renderizar; o conteúdo real
            # chega via LOOT_AVAILABLE (ver _dispatch_tick_deltas/
            # _spawn_and_start, inalterados).
            from engine.components import Renderable as _RenHv
            _corpse_hv = self._corpses.get(_hv.corpse_id, {})
            _ren_hv    = self.world.get_component(eid, _RenHv)
            return {
                "eid": eid, "kind": "harvestable",
                "tx": tm.current_tile_x, "ty": tm.current_tile_y,
                "name": _corpse_hv.get("name", "Objeto"),
                "sprite_id": _ren_hv.sprite_id if _ren_hv else "",
                "corpse_id": _hv.corpse_id,
            }
        from engine.components import (CombatStats, AIControlled, Renderable, SpawnZoneOwner,
                                SpawnZone, EntityIdentity, TrainingDummy as _TDpay, Faction as _FacPay,
                                NPC as _NPCpay, Merchant as _Merchpay, Blacksmith as _Blackpay,
                                Trainer as _Trainpay, QuestGiver as _QGpay, Tower as _TowerPay,
                                Minion as _MinionPay)
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
        elif self.world.get_component(eid, _TowerPay) is not None:
            # Torre (29/07/2026) não tem AIControlled — sempre ataca à
            # distância, então is_ranged é sempre True. Sem isso, o
            # payload sempre mandava is_ranged=False (preso no default
            # do topo da função, igual o bug de "Arqueiro (NPC)"
            # documentado acima) — cliente escolhia som/animação de
            # melee pra torre de flechas/fogo (bug real relatado pelo
            # usuário: "audio da flecha emitindo som de attack melee").
            is_ranged = True
        else:
            _minion_c = self.world.get_component(eid, _MinionPay)
            if _minion_c is not None:
                # Minion (30/07/2026) também não tem AIControlled (mesma
                # decisão de Torre — MinionSystem próprio) — deriva
                # is_ranged do próprio alcance de ataque do tipo, igual
                # MinionSystem já faz internamente (attack_range_tiles>1).
                # Sem isso, minion arqueiro tocava som/animação de melee
                # no cliente remoto (mesma classe de bug da Torre acima).
                is_ranged = _minion_c.attack_range_tiles > 1
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
                        map_file: str = "", viewer_eid: int = -1) -> list[dict]:
        """Retorna lista de mobs + harvestables no AOI — para WORLD_STATE
        inicial (login). Harvestable incluído aqui (Fase M1, revisão 2)
        pelo MESMO motivo que mob estacionário já precisava: sem isso, um
        player que loga JÁ DENTRO do AOI de um harvestable nunca o
        descobre, porque o sweep de `_build_update_for_session` só roda
        dentro de `_dispatch_tick_deltas`, que só dispara se `_on_tick`'s
        `has_pending` achar atividade — se o mundo ficar ocioso logo após
        o login, o harvestable parado nunca apareceria.

        `viewer_eid` (Fase M3, 25/07/2026): se passado, harvestable com
        `requires_quest` fica de fora do resultado pra quem não tem a
        quest ativa — mesmo filtro aplicado no sweep de tick (ver
        `_harvestable_visible_to`)."""
        from engine.components import TileMovement, MapLocation as _ML_gmai
        result = []
        for eid in self._mob_eids | self._harvestable_eids:
            if map_file:
                _ml = self.world.get_component(eid, _ML_gmai)
                if _ml and _ml.map_file != map_file:
                    continue
            if not self._harvestable_visible_to(eid, viewer_eid):
                continue
            tm = self.world.get_component(eid, TileMovement)
            if not tm:
                continue
            if abs(tm.current_tile_x - center_tx) <= radius and \
               abs(tm.current_tile_y - center_ty) <= radius:
                payload = self._build_mob_spawn_payload(eid, tm)
                result.append(payload)
        return result

    def _harvestable_visible_to(self, eid: int, viewer_eid: int) -> bool:
        """Fase M3 (25/07/2026): True pra qualquer entidade sem
        `Harvestable.requires_quest` setado (mob normal, harvestable sem
        trava) — quando setado, só é visível se `viewer_eid` tiver a
        quest ATIVA (mesmo princípio de `class_req`: 100% invisível, não
        só "sem poder saquear"). `viewer_eid=-1` (ex.: chamada sem
        contexto de sessão) trata como "sem trava" pra não quebrar
        call sites que não têm essa informação."""
        from engine.components import Harvestable as _HvVis, QuestLog as _QLVis
        hv = self.world.get_component(eid, _HvVis)
        if hv is None or not hv.requires_quest:
            return True
        if viewer_eid < 0:
            return True
        ql = self.world.get_component(viewer_eid, _QLVis)
        return bool(ql and hv.requires_quest in ql.active)

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
        # max_slots REAL do Inventory vivo (01/08/2026, bug latente achado ao
        # montar a loja de instância — inv de 6 slots, ver server/
        # instance_progression.py) — antes era um "24" hardcoded aqui,
        # dessincronizado do `inv.max_slots` que o passo 6 abaixo já respeita
        # de verdade; com inventário menor que 24 (instância), essa checagem
        # deixava passar uma compra que o passo 6 depois descartava
        # silenciosamente (`break` ao bater o teto real), cobrando o gold
        # sem entregar o item.
        inv_for_slots = self.world.get_component(eid, Inventory)
        max_slots = inv_for_slots.max_slots if inv_for_slots is not None else 24
        if inv_count + pending + 1 > max_slots:
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

        # Fase 6 (06/08/2026, bug real de playtest — ver
        # ARQUITETURA_ONLINE.md): não existia gate de morte pro lado da
        # CURA, só pro dano (apply_damage_core::"blocked_dead",
        # engine/core_systems.py). Mesmo invariante (current_hp<=0),
        # checado ANTES de qualquer outro bloqueio, mesma posição
        # relativa de apply_damage_core. Sem isso, poção usada no
        # instante da morte era consumida (item perdido) sem curar nada.
        if cs.current_hp <= 0:
            _reject("dead")
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
          level       int  OVERRIDE direto de level (01/08/2026) — cliente
                      aplica direto em CharacterStats.level, SEM passar por
                      process_levelups (que deriva level a partir de XP
                      ganho). Só pra quem muda level SEM XP — hoje só
                      server/instance_progression.py (progressão
                      normalizada de instância). Level-up normal continua
                      via `xp`/`xp_gained` (client re-deriva sozinho).
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

    def get_player_hotbar_data(self, session_id: str) -> list:
        """Serializa a hotbar ATUAL (`PlayerSkills.skills`, ao vivo) pra
        persistência — Fase 7 (06/08/2026, ver ARQUITETURA_ONLINE.md
        §34.74.47), mesmo padrão de `get_player_equipment_data` (mesmo
        bug de fundo já corrigido uma vez pro equipamento: cache do
        cliente em `session.last_client_payload` fica stale sempre que o
        servidor muda o estado sem o cliente reenviar um SAVE_STATE
        completo — `_handle_hotbar_update` já aplica reordenação DIRETO
        neste componente, então ele é sempre a fonte correta na hora do
        save, nunca o cache)."""
        from engine.components import PlayerSkills as _PSHb
        eid = self._player_eids.get(session_id)
        if eid is None:
            return []
        ps = self.world.get_component(eid, _PSHb)
        if ps is None:
            return []
        return [sk.skill_id if sk else None for sk in ps.skills]

    def get_player_talent_data(self, session_id: str) -> "dict | None":
        """Serializa o TalentTree ATUAL (ao vivo) pra persistência — Fase
        9 (06/08/2026, ver ARQUITETURA_ONLINE.md §34.74.49), TERCEIRA
        ocorrência do mesmo padrão nesta sessão (equipamento, hotbar
        Fase 7): `session.last_client_payload["talents"]` é um cache que
        fica stale quando o servidor muda o TalentTree por baixo dele —
        aqui especificamente, `enter_normalized_progression`
        (server/instance_progression.py) troca o componente por um
        overlay com TODO talento do build no MÁXIMO (decisão de design
        da instância); qualquer TALENT_UPDATE ou SAVE_STATE mandado
        ENQUANTO dentro cacheia esse overlay maxado — nada limpa o cache
        quando `exit_normalized_progression` restaura o TalentTree real,
        então esse cache poluído persistia no próximo save (bug real:
        personagem saía da BG com TODOS os talentos aplicados). Como
        `_persist_character` já recusa persistir com o player dentro da
        instância, o TalentTree vivo no momento em que ESTA função roda
        é sempre o real, nunca o overlay."""
        from engine.components import TalentTree as _TTHb
        eid = self._player_eids.get(session_id)
        if eid is None:
            return None
        tt = self.world.get_component(eid, _TTHb)
        if tt is None:
            return None
        return {
            "chosen_build":      tt.chosen_build,
            "allocated":         dict(tt.allocated),
            "available_points":  tt.available_points,
        }

    def get_player_inventory_data(self, session_id: str) -> "list | None":
        """Serializa o Inventory ATUAL (ao vivo) do player pra persistência
        — Fase 0 do roteiro de saneamento (07/08/2026, ver
        PROBLEMAS_ARQUITETURA.md §12/§13), QUARTA ocorrência do mesmo
        padrão nesta sessão (equipamento, hotbar, talentos): `_build_save_merge`
        usava `session.last_client_payload["inventory"]` sem nenhum
        fallback ao vivo — quando `enter_normalized_progression` troca o
        `Inventory` real por um overlay de 6 slots da instância (e
        `_handle_save_state`/`_handle_inventory_update` cacheiam esse
        overlay sem guard nenhum), o cache ficava contaminado e nada o
        corrigia depois de `exit_normalized_progression` restaurar o
        real — vetor confirmado: SAVE_STATE mandado durante a instância.

        Como `_persist_character` já recusa persistir com o player dentro
        da instância (`is_in_normalized_progression`), o `Inventory` vivo
        no momento em que ESTA função roda é sempre o real — mesma
        garantia de `get_player_equipment_data`. `sync_player_inventory`
        já mantém esse componente espelhado a partir do INV_SYNC do
        cliente (loot/compra/venda já validados pelo servidor antes de
        chegar aqui), então ler ao vivo não perde nada que o cache tinha
        de legítimo."""
        from engine.components import Inventory as _InvHb
        eid = self._player_eids.get(session_id)
        if eid is None:
            return None
        inv = self.world.get_component(eid, _InvHb)
        if inv is None:
            return None
        return [self._item_data_from_obj(it) for it in inv.items]

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
        self.running = True
        log.info(f"[WorldServer] zona='{self.zone_id}' @ {TICK_RATE} ticks/s")
        next_tick = time.perf_counter()
        while self.running:
            next_tick = await self._run_tick_or_sleep(next_tick)
        self._perf_log.close()

    async def _run_tick_or_sleep(self, next_tick: float) -> float:
        """Uma iteração do loop principal — extraído de `run()` (Fase 5,
        06/08/2026, ver ARQUITETURA_ONLINE.md §34.74.44) pra ser testável
        isoladamente (`while self.running` direto não dá, roda pra
        sempre). Roda `_tick()` se já passou do horário, senão dorme até
        o próximo. Devolve o `next_tick` atualizado.

        `await asyncio.sleep(0)` INCONDICIONAL no fim do branch de tick
        (achado real: antes só existia no branch `else`) — sem isso, se
        `_tick()` demorar consistentemente mais que `TICK_INTERVAL`
        (sobrecarga SUSTENTADA, não um pico isolado), este branch é
        sempre verdadeiro e o loop NUNCA cai no `else`, ou seja, nunca
        devolve controle ao event loop do asyncio — nenhuma mensagem de
        WebSocket é processada, nenhuma conexão nova é aceita. O processo
        continua vivo, mas pra quem está conectado o servidor trava (hang
        de rede — a causa real mais provável de "crashar com muitos
        players", não falta de orçamento de CPU em si)."""
        import gc as _gc_srv
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
            self._gc_ticks_since_collect += 1
            if self._gc_ticks_since_collect >= self._GC_EVERY_TICKS:
                self._gc_ticks_since_collect = 0
                # Log da coleta manual (05/08/2026) — correlacionar tick#
                # e duração do collect() com os picos isolados de
                # 75-190ms vistos em ai_bundles/bnd:map_1. Coleta roda
                # estritamente FORA de _tick() (helper _perf_mark não
                # alcança aqui), então nunca aparece dentro do breakdown
                # de um tick — mas se a duração dela for grande, o PRÓXIMO
                # tick começa atrasado (next_tick já avançou antes desta
                # chamada), o que pode aparecer como tick lento sem
                # nenhum sistema específico pesando no breakdown.
                _t0_gc = time.perf_counter()
                _gc_srv.collect()   # coleta manual entre ticks, nunca durante
                _gc_dt_ms = (time.perf_counter() - _t0_gc) * 1000.0
                if self._perf_proc is not None:
                    try:
                        _rss_gc_mb = self._perf_proc.memory_info().rss / (1024 * 1024)
                    except Exception:
                        _rss_gc_mb = -1.0
                else:
                    _rss_gc_mb = -1.0
                print(f"[PERF] gc.collect() tick#{self.tick_count} dur={_gc_dt_ms:.1f}ms "
                      f"rss_pos={_rss_gc_mb:.1f}MB", file=self._perf_log)
            # Fase 5 (06/08/2026) — ver docstring acima: garante o yield
            # mesmo quando o tick está consistentemente atrasado.
            await asyncio.sleep(0)
        else:
            # Dorme até o próximo tick — elimina busy-spin com sleep(0).
            # Threshold 1ms: abaixo disso yield simples para não overshooting.
            _sleep = next_tick - time.perf_counter()
            if _sleep > 0.001:
                await asyncio.sleep(_sleep)
            else:
                await asyncio.sleep(0)
        return next_tick

    def _perf_mark(self, label: str, t0: float) -> None:
        """Registra o tempo gasto desde `t0` sob `label` — acumulado em
        `_perf_accum` (média periódica, `_PERF_REPORT_TICKS`) E em
        `_perf_tick_now` (breakdown SÓ do tick atual, usado pelo aviso de
        "tick lento" — ver `_tick`). Chokepoint único de profiling
        (04/08/2026, pedido do usuário, §34.74.29): toda seção nova que
        precisar de medição usa ISTO, nunca duplica a leitura de
        `perf_counter()`/escrita nos 2 dicts na mão."""
        import time as _t_mark
        elapsed = _t_mark.perf_counter() - t0
        self._perf_accum[label]    = self._perf_accum.get(label, 0.0) + elapsed
        self._perf_tick_now[label] = self._perf_tick_now.get(label, 0.0) + elapsed

    def _update_overbudget_streak(self, tick_ms: float) -> None:
        """Detecta sobrecarga SUSTENTADA — diferente de "tick lento"
        (incidente isolado, já logado em `_tick`), isto conta ticks
        CONSECUTIVOS acima do budget (Fase 5, 06/08/2026, ver
        ARQUITETURA_ONLINE.md §34.74.44). Ao cruzar
        `_PERF_DEGRADED_STREAK_THRESHOLD`, loga uma vez "entrando em
        estado degradado"; ao voltar pro budget com a flag ligada, loga
        "recuperou" com a duração do episódio. Só observabilidade —
        nenhuma mudança de comportamento/timing do tick em si (ver
        `_run_tick_or_sleep` pro fix real de sobrecarga sustentada:
        garantir yield pro asyncio)."""
        if tick_ms > self._PERF_BUDGET_MS:
            self._perf_overbudget_streak += 1
            if (not self._perf_degraded
                    and self._perf_overbudget_streak >= self._PERF_DEGRADED_STREAK_THRESHOLD):
                self._perf_degraded = True
                print(f"[PERF] SERVIDOR DEGRADADO: {self._perf_overbudget_streak} ticks "
                      f"consecutivos acima do budget (tick#{self.tick_count})",
                      file=self._perf_log)
        else:
            if self._perf_degraded:
                print(f"[PERF] servidor recuperou (ficou degradado por "
                      f"{self._perf_overbudget_streak} ticks, tick#{self.tick_count})",
                      file=self._perf_log)
                self._perf_degraded = False
            self._perf_overbudget_streak = 0

    def _tick(self, dt: float) -> None:
        import time as _time_tick
        _t_tick_start = _time_tick.perf_counter()
        self._perf_tick_now = {}
        self.tick_count += 1
        # CPU % do processo neste tick (não-bloqueante: acumula desde a chamada anterior).
        if self._perf_proc is not None:
            try:
                _cpu_now = self._perf_proc.cpu_percent(interval=None)
                self._perf_cpu_sum  += _cpu_now
                if _cpu_now > self._perf_cpu_peak:
                    self._perf_cpu_peak = _cpu_now
                _rss_now = self._perf_proc.memory_info().rss
                self._perf_rss_sum  += _rss_now
                if _rss_now > self._perf_rss_peak:
                    self._perf_rss_peak = _rss_now
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
        _active_now = 0
        for _eid_sn in self._mob_eids:
            _ai_sn = self.world.get_component(_eid_sn, _AIC)
            if _ai_sn:
                self._mob_states_prev[_eid_sn] = _ai_sn.state
                if _ai_sn.state in ("CHASING", "ATTACKING", "AGGRO_DELAY"):
                    _active_now += 1
        self._perf_active_now      =  _active_now
        self._perf_active_mobs_sum += _active_now
        if _active_now > self._perf_active_mobs_peak:
            self._perf_active_mobs_peak = _active_now

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

        # Índice canônico de players por mapa, 1x por tick (05/08/2026,
        # pedido do usuário — "ai_bundles/map_1 custam ~7-8ms mesmo com só
        # eu jogando, isso não vai piorar com mais players?"). Antes,
        # EnemyAISystem (sleep-check de CADA mob IDLE + _select_target) e,
        # em menor grau, EnemyAbilitySystem/SpawnZoneSystem, faziam seu
        # PRÓPRIO `get_entities_with(...PlayerControlled...)` — o pior caso
        # (sleep-check) rodava esse scan por MOB, todo tick (~190x no mapa
        # aberto). Mesmo padrão já validado nesta sessão pra
        # `_combat_spatial_hash` (§34.74.30): 1 scan aqui, todo sistema de
        # proximidade consome — nunca reimplementa. `players_by_map=None`
        # nos `update()` de cada sistema cai no scan de sempre (compat com
        # teste que chama `.update()` direto, mesmo padrão de
        # `spatial_hash=None` do Tower/MinionSystem).
        from engine.components import PlayerControlled as _PCbm, MapLocation as _MLbm, Position as _Posbm
        _players_by_map: dict = {}
        for _pbm_eid, _pbm_pos, _pbm_tm, _pbm_pc, _pbm_cs in self.world.get_entities_with(
                _Posbm, TileMovement, _PCbm, CombatStats):
            _pbm_ml = self.world.get_component(_pbm_eid, _MLbm)
            _pbm_map = _pbm_ml.map_file if _pbm_ml else ""
            _players_by_map.setdefault(_pbm_map, []).append((_pbm_eid, _pbm_pos, _pbm_tm, _pbm_cs))

        # Índice canônico de MOBS por mapa, 1x por tick (05/08/2026, achado
        # secundário do §34.74.38, mesmo padrão do índice de players acima):
        # `EnemyAISystem.update()` buscava `get_entities_with(...)` SEM
        # filtro de mapa — os ~190 mobs do MUNDO TODO (3 mapas), descartando
        # os de outro bundle 1 a 1 dentro do próprio loop principal. Com 2+
        # mapas ativos ao mesmo tempo, cada bundle repetia esse scan global
        # inteiro. `mobs_by_map=None` no `update()` cai no scan de sempre
        # (compat com teste que chama `.update()` direto).
        from engine.components import (AIControlled as _AICbm,
                                        InitialPosition as _IPbm,
                                        DetectionRadius as _DRbm)
        _mobs_by_map: dict = {}
        for _mbm_eid, _mbm_pos, _mbm_ai, _mbm_ip, _mbm_dr, _mbm_tm, _mbm_cs in self.world.get_entities_with(
                _Posbm, _AICbm, _IPbm, _DRbm, TileMovement, CombatStats):
            _mbm_ml = self.world.get_component(_mbm_eid, _MLbm)
            _mbm_map = _mbm_ml.map_file if _mbm_ml else ""
            _mobs_by_map.setdefault(_mbm_map, []).append(
                (_mbm_eid, _mbm_pos, _mbm_ai, _mbm_ip, _mbm_dr, _mbm_tm, _mbm_cs))

        _t0p = _time_tick.perf_counter()
        for _bnd_key, _bnd in self._map_bundles.items():
            _has_player = _bnd_key in _maps_com_player
            _t0bnd = _time_tick.perf_counter()
            for system in _bnd.systems:
                if not _has_player and system in _bnd.ai_systems:
                    continue  # sem player neste mapa: pula AI (mobs ficam parados)
                # Sub-timer por sistema (05/08/2026) — "bnd:map_1"/"ai_bundles"
                # já mostravam O QUANTO custava, mas não QUAL sistema dentro do
                # bundle (tile_validation/spawn/AI/ability/taunt) — necessário
                # pra achar a origem real dos picos de 75-190ms vistos com 1-2
                # players, já que gc.collect() roda fora de _tick() (não pode
                # ser bucket errado) — o tempo é gasto de verdade aqui dentro.
                _t0sys = _time_tick.perf_counter()
                if system in _bnd.proximity_systems:
                    system.update(dt=dt, players_by_map=_players_by_map, mobs_by_map=_mobs_by_map,
                                  tick_count=self.tick_count)
                else:
                    system.update(dt=dt)
                self._perf_mark(f"sys:{type(system).__name__}", _t0sys)
                # Contador de mobs que sobraram do filtro do Achado 5
                # (05/08/2026) — só EnemyAISystem tem este atributo (hasattr
                # evita import de EnemyAISystem aqui só pra um isinstance).
                _lac = getattr(system, "_last_active_mob_count", None)
                if _lac is not None:
                    self._perf_ai_active_mobs_sum += _lac
                    if _lac > self._perf_ai_active_mobs_peak:
                        self._perf_ai_active_mobs_peak = _lac
            _bnd_label = "bnd:" + _bnd_key.split("/")[-1].replace(".csv", "")
            self._perf_mark(_bnd_label, _t0bnd)
        self._perf_mark("ai_bundles", _t0p)

        # Sistemas globais: rodam UMA vez por tick, após todos os bundles de IA.
        _t0p = _time_tick.perf_counter()
        self._global_tms.update(dt=dt)       # movement: progress → current_tile
        self._global_sfx_sys.update(dt=dt)   # status effects: DoT/HoT timers
        self._global_proj_sys.update(dt=dt)  # projéteis de mobs: posição + hit
        self._perf_mark("global_systems", _t0p)

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
            # Fase 6 (06/08/2026) — pausa o HoT em quem já morreu (não
            # consome tick_timer/ticks_remaining) em vez de curar o
            # "corpo": _handle_player_death já remove ActiveRegen, mas
            # roda DEPOIS deste loop no mesmo tick em que a morte
            # acontece — sem este guard, o HoT aplicava 1 tick de cura
            # nessa janela, current_hp>0 fazia qualquer mob aceitar o
            # corpo como alvo válido (bug real de playtest). Pausar (não
            # remover) preserva os ticks restantes se o player reviver.
            if _rcst.current_hp <= 0:
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
            # Fase 6 (06/08/2026) — mesmo guard do ActiveRegen (HP) acima,
            # por consistência (mana regen num corpo morto é o mesmo tipo
            # de inconsistência, mesmo não sendo o bug relatado).
            if _cs_mr is not None and _cs_mr.current_hp <= 0:
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
        self._perf_mark("skill_requests", _t0p)

        # Conclusão de spells com cast_time (Bola de Fogo, Nova Congelante, etc.)
        _t0p = _time_tick.perf_counter()
        self._process_spell_cast_completions(dt)
        self._perf_mark("spell_completions", _t0p)
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
        _t0p = _time_tick.perf_counter()
        self._process_player_attacks(dt, player_hp_snap)
        self._perf_mark("player_attacks", _t0p)

        # Sweep: mobs com HP <= 0 sem PendingDeath (DoT, outros caminhos fora de deal_damage)
        from engine.components import Enemy as _Enemy, CombatStats as _CS2, PendingDeath as _PD
        for eid in list(self._mob_eids):
            _cs = self.world.get_component(eid, _CS2)
            _pd = self.world.get_component(eid, _PD)
            if _cs and _cs.current_hp <= 0 and _pd is None:
                self.world.add_component(eid, _PD(killer_entity_id=-1))

        # Processa mortes (PendingDeath) — XP, SpawnZone, despawn, remove_entity
        _t0p = _time_tick.perf_counter()
        self._death_handler.update()
        for entry in self._death_handler.consume_despawns():
            eid = entry["eid"]
            self._mob_eids.discard(eid)
            self._mob_damage_log.pop(eid, None)  # limpa entradas stale
            self._mob_hp_prev.pop(eid, None)
            if not any(d["eid"] == eid for d in self._despawned_this_tick):
                self._despawned_this_tick.append(entry)
        for entry in self._death_handler.consume_xp():
            _xp_peid = entry["player_eid"]
            _xp_amt  = entry["xp"]
            # XP de instância (01/08/2026) usa curva/mecânica PRÓPRIA
            # (level cap 15, +3 pontos de talento por level — ver
            # server/instance_progression.py) — NUNCA o resto deste bloco
            # (process_levelups real usa a curva ERRADA; o save-to-DB mais
            # abaixo salvaria o estado RESETADO da instância como se fosse
            # o personagem real, mesma classe do incidente de disconnect
            # já documentado em §34.74.2/ARQUITETURA_ONLINE.md).
            # grant_instance_xp já cuida do próprio push pro cliente
            # (`level` direto via _push_stats_update, não "xp_gained").
            from server.instance_progression import (
                is_in_normalized_progression as _is_norm_xp,
                grant_instance_xp as _grant_ixp,
            )
            if _is_norm_xp(self, _xp_peid):
                _grant_ixp(self, _xp_peid, _xp_amt)
                continue
            self.queue_stats_update(entry)
            # Aplica XP no ECS do servidor para manter level/xp sincronizados no save
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
                    # DEBUG, não INFO (05/08/2026 — mesmo motivo de [XP]/[Death]
                    # abaixo, ver server_death_handler.py) — level-up de
                    # instância é frequente (XP por proximidade, waves
                    # contínuas), log síncrono por evento contribuía pro
                    # travamento real observado numa troca de lane da BG.
                    log.debug(f"[LevelUp] player {_xp_peid} → nivel {_char_xp.level} "
                          f"hp={_cs_xp.current_hp}/{_cs_xp.max_hp} "
                          f"talentos={_tt_lv.available_points if _tt_lv else '?'}")
            # Salva XP/level imediatamente após cada kill — crash do servidor
            # não perde progresso. Via SessionManager._persist_character
            # (server/session.py) — único chokepoint de save do projeto
            # (02/08/2026, ver ARQUITETURA_ONLINE.md §34.74.15): esta chamada
            # já era segura (o branch is_in_normalized_progression acima faz
            # `continue` antes de chegar aqui), mas unificada no mesmo guard
            # que todo outro save do jogo usa — nenhum ponto de persistência
            # deve montar get_player_save_data/_build_save_merge/
            # save_character na mão nunca mais, pra essa classe de bug
            # (esquecer o guard de instância em um call site novo) não poder
            # se repetir.
            _sid_xp = self._player_eid_to_sid.get(_xp_peid, "")
            if _sid_xp and _char_xp and _cs_xp:
                import asyncio as _asyncio_xp
                _mgr = getattr(self, "_session_manager", None)
                if _mgr:
                    _sess_xp = _mgr._sessions.get(_sid_xp)
                    if _sess_xp:
                        # Sobrescreve talentos com dados autoritativos do servidor ECS.
                        # last_client_payload ainda tem available_points antigo (notificação
                        # de level-up ainda não chegou ao cliente), então usar ECS evita
                        # salvar 0 pontos quando deveria salvar 1+.
                        from engine.components import TalentTree as _TTxp_save
                        _tt_xp = self.world.get_component(_xp_peid, _TTxp_save)

                        def _patch_talents_xp(merged_xp, _tt=_tt_xp):
                            if _tt is not None and isinstance(merged_xp.get("talents"), dict):
                                merged_xp["talents"]["available_points"] = _tt.available_points
                                merged_xp["talents"]["allocated"]        = dict(_tt.allocated)

                        _asyncio_xp.ensure_future(_mgr._persist_character(
                            _sess_xp, context="kill_xp", patch_fn=_patch_talents_xp))
            # DEBUG, não INFO — 1 linha por ENTRADA de XP, e uma morte de
            # minion por proximidade gera 1 entrada POR PLAYER perto (ver
            # comentário de [LevelUp] acima).
            log.debug(f"[XP] player {_xp_peid} ganhou {_xp_amt} XP (mob {entry['mob_eid']})")
        self._perf_mark("death_handling", _t0p)

        _t0p = _time_tick.perf_counter()
        self._process_loot_drops(dt)
        self._perf_mark("loot_drops", _t0p)

        _t0p = _time_tick.perf_counter()
        self._tick_harvestable_respawn(dt)
        self._tick_harvestable_zones(dt)
        self._tick_tower_respawns(dt)
        self._perf_mark("harvestable_tower_respawns", _t0p)

        # Índice espacial de entidades combatentes (04/08/2026, pedido do
        # usuário — log de perf mostrou tower_system/minion_system como
        # os maiores consumidores com a BG ativa): torre/minion sem alvo
        # faziam uma varredura GLOBAL de TODAS as entidades do jogo só
        # pra achar "tem hostil por perto?" — com uma wave de BG ativa
        # (dezenas de minions × centenas de entidades no mundo todo),
        # isso virava milhares de iterações por tick. Construído 1x aqui
        # (O(entidades), não O(buscadores×entidades)) e REAPROVEITADO
        # pelos 2 sistemas abaixo — mesmo índice, mesmo shape de
        # candidato que os dois já procuravam (Position+CombatStats+
        # TileMovement). Mesma técnica (`engine.utils.SpatialHash`) já
        # usada por `server/session.py` pro filtro de AOI de sessão —
        # nunca reimplementar, só reaproveitar (ver `_combat_candidates_
        # near`, `engine/world_systems.py`). cell_size=9: cobre o maior
        # alcance real (torre attack_range_tiles=8) com span pequeno.
        from engine.components import Position as _PosCH, MapLocation as _MLCH
        from engine.utils import SpatialHash as _SpatialHashCH
        _t0p = _time_tick.perf_counter()
        _combat_spatial_hash: dict = {}
        for _ceid, _cpos, _ccs, _ctm in self.world.get_entities_with(
                _PosCH, CombatStats, TileMovement):
            if _ccs.current_hp <= 0:
                continue
            _cml = self.world.get_component(_ceid, _MLCH)
            _cmap = _cml.map_file if _cml else ""
            # Só TowerSystem/MinionSystem consultam esta hash, e só existe
            # Tower/Minion em instância de Battleground (04/08/2026, pedido
            # do usuário) — mapa aberto (sem lane registrada em
            # _minion_lanes) nunca é procurado, então nem entra na hash.
            if _cmap not in self._minion_lanes:
                continue
            _chash = _combat_spatial_hash.get(_cmap)
            if _chash is None:
                _chash = _SpatialHashCH(cell_size=9)
                _combat_spatial_hash[_cmap] = _chash
            _chash.insert(_ceid, _ctm.current_tile_x, _ctm.current_tile_y)
        self._perf_mark("combat_spatial_hash_build", _t0p)

        # Torre (29/07/2026): combat_this_tick já está populado com os
        # eventos de dano DESTE tick (auto-attack/skill processados
        # acima) — precisa disso pro aggro-switch (troca de alvo pra
        # defender aliado atacado no alcance). Chamado ANTES do clear
        # de combat_this_tick no fim do tick (ver _tick()).
        _t0p = _time_tick.perf_counter()
        self._tower_system.update(dt, combat_this_tick=self._combat_this_tick,
                                  spatial_hash=_combat_spatial_hash)
        self._perf_mark("tower_system", _t0p)
        # Minion (03/08/2026): mesmo combat_this_tick, pra aggro por dano
        # de torre + espalhamento em área (MinionSystem._check_tower_aggro).
        _t0p = _time_tick.perf_counter()
        self._minion_system.update(dt, combat_this_tick=self._combat_this_tick,
                                   spatial_hash=_combat_spatial_hash,
                                   tick_count=self.tick_count)
        self._perf_mark("minion_system", _t0p)
        _t0p = _time_tick.perf_counter()
        self._tick_minion_waves(dt)
        self._perf_mark("minion_waves", _t0p)
        _t0p = _time_tick.perf_counter()
        self._tick_minion_spawn_queue(dt)
        self._perf_mark("minion_spawn_queue", _t0p)

        _t0p = _time_tick.perf_counter()
        self._tick_trade_distance_check()
        self._tick_duel_distance_check()
        self._tick_arena_queue()
        self._tick_arena_pending()
        self._tick_arena_results_timeout()
        self._perf_mark("trade_duel_arena_ticks", _t0p)

        _t0p = _time_tick.perf_counter()
        self._tick_bg_queue()
        self._tick_bg_pending()
        self._tick_bg_respawns()
        self._tick_bg_kda_hud()
        self._tick_bg_results_timeout()
        self._perf_mark("bg_queue_ticks", _t0p)

        _t0p = _time_tick.perf_counter()
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
        self._perf_mark("post_tick_bookkeeping", _t0p)

        # Limpa deltas de erro do try/except se necessário
        _t0p = _time_tick.perf_counter()
        deltas = self._collect_deltas()
        self._perf_mark("aoi_collect", _t0p)
        self._store_snapshot()

        for cb in self._on_tick_callbacks:
            cb(self.tick_count, deltas)

        # ── Relatório de performance do tick ─────────────────────────────────
        _tick_ms = (_time_tick.perf_counter() - _t_tick_start) * 1000.0
        self._perf_accum["TOTAL"] = self._perf_accum.get("TOTAL", 0.0) + _tick_ms / 1000.0
        self._perf_count += 1
        self._update_overbudget_streak(_tick_ms)
        if _tick_ms > self._PERF_BUDGET_MS:
            _extra = ""
            if _tick_ms > self._PERF_BREAKDOWN_MS:
                # Breakdown SÓ deste tick (não a média periódica) — top 8
                # seções que mais pesaram no PICO específico, pra
                # correlacionar "travada" percebida com o sistema
                # responsável (pedido do usuário, §34.74.29).
                _top = sorted(self._perf_tick_now.items(), key=lambda kv: -kv[1])[:8]
                _outros = max(0.0, _tick_ms / 1000.0 - sum(self._perf_tick_now.values()))
                _top_str = " | ".join(f"{k}={v*1000:.1f}ms" for k, v in _top)
                _extra = f" | TOP: {_top_str} | outros={_outros*1000:.1f}ms"
            # print de propósito: escreve no ARQUIVO de perf (não no console/log)
            print(f"[PERF] tick lento: {_tick_ms:.1f}ms (budget={self._PERF_BUDGET_MS:.0f}ms) "
                  f"tick#{self.tick_count} players={len(self._player_eids)} "
                  f"mobs={len(self._mob_eids)} ativos={self._perf_active_now}{_extra}", file=self._perf_log)
        if self._perf_count >= self._PERF_REPORT_TICKS:
            n = self._perf_count
            total_avg = self._perf_accum.get("TOTAL", 0.0) / n * 1000
            _n_maps_total  = len(self._map_bundles)
            _cur_players_by_map: dict[str, int] = {}
            for _sm in self._player_maps.values():
                _cur_players_by_map[_sm] = _cur_players_by_map.get(_sm, 0) + 1
            _cpu_avg  = self._perf_cpu_sum  / n if n else 0.0
            _cpu_peak = self._perf_cpu_peak
            _rss_avg_mb  = (self._perf_rss_sum / n / (1024 * 1024)) if n else 0.0
            _rss_peak_mb = self._perf_rss_peak / (1024 * 1024)
            _active_avg  = (self._perf_active_mobs_sum / n) if n else 0.0
            _active_peak = self._perf_active_mobs_peak
            _ai_active_avg  = (self._perf_ai_active_mobs_sum / n) if n else 0.0
            _ai_active_peak = self._perf_ai_active_mobs_peak
            _f = self._perf_log
            # print de propósito: escreve no ARQUIVO de perf (não no console/log)
            print(f"\n[PERF SRV] {n} ticks | avg={total_avg:.2f}ms/tick | budget={self._PERF_BUDGET_MS:.0f}ms"
                  f" | players={len(self._player_eids)} | maps_ativos_peak={self._perf_peak_maps}/{_n_maps_total}"
                  f" | cpu_proc avg={_cpu_avg:.1f}% peak={_cpu_peak:.1f}%"
                  f" | rss avg={_rss_avg_mb:.1f}MB peak={_rss_peak_mb:.1f}MB"
                  f" | mobs_ativos avg={_active_avg:.1f} peak={_active_peak}"
                  f" | mobs_no_filtro_ai avg={_ai_active_avg:.1f} peak={_ai_active_peak}", file=_f)
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
            self._perf_rss_sum    = 0.0
            self._perf_rss_peak   = 0.0
            self._perf_active_mobs_sum  = 0.0
            self._perf_active_mobs_peak = 0
            self._perf_ai_active_mobs_sum  = 0.0
            self._perf_ai_active_mobs_peak = 0

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
