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
# Histerese de fuga por HP baixo (08/09/2026, pedido do usuário — vale
# pras 3 classes: guerreiro corre em direção à própria base, mago/
# arqueiro kitam mantendo distância) — liga ao CAIR a
# BOT_LOW_HP_RETREAT_PCT, só desliga ao SUBIR a BOT_LOW_HP_REENGAGE_PCT
# (nunca o mesmo valor pros dois lados, senão o bot oscilaria fugir/
# brigar a cada decisão de 0.35s bem na borda de um threshold só). Ver
# `BotControlled.is_retreating` (engine/components.py) — estado por
# entidade, esta função só decide.
BOT_LOW_HP_RETREAT_PCT  = 0.30
BOT_LOW_HP_REENGAGE_PCT = 0.50
# Minion só vira alvo de ataque do bot quando está prestes a morrer —
# antes disso, o bot ignora minion saudável (que a própria lane/torre já
# está resolvendo sozinha) e só intervém pra fechar o abate (XP), nunca
# desperdiça hit/skill nele antes da hora (08/09/2026, pedido do
# usuário). Player inimigo sempre tem prioridade sobre qualquer minion.
BOT_MINION_LAST_HIT_PCT = 0.20
# Nº mínimo de inimigos (player+minion) num raio pra valer a pena gastar
# uma skill em ÁREA (Fatiador de Corpos/Nova Congelante) em vez de uma
# skill de alvo único — 1 alvo só não compensa o cooldown longo dessas
# skills (08/09/2026, pedido do usuário).
BOT_CLEAVE_MIN_TARGETS = 2

# IDs de skill usados pela prioridade tática por classe abaixo — nunca
# hardcoded solto no meio da lógica, todos num lugar só pra facilitar
# auditoria contra o catálogo real (`content/skill_config.py`).
_SKILL_EXECUTAR    = "executar"          # guerreiro: dano 3x se alvo HP<=30% (finisher)
_SKILL_FATIADOR    = "fatiador_de_corpos"  # guerreiro: cleave, raio 2 tiles
_SKILL_VITORIA     = "vitoria_iminente"    # guerreiro: carga ganha ao matar, gasta com HP<90%
_SKILL_INTERCEPTAR = "interceptar"         # guerreiro: gap-closer, 2-6 tiles
_SKILL_NOVA        = "nova_congelante"     # mago: dano + enraíza em área, raio 3 tiles
_SKILL_POLIMORFIA  = "polimorfia"          # mago: CC de alvo único, alcance 7
_SKILL_BLOCO       = "bloco_de_gelo"       # mago: imunidade + regen, self-cast
_SKILL_BOLA        = "bola_de_fogo"        # mago: ataque padrão à distância, alcance 6
_SKILL_PICADA      = "picada_escorpiao"    # arqueiro: slow, usado pra abrir espaço

# Thresholds/raios espelhados do catálogo real (`content/skill_config.py`)
# — mantidos aqui como constante nomeada em vez de reconsultar o dict a
# cada decisão, mesmo valor exato:
# executar.params.hp_threshold=0.3, fatiador_de_corpos.params.
# radius_tiles=2, vitoria_iminente (sem threshold próprio — carga, ver
# docstring de _try_priority_skill), nova_congelante.cast_range=3 (faz
# duplo papel de raio de área no servidor, `_server_nova_congelante`),
# interceptar.params: min_range=2/max_range=6 (`engine/skill_handlers.py`).
_EXECUTAR_HP_THRESHOLD_PCT   = 0.30
_VITORIA_HP_THRESHOLD_PCT    = 0.90
_FATIADOR_RADIUS_TILES       = 2
_NOVA_RADIUS_TILES           = 3
_INTERCEPTAR_MAX_RANGE_TILES = 6

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
        from engine.components import (BotControlled, TileMovement, Faction,
                                       GhostState, CombatStats)

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

            # Histerese de fuga por HP baixo — ver constantes no topo do
            # arquivo. Roda ANTES de qualquer decisão de alvo/engajamento,
            # pra fuga sempre poder interromper um combate em andamento.
            own_cs = world.get_component(eid, CombatStats)
            own_ratio = ((own_cs.current_hp / own_cs.max_hp)
                        if own_cs is not None and own_cs.max_hp > 0 else 1.0)
            if own_ratio <= BOT_LOW_HP_RETREAT_PCT:
                bot.is_retreating = True
            elif own_ratio >= BOT_LOW_HP_REENGAGE_PCT:
                bot.is_retreating = False

            target_eid, target_tm = BotAISystem._find_nearest_enemy(world_server, eid, fac, tm)

            if bot.is_retreating:
                class_id = BotAISystem._class_id_of(world_server, eid)
                if target_eid is None or not BotAISystem._try_defensive_skill(
                        world_server, sid, eid, target_eid, class_id):
                    BotAISystem._retreat(world_server, eid, bot, tm, fac, sid)
                continue

            if target_eid is not None:
                dist = max(abs(tm.current_tile_x - target_tm.current_tile_x),
                           abs(tm.current_tile_y - target_tm.current_tile_y))
                engage_range = BotAISystem._effective_engage_range(world_server, eid)
                if dist <= engage_range:
                    bot.path = None
                    class_id = BotAISystem._class_id_of(world_server, eid)
                    if not BotAISystem._try_priority_skill(world_server, sid, eid, target_eid, class_id):
                        BotAISystem._engage_target(world_server, sid, eid, target_eid)
                    continue
                BotAISystem._advance_toward(
                    world_server, eid, bot, tm,
                    (target_tm.current_tile_x, target_tm.current_tile_y),
                    fac=fac, dive_target_eid=target_eid)
                continue

            BotAISystem._push_lane(world_server, eid, bot, tm, fac, sid)

    @staticmethod
    def _class_id_of(world_server, eid: int) -> str:
        from engine.components import CharacterStats as _CSClass
        char = world_server.world.get_component(eid, _CSClass)
        return char.class_id if char is not None else ""

    @staticmethod
    def _find_nearest_enemy(world_server, eid: int, fac, tm) -> "tuple[int | None, object]":
        """Player inimigo SEMPRE vence minion (08/09/2026, pedido do
        usuário — antes disso era só "o mais perto entre os dois", então
        o bot podia trocar de brigar com um player pra bater num minion
        de passagem). Minion só vira alvo quando está prestes a morrer
        (`BOT_MINION_LAST_HIT_PCT`) — garante o abate (XP) sem gastar
        ataque/skill num minion saudável que a própria lane (minion
        aliado + torre) já está resolvendo sozinha. Nearest-wins
        continua valendo DENTRO de cada categoria, mesmo raio de sempre
        (`BOT_LANE_AWARENESS_RADIUS_TILES`). Torre continua fora de
        escopo (bot empurra até ela, não a ataca)."""
        from engine.components import (Faction as _FacFE, TileMovement as _TMFE,
                                       GhostState as _GSFE, Minion as _MinFE,
                                       MapLocation as _MLFE, CombatStats as _CSFE)

        world   = world_server.world
        my_sid  = world_server._player_eid_to_sid.get(eid)
        my_map  = world_server.get_player_map(my_sid) if my_sid else None

        def _dist_to(cand_tm) -> int:
            return max(abs(tm.current_tile_x - cand_tm.current_tile_x),
                       abs(tm.current_tile_y - cand_tm.current_tile_y))

        best_player_eid, best_player_tm, best_player_dist = None, None, None
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
            dist = _dist_to(other_tm)
            if dist > BOT_LANE_AWARENESS_RADIUS_TILES:
                continue
            if best_player_dist is None or dist < best_player_dist:
                best_player_eid, best_player_tm, best_player_dist = other_eid, other_tm, dist

        if best_player_eid is not None:
            return best_player_eid, best_player_tm

        best_minion_eid, best_minion_tm, best_minion_dist = None, None, None
        for m_eid, minion, m_fac, m_ml, m_tm in world.get_entities_with(
                _MinFE, _FacFE, _MLFE, _TMFE):
            if m_fac.faction_id == fac.faction_id or m_ml.map_file != my_map:
                continue
            m_cs = world.get_component(m_eid, _CSFE)
            if m_cs is None or m_cs.current_hp <= 0:
                continue
            if m_cs.max_hp <= 0 or (m_cs.current_hp / m_cs.max_hp) > BOT_MINION_LAST_HIT_PCT:
                continue  # ainda saudável demais -- deixa a lane resolver sozinha
            dist = _dist_to(m_tm)
            if dist > BOT_LANE_AWARENESS_RADIUS_TILES:
                continue
            if best_minion_dist is None or dist < best_minion_dist:
                best_minion_eid, best_minion_tm, best_minion_dist = m_eid, m_tm, dist

        return best_minion_eid, best_minion_tm

    @staticmethod
    def _count_nearby_enemies(world_server, eid: int, radius: int) -> int:
        """Conta inimigos (player OU minion, vivos, mesmo mapa) num raio
        Chebyshev da posição do bot — usado só pra decidir skill em ÁREA
        (Fatiador de Corpos/Nova Congelante) quando vale a pena de
        verdade (`BOT_CLEAVE_MIN_TARGETS`+), nunca pra seleção de alvo
        (isso é `_find_nearest_enemy`)."""
        from engine.components import (Faction as _FacCnt, TileMovement as _TMCnt,
                                       GhostState as _GSCnt, Minion as _MinCnt,
                                       MapLocation as _MLCnt, CombatStats as _CSCnt)

        world = world_server.world
        tm  = world.get_component(eid, _TMCnt)
        fac = world.get_component(eid, _FacCnt)
        if tm is None or fac is None:
            return 0
        my_sid = world_server._player_eid_to_sid.get(eid)
        my_map = world_server.get_player_map(my_sid) if my_sid else None
        count = 0

        for other_eid, other_fac in world.get_entities_with(_FacCnt):
            if other_eid == eid or other_fac.faction_id == fac.faction_id:
                continue
            other_sid = world_server._player_eid_to_sid.get(other_eid)
            if other_sid is None:
                continue
            if world_server.get_player_map(other_sid) != my_map:
                continue
            gst = world.get_component(other_eid, _GSCnt)
            if gst is not None and gst.is_dead:
                continue
            other_tm = world.get_component(other_eid, _TMCnt)
            if other_tm is None:
                continue
            if max(abs(tm.current_tile_x - other_tm.current_tile_x),
                  abs(tm.current_tile_y - other_tm.current_tile_y)) <= radius:
                count += 1

        for m_eid, minion, m_fac, m_ml, m_tm in world.get_entities_with(
                _MinCnt, _FacCnt, _MLCnt, _TMCnt):
            if m_fac.faction_id == fac.faction_id or m_ml.map_file != my_map:
                continue
            m_cs = world.get_component(m_eid, _CSCnt)
            if m_cs is not None and m_cs.current_hp <= 0:
                continue
            if max(abs(tm.current_tile_x - m_tm.current_tile_x),
                  abs(tm.current_tile_y - m_tm.current_tile_y)) <= radius:
                count += 1

        return count

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
    def _try_cast_specific(world_server, sid: str, eid: int, target_eid: int,
                           skill_ids: list) -> bool:
        """Mesmo gate de `_try_cast_offensive_skill` (cooldown real via
        `_skill_ready`, carga disponível), mas escolhendo por ID em vez
        de "a 1ª do hotbar" — usado pela prioridade tática por classe
        (`_try_priority_skill`/`_try_defensive_skill`) pra tentar uma
        skill ESPECÍFICA (só dispara se o bot já tiver essa skill
        aprendida — nível insuficiente dentro da instância = ainda não
        está em `ps.skills`, tentativa vira no-op)."""
        from engine.components import PlayerSkills as _PSSpec
        import time as _t_spec

        ps = world_server.world.get_component(eid, _PSSpec)
        if ps is None:
            return False
        for wanted_id in skill_ids:
            for skill in ps.skills:
                if skill is None or skill.skill_id != wanted_id:
                    continue
                if not world_server._skill_ready(eid, skill)[0]:
                    continue
                if skill.max_charges > 0 and skill.charges <= 0:
                    continue
                world_server.queue_skill(sid, skill.skill_id, target_eid, 0.0, 0.0,
                                         int(_t_spec.time() * 1000))
                return True
        return False

    @staticmethod
    def _try_priority_skill(world_server, sid: str, eid: int, target_eid: int,
                            class_id: str) -> bool:
        """Discernimento tático por classe (08/09/2026, pedido do
        usuário) — substitui "sempre a 1ª skill pronta do hotbar" por
        uma ordem que considera a SITUAÇÃO (HP do alvo, nº de inimigos
        ao redor, HP próprio, distância). Cada classe tenta, em ordem, a
        skill que faz sentido; se nenhuma bater, cai pro fallback
        genérico (`_try_cast_offensive_skill`) — cobre golpe_poderoso/
        impacto/flecha_reiterada/tiro_multiplo/etc., que não têm regra
        tática própria e continuam "a 1ª pronta"."""
        from engine.components import CombatStats as _CSPri, TileMovement as _TMPri

        if class_id == "guerreiro":
            target_cs = world_server.world.get_component(target_eid, _CSPri)
            if (target_cs is not None and target_cs.max_hp > 0
                    and (target_cs.current_hp / target_cs.max_hp) <= _EXECUTAR_HP_THRESHOLD_PCT
                    and BotAISystem._try_cast_specific(world_server, sid, eid, target_eid,
                                                        [_SKILL_EXECUTAR])):
                return True
            if (BotAISystem._count_nearby_enemies(world_server, eid, _FATIADOR_RADIUS_TILES)
                    >= BOT_CLEAVE_MIN_TARGETS
                    and BotAISystem._try_cast_specific(world_server, sid, eid, target_eid,
                                                        [_SKILL_FATIADOR])):
                return True
            own_cs = world_server.world.get_component(eid, _CSPri)
            own_ratio = ((own_cs.current_hp / own_cs.max_hp)
                        if own_cs is not None and own_cs.max_hp > 0 else 1.0)
            if (own_ratio < _VITORIA_HP_THRESHOLD_PCT
                    and BotAISystem._try_cast_specific(world_server, sid, eid, target_eid,
                                                        [_SKILL_VITORIA])):
                return True
            tm = world_server.world.get_component(eid, _TMPri)
            target_tm = world_server.world.get_component(target_eid, _TMPri)
            if tm is not None and target_tm is not None:
                dist = max(abs(tm.current_tile_x - target_tm.current_tile_x),
                          abs(tm.current_tile_y - target_tm.current_tile_y))
                if (1 < dist <= _INTERCEPTAR_MAX_RANGE_TILES
                        and BotAISystem._try_cast_specific(world_server, sid, eid, target_eid,
                                                            [_SKILL_INTERCEPTAR])):
                    return True

        elif class_id == "mago":
            if (BotAISystem._count_nearby_enemies(world_server, eid, _NOVA_RADIUS_TILES)
                    >= BOT_CLEAVE_MIN_TARGETS
                    and BotAISystem._try_cast_specific(world_server, sid, eid, target_eid,
                                                        [_SKILL_NOVA])):
                return True
            if BotAISystem._try_cast_specific(world_server, sid, eid, target_eid, [_SKILL_BOLA]):
                return True

        return BotAISystem._try_cast_offensive_skill(world_server, sid, eid, target_eid)

    @staticmethod
    def _try_defensive_skill(world_server, sid: str, eid: int, target_eid: int,
                             class_id: str) -> bool:
        """HP crítico (`BotControlled.is_retreating`) — tenta a skill de
        escape/defensiva da classe (mesma ordem "skill > movimento" do
        resto do arquivo: se conseguir castar, este tick não anda; a
        fuga em si continua no próximo tick via `_retreat`). Mago:
        Nova Congelante primeiro (dano + enraíza QUEM ESTÁ PERSEGUINDO,
        ajuda a fugir de verdade — não só CC), senão Polimorfia, senão
        Bloco de Gelo. Arqueiro: Picada de Escorpião (slow no
        perseguidor). Guerreiro não tem ferramenta de escape real
        (Interceptar aproxima, Vitória Iminente exige adjacência) —
        sempre cai direto pra `_retreat` (correr, sem skill)."""
        if class_id == "mago":
            return BotAISystem._try_cast_specific(
                world_server, sid, eid, target_eid,
                [_SKILL_NOVA, _SKILL_POLIMORFIA, _SKILL_BLOCO])
        if class_id == "arqueiro":
            return BotAISystem._try_cast_specific(
                world_server, sid, eid, target_eid, [_SKILL_PICADA])
        return False

    @staticmethod
    def _retreat(world_server, eid: int, bot, tm, fac, sid: str) -> None:
        """Foge em direção à PRÓPRIA base (`bot.route[0]` — a ponta da
        rota de lane mais perto do spawn, já calculada por `_push_lane`/
        `schedule_lane_push`) reaproveitando `_advance_toward` (mesmo
        A*/cache/dive-gate/torres-como-obstáculo de sempre — nenhuma
        lógica de movimento nova). Sem rota calculada ainda (raro — HP
        caiu antes do 1º `_push_lane` rodar, ex: focado por skill logo
        no spawn): foge 1 tile na direção oposta ao inimigo mais
        próximo conhecido, sem exigir pathfinding pronto."""
        if bot.route:
            BotAISystem._advance_toward(world_server, eid, bot, tm, bot.route[0],
                                        fac=fac, dive_target_eid=None)
            return

        target_eid, target_tm = BotAISystem._find_nearest_enemy(world_server, eid, fac, tm)
        if target_tm is None:
            return
        dx = 0 if target_tm.current_tile_x == tm.current_tile_x else (
            -1 if target_tm.current_tile_x > tm.current_tile_x else 1)
        dy = 0 if target_tm.current_tile_y == tm.current_tile_y else (
            -1 if target_tm.current_tile_y > tm.current_tile_y else 1)
        if dx == 0 and dy == 0:
            return
        world_server.move_player(sid, tm.current_tile_x + dx, tm.current_tile_y + dy)

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

        if not BotAISystem._dive_allowed(world_server, map_file, nxt, fac.faction_id, None, cur,
                                         my_eid=eid):
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
                      my_tile: tuple, my_eid: "int | None" = None) -> bool:
        """Gate do dive (16/08/2026, pedido do usuário; condição (a)
        reajustada 25/08/2026; gate de HP PRÓPRIO adicionado 08/09/2026,
        pedido do usuário): entrar num tile coberto por torre inimiga só
        é permitido se, ANTES de mais nada, o HP do PRÓPRIO bot estiver
        acima de `BOT_DIVE_HP_THRESHOLD_PCT` (nunca arrisca morrer pro
        dano da torre, mesmo com minion aliado por perto ou alvo baixo —
        as duas condições abaixo existem pra fechar KILL, não valem a
        pena se o bot morre no caminho) — e, satisfeito isso, (a) existe
        minion ALIADO perto DO PRÓPRIO BOT agora
        (`BOT_DIVE_ALLY_MINION_RADIUS_TILES` — andando JUNTO da leva),
        ou (b) o ALVO de combate atual do bot está abaixo do mesmo
        threshold (fechar kill). Tile fora de qualquer alcance de torre
        inimiga: sempre permitido (não precisa nem varrer torre, nem
        checar HP de ninguém).

        Condição (a) antes exigia minion aliado DENTRO do alcance da
        MESMA torre — janela frágil (minion morre rápido pra torre, não
        fica parado lá), causava o bot travar indefinidamente perto de
        torre mesmo com a leva por perto mas já tendo passado ou ainda
        não tendo chegado exatamente na torre (bug real de playtest,
        confirmado por reprodução — ver PROBLEMAS_ARQUITETURA.md)."""
        towers = world_server._enemy_towers_covering(map_file, tile, my_faction_id)
        if not towers:
            return True
        if my_eid is not None:
            from engine.components import CombatStats as _CSDiveMe
            my_cs = world_server.world.get_component(my_eid, _CSDiveMe)
            if (my_cs is not None and my_cs.max_hp > 0
                    and (my_cs.current_hp / my_cs.max_hp) <= BOT_DIVE_HP_THRESHOLD_PCT):
                return False
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
                world_server, map_file, next_tile, fac.faction_id, dive_target_eid, cur,
                my_eid=eid):
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
