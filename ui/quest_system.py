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
import math
import pygame
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_helpers import fill_surf
from ui.fonts import make as _font

from ui.systems import System
from engine.quest_events import QUEST_EVENTS
from content.quests_data import QUESTS, ObjectiveDef
import engine.quest_logic as quest_logic
from ui.combat_log import LOG
from ui.floating_text import PROC
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI
from ui.npc_interaction import walk_to_npc, suppress_chase

T, S = aw.T, aw.S


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

    _FONT_BASES = {"_font_title": 23, "_font_obj": 21}

    def __init__(self, world, player_entity: int) -> None:
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self._hud_cache_key:  "tuple | None"          = None
        self._hud_cache_surf: "pygame.Surface | None" = None
        # Fase F (23/07/2026) — tracker minimizável (WoW-style) + geometria
        # do último frame desenhado (só existe pra hit-test do botão de
        # minimizar, já que o tamanho do painel varia com o conteúdo).
        self._tracker_minimized: bool          = False
        self._last_hud_rect:     "pygame.Rect | None" = None
        self._current_map: str = ""
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

    def update(self, events=None, dt: float = 0) -> None:
        from engine.components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            QUEST_EVENTS.clear()
            return

        # Servidor é o produtor autoritativo de progresso de quest. A maioria
        # dos eventos (kill, use_skill, etc.) já chega no servidor via
        # gatilhos server-side. Porém eventos disparados por sistemas
        # PURAMENTE client-side (ShopSystem.open → "talk_to_npc") nunca
        # chegam ao servidor. Para esses, encaminhamos via QUEST_ACCEPT
        # com quest_id="" — o handler server-side aplica o evento de
        # talk_to_npc e responde com QUEST_UPDATE se algo mudou.
        # Todos os outros eventos são descartados (o servidor já os processa
        # pelos próprios gatilhos). O processamento LOCAL de eventos
        # (quest_logic.apply_event/reach_tile/sync_collect) era do modo
        # offline — removido deste branch (15/07/2026, item A2 §11).
        while QUEST_EVENTS:
            _evt, _peid, _data = QUEST_EVENTS.popleft()
            if _evt == "talk_to_npc" and self._net:
                _npc_name = _data.get("npc_name", "")
                if _npc_name:
                    from shared.messages import MsgType as _MTtalk
                    self._net.send(_MTtalk.QUEST_ACCEPT, {
                        "quest_id": "",       # nenhuma quest pra aceitar —
                        "npc_name": _npc_name,# só dispara talk_to_npc no servidor
                    })

    def _process_talk_to_npc(self, npc_name: str) -> None:
        """No-op mantido por compatibilidade de chamada
        (QuestDialogSystem._open_dialog) — o processamento local era do modo
        offline (removido, item A2 §11); online o servidor processa o mesmo
        evento em _handle_quest_accept/_handle_quest_turn_in usando o
        npc_name enviado no payload."""
        return

    # ── API para QuestDialogSystem ───────────────────────────────────────────

    def can_turn_in(self, qid: str) -> bool:
        """True se a quest está ativa e todos os objetivos concluídos."""
        from engine.components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return False
        return quest_logic.can_turn_in(ql, qid)

    def turn_in(self, qid: str) -> None:
        """Completa e recompensa a quest (caminho OFFLINE — online, o
        QuestDialogSystem manda QUEST_TURN_IN em vez de chamar isto)."""
        from engine.components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql:
            self._complete_quest(ql, qid)

    # ── HUD ─────────────────────────────────────────────────────────────────

    def _sorted_active_items(self, ql) -> list:
        """Fase F (23/07/2026, pedido do usuário) — ordena por progresso
        ABSOLUTO somado (sum(prog), não proporcional): uma quest com poucos
        objetivos quase completos não deve furar fila na frente de uma
        quest grande já bem avançada em valor absoluto."""
        return sorted(ql.active.items(), key=lambda kv: sum(kv[1]), reverse=True)

    def render_hud(self, screen: pygame.Surface) -> None:
        from engine.components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None or not ql.active:
            self._hud_cache_key  = None
            self._hud_cache_surf = None
            self._last_hud_rect  = None
            return

        sorted_items = self._sorted_active_items(ql)[:self.MAX_HUD_QUESTS]
        cache_key = (self._tracker_minimized, len(ql.active),
                     tuple((qid, tuple(prog)) for qid, prog in sorted_items))
        if cache_key != self._hud_cache_key:
            self._hud_cache_key  = cache_key
            self._hud_cache_surf = self._build_hud_surf(ql, sorted_items)

        if self._hud_cache_surf is None:
            self._last_hud_rect = None
            return

        sw  = screen.get_width()
        pos = (sw - self._u(self.HUD_MARGIN_RIGHT) - self._hud_cache_surf.get_width(),
               self._u(self.HUD_MARGIN_TOP))
        screen.blit(self._hud_cache_surf, pos)
        # Geometria do frame atual — só existe pra hit-test do botão de
        # minimizar (handle_tracker_click); tamanho varia com o conteúdo,
        # por isso não dá pra fixar um Rect estático de antemão.
        self._last_hud_rect = pygame.Rect(pos, self._hud_cache_surf.get_size())

    def handle_tracker_click(self, event) -> bool:
        """Clique no botão minimizar/expandir do tracker (Fase F) —
        chamado incondicionalmente pelo loop de eventos (game.py), já que
        o tracker é HUD permanente, sem um `_show_x` de modal. True =
        consumiu o clique (não repassar pra outros handlers)."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        if self._last_hud_rect is None:
            return False
        btn = pygame.Rect(self._last_hud_rect.right - self._u(20), self._last_hud_rect.y,
                          self._u(18), self._u(18))
        if btn.collidepoint(event.pos):
            self._tracker_minimized = not self._tracker_minimized
            self._hud_cache_key = None  # força rebuild imediato
            return True
        return False

    def _build_hud_surf(self, ql, sorted_items: list) -> "pygame.Surface | None":
        lines: list[tuple] = []

        header = self._font_title.render(f"Quests ({len(ql.active)})", False, self.COL_TITLE)
        header_h = max(header.get_height(), self._u(18))
        lines.append((header, 0))
        y = header_h + self._u(4)

        if not self._tracker_minimized:
            for qid, prog in sorted_items:
                qdef = QUESTS.get(qid)
                if qdef is None:
                    continue
                ts = self._font_title.render(qdef.title, False, self.COL_TITLE)
                lines.append((ts, y))
                y += self._u(20)
                for i, obj in enumerate(qdef.objectives):
                    p = prog[i]
                    if p >= obj.count:
                        color = self.COL_DONE
                    elif p > 0:
                        color = self.COL_ACTIVE
                    else:
                        color = self.COL_PROG
                    surf = self._font_obj.render(self._obj_label(obj, p), False, color)
                    lines.append((surf, y))
                    y += self._u(17)
                y += self._u(5)

        btn_w   = self._u(22)   # espaço reservado pro botão minimizar/expandir
        max_w   = max(s.get_width() for s, _ in lines) + btn_w
        total_h = y
        hud = pygame.Surface((max_w, total_h), pygame.SRCALPHA)
        hud.fill((0, 0, 0, 0))
        for surf, ypos in lines:
            hud.blit(surf, (max_w - btn_w - surf.get_width(), ypos))

        btn_rect = pygame.Rect(max_w - self._u(18), 0, self._u(18), self._u(18))
        pygame.draw.rect(hud, (60, 50, 30), btn_rect, border_radius=3)
        glyph = "+" if self._tracker_minimized else "-"
        gs = self._font_title.render(glyph, False, (230, 210, 150))
        hud.blit(gs, gs.get_rect(center=btn_rect.center))

        return hud

    # ── Internos ─────────────────────────────────────────────────────────────

    def auto_start_quests(self) -> None:
        """Inicia quests com auto_start=True elegíveis. Chamado no boot."""
        from engine.components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return
        for qid, qdef in QUESTS.items():
            if qdef.auto_start:
                self._try_start(ql, qid)

    def _player_level(self) -> int:
        from engine.components import CharacterStats
        char = self.world.get_component(self.player_entity, CharacterStats)
        return char.level if char else 1

    def _player_class_id(self) -> str:
        from engine.components import CharacterStats
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

    def _complete_quest(self, ql, qid: str) -> None:
        """Caminho OFFLINE — online, o QuestDialogSystem manda QUEST_TURN_IN
        em vez de chamar isto (servidor valida/aplica XP/gold/itens)."""
        from engine.save_system import request_autosave

        qdef = QUESTS.get(qid)
        if qdef is None:
            return

        # _consumed ignorado aqui: offline, o Inventory mutado por
        # complete_quest já É o do jogador local — não existe cliente
        # separado pra avisar (diferente do caminho online, ver
        # server/session.py::_handle_quest_turn_in).
        reward, _consumed = quest_logic.complete_quest(self.world, self.player_entity, ql, qid)
        if reward is None:
            return
        self._hud_cache_key = None

        if reward.xp > 0:
            from engine.components import CharacterStats, CombatStats, PermanentStats, PlayerControlled
            from engine.stats_system import process_levelups
            _xp_first = self.world.get_first(CharacterStats, CombatStats, PlayerControlled)
            if _xp_first is not None:
                eid, char, cs, _ = _xp_first
                perm = self.world.get_component(eid, PermanentStats)
                char.current_xp += reward.xp
                process_levelups(self.world, eid, char, cs, perm)

        if reward.gold > 0:
            from engine.components import Wallet, PlayerControlled as _PCgold
            _gold_first = self.world.get_first(Wallet, _PCgold)
            if _gold_first is not None:
                wlt = _gold_first[1]
                wlt.gold += reward.gold

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
        from content.skill_config import SKILL_CATALOG
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
        elif obj.type == "auto_attack_hit":
            alvo = obj.target if obj.target != "*" else "inimigo"
            desc = f"Acertar {alvo} com ataque básico"
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

    PAD     = UI.QUEST_DIALOG_PAD

    COL_GOLD   = (255, 215,   0)
    COL_GREY   = (150, 150, 150)
    COL_WHITE  = (220, 220, 220)
    COL_TITLE  = (220, 200, 120)
    COL_GREEN  = ( 80, 200, 100)
    COL_BG     = ( 15,  10,   5, 235)
    COL_BORDER = (140, 100,  60)

    _FONT_BASES = {"_font_lg": 29, "_font_body": 24, "_font_sm": 21}

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

        # Cache de superfícies de marcador-fallback, por (cor, símbolo) — era
        # atributo do CORPO da classe (compartilhado por acidente entre
        # instâncias, achado 04 do benchmark, PROBLEMAS_ARQUITETURA.md
        # §12/§13); por-instância, correto. (Corrigido 07/08/2026: a limpeza
        # anterior tinha posto isso em QuestSystem.__init__ por engano —
        # _fallback_marker_surf/render_world são de QuestDialogSystem, uma
        # classe diferente; suíte automatizada não pegou porque nenhum teste
        # exercitava esse caminho de render, só apareceu no playtest manual.)
        self._fallback_marker_cache: dict = {}

        # Escolha de recompensa (23/07/2026, pedido do usuário) — só
        # relevante quando QuestReward.choice não é vazio. Escolha em si
        # reseta sempre que um diálogo de entrega NOVO abre (_open_dialog/
        # clique na lista), pra nunca vazar a escolha de uma quest anterior.
        self._turnin_chosen_item: "str | None" = None

        # Tooltip de item de recompensa (hover nos ícones) — mesmo padrão
        # de LootSystem/ShopSystem/CraftingSystem (ui/systems.py): campo
        # `pending_tooltip` (sem underscore) lido por GameEngine no fim do
        # frame e copiado pra self._pending_tooltip (ver game.py).
        self.pending_tooltip = None

        # Rolagem do conteúdo do pergaminho (texto + recompensas variam por
        # quest e podem passar da altura da janela) — ver render().
        self._scroll = aw.ScrollState()
        # Botão afundado (age no soltar), eventos do frame (guardados em
        # handle_events, consumidos em render), área segura da tela
        # (injetada por game.py — GameEngine._mm_safe_area) e rects de
        # tela dos elementos clicáveis (só pra teste/depuração).
        self._press = aw.PressState()
        self._frame_events: list = []
        self.safe_area_fn = None
        self._hit_rects: dict = {}

        # Diálogo aberto a partir de um item (clique direito na bag), sem
        # NPC real por trás (25/07/2026, pedido do usuário: reaproveitar o
        # MESMO modal do NPC em vez de um popup novo — client/
        # inventory_handlers.py::_try_open_item_quest_dialog). Quando
        # setado, render()/handle_events() pulam qualquer lógica
        # NPC-dependente (QuestGiver/NPC component, talk_to_npc, distância
        # pra auto-fechar) — ver open_for_item().
        self._item_source:       bool = False
        self._item_source_label: str  = ""

    @property
    def is_open(self) -> bool:
        return self._dialog_npc_id != -1

    # ── Update ──────────────────────────────────────────────────────────────

    def update(self, events=None, dt: float = 0) -> None:
        from engine.components import (QuestGiver as _QG, Position, Renderable,
                                       PlayerAutoMove, NPCInteractionRequest)

        # Pedido de abertura vindo de outro System (ex: BlacksmithSystem/
        # TrainerSystem, opção "Quests" do menu de ação) — 15/09/2026,
        # item 4 da revisão de performance (ver docstring de
        # NPCInteractionRequest, engine/components.py). Só remove o
        # componente quando o `kind` é DESTE system.
        _npc_req = self.world.get_component(self.player_entity, NPCInteractionRequest)
        if _npc_req is not None and _npc_req.kind == "open_quest_dialog":
            self._open_dialog(_npc_req.target_eid)
            self.world.remove_component(self.player_entity, NPCInteractionRequest)

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
                            suppress_chase(self.world, self.player_entity)
                            self._open_dialog(eid)
                        else:
                            self._pending_npc_id = eid
                            walk_to_npc(self.world, self.player_entity, pt, gt)
                        self._right_click_consumed = True
                        break

    # ── Eventos do modal ─────────────────────────────────────────────────────

    def handle_events(self, events: list) -> None:
        """ESC fecha; o resto (botões, linhas da lista, ícones de escolha,
        rolagem) é tratado em render() pelo Frame do atlas
        (ui/atlas_window.py — botões agem no SOLTAR do mouse), com os
        eventos guardados aqui."""
        if not self.is_open:
            return
        for event in events:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self._close()
                return
        self._frame_events = list(events)

    def _select_from_list(self, qid: str) -> None:
        self._dialog_selected_qid = qid
        self._scroll              = aw.ScrollState()
        self._turnin_chosen_item  = None
        # Bug real relatado pelo usuário (05/10/2026): clicar numa quest
        # completável-por-talk_to_npc (ex: "bem_vindo_guerreiro", único
        # objetivo é falar com o NPC) dentro da LISTA abria o diálogo de
        # ACEITAR de novo em vez de ENTREGAR. self._qs.can_turn_in(qid) só
        # olha o progresso bruto de ql.active — online, _process_talk_to_npc
        # é no-op de propósito (nunca muta QuestLog sem o servidor
        # confirmar). Precisa do MESMO critério de _open_dialog
        # (_get_completable_quests com pending_npc_name).
        from engine.components import NPC as _NPCclick
        _npc_c_click = self.world.get_component(self._dialog_npc_id, _NPCclick)
        _npc_name_click = _npc_c_click.name if _npc_c_click else ""
        _comp_click = self._get_completable_quests(self._dialog_npc_id, _npc_name_click)
        self._dialog_state = "turnin" if qid in _comp_click else "detail"

    def _accept(self) -> None:
        qid = self._dialog_selected_qid
        # Server-autoritativo: aceite local era do modo offline, removido
        # deste branch (15/07/2026, item A2 §11).
        if self._qs._net:
            from shared.messages import MsgType as _MTqa
            # Fonte "item" não tem NPC real — manda npc_name="" pra não
            # disparar talk_to_npc de um NPC inexistente
            # (server/session.py::_handle_quest_accept só aplica o evento
            # se npc_name vier preenchido).
            _npc_nm = "" if self._item_source else self._npc_name(self._dialog_npc_id)
            self._qs._net.send(_MTqa.QUEST_ACCEPT, {"quest_id": qid, "npc_name": _npc_nm})
        self._close()

    def _complete(self) -> None:
        qdef = QUESTS.get(self._dialog_selected_qid)
        if qdef and qdef.reward.choice and self._turnin_chosen_item is None:
            return   # sem escolha ainda — botão "desabilitado" (ver render())
        saved_npc = self._dialog_npc_id
        qid = self._dialog_selected_qid
        chosen = self._turnin_chosen_item or ""
        self._close()
        # Server-autoritativo: entrega local era do modo offline, removida
        # deste branch (15/07/2026, item A2 §11). Reabertura de quests em
        # cadeia depende da confirmação do servidor (QUEST_UPDATE).
        if self._qs._net:
            from shared.messages import MsgType as _MTqt
            self._qs._net.send(_MTqt.QUEST_TURN_IN, {
                "quest_id": qid, "npc_name": self._npc_name(saved_npc),
                "chosen_item": chosen,
            })

    # ── Marcador de estado de quest (fonte única) ────────────────────────────
    # Reaproveitado por render_world() (indicador acima da cabeça, no mundo)
    # E por ui/map_markers.py (ícone no mapa/minimapa) — mesma regra de
    # estado nos 3 lugares, nunca duplicada.

    # icon_name segue a convenção map_<algo>.png de assets/icons/ (mesmo
    # padrão de skill_/item_/enemy_ já usado por IconManager). Ainda não
    # existem os arquivos — ICONS.get() retorna None e quem desenha cai pro
    # fallback (color, symbol) automaticamente.
    _QUEST_ICONS = {
        "completable": "map_quest_complete",
        "avail":       "map_quest_available",
        "inprog":      "map_quest_inprogress",
        "locked":      "map_quest_locked",
    }

    # Animação de mundo por estado (09/10/2026, ver CLAUDE.md) — só os 3
    # estados com arte nova (assets/sprites/quest_*.png); "locked" não
    # tem animação e continua no ícone estático/fallback de sempre.
    # NUNCA usado por map_markers.py/MapOverlay/Minimap — só por
    # render_world() abaixo (pedido explícito do usuário: mapa/minimapa
    # mantêm o ícone de sempre, só o mundo ganha a animação).
    _QUEST_WORLD_ANIM = {
        "map_quest_available": "quest_available",
        "map_quest_inprogress": "quest_incomplete",
        "map_quest_complete":  "quest_complete",
    }
    _QUEST_WORLD_ANIM_SIZE = 32   # tamanho nativo do quadro — sem redimensionar

    def _marker_from_lists(self, avail, completable, inprog, locked) -> "tuple[str, tuple, str] | None":
        """Núcleo de marker_for/marker_for_static — decide (icon_name, cor,
        símbolo) a partir das 4 listas já resolvidas. Nunca duplicar esta
        decisão fora daqui."""
        if completable:
            state, color, symbol = "completable", self.COL_GOLD, "?"
        elif avail:
            state, color, symbol = "avail",       self.COL_GOLD, "!"
        elif inprog:
            state, color, symbol = "inprog",      self.COL_GREY, "?"
        elif locked:
            state, color, symbol = "locked",      self.COL_GREY, "!"
        else:
            return None
        return self._QUEST_ICONS[state], color, symbol

    def marker_for(self, npc_id: int) -> "tuple[str, tuple, str] | None":
        """Estado de quest deste NPC pro player local: (icon_name, cor de
        fallback, símbolo de fallback), ou None se não há nada a mostrar
        (sem quest disponível/em progresso/completável/bloqueada).

        `pending_npc_name` (04/09/2026, bug real reportado pelo usuário):
        sem isso, o ícone acima da cabeça mostrava "em progresso" (?cinza)
        em vez de "completável" (?dourado) pra uma quest cujo único
        objetivo pendente é justamente falar com ESTE NPC — mesma
        checagem que `_open_dialog` já faz certo."""
        from engine.components import NPC as _NPCmk
        _npc_c = self.world.get_component(npc_id, _NPCmk)
        _npc_name = _npc_c.name if _npc_c else ""

        avail       = self._get_available_quests(npc_id)
        completable = self._get_completable_quests(npc_id, _npc_name)
        inprog      = self._get_inprogress_quests(npc_id)
        locked      = self._get_locked_quests(npc_id)
        return self._marker_from_lists(avail, completable, inprog, locked)

    def marker_for_static(self, npc_name: str, quest_ids: tuple,
                          turn_in_ids: tuple) -> "tuple[str, tuple, str] | None":
        """Mesmo que marker_for, mas pro caso do NPC NÃO existir localmente
        (fora da AOI — ícone no mapa M/minimapa precisa continuar
        aparecendo mesmo assim, ver PROBLEMAS_ARQUITETURA.md, 06/10/2026).
        `quest_ids`/`turn_in_ids` vêm direto dos dados estáticos do mapa
        (`spawn_points["quest_givers"]`), nunca de um componente vivo."""
        from engine.components import QuestGiver as _QGStatic
        giver = _QGStatic(quest_ids=tuple(quest_ids), turn_in_ids=tuple(turn_in_ids))
        avail       = quest_logic.get_available_quests(self.world, self.player_entity, -1, giver=giver)
        completable = quest_logic.get_completable_quests(self.world, self.player_entity, -1,
                                                          npc_name, giver=giver)
        inprog      = quest_logic.get_inprogress_quests(self.world, self.player_entity, -1, giver=giver)
        locked      = quest_logic.get_locked_quests(self.world, self.player_entity, -1, giver=giver)
        return self._marker_from_lists(avail, completable, inprog, locked)

    # ── Render — indicadores no mundo ────────────────────────────────────────

    def render_world(self, cam_x: float = 0, cam_y: float = 0, zoom: float = 1.0) -> None:
        from engine.components import QuestGiver as _QG, Position, Renderable, Visible
        from ui.icon_manager import ICONS
        from ui.world_labels import WORLD_LABELS

        # Ícone e nome do NPC (ui/systems.py::RenderSystem) empilham no
        # MESMO stack_key=eid em WORLD_LABELS — desenhados em espaço de
        # tela, pós-zoom, nunca dentro do world_surf (fix 11/07/2026: fonte/
        # ícone pixel-perfect borravam no transform.scale do zoom da
        # câmera). Sem compensação de zoom no tamanho — 8px literal, igual
        # mapa/minimapa (23.1/23.2), já que isso nunca mais passa pela
        # escala do mundo.
        icon_size = 8
        now_ms = pygame.time.get_ticks()

        for eid, pos, rend, _, _ in self.world.get_entities_with(Position, Renderable, _QG, Visible):
            marker = self.marker_for(eid)
            if marker is None:
                continue
            icon_name, color, symbol = marker

            anim_name = self._QUEST_WORLD_ANIM.get(icon_name)
            icon = None
            if anim_name is not None:
                from ui.world_icon_animator import get_frame as _quest_anim_frame
                icon = _quest_anim_frame(anim_name, self._QUEST_WORLD_ANIM_SIZE, now_ms)
            if icon is None:
                icon = ICONS.get(icon_name, icon_size)
            if icon is None:
                icon = self._fallback_marker_surf(color, symbol)
            WORLD_LABELS.add_icon(pos.x, pos.y - rend.height / 2, icon, stack_key=eid)

    def _fallback_marker_surf(self, color: tuple, symbol: str) -> "pygame.Surface":
        """Círculo + glifo pra quando o arquivo map_quest_*.png ainda não
        existe — mesmo visual de antes (23.2), agora numa Surface própria
        (cacheada por cor+símbolo) pra poder ir pro WORLD_LABELS igual um
        ícone de verdade."""
        key = (color, symbol)
        surf = self._fallback_marker_cache.get(key)
        if surf is None:
            size = 18
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            pygame.draw.circle(surf, (30, 25, 10), (size // 2, size // 2), size // 2 - 1)
            pygame.draw.circle(surf, color, (size // 2, size // 2), size // 2 - 1, 2)
            glyph = self._font_lg.render(symbol, False, color)
            surf.blit(glyph, (size // 2 - glyph.get_width() // 2,
                              size // 2 - glyph.get_height() // 2))
            self._fallback_marker_cache[key] = surf
        return surf

    # ── Render — modal (atlas de UI, janela de pergaminho do mockup) ─────────
    # Layout em tiles (ui_atlas.quest_window_raw): linha 0 = título (nome do
    # NPC) + X; pergaminho com o texto rolável; coluna da direita = barra
    # de rolagem; rodapé escuro = botões "altos". Conteúdo (texto + slots
    # de recompensa) é montado inteiro num Surface à parte e recortado
    # pela área visível do pergaminho — os slots rolam junto com o texto
    # (decisão do usuário: recompensas no fim do texto).

    _PADX      = 10   # px RAW do texto até a borda esq./dir. do pergaminho
    _VIEW_PAD  = 6    # px RAW de folga no topo/base do pergaminho
    _SLOT_GAP  = 2    # px RAW entre slots de recompensa
    _MIN_TW, _MIN_TH = 11, 13   # tamanho da janela do mockup

    INK        = ( 62,  40,  22)
    INK_SOFT   = (105,  78,  50)
    INK_TITLE  = (120,  36,  24)
    INK_GOLD   = (135,  90,  10)
    INK_HOVER  = (165,  55,  30)
    TITLE_BAR  = (240, 228, 200)

    def _lh(self, font) -> int:
        """Altura de linha da fonte em px RAW (fonte acompanha a escala de
        UI do jogador, o atlas não)."""
        return math.ceil(font.get_linesize() / S)

    def _window_tiles_w(self, footer_labels: list) -> int:
        wrap_raw = self._u(UI.QUEST_DIALOG_W - 2 * self.PAD - 32) / S
        tw = math.ceil((wrap_raw + 2 * self._PADX) / T) + 1
        bw = aw.tiles_for(self._font_body, footer_labels or ["Fechar"], 3)
        tw = max(self._MIN_TW, tw, 2 * bw + 3)
        max_tw = self._area().width // (T * S)
        return max(4, min(tw, max_tw))

    def _area(self) -> pygame.Rect:
        if self.safe_area_fn is not None:
            return self.safe_area_fn()
        SW, SH = self.hud_surf.get_size()
        return aw.safe_area(SW, SH, SH)

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        events, self._frame_events = self._frame_events, []
        if not self.is_open:
            return
        from engine.components import QuestGiver as _QG, NPC as _NPC

        if self._item_source:
            # Sem NPC real por trás (aberto via item na bag) — título usa o
            # nome do item; estado sempre "detail" (open_for_item).
            npc_name = self._item_source_label or "Missão"
        else:
            if self.world.get_component(self._dialog_npc_id, _QG) is None:
                self._close()
                return
            _npc_comp = self.world.get_component(self._dialog_npc_id, _NPC)
            npc_name  = _npc_comp.name if _npc_comp else "NPC"

        self.pending_tooltip = None
        self._hit_rects = {}
        state = self._dialog_state
        qdef = QUESTS.get(self._dialog_selected_qid)
        if state in ("detail", "turnin") and qdef is None:
            return

        if state == "detail":
            footer = [("q:decline", "Recusar", True), ("q:accept", "Aceitar", True)]
        elif state == "turnin":
            needs_choice = bool(qdef.reward.choice) and self._turnin_chosen_item is None
            footer = [("q:complete", "Concluir", not needs_choice)]
        else:
            footer = [("q:fechar", "Fechar", True)]

        tw = self._window_tiles_w([lbl for _, lbl, _ in footer])
        content_w = (tw - 1) * T
        wrap_px = (content_w - 2 * self._PADX) * S
        if state == "list":
            items, content_h = self._content_list(npc_name)
        elif state == "detail":
            items, content_h = self._content_detail(qdef, wrap_px)
        else:
            items, content_h = self._content_turnin(qdef, wrap_px)

        max_th = self._area().height // (T * S)
        need_th = 3 + math.ceil((content_h + 2 * self._VIEW_PAD) / T)
        th = max(min(self._MIN_TH, max_th), min(need_th, max_th), 6)
        view_y0 = T + self._VIEW_PAD
        view_h  = (th - 3) * T - 2 * self._VIEW_PAD
        max_scroll = max(0, content_h - view_h)

        f = aw.Frame(self.hud_surf, events, tw, th, area=self._area(), press=self._press,
                     font=self._font_body, raw=ui_atlas.quest_window_raw(tw, th),
                     offset=(self._u(UI.QUEST_DIALOG_OFFSET_X), self._u(UI.QUEST_DIALOG_OFFSET_Y)))
        action = None

        # Título (nome do NPC) + X
        f.text(npc_name, content_w / 2, T / 2, self.TITLE_BAR)
        if f.press("q:close", (tw - 1) * T, 0, tw * T, T)[1]:
            action = ("close",)
        self._hit("q:close", f, (tw - 1) * T, 0, tw * T, T)

        scroll = f.scrollbar(self._scroll, tw - 1, 1, th - 3, max_scroll)

        # Botões do rodapé (1 = centralizado; 2 = nas pontas, como no mockup)
        bw = aw.tiles_for(self._font_body, [lbl for _, lbl, _ in footer], 3)
        if len(footer) == 1:
            cols = [(tw - bw) // 2]
        else:
            cols = [1, tw - 1 - bw]
        by = th - ui_atlas.QUEST_FOOTER_ROWS
        ay0, ay1 = ui_atlas.button_art_y(True)
        for (bid, label, enabled), col in zip(footer, cols):
            if f.button(bid, col, by, bw, label, tall=True, enabled=enabled):
                action = (bid,)
            self._hit(bid, f, col * T, by * T + ay0, (col + bw) * T, by * T + ay1)

        # Conteúdo rolável — mouse convertido pro espaço do conteúdo só
        # quando está dentro da área visível do pergaminho.
        in_view = view_y0 <= f.ry < view_y0 + view_h and 0 <= f.rx < content_w
        fc = f.child(pygame.Surface((content_w, max(1, content_h)), pygame.SRCALPHA),
                     f.ry - view_y0 + scroll if in_view else -10_000)
        icons = []   # (surf, x_raw, y_raw, dim, selected, stack)
        for it in items:
            kind = it[0]
            if kind == "text":
                _, text, x, y, color, font = it
                fc.text(text, x, y, color, font=font, anchor="topleft")
            elif kind == "row":
                _, qid, label, y0, y1 = it
                hov = 0 <= fc.rx < content_w and y0 <= fc.ry < y1
                if fc.press(f"q:row:{qid}", 0, y0, content_w, y1)[1]:
                    action = ("row", qid)
                fc.text(label, self._PADX, y0 + 2, self.INK_HOVER if hov else self.INK,
                        font=self._font_body, anchor="topleft")
                self._hit(f"q:row:{qid}", f, 0, view_y0 + y0 - scroll, content_w, view_y0 + y1 - scroll)
            elif kind == "slot":
                _, sel_kind, key, obj, x, y = it
                fc.raw.blit(ui_atlas.slot_raw(), (x, y))
                SL = aw.SLOT
                hov = x <= fc.rx < x + SL and y <= fc.ry < y + SL
                selectable = sel_kind == "choice"
                selected = selectable and self._turnin_chosen_item == key
                dim = selectable and self._turnin_chosen_item is not None and not selected
                if selectable and fc.press(f"q:choice:{key}", x, y, x + SL, y + SL)[1]:
                    action = ("choice", key)
                self._hit(f"q:slot:{sel_kind}:{key}", f, x, view_y0 + y - scroll, x + SL,
                          view_y0 + y + SL - scroll)
                icons.append((sel_kind, obj, x, y, dim, selected))
                if hov:
                    self._set_reward_tooltip(sel_kind, obj)

        f.end()
        content = ui_atlas.scale_up(fc.raw)
        aw.draw_texts(content, fc.texts, self._font_body)
        self._draw_reward_icons(content, icons)
        top = min(scroll, max(0, content.get_height() // S - view_h))
        view = content.subsurface((0, top * S, content.get_width(),
                                   min(view_h, content_h - top) * S))
        self.hud_surf.blit(view, (f.px, f.py + view_y0 * S))

        if action is None:
            return
        if action[0] in ("close", "q:fechar", "q:decline"):
            self._close()
        elif action[0] == "q:accept":
            self._accept()
        elif action[0] == "q:complete":
            self._complete()
        elif action[0] == "row":
            self._select_from_list(action[1])
        elif action[0] == "choice":
            self._turnin_chosen_item = action[1]

    def _hit(self, hit_id: str, f, x0, y0, x1, y1) -> None:
        """Rect de TELA de um elemento clicável (RAW → tela) — usado pelos
        testes pra achar onde clicar; a interação de verdade é do Frame."""
        self._hit_rects[hit_id] = pygame.Rect(f.px + round(x0 * S), f.py + round(y0 * S),
                                              round((x1 - x0) * S), round((y1 - y0) * S))

    # ── Conteúdo de cada estado: lista de itens + altura (px RAW) ───────────

    def _text_items(self, items: list, y: int, text: str, color, font, wrap_px: int,
                    indent: int = 0) -> int:
        for line in self._wrap(text, wrap_px - indent * S, font):
            items.append(("text", line, self._PADX + indent, y, color, font))
            y += self._lh(font)
        return y

    def _content_list(self, npc_name: str) -> tuple:
        """`npc_name` vai pra `_get_completable_quests` como
        `pending_npc_name` (04/09/2026, bug real): sem isso, uma quest cujo
        único objetivo pendente é "falar com ESTE NPC" ficava de fora da
        lista mesmo já completável."""
        avail = self._get_available_quests(self._dialog_npc_id)
        comp  = self._get_completable_quests(self._dialog_npc_id, npc_name)
        items, y = [], 0
        items.append(("text", "Escolha uma missão:", self._PADX, y, self.INK_SOFT, self._font_sm))
        y += self._lh(self._font_sm) + 4
        row_h = self._lh(self._font_body) + 4
        for qid, is_turnin in [(q, True) for q in comp] + [(q, False) for q in avail]:
            qdef = QUESTS.get(qid)
            if qdef is None:
                continue
            items.append(("row", qid, f"[{'?' if is_turnin else '!'}]  {qdef.title}", y, y + row_h))
            y += row_h + 2
        return items, y

    def _reward_text(self, qdef) -> str:
        parts = []
        if qdef.reward.xp:   parts.append(f"+{qdef.reward.xp} XP")
        if qdef.reward.gold: parts.append(f"+{qdef.reward.gold} ouro")
        return ", ".join(parts)

    def _content_detail(self, qdef, wrap_px: int) -> tuple:
        from engine.components import CharacterStats as _CharQD
        from engine.quest_logic import format_quest_text as _fmt_qd
        _char_qd = self.world.get_component(self.player_entity, _CharQD)
        items, y = [], 0
        y = self._text_items(items, y, qdef.title, self.INK_TITLE, self._font_lg, wrap_px) + 4
        desc = _fmt_qd(qdef.description, _char_qd.name if _char_qd else "")
        y = self._text_items(items, y, desc, self.INK, self._font_body, wrap_px) + 6
        y = self._text_items(items, y, "Objetivos:", self.INK_SOFT, self._font_sm, wrap_px)
        for obj in qdef.objectives:
            y = self._text_items(items, y, QuestSystem._obj_label(obj, 0, show_progress=False),
                                 self.INK_SOFT, self._font_sm, wrap_px, indent=8)
        rew = self._reward_text(qdef)
        if rew:
            y = self._text_items(items, y + 6, "Recompensa: " + rew, self.INK_GOLD,
                                 self._font_sm, wrap_px)
        return items, y

    def _content_turnin(self, qdef, wrap_px: int) -> tuple:
        from engine.components import CharacterStats as _CharQT
        from engine.quest_logic import format_quest_text as _fmt_qt
        _char_qt = self.world.get_component(self.player_entity, _CharQT)
        items, y = [], 0
        y = self._text_items(items, y, "Missão Completa!", self.INK_GOLD, self._font_lg, wrap_px)
        y = self._text_items(items, y, qdef.title, self.INK_TITLE, self._font_body, wrap_px) + 6
        completion = getattr(qdef, "completion", "") or "Bom trabalho. Aqui está sua recompensa."
        completion = _fmt_qt(completion, _char_qt.name if _char_qt else "")
        y = self._text_items(items, y, completion, self.INK, self._font_body, wrap_px) + 8
        rew = self._reward_text(qdef)
        if rew:
            y = self._text_items(items, y, "Recompensa: " + rew, self.INK_GOLD,
                                 self._font_sm, wrap_px) + 4

        # Itens de recompensa (23/07/2026) — fixos (`items`, sempre
        # concedidos) + skill + pool de escolha (`choice`, jogador escolhe
        # 1). Cada um num slot do atlas, no fim do texto (rola junto).
        fixed  = [("item", k, it) for k, it in self._reward_item_objects(qdef.reward.items)]
        skill  = self._reward_skill_info(qdef.reward.skill)
        if skill:
            fixed.append(("skill", skill[0], skill))
        y = self._slot_row(items, y, fixed)
        choice = [("choice", k, it) for k, it in self._reward_item_objects(qdef.reward.choice)]
        if choice:
            y = self._text_items(items, y + 2, "Escolha uma recompensa:", self.INK_SOFT,
                                 self._font_sm, wrap_px) + 2
            y = self._slot_row(items, y, choice)
        return items, y

    def _slot_row(self, items: list, y: int, entries: list) -> int:
        if not entries:
            return y
        x = self._PADX
        for sel_kind, key, obj in entries:
            items.append(("slot", sel_kind, key, obj, x, y))
            x += aw.SLOT + self._SLOT_GAP
        return y + aw.SLOT + 4

    # ── Recompensas: ícones e tooltip ────────────────────────────────────────

    def _draw_reward_icons(self, content: pygame.Surface, icons: list) -> None:
        """Ícones por cima dos slots já ampliados (ícone em resolução de
        tela, nunca ampliado em bloco). Escolha: selecionado ganha
        contorno dourado; os outros ficam esmaecidos depois de escolher."""
        from ui.icon_manager import ICONS
        inner = aw.slot_icon_px()
        for sel_kind, obj, x, y, dim, selected in icons:
            px, py = (x + 1) * S, (y + 1) * S
            if sel_kind == "skill":
                surf = ICONS.get(f"skill_{obj[0]}", inner)
                stack = 1
            else:
                surf = aw.item_icon(obj, self._font_sm, inner)
                stack = obj.stack
            if surf is not None:
                content.blit(surf, (px, py))
            if dim:
                content.blit(fill_surf((inner, inner), (0, 0, 0, 150)), (px, py))
            if selected:
                pygame.draw.rect(content, (255, 215, 60), (x * S, y * S, aw.SLOT * S, aw.SLOT * S), 2)
            if stack > 1:
                st = self._font_sm.render(str(stack), False, (255, 255, 255))
                content.blit(st, (px + inner - st.get_width(), py + inner - st.get_height()))

    def _set_reward_tooltip(self, sel_kind: str, obj) -> None:
        mx, my = pygame.mouse.get_pos()
        if sel_kind == "skill":
            skill_id, name = obj
            self.pending_tooltip = (mx, my, name, self._reward_skill_tooltip_lines(skill_id),
                                    (180, 140, 230))
            return
        from ui.ui_helpers import item_tooltip_lines, RARITY_COLORS
        from engine.components import CharacterStats as _CharRI
        _char_ri = self.world.get_component(self.player_entity, _CharRI)
        rar_col = RARITY_COLORS.get(obj.rarity, (200, 200, 200))
        self.pending_tooltip = (mx, my, obj.name,
                                item_tooltip_lines(obj, getattr(_char_ri, "class_id", None)), rar_col)

    def _reward_item_objects(self, entries: tuple) -> list:
        """Normaliza e instancia os itens de QuestReward.items/choice pra
        exibição (ícone/tooltip) — entradas que não resolvem (item_key
        digitado errado no catálogo) são puladas silenciosamente aqui
        (o servidor loga um warning na entrega de verdade, ver
        server/session.py::_handle_quest_turn_in). Retorna [(item_key,
        Item), ...]."""
        from engine.quest_logic import normalize_reward_entry, resolve_reward_item_factory
        out = []
        for entry in entries:
            item_key, stack = normalize_reward_entry(entry)
            factory = resolve_reward_item_factory(item_key)
            if factory is None:
                continue
            try:
                item = factory()
            except Exception:
                continue
            item.stack = max(1, min(stack, item.max_stack))
            out.append((item_key, item))
        return out

    def _reward_skill_info(self, skill_id: str) -> "tuple | None":
        """Resolve QuestReward.skill em (skill_id, name) pra exibição —
        None se a chave não existir em SKILL_CATALOG (mesmo espírito
        silencioso de _reward_item_objects; servidor loga o warning na
        entrega de verdade, ver server/session.py::_handle_quest_turn_in)."""
        if not skill_id:
            return None
        from content.skill_config import SKILL_CATALOG
        entry = SKILL_CATALOG.get(skill_id)
        if entry is None:
            return None
        return skill_id, entry.get("name", skill_id)

    def _reward_skill_tooltip_lines(self, skill_id: str) -> list:
        """Linhas de tooltip pra prévia de skill de recompensa — lê direto
        de SKILL_CATALOG (nome/desc/cooldown/custo). Diferente de
        client/tooltip_handlers.py::_skill_tooltip_lines, que exige um
        objeto Skill "ao vivo" com estado de cooldown/proc — não serve
        pra uma skill que o player ainda nem tem."""
        from content.skill_config import SKILL_CATALOG
        entry = SKILL_CATALOG.get(skill_id, {})
        lines = []
        desc = entry.get("desc", "")
        if desc:
            for ln in self._wrap(desc, self._u(220), self._font_sm):
                lines.append((ln, (200, 200, 200)))
        cd = entry.get("cooldown", 0.0)
        if cd:
            lines.append((f"Recarga: {cd:.0f}s", (160, 160, 160)))
        rage = entry.get("rage_cost", 0)
        if rage:
            lines.append((f"Custo: {rage} de raiva", (160, 160, 160)))
        return lines

    # ── Helpers de diálogo ───────────────────────────────────────────────────

    def _open_dialog(self, npc_id: int) -> None:
        from engine.components import NPC as _NPC
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

        self._dialog_npc_id      = npc_id
        self._scroll             = aw.ScrollState()
        self._turnin_chosen_item = None

        if len(total) == 1:
            qid = total[0]
            self._dialog_selected_qid = qid
            self._dialog_state = "turnin" if qid in comp else "detail"
        else:
            self._dialog_state        = "list"
            self._dialog_selected_qid = ""

    def open_for_item(self, qid: str, label: str = "") -> bool:
        """Abre o modal direto no estado 'detail' pra uma quest concedida
        por item (clique direito na bag) — MESMO diálogo do NPC, sem NPC
        real por trás (25/07/2026, pedido do usuário). `label` é o nome do
        item, usado como cabeçalho no lugar do nome de NPC. Retorna False
        (não abre nada) se a quest não existe ou já está ativa/completa —
        mesmo critério de elegibilidade que _open_dialog já aplica pra
        NPC, só que checado aqui direto (sem lista, vai reto pro detail)."""
        if QUESTS.get(qid) is None:
            return False
        from engine.components import QuestLog as _QL_item
        ql = self.world.get_component(self.player_entity, _QL_item)
        if ql is not None and (qid in ql.active or qid in ql.completed):
            return False
        self._dialog_npc_id       = -2   # sentinel: sem NPC real
        self._item_source         = True
        self._item_source_label   = label
        self._dialog_selected_qid = qid
        self._dialog_state        = "detail"
        self._scroll              = aw.ScrollState()
        self._turnin_chosen_item  = None
        return True

    def _close(self) -> None:
        self._dialog_npc_id       = -1
        self._dialog_state        = ""
        self._dialog_selected_qid = ""
        self._item_source         = False
        self._item_source_label   = ""
        self._turnin_chosen_item  = None

    # ── Consultas ao QuestLog ────────────────────────────────────────────────

    def _get_available_quests(self, npc_id: int) -> list:
        """Wrapper fino — lógica real em engine.quest_logic.get_available_quests
        (15/09/2026, item 4 da revisão de performance — extraída pra função
        pura de módulo, chamável por qualquer System sem referência a uma
        instância de QuestDialogSystem, ver arquitetura/
        CLIENT_PERFORMANCE_REVIEW.md)."""
        return quest_logic.get_available_quests(self.world, self.player_entity, npc_id)

    def _get_locked_quests(self, npc_id: int) -> list:
        """Wrapper fino — lógica real em
        engine.quest_logic.get_locked_quests (06/10/2026, mesmo motivo de
        _get_available_quests acima — era a única das 4 consultas
        duplicada aqui em vez de extraída)."""
        return quest_logic.get_locked_quests(self.world, self.player_entity, npc_id)

    def _get_completable_quests(self, npc_id: int, pending_npc_name: str = "") -> list:
        """Wrapper fino — lógica real em
        engine.quest_logic.get_completable_quests (mesmo motivo de
        _get_available_quests acima)."""
        return quest_logic.get_completable_quests(
            self.world, self.player_entity, npc_id, pending_npc_name)

    def _get_inprogress_quests(self, npc_id: int) -> list:
        """Wrapper fino — ver engine.quest_logic.get_inprogress_quests."""
        return quest_logic.get_inprogress_quests(self.world, self.player_entity, npc_id)

    # ── Helpers de navegação/tile ────────────────────────────────────────────

    def _get_cam(self):
        # Usa world_surf (superfície lógica de zoom), não hud_surf (tela
        # real) — sem isso o clique calcula a posição mundial errada quando
        # self._zoom != 1.0. _open_dialog/click usa esse offset + _sc (ver
        # update()). Fallback pra hud_surf se world_surf ainda não foi
        # atribuído (1º frame, antes de game.py::_assign_world_surf rodar —
        # zoom=1.0). Delega pro ponto único de verdade (engine.utils.
        # get_camera_offset) — já inclui o clamp de borda de mapa; uma cópia
        # local sem o clamp ficava fora de sincronia com o que é desenhado
        # de verdade perto da borda (ver PROBLEMAS_ARQUITETURA.md §190).
        from engine.utils import get_camera_offset
        SW, SH = (self.world_surf or self.hud_surf).get_size()
        return get_camera_offset(self.world, SW, SH)

    def _player_tile(self):
        from engine.components import TileMovement
        tm = self.world.get_component(self.player_entity, TileMovement)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _npc_tile(self, eid: int):
        from engine.tileset import TILE_SIZE as TS
        from engine.components import Position
        pos = self.world.get_component(eid, Position)
        return (int(pos.x / TS), int(pos.y / TS)) if pos else None

    def _npc_name(self, eid: int) -> str:
        from engine.components import NPC as _NPCname
        npc = self.world.get_component(eid, _NPCname)
        return npc.name if npc else "NPC"

    @staticmethod
    def _cheby(t1, t2) -> int:
        from engine.utils import chebyshev
        return chebyshev(t1[0], t1[1], t2[0], t2[1])

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
            from engine.components import QuestLog
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
        from engine.components import QuestLog
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
        title_s = self._font_title.render("Diario de Quests", False, self.COL_HEADER)
        self.hud_surf.blit(title_s, (x0 + self._u(self.PAD), y0 + self._u(self.PAD)))

        # Botão X
        close_r = pygame.Rect(x0 + PANEL_W - self._u(28), y0 + self._u(8), self._u(22), self._u(22))
        pygame.draw.rect(self.hud_surf, (80, 40, 30), close_r, border_radius=3)
        pygame.draw.rect(self.hud_surf, self.COL_BORDER, close_r, 1, border_radius=3)
        xs = self._font_body.render("X", False, (220, 180, 140))
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
                sep_s = self._font_sm.render(label, False, color)
                self.hud_surf.blit(sep_s, (row_abs.x + self._u(4), row_abs.centery - sep_s.get_height() // 2))
                continue

            is_sel = (qid == self._selected_qid)
            is_hov = row_abs.collidepoint(mx, my)
            bg = self.COL_SEL_BG if is_sel else (self.COL_HOVER_BG if is_hov else None)
            if bg:
                pygame.draw.rect(self.hud_surf, bg, row_abs)
            if is_sel:
                pygame.draw.rect(self.hud_surf, self.COL_BORDER, row_abs, 1)

            txt_s = self._font_sm.render(label, False, color)
            self.hud_surf.blit(txt_s, (row_abs.x + self._u(8), row_abs.centery - txt_s.get_height() // 2))
            self._entry_rects.append((row_abs, qid))

    def _render_detail(self, x0: int, y0: int) -> None:
        from engine.components import QuestLog
        if not self._selected_qid:
            hint = self._font_body.render("Selecione uma quest na lista.", False, self.COL_GREY)
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
        title_s = self._font_title.render(title_text, False, col_t)
        self.hud_surf.blit(title_s, (dx, dy))
        dy += title_s.get_height() + self._u(4)

        # Nível requerido
        if qdef.level_req > 0:
            plvl = self._qs._player_level()
            lvl_col = self.COL_GREY if plvl >= qdef.level_req else (200, 80, 80)
            lvl_s = self._font_sm.render(f"Nivel minimo: {qdef.level_req}", False, lvl_col)
            self.hud_surf.blit(lvl_s, (dx, dy))
            dy += lvl_s.get_height() + self._u(6)
        else:
            dy += self._u(2)

        # Linha separadora
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (dx, dy), (dx + max_w, dy))
        dy += self._u(8)

        # Descrição
        from engine.components import CharacterStats as _CharQJ
        from engine.quest_logic import format_quest_text as _fmt_qj
        _char_qj = self.world.get_component(self.player_entity, _CharQJ)
        description_txt = _fmt_qj(qdef.description, _char_qj.name if _char_qj else "")
        for line in self._wrap(description_txt, max_w, self._font_body):
            s = self._font_body.render(line, False, self.COL_WHITE)
            self.hud_surf.blit(s, (dx, dy))
            dy += s.get_height() + self._u(2)
        dy += self._u(8)

        # Objetivos
        obj_hdr = self._font_sm.render("Objetivos:", False, self.COL_HEADER)
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
            obj_s = self._font_body.render(f"  {label}", False, col)
            self.hud_surf.blit(obj_s, (dx, dy))
            dy += obj_s.get_height() + self._u(2)
        dy += self._u(8)

        # Recompensas
        rew_hdr = self._font_sm.render("Recompensas:", False, self.COL_HEADER)
        self.hud_surf.blit(rew_hdr, (dx, dy))
        dy += rew_hdr.get_height() + self._u(4)

        parts = []
        if qdef.reward.xp:   parts.append(f"{qdef.reward.xp} XP")
        if qdef.reward.gold: parts.append(f"{qdef.reward.gold} Ouro")
        rew_text = "  " + "  |  ".join(parts) if parts else "  Nenhuma"
        rew_s = self._font_body.render(rew_text, False, self.COL_REWARD)
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
            lbl = self._font_body.render("Abandonar Quest", False, (230, 160, 140))
            self.hud_surf.blit(lbl, (abn_r.centerx - lbl.get_width() // 2,
                                   abn_r.centery - lbl.get_height() // 2))
            self._abandon_rect = abn_r

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _rebuild_list(self) -> None:
        from engine.components import QuestLog
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
