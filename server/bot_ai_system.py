"""
server/bot_ai_system.py
IA do bot 100% servidor da fila BG (16/08/2026, pedido do usuário — ver
`server/bg_queue_processor.py`, docstring do módulo, e
`arquitetura/PROBLEMAS_ARQUITETURA.md`). Modelo de decisão simplificado
estilo Dota 2 bot API / LoL Co-op vs AI (pesquisa de referência antes de
desenhar) — a cada decisão, escolhe UMA ação por prioridade: skill
ofensiva > auto-attack > perseguir alvo > empurrar lane. Sem "recuar" na
v1 (decisão explícita do usuário) — bot morre, respawna pelo mesmo timer
automático que qualquer player da BG já usa (`_tick_bg_respawns`).

Chama só as MESMAS funções internas que os handlers de rede já chamam
por trás (`WorldServer.move_player`/`set_player_target`/`queue_skill`/
`process_shop_buy`/`equip_item_from_inventory`) — nenhuma lógica de
combate/cast/compra duplicada aqui, só a DECISÃO de qual ação tomar.
`BotAISystem` não é um System ECS convencional (não herda nada, não é
registrado no loop de render/física) — é chamado 1x por tick por
`WorldServer._tick` (ver `bg_queue_ticks`), puramente server-side.

v2 (16/08/2026, pedido do usuário): cada bot sorteia CLASSE (guerreiro/
arqueiro/mago, ver `BgQueueProcessorMixin._spawn_bot_player`) e LANE
(top/mid/bot, 1 por time sem repetir — `_tick_bg_queue`), compra e
equipa um kit inicial (`do_initial_shopping`, chamado 1x por
`request_bg_accept`), espera a PRÓPRIA wave nascer + 10s antes de
empurrar (`push_start_at`, ver `schedule_lane_push`), e passa a mirar
minion inimigo além de player (não só player, como no v1) — segue o
MESMO caminho que os minions da lane usam (`WorldServer.
_compute_lane_route_tail`, extraído de `_tick_minion_waves` pra virar
ponto único de verdade). Só continua fora de escopo: bot não visa
TORRE como alvo de ataque (ainda empurra até ela, só não a ataca —
quem derruba torre são os minions/players).
"""
from __future__ import annotations

# Throttle de decisão — ~3x/s é responsivo o bastante pra não parecer
# "travado", e barato o bastante pra não pesar (só 1-4 bots por partida,
# nunca centenas como a IA de mob mundo aberto). Movimento em si (1 tile
# por decisão) já é suave o suficiente nessa cadência.
BOT_DECISION_INTERVAL_S = 0.35
# Alcance de engajamento FALLBACK (Chebyshev, tiles) — só usado quando
# `BotAISystem._effective_engage_range` não acha nada mais específico
# (sem skill ofensiva aprendida, classe sem CombatStats). Até 26/08/2026
# esta era a ÚNICA distância usada por QUALQUER classe pra decidir
# "parei de andar, hora de brigar" — bug real relatado pelo usuário:
# guerreiro parava a 2 tiles e nunca acertava golpe_poderoso/auto-attack
# (exigem Chebyshev ≤1 de verdade, `_resolve_target`/`_resolve_auto_
# attack`), enquanto mago/arqueiro paravam bem mais perto do que o
# alcance real da skill/arco permitia, se expondo à toa (pesquisa
# externa — Game AI Pro 3, "Paragon Bots" — confirma que bot de MOBA
# deve usar o alcance EFETIVO de cada herói, nunca 1 raio genérico
# pra papéis diferentes). Ver PROBLEMAS_ARQUITETURA.md.
BOT_ENGAGE_RANGE_TILES = 1
# Raio (Chebyshev) de "percepção" de inimigo — players E minions dentro
# deste raio da posição ATUAL do bot competem por atenção; fora dele,
# ignorados (16/08/2026 — antes o scan de player era SEM limite de
# distância, ok com poucos players no mapa; com minion de lane incluído
# no scan, isso puxaria o bot pra longe da PRÓPRIA lane atrás do
# inimigo mais próximo em QUALQUER lugar do mapa). Maior que
# BOT_ENGAGE_RANGE_TILES (que decide "parar de andar e brigar AGORA"),
# curto o bastante pra não vazar pra lane vizinha.
BOT_LANE_AWARENESS_RADIUS_TILES = 8
# HP% abaixo do qual o bot arrisca entrar no alcance de torre inimiga
# sozinho pra fechar uma kill (dive, 16/08/2026, pedido do usuário —
# mesmo padrão de bot de Dota/LoL em dificuldade mais alta: "não se
# intimida com torre quando o alvo está baixo").
BOT_DIVE_HP_THRESHOLD_PCT = 0.30
# Raio (Chebyshev) em que um minion aliado perto do PRÓPRIO BOT libera o
# dive-gate (25/08/2026 — substitui "minion dentro do alcance da MESMA
# torre", condição frágil demais: minion morre rápido pra torre, raramente
# está lá no instante exato que o bot chega). Metade de
# BOT_LANE_AWARENESS_RADIUS_TILES — perto o bastante pra ser "andando
# junto da leva", sem exigir o mesmo tile.
BOT_DIVE_ALLY_MINION_RADIUS_TILES = 4
# Kit inicial por classe (16/08/2026, pedido do usuário; reajustado
# 25/08/2026 quando a loja de instância passou a vender o catálogo
# inteiro com preço por raridade + filtro temático de classe — ver
# content/merchant_data.py::instance_shop_class_allows/
# INSTANCE_SHOP_RARITY_PRICE). Cada item precisa (a) caber em
# INSTANCE_STARTING_GOLD=150 e (b) passar em instance_shop_class_allows
# pra classe do bot, ou a compra falha silenciosamente em
# do_initial_shopping():
#   guerreiro: iron_sword(uncommon,50) + iron_shield(uncommon,50) = 100g
#   arqueiro:  hunter_bow(uncommon,50) + battleground_quiver(rare,100)
#              = 150g exato — "Anel do Poder" não cabe mais (viraria
#              250g com o preço novo, saiu do kit)
#   mago:      arcane_wand(uncommon,50) + silk_robe(uncommon,tecido,50)
#              = 100g — trocou "Escudo de Ferro" (agora só guerreiro no
#              filtro temático) por uma peça de armadura de tecido
BOT_STARTER_KIT_BY_CLASS: dict[str, list[str]] = {
    "guerreiro": ["iron_sword", "iron_shield"],
    "arqueiro":  ["hunter_bow", "battleground_quiver"],
    "mago":      ["arcane_wand", "silk_robe"],
}
# Fallback de "quanto tempo depois do portão abrir a 1ª wave da lane
# nasce", só usado se a lane não declarar `first_wave_delay_s`
# explícito (o mapa da BG real sempre declara — ver moba_battleground_
# entities.json — então este valor raramente importa na prática).
BOT_WAVE_DELAY_FALLBACK_S = 15.0
# Buffer pedido pelo usuário: espera os minions terem espaço real pra
# avançar antes do bot sair atrás deles.
BOT_WAVE_HEAD_START_S = 10.0


class BotAISystem:
    """Sem estado próprio — todo estado de IA vive em `BotControlled`
    (dado, não lógica) por entidade. `update` é `@staticmethod` de
    propósito: não há necessidade de instanciar nada."""

    # ── Ciclo de vida (chamado 1x, fora do loop de decisão) ────────────────

    @staticmethod
    def do_initial_shopping(world_server, eid: int) -> None:
        """Compra + equipa o kit de `BOT_STARTER_KIT_BY_CLASS` pra classe
        do bot — chamado 1x por `BgQueueProcessorMixin.request_bg_accept`,
        logo após `enter_normalized_progression` conceder o
        `INSTANCE_STARTING_GOLD`. Reaproveita os MESMOS métodos síncronos
        que um player real usa via rede (`WorldServer.process_shop_buy`/
        `equip_item_from_inventory`, ver `server/session.py::
        _handle_buy_request`/`_handle_equip_item`) — só chamados direto,
        sem BUY_RESULT/EQUIP_REJECTED (o bot não tem cliente pra receber).
        Item que falhar (sem gold/estoque) é só pulado, sem travar o
        resto do kit."""
        from engine.components import CharacterStats as _CSShop, Inventory as _InvShop

        sid = world_server._player_eid_to_sid.get(eid)
        if sid is None:
            return
        char = world_server.world.get_component(eid, _CSShop)
        if char is None:
            return
        for item_id in BOT_STARTER_KIT_BY_CLASS.get(char.class_id, []):
            result = world_server.process_shop_buy(
                sid, "instance_shop", "", 1, item_id=item_id)
            if not result.get("success"):
                continue
            inv = world_server.world.get_component(eid, _InvShop)
            if inv is None or not inv.items:
                continue
            world_server.equip_item_from_inventory(sid, len(inv.items) - 1)

    @staticmethod
    def schedule_lane_push(world_server, eid: int, map_file: str,
                           faction_id: str, countdown_deadline: float) -> None:
        """Calcula `BotControlled.push_start_at` — chamado 1x por
        `request_bg_accept`, quando a instância já está garantidamente
        carregada (`_minion_lanes[map_file]` populado). Ancorado no
        horário em que o PORTÃO da partida abre (`countdown_deadline`),
        não em "agora" (bot aceita quase instantâneo, mas ancorar no
        aceite quebraria se isso mudar) — soma o atraso REAL da 1ª wave
        da lane sorteada (`first_wave_delay_s`, mesmo dado que
        `_activate_minion_lanes` já usa) + `BOT_WAVE_HEAD_START_S` de
        folga pedida pelo usuário."""
        from engine.components import BotControlled as _BotCSched

        bot = world_server.world.get_component(eid, _BotCSched)
        if bot is None:
            return
        lane = BotAISystem._find_lane(world_server, map_file, faction_id, bot.lane_id)
        delay = float(lane.get("first_wave_delay_s", BOT_WAVE_DELAY_FALLBACK_S)) \
            if lane else BOT_WAVE_DELAY_FALLBACK_S
        bot.push_start_at = countdown_deadline + delay + BOT_WAVE_HEAD_START_S

    @staticmethod
    def _find_lane(world_server, map_file: str, faction_id: str, lane_id: str) -> "dict | None":
        for lane in world_server._minion_lanes.get(map_file, []):
            if lane.get("faction") == faction_id and lane.get("lane_id", "default") == lane_id:
                return lane
        return None

    # ── Loop de decisão (1x por BOT_DECISION_INTERVAL_S) ───────────────────

    @staticmethod
    def update(world_server, dt: float) -> None:
        from engine.components import BotControlled, TileMovement, Faction, GhostState

        world = world_server.world
        for eid, bot in list(world.get_entities_with(BotControlled)):
            bot.decision_timer -= dt
            if bot.decision_timer > 0:
                continue
            bot.decision_timer = BOT_DECISION_INTERVAL_S

            gst = world.get_component(eid, GhostState)
            if gst is not None and gst.is_dead:
                continue  # respawn já é tratado por WorldServer._tick_bg_respawns

            sid = world_server._player_eid_to_sid.get(eid)
            tm  = world.get_component(eid, TileMovement)
            fac = world.get_component(eid, Faction)
            if sid is None or tm is None or fac is None:
                continue

            target_eid, target_tm = BotAISystem._find_nearest_enemy(world_server, eid, fac, tm)

            if target_eid is not None:
                dist = max(abs(tm.current_tile_x - target_tm.current_tile_x),
                           abs(tm.current_tile_y - target_tm.current_tile_y))
                engage_range = BotAISystem._effective_engage_range(world_server, eid)
                if dist <= engage_range:
                    bot.path = None
                    if not BotAISystem._try_cast_offensive_skill(world_server, sid, eid, target_eid):
                        BotAISystem._engage_target(world_server, sid, eid, target_eid)
                    continue
                BotAISystem._advance_toward(
                    world_server, eid, bot, tm,
                    (target_tm.current_tile_x, target_tm.current_tile_y),
                    fac=fac, dive_target_eid=target_eid)
                continue

            BotAISystem._push_lane(world_server, eid, bot, tm, fac, sid)

    @staticmethod
    def _find_nearest_enemy(world_server, eid: int, fac, tm) -> "tuple[int | None, object]":
        """Player OU minion inimigo (16/08/2026 — v1 só mirava player;
        agora "mesmo comportamento" de brigar com quem entra no alcance,
        pedido do usuário), o que estiver mais perto, dentro de
        `BOT_LANE_AWARENESS_RADIUS_TILES` da posição atual do bot — sem
        limite de raio aqui, um minion de outra lane (podem existir ~24
        no mapa ao mesmo tempo) puxaria o bot pra longe da PRÓPRIA lane.
        Torre continua fora de escopo (bot empurra até ela, não a
        ataca)."""
        from engine.components import (Faction as _FacFE, TileMovement as _TMFE,
                                       GhostState as _GSFE, Minion as _MinFE,
                                       MapLocation as _MLFE, CombatStats as _CSFE)

        world   = world_server.world
        my_sid  = world_server._player_eid_to_sid.get(eid)
        my_map  = world_server.get_player_map(my_sid) if my_sid else None
        best_eid, best_tm, best_dist = None, None, None

        def _consider(cand_eid: int, cand_tm) -> None:
            nonlocal best_eid, best_tm, best_dist
            dist = max(abs(tm.current_tile_x - cand_tm.current_tile_x),
                       abs(tm.current_tile_y - cand_tm.current_tile_y))
            if dist > BOT_LANE_AWARENESS_RADIUS_TILES:
                return
            if best_dist is None or dist < best_dist:
                best_eid, best_tm, best_dist = cand_eid, cand_tm, dist

        # Players (real ou bot — indistinguível de propósito).
        for other_eid, other_fac in world.get_entities_with(_FacFE):
            if other_eid == eid or other_fac.faction_id == fac.faction_id:
                continue
            other_sid = world_server._player_eid_to_sid.get(other_eid)
            if other_sid is None:
                continue  # não é player (torre/minion) — tratado abaixo
            if world_server.get_player_map(other_sid) != my_map:
                continue
            gst = world.get_component(other_eid, _GSFE)
            if gst is not None and gst.is_dead:
                continue
            other_tm = world.get_component(other_eid, _TMFE)
            if other_tm is None:
                continue
            _consider(other_eid, other_tm)

        # Minions inimigos (16/08/2026, novo) — mesmo filtro de time que
        # o loop de player acima já usa (mesmas 2 facções em jogo numa
        # BG, arena_time_a/b — comparação direta de faction_id é
        # suficiente, mesmo padrão do loop de player, sem precisar de
        # is_hostile()/get_relationship_between aqui).
        for m_eid, minion, m_fac, m_ml, m_tm in world.get_entities_with(
                _MinFE, _FacFE, _MLFE, _TMFE):
            if m_fac.faction_id == fac.faction_id or m_ml.map_file != my_map:
                continue
            m_cs = world.get_component(m_eid, _CSFE)
            if m_cs is not None and m_cs.current_hp <= 0:
                continue
            _consider(m_eid, m_tm)

        return best_eid, best_tm

    @staticmethod
    def _effective_engage_range(world_server, eid: int) -> int:
        """Distância (Chebyshev, tiles) em que o bot para de andar e passa
        a tentar brigar — por CLASSE/EQUIPAMENTO real, nunca 1 constante
        genérica pra guerreiro/arqueiro/mago (26/08/2026, bug relatado
        pelo usuário: guerreiro parava a 2 tiles e nunca acertava nada,
        mago/arqueiro paravam bem mais perto do que precisavam). Ordem:

        1. Arqueiro com arco de VERDADE equipado → alcance do arco
           (`CombatProcessorMixin._ranged_weapon_range`, mesma função que
           `_resolve_auto_attack` usa pra validar o auto-attack de
           verdade — ponto único, nunca reimplementado aqui).
        2. Caster com skill ofensiva aprendida cujo `cast_range` (SKILL_
           CATALOG) é maior que melee → alcance dessa skill. Sem
           hardcode de "bola_de_fogo"/"mago" — lê a 1ª skill ofensiva do
           hotbar, igual `_try_cast_offensive_skill` já faz.
        3. Fallback: `BOT_ENGAGE_RANGE_TILES` (melee puro — guerreiro, ou
           qualquer classe sem opção de longo alcance disponível)."""
        from engine.components import CombatStats as _CSRange, PlayerSkills as _PSRange
        from content.skill_config import SKILL_CATALOG as _SCRange

        world = world_server.world
        cs = world.get_component(eid, _CSRange)
        class_is_ranged = getattr(cs, "is_ranged", False) if cs else False
        is_ranged, weapon_range = world_server._ranged_weapon_range(eid, class_is_ranged)
        if is_ranged:
            return weapon_range

        ps = world.get_component(eid, _PSRange)
        if ps:
            for skill in ps.skills:
                if skill is None or not skill.skill_id:
                    continue
                if not _SCRange.get(skill.skill_id, {}).get("offensive", True):
                    continue
                cast_range = _SCRange.get(skill.skill_id, {}).get("cast_range", 0)
                if cast_range > BOT_ENGAGE_RANGE_TILES:
                    return cast_range
                break  # 1ª skill ofensiva já decide (mesma ordem de _try_cast_offensive_skill)

        return BOT_ENGAGE_RANGE_TILES

    @staticmethod
    def _try_cast_offensive_skill(world_server, sid: str, eid: int, target_eid: int) -> bool:
        """1ª skill ofensiva PRONTA (cooldown real, não o campo local) do
        hotbar do bot — catálogo único de verdade (`SKILL_CATALOG`), sem
        hardcode de skill específica aqui. Cooldown consultado via
        `WorldServer._skill_ready` (25/08/2026) — NUNCA
        `skill.current_cooldown` (só atualizado por SKILL_RESULT via
        rede; bot não tem cliente, esse campo nunca decai sozinho, então
        o bot ficava tentando a MESMA skill em cooldown real pra sempre
        e nunca caía pro auto-attack — bug confirmado em log real, ver
        PROBLEMAS_ARQUITETURA.md). Fire-and-forget quanto ao resto
        (recurso/alcance): se não bater, `_process_skill_requests`
        (server autoritativo) só ignora — mesma tolerância que um
        client real teria via SKILL_RESULT.failed."""
        from engine.components import PlayerSkills as _PSTry
        from content.skill_config import SKILL_CATALOG
        import time as _t_try

        ps = world_server.world.get_component(eid, _PSTry)
        if ps is None:
            return False
        for skill in ps.skills:
            if skill is None or not skill.skill_id:
                continue
            if not world_server._skill_ready(eid, skill)[0]:
                continue
            if skill.max_charges > 0 and skill.charges <= 0:
                continue
            if not SKILL_CATALOG.get(skill.skill_id, {}).get("offensive", True):
                continue
            world_server.queue_skill(sid, skill.skill_id, target_eid, 0.0, 0.0,
                                     int(_t_try.time() * 1000))
            return True
        return False

    @staticmethod
    def _engage_target(world_server, sid: str, eid: int, target_eid: int) -> None:
        """Auto-attack — mesma sequência que `server/session.py::
        _handle_auto_attack` já faz por trás de AUTO_ATTACK (set_player_
        target + enter_combat + is_pursuing=True); dano em si já é 100%
        automático depois disso via `CombatProcessorMixin.
        _process_player_attacks`, sem o bot precisar fazer mais nada.
        Funciona igual contra player OU minion (16/08/2026) — o gate de
        auto-attack (`_resolve_auto_attack`, §61) já é genérico por
        alvo, nunca checou "é player" especificamente."""
        from engine.components import CombatState as _CSEng
        from engine.stat_fns import enter_combat as _ecEng

        world_server.set_player_target(sid, target_eid)
        cs = world_server.world.get_component(eid, _CSEng)
        if cs is not None:
            _ecEng(cs)
            cs.is_pursuing = True

    # ── Empurrar lane (segue o MESMO caminho que os minions usam) ──────────

    @staticmethod
    def _push_lane(world_server, eid: int, bot, tm, fac, sid: str) -> None:
        """Sem inimigo por perto — espera `push_start_at` (10s depois da
        PRÓPRIA wave nascer, ver `schedule_lane_push`) e então consome
        `bot.route` tile a tile, mesmo princípio de `MinionSystem.
        _advance_along_route` (engine/world_systems.py): caso comum é só
        checagem de tile adjacente, sem pathfinding; só cai pro A* de
        `_advance_toward` (mirando um ponto alguns tiles à FRENTE na
        PRÓPRIA rota, nunca o destino final) se o próximo tile da rota
        estiver bloqueado — evita redescobrir atalho global pelo
        "gargalo" central do mapa (mesmo motivo documentado em
        `engine/components.py::Minion`)."""
        import time as _t_pl

        if _t_pl.time() < bot.push_start_at:
            return

        map_file = world_server.get_player_map(sid)
        if bot.route is None:
            lane = BotAISystem._find_lane(world_server, map_file, fac.faction_id, bot.lane_id)
            if lane is None:
                return
            tail = world_server._compute_lane_route_tail(map_file, lane)
            if not tail:
                return
            bot.route = [(tm.current_tile_x, tm.current_tile_y)] + tail
            bot.route_idx = 0

        if bot.route_idx + 1 >= len(bot.route):
            return  # chegou no fim da rota (base inimiga) — nada mais a fazer aqui

        cur = (tm.current_tile_x, tm.current_tile_y)
        nxt = bot.route[bot.route_idx + 1]

        if not BotAISystem._dive_allowed(world_server, map_file, nxt, fac.faction_id, None, cur):
            return  # fica parado — espera minion aliado chegar perto, ou desiste no próx tick

        from engine.world_systems import is_tile_walkable as _walkable_pl
        if (max(abs(cur[0] - nxt[0]), abs(cur[1] - nxt[1])) <= 1
                and _walkable_pl(eid, nxt[0], nxt[1], cur[0], cur[1])):
            if world_server.move_player(sid, nxt[0], nxt[1]):
                bot.route_idx += 1
            return

        # Bloqueado ou fisicamente fora da rota (ex: acabou de perseguir
        # um alvo pra longe) — recorre ao A* orçado de _advance_toward
        # mirando um ponto à FRENTE na rota, nunca o destino final.
        lookahead_idx = min(bot.route_idx + 6, len(bot.route) - 1)
        BotAISystem._advance_toward(
            world_server, eid, bot, tm, bot.route[lookahead_idx],
            fac=fac, dive_target_eid=None, route_cache=False)
        if cur == bot.route[lookahead_idx]:
            bot.route_idx = lookahead_idx

    @staticmethod
    def _dive_allowed(world_server, map_file: str, tile: tuple,
                      my_faction_id: str, target_eid: "int | None",
                      my_tile: tuple) -> bool:
        """Gate do dive (16/08/2026, pedido do usuário; condição (a)
        reajustada 25/08/2026): entrar num tile coberto por torre
        inimiga só é permitido se (a) existe minion ALIADO perto DO
        PRÓPRIO BOT agora (`BOT_DIVE_ALLY_MINION_RADIUS_TILES` — andando
        JUNTO da leva), ou (b) o alvo de combate atual do bot está
        abaixo de `BOT_DIVE_HP_THRESHOLD_PCT` (fechar kill). Tile fora
        de qualquer alcance de torre inimiga: sempre permitido (não
        precisa nem varrer torre).

        Condição (a) antes exigia minion aliado DENTRO do alcance da
        MESMA torre — janela frágil (minion morre rápido pra torre, não
        fica parado lá), causava o bot travar indefinidamente perto de
        torre mesmo com a leva por perto mas já tendo passado ou ainda
        não tendo chegado exatamente na torre (bug real de playtest,
        confirmado por reprodução — ver PROBLEMAS_ARQUITETURA.md)."""
        towers = world_server._enemy_towers_covering(map_file, tile, my_faction_id)
        if not towers:
            return True
        if world_server._allied_minion_near_tile(
                map_file, my_tile, my_faction_id, BOT_DIVE_ALLY_MINION_RADIUS_TILES):
            return True
        if target_eid is not None:
            from engine.components import CombatStats as _CSDive
            cs = world_server.world.get_component(target_eid, _CSDive)
            if cs is not None and cs.max_hp > 0 and (cs.current_hp / cs.max_hp) < BOT_DIVE_HP_THRESHOLD_PCT:
                return True
        return False

    @staticmethod
    def _advance_toward(world_server, eid: int, bot, tm, goal: "tuple[int, int]", *,
                        fac=None, dive_target_eid: "int | None" = None,
                        route_cache: bool = True) -> None:
        """1 tile por decisão (mesmo limite de `move_player`, distância
        1 por MOVE). Rota calculada via o MESMO pathfinding server-side
        que `_tick_minion_waves` já usa (`WorldServer.
        _get_pathfinding_for_map_file`) — sem duplicar A*. Cache em
        `BotControlled.path`/`path_goal` (dado, não lógica) — só
        recalcula quando o objetivo muda (perseguir alvo que se move
        recalcula quase toda decisão; `route_cache=False` — chamada de
        `_push_lane` — nunca reusa esse cache, a rota "de verdade" já
        está em `bot.route`, isto aqui é só o desvio local pontual).

        Torres como `dynamic_obstacles` — mesmo motivo e mesma fonte
        (`WorldServer._get_tower_tiles_for_map`) que já corrige o
        minion-wave: sem isso o A* podia entregar o tile EXATO de uma
        torre como próximo passo, e como o bot só anda 1 tile por
        decisão (sem espaço pra desviar), ele travava tentando pisar num
        tile que nunca libera.

        Dive gate (16/08/2026, `fac`/`dive_target_eid`): peek do próximo
        tile ANTES de consumir do `path` (`pop` só depois de aprovado) —
        senão um passo negado pelo dive ficaria preso na MESMA
        dessincronia que o bug de `move_player` rejeitado já documentou
        abaixo."""
        cur = (tm.current_tile_x, tm.current_tile_y)
        if cur == goal:
            return
        if not route_cache or bot.path_goal != goal or not bot.path:
            sid = world_server._player_eid_to_sid.get(eid)
            map_file = world_server.get_player_map(sid)
            pathfinding = world_server._get_pathfinding_for_map_file(map_file)
            if pathfinding is None:
                return
            tower_tiles = world_server._get_tower_tiles_for_map(map_file)
            path = pathfinding.find_path(cur, goal, dynamic_obstacles=tower_tiles,
                                         manhattan_limit=None, max_nodes=2000)
            if not path:
                return
            if route_cache:
                bot.path = list(path)
                bot.path_goal = goal
            else:
                bot.path = list(path)
        if not bot.path:
            return

        sid = world_server._player_eid_to_sid.get(eid)
        map_file = world_server.get_player_map(sid)
        next_tile = bot.path[0]
        if fac is not None and not BotAISystem._dive_allowed(
                world_server, map_file, next_tile, fac.faction_id, dive_target_eid, cur):
            return  # espera a condição do dive ficar verdadeira, sem descartar o path
        bot.path.pop(0)
        if not world_server.move_player(sid, next_tile[0], next_tile[1]):
            # Passo rejeitado (tile ocupado por outro bot/minion/player no
            # instante do MOVE, ou qualquer outro motivo de move_player
            # recusar — 16/08/2026): o `pop(0)` acima já consumiu esse
            # tile da rota em cache mesmo sem o bot ter realmente se
            # movido, então a rota fica DESSINCRONIZADA da posição real —
            # a decisão seguinte tentaria o PRÓXIMO tile da rota (2 tiles
            # de distância da posição real), que também seria rejeitado
            # (move_player só aceita distância 1), e assim por diante:
            # bot "trava" (posição real nunca muda) por várias decisões
            # enquanto a rota em cache é silenciosamente descartada tile a
            # tile, até esvaziar e recalcular do zero — daí os "saltos"
            # reportados (fica parado vários ciclos, depois pula quando a
            # rota finalmente recalcula do lugar certo). Forçar
            # recálculo AGORA (não só quando a rota já estiver vazia)
            # evita a cascata de tentativas inválidas.
            bot.path = None
