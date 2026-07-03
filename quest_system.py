"""
quest_system.py — Lógica do sistema de quests.

QuestSystem        — processa QUEST_EVENTS, atualiza progresso, concede recompensas,
                     renderiza HUD lateral.
QuestDialogSystem  — modal WoW-style: indicadores !/? sobre NPCs, diálogo de aceite
                     e entrega de quests, lista de quests quando o NPC tem mais de uma.

Dados:  quests_data.py  (QuestDef, ObjectiveDef, QuestReward, QUESTS, QUEST_ITEMS)
Evento: quest_events.py (fire(), QUEST_EVENTS deque)
Comp:   components.py   (QuestLog, QuestGiver)
"""
from __future__ import annotations
import pygame
from ui_helpers import fill_surf
from fonts import make as _font

from systems import System
from quest_events import QUEST_EVENTS
from quests_data import QUESTS, ObjectiveDef
import quest_logic
from combat_log import LOG
from floating_text import PROC
from ui_scale_mixin import UIScaleMixin
from ui_sizes import UI


# ═══════════════════════════════════════════════════════════════════════════════
# QuestSystem — progressão e recompensas
# ═══════════════════════════════════════════════════════════════════════════════

class QuestSystem(UIScaleMixin, System):
    # ── HUD layout ──────────────────────────────────────────────────────────
    MAX_HUD_QUESTS   = UI.QUEST_HUD_MAX_VISIBLE
    HUD_MARGIN_RIGHT = UI.QUEST_HUD_MARGIN_RIGHT
    # Abaixo do minimapa — derivado de UI.MINIMAP_MARGIN_TOP + UI.MINIMAP_SIZE
    # (antes era um número fixo, 196, que tinha ficado desatualizado depois
    # que o minimapa cresceu de 120 pra 220px — corrigido junto da
    # centralização em ui_sizes.py).
    HUD_MARGIN_TOP   = UI.MINIMAP_MARGIN_TOP + UI.MINIMAP_SIZE + 6

    # Cores
    COL_TITLE  = (220, 200, 120)
    COL_PROG   = (170, 170, 170)  # cinza — sem nenhuma evolução ainda (progress == 0)
    COL_ACTIVE = (230, 230, 230)  # branco — alguma evolução, ainda não completo
    COL_DONE   = ( 80, 200, 100)  # verde — objetivo completo
    COL_REWARD = (255, 215,   0)

    _FONT_BASES = {"_font_title": 18, "_font_obj": 16}

    def __init__(self, world, player_entity: int) -> None:
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self._hud_cache_key:  "tuple | None"          = None
        self._hud_cache_surf: "pygame.Surface | None" = None
        self._current_map: str = ""
        self._last_reach_tile: tuple = (-1, -1, "")  # (tx, ty, map) — evita disparo por frame
        # Online: servidor é o único produtor autoritativo de progresso (ver
        # quest_logic.py/PROBLEMAS_ARQUITETURA.md) — injetado por game.py
        # após _connect_online(). None/falsy = caminho offline, inalterado.
        self._net = None

    def set_ui_scale(self, scale: float) -> None:
        super().set_ui_scale(scale)
        self._hud_cache_key = None  # força rebuild do cache na próxima render_hud()

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        pass  # renderização feita via render_hud() chamado pelo GameEngine

    # ── Update ──────────────────────────────────────────────────────────────

    def set_current_map(self, map_file: str) -> None:
        """Chamado pelo GameEngine sempre que o mapa muda."""
        self._current_map = map_file
        self._last_reach_tile = (-1, -1, "")

    def update(self, events=None, dt: float = 0) -> None:
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            QUEST_EVENTS.clear()
            return

        if self._net:
            # Online: servidor é o produtor autoritativo de progresso de quest.
            # A maioria dos eventos (kill, use_skill, etc.) já chega no servidor
            # via gatilhos server-side. Porém eventos disparados por sistemas
            # PURAMENTE client-side (ShopSystem.open → "talk_to_npc") nunca
            # chegam ao servidor. Para esses, encaminhamos via QUEST_ACCEPT
            # com quest_id="" — o handler server-side aplica o evento de
            # talk_to_npc e responde com QUEST_UPDATE se algo mudou.
            # Todos os outros eventos são descartados (o servidor já os processa
            # pelos próprios gatilhos).
            while QUEST_EVENTS:
                _evt, _peid, _data = QUEST_EVENTS.popleft()
                if _evt == "talk_to_npc":
                    _npc_name = _data.get("npc_name", "")
                    if _npc_name:
                        from shared.messages import MsgType as _MTtalk
                        self._net.send(_MTtalk.QUEST_ACCEPT, {
                            "quest_id": "",       # nenhuma quest pra aceitar —
                            "npc_name": _npc_name,# só dispara talk_to_npc no servidor
                        })
            return

        # Dispara reach_tile com posição atual do player a cada frame
        self._fire_reach_tile(ql)

        while QUEST_EVENTS:
            event_type, _player_eid, data = QUEST_EVENTS.popleft()
            if quest_logic.apply_event(ql, event_type, data):
                self._hud_cache_key = None   # invalida cache HUD

        # collect_item: sincroniza progresso com inventário real (cobre itens já na bag)
        self._sync_collect_progress(ql)

    def _process_talk_to_npc(self, npc_name: str) -> None:
        """Processa imediatamente um evento talk_to_npc sem passar pela fila.
        Chamado por QuestDialogSystem._open_dialog antes de calcular o estado do
        diálogo. Só no caminho OFFLINE — online, o servidor processa o mesmo
        evento dentro de _handle_quest_accept/_handle_quest_turn_in (ver
        QuestDialogSystem.handle_events) usando o npc_name enviado no payload."""
        if self._net:
            return
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return
        if quest_logic.apply_event(ql, "talk_to_npc", {"npc_name": npc_name}):
            self._hud_cache_key = None

    # ── API para QuestDialogSystem ───────────────────────────────────────────

    def can_turn_in(self, qid: str) -> bool:
        """True se a quest está ativa e todos os objetivos concluídos."""
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return False
        return quest_logic.can_turn_in(ql, qid)

    def turn_in(self, qid: str) -> None:
        """Completa e recompensa a quest (caminho OFFLINE — online, o
        QuestDialogSystem manda QUEST_TURN_IN em vez de chamar isto)."""
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql:
            self._complete_quest(ql, qid)

    # ── Drop condicional ─────────────────────────────────────────────────────

    def get_conditional_loot(self, enemy_name: str, enemy_race: str) -> list:
        """Caminho OFFLINE — online, o drop condicional é rolado no servidor
        (server/server_death_handler.py) contra o QuestLog autoritativo."""
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return []
        return quest_logic.roll_conditional_loot(ql, enemy_name, enemy_race)

    # ── HUD ─────────────────────────────────────────────────────────────────

    def render_hud(self, screen: pygame.Surface) -> None:
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None or not ql.active:
            self._hud_cache_key  = None
            self._hud_cache_surf = None
            return

        cache_key = tuple(
            (qid, tuple(prog))
            for qid, prog in list(ql.active.items())[:self.MAX_HUD_QUESTS]
        )
        if cache_key != self._hud_cache_key:
            self._hud_cache_key  = cache_key
            self._hud_cache_surf = self._build_hud_surf(ql)

        if self._hud_cache_surf is None:
            return

        sw = screen.get_width()
        screen.blit(self._hud_cache_surf,
                    (sw - self._u(self.HUD_MARGIN_RIGHT) - self._hud_cache_surf.get_width(),
                     self._u(self.HUD_MARGIN_TOP)))

    def _build_hud_surf(self, ql) -> "pygame.Surface | None":
        lines: list[tuple] = []
        y = 0
        shown = 0
        for qid, prog in list(ql.active.items()):
            if shown >= self.MAX_HUD_QUESTS:
                break
            qdef = QUESTS.get(qid)
            if qdef is None:
                continue
            ts = self._font_title.render(qdef.title, True, self.COL_TITLE)
            lines.append((ts, y))
            y += self._u(17)
            for i, obj in enumerate(qdef.objectives):
                p = prog[i]
                if p >= obj.count:
                    color = self.COL_DONE
                elif p > 0:
                    color = self.COL_ACTIVE
                else:
                    color = self.COL_PROG
                surf = self._font_obj.render(self._obj_label(obj, p), True, color)
                lines.append((surf, y))
                y += self._u(14)
            y += self._u(5)
            shown += 1

        if not lines:
            return None

        max_w   = max(s.get_width() for s, _ in lines)
        total_h = y
        hud = pygame.Surface((max_w, total_h), pygame.SRCALPHA)
        hud.fill((0, 0, 0, 0))
        for surf, ypos in lines:
            hud.blit(surf, (max_w - surf.get_width(), ypos))
        return hud

    # ── Internos ─────────────────────────────────────────────────────────────

    def auto_start_quests(self) -> None:
        """Inicia quests com auto_start=True elegíveis. Chamado no boot."""
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return
        for qid, qdef in QUESTS.items():
            if qdef.auto_start:
                self._try_start(ql, qid)

    def _player_level(self) -> int:
        from components import CharacterStats
        char = self.world.get_component(self.player_entity, CharacterStats)
        return char.level if char else 1

    def _player_class_id(self) -> str:
        from components import CharacterStats
        char = self.world.get_component(self.player_entity, CharacterStats)
        return char.class_id if char else ""

    def _try_start(self, ql, qid: str) -> bool:
        """Caminho OFFLINE — online, o QuestDialogSystem manda QUEST_ACCEPT
        em vez de chamar isto (servidor valida com seus próprios dados)."""
        qdef = QUESTS.get(qid)
        if qdef and qid not in ql.active and qid not in ql.completed and \
                all(r in ql.completed for r in qdef.requires) and \
                qdef.level_req > 0 and self._player_level() < qdef.level_req:
            LOG.add(f'Nivel {qdef.level_req} necessario para "{qdef.title}".', (200, 80, 80))
        ok = quest_logic.try_start(self.world, self.player_entity, ql, qid)
        if ok:
            self._hud_cache_key = None
            LOG.add(f'Quest: "{qdef.title}" iniciada.', self.COL_TITLE)
        return ok

    def _fire_reach_tile(self, ql) -> None:
        """Dispara reach_tile apenas quando o player muda de tile (não todo frame)."""
        from components import TileMovement
        tm = self.world.get_component(self.player_entity, TileMovement)
        if tm is None or tm.is_moving:
            return
        current = (tm.current_tile_x, tm.current_tile_y, self._current_map)
        if current == self._last_reach_tile:
            return  # mesmo tile do último disparo — não reenfileira
        has_reach = any(
            obj.type == "reach_tile"
            for prog, qid in ((ql.active[q], q) for q in ql.active)
            for i, obj in enumerate(QUESTS[qid].objectives)
            if QUESTS.get(qid) and prog[i] < QUESTS[qid].objectives[i].count
        )
        if not has_reach:
            return
        self._last_reach_tile = current
        from quest_events import fire as _qfire_rt
        _qfire_rt("reach_tile", tx=tm.current_tile_x, ty=tm.current_tile_y, map=self._current_map)

    def _sync_collect_progress(self, ql) -> None:
        """Sincroniza objetivos collect_item com o inventário real do player."""
        from components import Inventory
        inv = self.world.get_component(self.player_entity, Inventory)
        if quest_logic.sync_collect_progress(ql, inv):
            self._hud_cache_key = None

    def _complete_quest(self, ql, qid: str) -> None:
        """Caminho OFFLINE — online, o QuestDialogSystem manda QUEST_TURN_IN
        em vez de chamar isto (servidor valida/aplica XP/gold/itens)."""
        from save_system import request_autosave

        qdef = QUESTS.get(qid)
        if qdef is None:
            return

        reward = quest_logic.complete_quest(self.world, self.player_entity, ql, qid)
        if reward is None:
            return
        self._hud_cache_key = None

        if reward.xp > 0:
            from components import CharacterStats, CombatStats, PermanentStats, PlayerControlled
            from stats_system import process_levelups
            for eid, char, cs, _ in self.world.get_entities_with(
                    CharacterStats, CombatStats, PlayerControlled):
                perm = self.world.get_component(eid, PermanentStats)
                char.current_xp += reward.xp
                process_levelups(self.world, eid, char, cs, perm)
                break

        if reward.gold > 0:
            from components import Wallet, PlayerControlled as _PCgold
            for _, wlt, _ in self.world.get_entities_with(Wallet, _PCgold):
                wlt.gold += reward.gold
                break

        parts = []
        if reward.xp:   parts.append(f"+{reward.xp} XP")
        if reward.gold: parts.append(f"+{reward.gold} ouro")
        reward_str = f" ({', '.join(parts)})" if parts else ""
        LOG.add(f'Quest completa: "{qdef.title}"{reward_str}!', self.COL_REWARD)
        PROC.add("Quest Completa!", self.COL_REWARD)

        request_autosave()

    # ── Match de evento ──────────────────────────────────────────────────────

    @staticmethod
    def _matches(event_type: str, data: dict, obj: ObjectiveDef) -> bool:
        return quest_logic.match_objective(event_type, data, obj)

    @staticmethod
    def _skill_label(skill_id: str) -> str:
        """Nome amigável de uma skill pro texto de objetivo de quest (ex:
        'bola_de_fogo' -> 'Bola de Fogo'). Cai pro id cru se não achar no catálogo."""
        from skill_config import SKILL_CATALOG
        entry = SKILL_CATALOG.get(skill_id, {})
        return entry.get("name", skill_id) if isinstance(entry, dict) else skill_id

    @staticmethod
    def _obj_label(obj: ObjectiveDef, progress: int, show_progress: bool = True) -> str:
        """Texto do objetivo. Padrão único pra todos os tipos: '<descrição>
        (progresso/total)' — nunca 'sim'/'não', sempre numérico (ver pedido do
        usuário: progresso 0 até completar, N/N quando completo).
        show_progress=False omite o '(x/y)' — usado na apresentação da quest
        (diálogo de aceitar, antes de iniciada) a pedido do usuário; HUD e
        diário continuam mostrando o progresso numérico."""
        if obj.type == "kill":
            alvo = obj.target if obj.target != "*" else "inimigo"
            desc = f"Matar {alvo}"
        elif obj.type == "collect_item":
            nome = obj.loot_item or obj.target
            desc = f"Coletar {nome}"
        elif obj.type == "reach_tile":
            desc = "Chegar ao destino"
        elif obj.type == "use_skill":
            nome = QuestSystem._skill_label(obj.target)
            desc = (f"Treinar {nome} no boneco de treino" if obj.params.get("on_dummy")
                    else f"Usar {nome}")
        elif obj.type == "learn_skill":
            desc = f"Aprender {QuestSystem._skill_label(obj.target)}"
        elif obj.type == "use_consumable":
            alvo = obj.target if obj.target != "*" else "consumivel"
            desc = f"Usar {alvo}"
        elif obj.type == "reach_level":
            desc = f"Alcançar nivel {obj.count}"
        elif obj.type == "talk_to_npc":
            alvo = obj.target if obj.target != "*" else "NPC"
            desc = f"Falar com {alvo}"
        elif obj.type == "equip_item":
            desc = f"Equipar {obj.target}"
        elif obj.type == "use_item_on_target":
            item  = obj.params.get("item_name", "?")
            alvo  = obj.target if obj.target != "*" else "inimigo"
            desc = f"Usar {item} em {alvo}"
        else:
            desc = obj.type

        if not show_progress:
            return desc
        return f"{desc} ({progress}/{obj.count})"


# ═══════════════════════════════════════════════════════════════════════════════
# QuestDialogSystem — modal WoW-style
# ═══════════════════════════════════════════════════════════════════════════════

class QuestDialogSystem(UIScaleMixin, System):
    """
    Gerencia a interação visual com NPCs que têm quests.

    Estados do diálogo:
        "list"   — NPC tem mais de uma quest; lista para seleção.
        "detail" — Descrição + objetivos + recompensas de uma quest disponível.
                   Botões: [Aceitar] [Recusar]
        "turnin" — Quest completa pronta para entrega.
                   Botão: [Completar Missao]

    Indicadores no mundo (renderizados sobre o NPC):
        ! amarelo — quest disponível para aceitar
        ? amarelo — quest pronta para entrega
        ? cinza   — quest aceita e em progresso
    Prioridade: ? amarelo > ! amarelo > ? cinza
    """

    PANEL_W = UI.QUEST_DIALOG_W
    PANEL_H = UI.QUEST_DIALOG_H
    PAD     = UI.QUEST_DIALOG_PAD

    COL_GOLD   = (255, 215,   0)
    COL_GREY   = (150, 150, 150)
    COL_WHITE  = (220, 220, 220)
    COL_TITLE  = (220, 200, 120)
    COL_GREEN  = ( 80, 200, 100)
    COL_BG     = ( 15,  10,   5, 235)
    COL_BORDER = (140, 100,  60)

    _FONT_BASES = {"_font_lg": 26, "_font_body": 21, "_font_sm": 18}

    def __init__(self, world, player_entity: int,
                 screen: pygame.Surface, quest_system: QuestSystem) -> None:
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.hud_surf        = screen
        self._qs           = quest_system

        # Estado do diálogo
        self._dialog_npc_id:      int = -1
        self._dialog_state:       str = ""   # "list" | "detail" | "turnin"
        self._dialog_selected_qid: str = ""

        # Caminhada até o NPC
        self._pending_npc_id:       int  = -1
        self._right_click_consumed: bool = False

        # Rects para hit-test em handle_events
        self._list_rects:    dict                    = {}
        self._accept_rect:   "pygame.Rect | None"    = None
        self._decline_rect:  "pygame.Rect | None"    = None
        self._complete_rect: "pygame.Rect | None"    = None
        self._close_rect:    "pygame.Rect | None"    = None

    @property
    def is_open(self) -> bool:
        return self._dialog_npc_id != -1

    # ── Update ──────────────────────────────────────────────────────────────

    def update(self, events=None, dt: float = 0) -> None:
        from components import QuestGiver as _QG, Position, Renderable, PlayerAutoMove
        self._right_click_consumed = False

        # Verifica se o player chegou até o NPC pendente
        if self._pending_npc_id != -1:
            nt = self._npc_tile(self._pending_npc_id)
            pt = self._player_tile()
            if nt is None:
                self._pending_npc_id = -1
            elif pt and self._cheby(pt, nt) <= 1:
                auto = self.world.get_component(self.player_entity, PlayerAutoMove)
                if auto:
                    auto.active        = False
                    auto.path          = []
                    auto.ground_target = None
                npc_id = self._pending_npc_id
                self._pending_npc_id = -1
                self._open_dialog(npc_id)

        if not events:
            return

        cam_x, cam_y = self._get_cam()

        for event in events:
            if (event.type == pygame.MOUSEBUTTONDOWN
                    and event.button == 3
                    and not self._right_click_consumed
                    and not self.is_open):
                mx, my = event.pos
                _sc = (self.world_surf or self.hud_surf).get_width() / max(1, self.hud_surf.get_width())
                wx, wy = mx * _sc + cam_x, my * _sc + cam_y
                for eid, pos, rend, _ in self.world.get_entities_with(
                        Position, Renderable, _QG):
                    hw = rend.width  / 2
                    hh = rend.height / 2
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        gt = self._npc_tile(eid)
                        if pt and gt and self._cheby(pt, gt) <= 1:
                            self._open_dialog(eid)
                        else:
                            self._pending_npc_id = eid
                            self._walk_to_npc(eid)
                        self._right_click_consumed = True
                        break

    # ── Eventos do modal ─────────────────────────────────────────────────────

    def handle_events(self, events: list) -> None:
        if not self.is_open:
            return
        for event in events:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._close()
                return
            if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
                continue
            mx, my = event.pos

            # Botão X (fechar)
            if hasattr(self, "_close_rect") and self._close_rect and \
                    self._close_rect.collidepoint(mx, my):
                self._close()
                return


            if self._dialog_state == "list":
                for qid, rect in self._list_rects.items():
                    if rect.collidepoint(mx, my):
                        self._dialog_selected_qid = qid
                        self._dialog_state = "turnin" if self._qs.can_turn_in(qid) else "detail"
                        return

            elif self._dialog_state == "detail":
                if self._accept_rect and self._accept_rect.collidepoint(mx, my):
                    qid = self._dialog_selected_qid
                    if self._qs._net:
                        from shared.messages import MsgType as _MTqa
                        self._qs._net.send(_MTqa.QUEST_ACCEPT, {
                            "quest_id": qid, "npc_name": self._npc_name(self._dialog_npc_id),
                        })
                    else:
                        from components import QuestLog
                        ql = self.world.get_component(self.player_entity, QuestLog)
                        if ql:
                            self._qs._try_start(ql, qid)
                    self._close()
                    return
                if self._decline_rect and self._decline_rect.collidepoint(mx, my):
                    self._close()
                    return

            elif self._dialog_state == "turnin":
                if self._complete_rect and self._complete_rect.collidepoint(mx, my):
                    saved_npc = self._dialog_npc_id
                    qid = self._dialog_selected_qid
                    self._close()
                    if self._qs._net:
                        from shared.messages import MsgType as _MTqt
                        self._qs._net.send(_MTqt.QUEST_TURN_IN, {
                            "quest_id": qid, "npc_name": self._npc_name(saved_npc),
                        })
                        # Online: reabertura de quests em cadeia depende da
                        # confirmação do servidor (QUEST_UPDATE) — não reflete
                        # nesta mesma frame, latência aceitável (1 round-trip).
                    else:
                        self._qs.turn_in(qid)
                        # Re-abre se o NPC tiver mais quests (cadeia ou múltiplas)
                        avail = self._get_available_quests(saved_npc)
                        comp  = self._get_completable_quests(saved_npc)
                        if avail or comp:
                            self._open_dialog(saved_npc)
                    return

    # ── Render — indicadores no mundo ────────────────────────────────────────

    def render_world(self, cam_x: float = 0, cam_y: float = 0) -> None:
        from components import QuestGiver as _QG, Position, Renderable, Visible

        for eid, pos, rend, _, _ in self.world.get_entities_with(Position, Renderable, _QG, Visible):
            avail       = self._get_available_quests(eid)
            completable = self._get_completable_quests(eid)
            inprog      = self._get_inprogress_quests(eid)
            locked      = self._get_locked_quests(eid)

            if not avail and not completable and not inprog and not locked:
                continue

            if completable:
                color, symbol = self.COL_GOLD, "?"
            elif avail:
                color, symbol = self.COL_GOLD, "!"
            elif inprog:
                color, symbol = self.COL_GREY, "?"
            else:
                # somente quests bloqueadas por nível
                color, symbol = self.COL_GREY, "!"

            sx = int(pos.x - cam_x)
            sy = int(pos.y - cam_y - rend.height / 2 - 14)

            pygame.draw.circle(self.world_surf, (30, 25, 10), (sx, sy), 9)
            pygame.draw.circle(self.world_surf, color, (sx, sy), 9, 2)
            glyph = self._font_lg.render(symbol, True, color)
            self.world_surf.blit(glyph, (sx - glyph.get_width() // 2,
                                     sy - glyph.get_height() // 2))

    # ── Render — modal ───────────────────────────────────────────────────────

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        if not self.is_open:
            return
        from components import QuestGiver as _QG, NPC as _NPC

        giver = self.world.get_component(self._dialog_npc_id, _QG)
        if giver is None:
            self._close()
            return
        _npc_comp = self.world.get_component(self._dialog_npc_id, _NPC)
        npc_name  = _npc_comp.name if _npc_comp else "NPC"

        SW, SH = self.hud_surf.get_size()
        self.hud_surf.blit(fill_surf((SW, SH), (0, 0, 0, 160)), (0, 0))

        x0, y0 = self._panel_origin()
        W, H   = self._u(self.PANEL_W), self._u(self.PANEL_H)
        self.hud_surf.blit(fill_surf((W, H), self.COL_BG), (x0, y0))
        pygame.draw.rect(self.hud_surf, self.COL_BORDER, (x0, y0, W, H), 2, border_radius=4)

        # Header: nome do NPC
        npc_surf = self._font_lg.render(npc_name, True, self.COL_TITLE)
        self.hud_surf.blit(npc_surf, (x0 + self._u(self.PAD), y0 + self._u(self.PAD)))
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (x0 + self._u(4), y0 + self._u(42)), (x0 + W - self._u(4), y0 + self._u(42)))

        # Botão fechar (X)
        close_r = pygame.Rect(x0 + W - self._u(36), y0 + self._u(4), self._u(32), self._u(32))
        mx, my  = pygame.mouse.get_pos()
        hov_x   = close_r.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (180, 60, 60) if hov_x else (100, 35, 35),
                         close_r, border_radius=3)
        xs = self._font_body.render("X", True, (255, 255, 255))
        self.hud_surf.blit(xs, (close_r.centerx - xs.get_width() // 2,
                              close_r.centery - xs.get_height() // 2))
        self._close_rect = close_r

        if self._dialog_state == "list":
            self._render_list(x0, y0, giver)
        elif self._dialog_state == "detail":
            self._render_detail(x0, y0)
        elif self._dialog_state == "turnin":
            self._render_turnin(x0, y0)

    # ── Sub-renders ──────────────────────────────────────────────────────────

    def _render_list(self, x0: int, y0: int, giver) -> None:
        self._list_rects = {}
        avail = self._get_available_quests(self._dialog_npc_id)
        comp  = self._get_completable_quests(self._dialog_npc_id)
        mx, my = pygame.mouse.get_pos()
        PAD = self._u(self.PAD)
        y = y0 + self._u(52)

        hint = self._font_sm.render("Escolha uma missao:", True, self.COL_GREY)
        self.hud_surf.blit(hint, (x0 + PAD, y))
        y += self._u(22)

        for qid, is_turnin in [(q, True) for q in comp] + [(q, False) for q in avail]:
            qdef = QUESTS.get(qid)
            if qdef is None:
                continue
            symbol = "?" if is_turnin else "!"
            color  = self.COL_GOLD
            row_r  = pygame.Rect(x0 + PAD, y, self._u(self.PANEL_W) - PAD * 2, self._u(30))
            if row_r.collidepoint(mx, my):
                pygame.draw.rect(self.hud_surf, (45, 38, 18), row_r, border_radius=3)
                pygame.draw.rect(self.hud_surf, self.COL_BORDER, row_r, 1, border_radius=3)
            label = self._font_body.render(f"[{symbol}]  {qdef.title}", True, color)
            self.hud_surf.blit(label, (x0 + PAD + self._u(6), y + self._u(6)))
            self._list_rects[qid] = row_r
            y += self._u(34)

    def _render_detail(self, x0: int, y0: int) -> None:
        self._accept_rect  = None
        self._decline_rect = None
        qdef = QUESTS.get(self._dialog_selected_qid)
        if qdef is None:
            return

        W, PAD = self._u(self.PANEL_W), self._u(self.PAD)
        mx, my = pygame.mouse.get_pos()
        y = y0 + self._u(52)

        # Título da quest
        ts = self._font_lg.render(qdef.title, True, self.COL_TITLE)
        self.hud_surf.blit(ts, (x0 + PAD, y))
        y += self._u(28)

        # Descrição
        for line in self._wrap(qdef.description, W - PAD * 2, self._font_body):
            self.hud_surf.blit(self._font_body.render(line, True, self.COL_WHITE),
                             (x0 + PAD, y))
            y += self._u(20)
        y += self._u(8)

        # Objetivos
        self.hud_surf.blit(self._font_sm.render("Objetivos:", True, self.COL_GREY),
                         (x0 + PAD, y))
        y += self._u(18)
        for obj in qdef.objectives:
            s = self._font_sm.render(f"  {QuestSystem._obj_label(obj, 0, show_progress=False)}",
                                     True, self.COL_GREY)
            self.hud_surf.blit(s, (x0 + PAD, y))
            y += self._u(16)
        y += self._u(8)

        # Recompensas
        parts = []
        if qdef.reward.xp:   parts.append(f"+{qdef.reward.xp} XP")
        if qdef.reward.gold: parts.append(f"+{qdef.reward.gold} ouro")
        if parts:
            rew = self._font_sm.render("Recompensa: " + ", ".join(parts),
                                       True, self.COL_GOLD)
            self.hud_surf.blit(rew, (x0 + PAD, y))

        # Botões na base do painel — alinhados à direita: [Recusar] [Aceitar]
        btn_y  = y0 + self._u(self.PANEL_H) - self._u(48)
        acc_r  = pygame.Rect(x0 + self._u(self.PANEL_W) - PAD - self._u(150), btn_y, self._u(150), self._u(32))
        dec_r  = pygame.Rect(x0 + self._u(self.PANEL_W) - PAD - self._u(150) - self._u(158), btn_y, self._u(150), self._u(32))
        for rect, label, c_hov, c_nor in [
            (dec_r, "Recusar",  (120, 50, 50), (70, 30, 30)),
            (acc_r, "Aceitar",  (60, 120, 60), (35, 70, 35)),
        ]:
            hov = rect.collidepoint(mx, my)
            pygame.draw.rect(self.hud_surf, c_hov if hov else c_nor, rect, border_radius=4)
            pygame.draw.rect(self.hud_surf, self.COL_BORDER, rect, 1, border_radius=4)
            txt = self._font_body.render(label, True, self.COL_WHITE)
            self.hud_surf.blit(txt, (rect.centerx - txt.get_width() // 2,
                                   rect.centery - txt.get_height() // 2))
        self._accept_rect  = acc_r
        self._decline_rect = dec_r

    def _render_turnin(self, x0: int, y0: int) -> None:
        self._complete_rect = None
        qdef = QUESTS.get(self._dialog_selected_qid)
        if qdef is None:
            return

        PAD      = self._u(self.PAD)
        max_w    = self._u(self.PANEL_W) - PAD * 2
        mx, my   = pygame.mouse.get_pos()
        btn_y    = y0 + self._u(self.PANEL_H) - self._u(48)
        y        = y0 + self._u(52)

        # Cabeçalho
        hdr = self._font_lg.render("Missao Completa!", True, self.COL_GOLD)
        self.hud_surf.blit(hdr, (x0 + PAD, y))
        y += self._u(28)

        # Título da quest
        ts = self._font_body.render(qdef.title, True, self.COL_TITLE)
        self.hud_surf.blit(ts, (x0 + PAD, y))
        y += self._u(22)

        # Linha separadora
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (x0 + PAD, y), (x0 + self._u(self.PANEL_W) - PAD, y))
        y += self._u(10)

        # Texto de conclusão do NPC (ou fallback genérico)
        completion_text = getattr(qdef, "completion", "") or "Bom trabalho. Aqui esta sua recompensa."
        for line in self._wrap(completion_text, max_w, self._font_body):
            s = self._font_body.render(line, True, self.COL_WHITE)
            self.hud_surf.blit(s, (x0 + PAD, y))
            y += s.get_height() + self._u(2)
        y += self._u(12)

        # Recompensas
        parts = []
        if qdef.reward.xp:   parts.append(f"+{qdef.reward.xp} XP")
        if qdef.reward.gold: parts.append(f"+{qdef.reward.gold} ouro")
        if parts:
            rew_hdr = self._font_sm.render("Recompensa:", True, (160, 140, 80))
            self.hud_surf.blit(rew_hdr, (x0 + PAD, y))
            y += rew_hdr.get_height() + self._u(4)
            rew = self._font_body.render("  " + "  |  ".join(parts), True, self.COL_GOLD)
            self.hud_surf.blit(rew, (x0 + PAD, y))

        # Botão Concluir
        comp_r = pygame.Rect(x0 + self._u(self.PANEL_W) - PAD - self._u(180), btn_y, self._u(180), self._u(32))
        hov    = comp_r.collidepoint(mx, my)
        pygame.draw.rect(self.hud_surf, (60, 110, 60) if hov else (35, 65, 35),
                         comp_r, border_radius=4)
        pygame.draw.rect(self.hud_surf, self.COL_BORDER, comp_r, 1, border_radius=4)
        txt = self._font_body.render("Concluir", True, self.COL_WHITE)
        self.hud_surf.blit(txt, (comp_r.centerx - txt.get_width() // 2,
                               comp_r.centery - txt.get_height() // 2))
        self._complete_rect = comp_r

    # ── Helpers de diálogo ───────────────────────────────────────────────────

    def _open_dialog(self, npc_id: int) -> None:
        from components import NPC as _NPC
        _npc_c = self.world.get_component(npc_id, _NPC)
        _npc_name = _npc_c.name if _npc_c else "NPC"

        # Processa talk_to_npc ANTES de calcular o estado do diálogo
        # para que objetivos de "falar com NPC" fiquem completos antes da checagem.
        self._qs._process_talk_to_npc(_npc_name)

        avail = self._get_available_quests(npc_id)
        # Re-calcula completáveis APÓS processar talk_to_npc.
        # Passa _npc_name: online, _process_talk_to_npc é no-op mas o check
        # de can_turn_in_after_talk permite abrir o modal de entrega para
        # quests cujo único objetivo pendente é talk_to_npc deste NPC.
        comp  = self._get_completable_quests(npc_id, _npc_name)
        total = comp + avail   # completáveis têm prioridade na lista

        if not total:
            inprog = self._get_inprogress_quests(npc_id)
            name  = _npc_name
            if inprog:
                LOG.add(f"{name}: Continue sua missao.", self._qs.COL_PROG)
            else:
                LOG.add(f"{name}: Sem missoes para voce no momento.", self._qs.COL_PROG)
            return

        self._dialog_npc_id = npc_id
        self._list_rects    = {}

        if len(total) == 1:
            qid = total[0]
            self._dialog_selected_qid = qid
            self._dialog_state = "turnin" if qid in comp else "detail"
        else:
            self._dialog_state        = "list"
            self._dialog_selected_qid = ""

    def _close(self) -> None:
        self._dialog_npc_id       = -1
        self._dialog_state        = ""
        self._dialog_selected_qid = ""
        self._list_rects          = {}
        self._accept_rect         = None
        self._decline_rect        = None
        self._complete_rect       = None
        self._close_rect          = None

    # ── Consultas ao QuestLog ────────────────────────────────────────────────

    def _get_available_quests(self, npc_id: int) -> list:
        """Quests que o NPC pode oferecer (não iniciadas, pré-req ok, nível ok,
        classe ok). Quest com class_req != classe do player é tratada como se
        não existisse pra ele — nunca aparece aqui nem em _get_locked_quests."""
        from components import QuestLog, QuestGiver as _QG
        giver = self.world.get_component(npc_id, _QG)
        if giver is None:
            return []
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return []
        plvl  = self._qs._player_level()
        pclass = self._qs._player_class_id()
        result = []
        for qid in giver.quest_ids:
            if qid in ql.active or qid in ql.completed:
                continue
            qdef = QUESTS.get(qid)
            if qdef is None or (qdef.class_req and qdef.class_req != pclass):
                continue
            if all(r in ql.completed for r in qdef.requires):
                if qdef.level_req <= plvl:
                    result.append(qid)
        return result

    def _get_locked_quests(self, npc_id: int) -> list:
        """Quests com level_req acima do nível atual (pré-req ok mas bloqueadas).
        Quest restrita a outra classe NUNCA aparece aqui (fica invisível, não
        bloqueada) — ver _get_available_quests."""
        from components import QuestLog, QuestGiver as _QG
        giver = self.world.get_component(npc_id, _QG)
        if giver is None:
            return []
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return []
        plvl  = self._qs._player_level()
        pclass = self._qs._player_class_id()
        result = []
        for qid in giver.quest_ids:
            if qid in ql.active or qid in ql.completed:
                continue
            qdef = QUESTS.get(qid)
            if qdef is None or (qdef.class_req and qdef.class_req != pclass):
                continue
            if all(r in ql.completed for r in qdef.requires):
                if qdef.level_req > plvl:
                    result.append(qid)
        return result

    def _get_completable_quests(self, npc_id: int, pending_npc_name: str = "") -> list:
        """Quests que o NPC aceita para entrega e estão 100% concluídas.

        pending_npc_name: se fornecido, também inclui quests cujo único
        objetivo pendente é talk_to_npc direcionado a este NPC — espelha o
        comportamento do servidor que aplica talk_to_npc antes de can_turn_in."""
        from components import QuestGiver as _QG
        giver = self.world.get_component(npc_id, _QG)
        if giver is None:
            return []
        ids = giver.turn_in_ids if giver.turn_in_ids else giver.quest_ids
        result = []
        for qid in ids:
            if self._qs.can_turn_in(qid):
                result.append(qid)
            elif pending_npc_name:
                from components import QuestLog as _QL_ct
                ql = self.world.get_component(self.player_entity, _QL_ct)
                if ql and quest_logic.can_turn_in_after_talk(ql, qid, pending_npc_name):
                    result.append(qid)
        return result

    def _get_inprogress_quests(self, npc_id: int) -> list:
        """Quests aceitas e em progresso (incompletas)."""
        from components import QuestGiver as _QG
        giver = self.world.get_component(npc_id, _QG)
        if giver is None:
            return []
        ids = giver.turn_in_ids if giver.turn_in_ids else giver.quest_ids
        result = []
        for qid in ids:
            if self._qs.can_turn_in(qid):
                continue   # completável, não em progresso
            from components import QuestLog
            ql = self.world.get_component(self.player_entity, QuestLog)
            if ql and qid in ql.active:
                result.append(qid)
        return result

    # ── Helpers de navegação/tile ────────────────────────────────────────────

    def _get_cam(self):
        # Usa world_surf (superfície lógica de zoom), não hud_surf (tela
        # real) — sem isso o clique calcula a posição mundial errada quando
        # self._zoom != 1.0 (ver ShopSystem._get_cam() em systems.py, que já
        # faz certo). _open_dialog/click usa esse offset + _sc (ver update()).
        # Fallback pra hud_surf se world_surf ainda não foi atribuído (1º
        # frame, antes de game.py::_assign_world_surf rodar — zoom=1.0).
        from components import Camera, Position as _Pos
        SW, SH = (self.world_surf or self.hud_surf).get_size()
        for _, pos, _ in self.world.get_entities_with(_Pos, Camera):
            return pos.x - SW / 2, pos.y - SH / 2
        return 0.0, 0.0

    def _player_tile(self):
        from components import TileMovement
        tm = self.world.get_component(self.player_entity, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _npc_tile(self, eid: int):
        from tileset import TILE_SIZE as TS
        from components import Position
        pos = self.world.get_component(eid, Position)
        return (int(pos.x / TS), int(pos.y / TS)) if pos else None

    def _npc_name(self, eid: int) -> str:
        from components import NPC as _NPCname
        npc = self.world.get_component(eid, _NPCname)
        return npc.name if npc else "NPC"

    @staticmethod
    def _cheby(t1, t2) -> int:
        from utils import chebyshev
        return chebyshev(t1[0], t1[1], t2[0], t2[1])

    def _walk_to_npc(self, npc_eid: int) -> None:
        from components import PlayerAutoMove
        pt = self._player_tile()
        nt = self._npc_tile(npc_eid)
        if not pt or not nt:
            return
        adj = [(nt[0]+dx, nt[1]+dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        target = min(adj, key=lambda t: abs(t[0] - pt[0]) + abs(t[1] - pt[1]))
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        if auto:
            auto.ground_target     = target
            auto.path              = []
            auto.active            = True
            auto.path_recalc_timer = 0.0

    def _panel_origin(self):
        x0, y0 = self._safe_panel_origin(self.PANEL_W, self.PANEL_H)
        return x0 + UI.QUEST_DIALOG_OFFSET_X, y0 + UI.QUEST_DIALOG_OFFSET_Y

    @staticmethod
    def _wrap(text: str, max_px: int, font) -> list:
        words = text.split()
        lines, cur = [], ""
        for word in words:
            test = cur + (" " if cur else "") + word
            if font.size(test)[0] <= max_px:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines


# ═══════════════════════════════════════════════════════════════════════════════
# QuestJournalSystem — modal do diário de quests (tecla J)
# ═══════════════════════════════════════════════════════════════════════════════

class QuestJournalSystem(UIScaleMixin, System):
    """
    Diário de quests acessível pela tecla J.

    Layout:
        Painel 700×520 centralizado.
        Coluna esquerda (220px): lista de quests ativas (amarelo) e completas (verde).
        Coluna direita (440px): detalhes da quest selecionada.

    Nenhuma ação pode ser executada aqui — é somente leitura.
    Aceitar / entregar quests é feito pelo diálogo com o NPC.
    """

    PANEL_W  = UI.QUEST_JOURNAL_W
    PANEL_H  = UI.QUEST_JOURNAL_H
    LIST_W   = UI.QUEST_JOURNAL_LIST_W
    PAD      = UI.QUEST_JOURNAL_PAD

    COL_ACTIVE    = (220, 200, 120)
    COL_DONE_TITLE = ( 80, 200, 100)
    COL_LOCKED    = (120, 100,  60)
    COL_PROG      = (160, 160, 160)
    COL_DONE_OBJ  = ( 80, 200, 100)
    COL_REWARD    = (255, 215,   0)
    COL_WHITE     = (220, 220, 220)
    COL_GREY      = (140, 140, 140)
    COL_HEADER    = (180, 150,  70)
    COL_BG        = ( 15,  10,   5, 235)
    COL_BORDER    = (140, 100,  60)
    COL_SEL_BG    = ( 50,  40,  15)
    COL_HOVER_BG  = ( 35,  28,   8)

    _FONT_BASES = {"_font_title": 24, "_font_body": 21, "_font_sm": 18}

    def __init__(self, world, player_entity: int,
                 screen: pygame.Surface, quest_system: QuestSystem) -> None:
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.hud_surf        = screen
        self._qs           = quest_system

        self.is_open         = False
        self._selected_qid   = ""
        self._scroll_offset  = 0          # pixels de scroll na lista
        self._list_entries: list = []     # [(qid, label, color, is_section_header)]
        self._entry_rects:  list = []     # pygame.Rect por entry (somente não-headers)
        self._close_rect:   "pygame.Rect | None" = None
        self._abandon_rect: "pygame.Rect | None" = None

    # ── API pública ──────────────────────────────────────────────────────────

    def open(self) -> None:
        self.is_open        = True
        self._scroll_offset = 0
        self._rebuild_list()
        # Seleciona a primeira quest ativa por padrão
        if not self._selected_qid:
            from components import QuestLog
            ql = self.world.get_component(self.player_entity, QuestLog)
            if ql and ql.active:
                self._selected_qid = next(iter(ql.active))

    def close(self) -> None:
        self.is_open       = False
        self._entry_rects  = []

    # ── Events ───────────────────────────────────────────────────────────────

    def handle_events(self, events: list) -> None:
        if not self.is_open:
            return
        for event in events:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.close()
                return
            if event.type == pygame.MOUSEWHEEL:
                self._scroll_offset = max(0, self._scroll_offset - event.y * self._u(20))
                return
            if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
                continue
            mx, my = event.pos

            if self._close_rect and self._close_rect.collidepoint(mx, my):
                self.close()
                return

            if self._abandon_rect and self._abandon_rect.collidepoint(mx, my):
                self._abandon_selected()
                return

            for rect, qid in self._entry_rects:
                if rect.collidepoint(mx, my):
                    self._selected_qid = qid
                    return

    # ── Abandon ──────────────────────────────────────────────────────────────

    def _abandon_selected(self) -> None:
        from components import QuestLog
        if not self._selected_qid:
            return
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None or self._selected_qid not in ql.active:
            return
        qdef = QUESTS.get(self._selected_qid)
        del ql.active[self._selected_qid]
        self._qs._hud_cache_key = None
        if qdef:
            LOG.add(f'Quest abandonada: "{qdef.title}".', (180, 100, 80))
        self._selected_qid = ""
        self._abandon_rect = None

    # ── Render ───────────────────────────────────────────────────────────────

    def render(self) -> None:
        if not self.is_open:
            return
        x0, y0 = self._safe_panel_origin(self.PANEL_W, self.PANEL_H)
        x0, y0 = x0 + UI.QUEST_JOURNAL_OFFSET_X, y0 + UI.QUEST_JOURNAL_OFFSET_Y
        self._rebuild_list()

        SW, SH = self.hud_surf.get_size()
        PANEL_W, PANEL_H = self._u(self.PANEL_W), self._u(self.PANEL_H)

        # Overlay
        self.hud_surf.blit(fill_surf((SW, SH), (0, 0, 0, 160)), (0, 0))

        # Painel fundo
        self.hud_surf.blit(fill_surf((PANEL_W, PANEL_H), self.COL_BG), (x0, y0))
        pygame.draw.rect(self.hud_surf, self.COL_BORDER,
                         (x0, y0, PANEL_W, PANEL_H), 2, border_radius=6)

        # Titulo
        title_s = self._font_title.render("Diario de Quests", True, self.COL_HEADER)
        self.hud_surf.blit(title_s, (x0 + self._u(self.PAD), y0 + self._u(self.PAD)))

        # Botão X
        close_r = pygame.Rect(x0 + PANEL_W - self._u(28), y0 + self._u(8), self._u(22), self._u(22))
        pygame.draw.rect(self.hud_surf, (80, 40, 30), close_r, border_radius=3)
        pygame.draw.rect(self.hud_surf, self.COL_BORDER, close_r, 1, border_radius=3)
        xs = self._font_body.render("X", True, (220, 180, 140))
        self.hud_surf.blit(xs, (close_r.centerx - xs.get_width() // 2,
                               close_r.centery - xs.get_height() // 2))
        self._close_rect = close_r

        # Divisória vertical
        lx = x0 + self._u(self.LIST_W)
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (lx, y0 + self._u(40)), (lx, y0 + PANEL_H - self._u(self.PAD)))

        # Renderiza lista e detalhe
        self._render_list(x0, y0)
        self._render_detail(x0, y0)

    def _render_list(self, x0: int, y0: int) -> None:
        self._entry_rects = []
        mx, my = pygame.mouse.get_pos()

        list_area = pygame.Rect(x0 + self._u(2), y0 + self._u(42),
                                 self._u(self.LIST_W) - self._u(4), self._u(self.PANEL_H) - self._u(50))
        clip_surf = pygame.Surface((list_area.width, list_area.height), pygame.SRCALPHA)
        clip_surf.fill((0, 0, 0, 0))

        row_h = self._u(24)
        y_local = -self._scroll_offset
        total_content_h = 0

        for entry in self._list_entries:
            qid, label, color, is_header = entry
            row_r_global = pygame.Rect(list_area.x, list_area.y + y_local + total_content_h,
                                       list_area.width, row_h)
            # Calcula posição relativa ao clip_surf
            ry = y_local + total_content_h - (0 if y_local >= 0 else 0)
            total_content_h += row_h

        # Limita scroll máximo
        max_scroll = max(0, len(self._list_entries) * row_h - list_area.height)
        self._scroll_offset = min(self._scroll_offset, max_scroll)

        # Desenha entradas
        y_cur = -self._scroll_offset
        for entry in self._list_entries:
            qid, label, color, is_header = entry
            row_abs = pygame.Rect(list_area.x, list_area.y + y_cur, list_area.width, row_h)
            y_cur += row_h

            # Só renderiza se visível na área
            if row_abs.bottom < list_area.top or row_abs.top > list_area.bottom:
                continue

            if is_header:
                sep_s = self._font_sm.render(label, True, color)
                self.hud_surf.blit(sep_s, (row_abs.x + self._u(4), row_abs.centery - sep_s.get_height() // 2))
                continue

            is_sel = (qid == self._selected_qid)
            is_hov = row_abs.collidepoint(mx, my)
            bg = self.COL_SEL_BG if is_sel else (self.COL_HOVER_BG if is_hov else None)
            if bg:
                pygame.draw.rect(self.hud_surf, bg, row_abs)
            if is_sel:
                pygame.draw.rect(self.hud_surf, self.COL_BORDER, row_abs, 1)

            txt_s = self._font_sm.render(label, True, color)
            self.hud_surf.blit(txt_s, (row_abs.x + self._u(8), row_abs.centery - txt_s.get_height() // 2))
            self._entry_rects.append((row_abs, qid))

    def _render_detail(self, x0: int, y0: int) -> None:
        from components import QuestLog
        if not self._selected_qid:
            hint = self._font_body.render("Selecione uma quest na lista.", True, self.COL_GREY)
            dx = x0 + self._u(self.LIST_W) + self._u(self.PAD)
            dy = y0 + self._u(self.PANEL_H) // 2
            self.hud_surf.blit(hint, (dx, dy))
            return

        qdef = QUESTS.get(self._selected_qid)
        if qdef is None:
            return

        ql = self.world.get_component(self.player_entity, QuestLog)
        is_completed = ql and self._selected_qid in ql.completed
        prog = ql.active.get(self._selected_qid) if ql else None

        dx = x0 + self._u(self.LIST_W) + self._u(self.PAD)
        dy = y0 + self._u(42)
        max_w = self._u(self.PANEL_W) - self._u(self.LIST_W) - self._u(self.PAD) * 2

        # Título
        col_t = self.COL_GREY if is_completed else self.COL_ACTIVE
        title_text = f"{qdef.title} (Done)" if is_completed else qdef.title
        title_s = self._font_title.render(title_text, True, col_t)
        self.hud_surf.blit(title_s, (dx, dy))
        dy += title_s.get_height() + self._u(4)

        # Nível requerido
        if qdef.level_req > 0:
            plvl = self._qs._player_level()
            lvl_col = self.COL_GREY if plvl >= qdef.level_req else (200, 80, 80)
            lvl_s = self._font_sm.render(f"Nivel minimo: {qdef.level_req}", True, lvl_col)
            self.hud_surf.blit(lvl_s, (dx, dy))
            dy += lvl_s.get_height() + self._u(6)
        else:
            dy += self._u(2)

        # Linha separadora
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (dx, dy), (dx + max_w, dy))
        dy += self._u(8)

        # Descrição
        for line in self._wrap(qdef.description, max_w, self._font_body):
            s = self._font_body.render(line, True, self.COL_WHITE)
            self.hud_surf.blit(s, (dx, dy))
            dy += s.get_height() + self._u(2)
        dy += self._u(8)

        # Objetivos
        obj_hdr = self._font_sm.render("Objetivos:", True, self.COL_HEADER)
        self.hud_surf.blit(obj_hdr, (dx, dy))
        dy += obj_hdr.get_height() + self._u(4)

        for i, obj in enumerate(qdef.objectives):
            if is_completed:
                done, cur = True, obj.count
            elif prog:
                cur  = prog[i]
                done = cur >= obj.count
            else:
                cur, done = 0, False

            if done:
                col = self.COL_DONE_OBJ
            elif cur > 0:
                col = self.COL_WHITE
            else:
                col = self.COL_PROG
            label = QuestSystem._obj_label(obj, cur)
            obj_s = self._font_body.render(f"  {label}", True, col)
            self.hud_surf.blit(obj_s, (dx, dy))
            dy += obj_s.get_height() + self._u(2)
        dy += self._u(8)

        # Recompensas
        rew_hdr = self._font_sm.render("Recompensas:", True, self.COL_HEADER)
        self.hud_surf.blit(rew_hdr, (dx, dy))
        dy += rew_hdr.get_height() + self._u(4)

        parts = []
        if qdef.reward.xp:   parts.append(f"{qdef.reward.xp} XP")
        if qdef.reward.gold: parts.append(f"{qdef.reward.gold} Ouro")
        rew_text = "  " + "  |  ".join(parts) if parts else "  Nenhuma"
        rew_s = self._font_body.render(rew_text, True, self.COL_REWARD)
        self.hud_surf.blit(rew_s, (dx, dy))

        # Botão Abandonar — só para quests ativas (não completas)
        self._abandon_rect = None
        if not is_completed and prog is not None:
            mx_cur, my_cur = pygame.mouse.get_pos()
            btn_y  = y0 + self._u(self.PANEL_H) - self._u(46)
            abn_r  = pygame.Rect(dx, btn_y, self._u(140), self._u(28))
            hov    = abn_r.collidepoint(mx_cur, my_cur)
            pygame.draw.rect(self.hud_surf, (100, 35, 25) if hov else (65, 20, 15),
                             abn_r, border_radius=4)
            pygame.draw.rect(self.hud_surf, (160, 70, 50), abn_r, 1, border_radius=4)
            lbl = self._font_body.render("Abandonar Quest", True, (230, 160, 140))
            self.hud_surf.blit(lbl, (abn_r.centerx - lbl.get_width() // 2,
                                   abn_r.centery - lbl.get_height() // 2))
            self._abandon_rect = abn_r

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _rebuild_list(self) -> None:
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        entries = []

        active_qids = list(ql.active.keys()) if ql else []
        completed_qids = sorted(ql.completed) if ql else []

        if active_qids:
            entries.append(("", "ATIVAS", self.COL_HEADER, True))
            for qid in active_qids:
                qdef = QUESTS.get(qid)
                if qdef:
                    # Marca se completa mas não entregue
                    prog = ql.active[qid]
                    all_done = all(prog[i] >= obj.count for i, obj in enumerate(qdef.objectives))
                    col = self.COL_REWARD if all_done else self.COL_ACTIVE
                    label = qdef.title + (" [!]" if all_done else "")
                    entries.append((qid, label, col, False))

        if completed_qids:
            entries.append(("", "COMPLETAS", self.COL_HEADER, True))
            for qid in completed_qids:
                qdef = QUESTS.get(qid)
                if qdef:
                    entries.append((qid, f"{qdef.title} (Done)", self.COL_GREY, False))

        if not entries:
            entries.append(("", "Nenhuma quest.", self.COL_GREY, True))

        self._list_entries = entries

        # Se a quest selecionada não existe mais, limpa
        all_qids = {e[0] for e in entries if not e[3]}
        if self._selected_qid not in all_qids:
            self._selected_qid = ""

    @staticmethod
    def _wrap(text: str, max_px: int, font) -> list:
        words = text.split()
        lines, cur = [], ""
        for word in words:
            test = cur + (" " if cur else "") + word
            if font.size(test)[0] <= max_px:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines
