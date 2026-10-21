# trainer_system.py
"""
TrainerSystem — gerencia interação com NPCs treinadores de classe.

Fluxo:
  1. Clique direito em NPC com componente Trainer → action menu (ou pula
     direto pro destino se só houver 1 assunto pra essa classe, ou pra
     entrega de quest se houver alguma completável — ver _open_menu)
  2. Action menu: Treinamento / Quests (cada um só aparece se houver algo
     de fato disponível pra essa classe — ver _compute_options)
  3. Modal de treinamento: lista de skills com requisito de nível e custo
"""
from __future__ import annotations
import math
import pygame
from ui.systems import System
from engine.components import (Position, Renderable, TileMovement,
                        NPC, Trainer, Wallet, NPCInteractionRequest)
from engine.utils import get_camera_offset, chebyshev
import engine.quest_logic as quest_logic
from content.skill_config import (SKILL_CATALOG, SKILL_LEVEL_REQUIREMENTS, SKILL_COSTS,
                          NUM_SLOTS, SKILL_ORDER_BY_CLASS)
from ui.icon_manager import ICONS
from ui.combat_log import LOG
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_scale_mixin import UIScaleMixin
from ui.npc_interaction import walk_to_npc, suppress_chase

# ---------------------------------------------------------------------------
# Constantes visuais
# ---------------------------------------------------------------------------
_COL_BG      = (12,  10,   6)
_COL_PANEL   = (22,  18,  10)
_COL_BORDER  = (90,  70,  30)
_COL_TITLE   = (220, 180,  80)
_COL_WHITE   = (240, 220, 180)
_COL_GREY    = (110,  90,  60)
_COL_GOLD    = (220, 180,  50)
_COL_RED     = (200,  60,  60)
_COL_GREEN   = ( 60, 180,  60)
_COL_LEARNED = ( 60,  55,  40)
_COL_DARK    = ( 16,  13,   6)
_COL_BTN_OK  = ( 30,  90,  30)
_COL_BTN_DIS = ( 40,  35,  25)


# Ordem padrão (guerreiro) — substituída dinamicamente por _skill_order_for()
_SKILL_ORDER = ["golpe_poderoso", "impacto", "vitoria_iminente", "interceptar", "executar"]


def _skill_order_for(class_id: str) -> list:
    return SKILL_ORDER_BY_CLASS.get(class_id, _SKILL_ORDER)


class TrainerSystem(UIScaleMixin, System):
    """Gerencia o menu de ação e o modal de treinamento de skills."""

    STATE_CLOSED   = "CLOSED"
    STATE_MENU     = "ACTION_MENU"
    STATE_TRAINING = "TRAINING"

    _FONT_BASES = {"_font_xs": 15, "_font_sm": 20, "_font_md": 26, "_font_lg": 32, "_font_xl": 38}

    def __init__(self, world, player_entity: int, screen):
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.hud_surf        = screen

        SW, SH = screen.get_size()

        # Estado
        self._state:   str = self.STATE_CLOSED
        self._tr_eid:  int = -1
        self._tr_name: str = ""
        self._tr_class_id: str = "guerreiro"
        self._pending_tr_id: int = -1

        self._right_click_consumed: bool = False
        self._open_cooldown: float = 0.0
        # Envia TRAIN_SKILL_REQUEST ao servidor (04/10/2026) — setado de
        # fora (game.py), mesmo padrão de ShopSystem/BlacksmithSystem._net.
        self._net = None

        # Atlas de UI: botão afundado, rolagem da lista, eventos do frame
        # (guardados em handle_events, consumidos em render), área segura
        # (injetada por game.py), rects clicáveis (testes).
        self._press = aw.PressState()
        self._scroll = aw.ScrollState()
        self._frame_events: list = []
        self.safe_area_fn = None
        self._hit_rects: dict = {}

    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._state != self.STATE_CLOSED

    # ------------------------------------------------------------------
    # Helpers de navegação
    # ------------------------------------------------------------------
    def _player_tile(self):
        from engine.components import TileMovement as _TM
        tm = self.world.get_component(self.player_entity, _TM)
        return (tm.current_tile_x, tm.current_tile_y) if tm else None

    def _tr_tile(self, eid: int):
        from engine.tileset import TILE_SIZE as _TS
        tm = self.world.get_component(eid, TileMovement)
        if tm:
            return (tm.current_tile_x, tm.current_tile_y)
        pos = self.world.get_component(eid, Position)
        if pos:
            return (int(pos.x // _TS), int(pos.y // _TS))
        return None

    @staticmethod
    def _cheby(a, b) -> int:
        return chebyshev(a[0], a[1], b[0], b[1])

    def _player_level(self) -> int:
        from engine.components import CharacterStats
        cs = self.world.get_component(self.player_entity, CharacterStats)
        return cs.level if cs else 1

    def _player_skills(self):
        from engine.components import PlayerSkills
        return self.world.get_component(self.player_entity, PlayerSkills)

    def _player_wallet(self):
        return self.world.get_component(self.player_entity, Wallet)

    # ------------------------------------------------------------------
    # update() — detecção de clique + pendência de chegada
    # ------------------------------------------------------------------
    def update(self, events=None, dt: float = 0):
        if events is None:
            events = []

        self._right_click_consumed = False

        if self._open_cooldown > 0:
            self._open_cooldown -= dt

        # Chegou ao treinador pendente?
        if self._pending_tr_id != -1:
            pt = self._player_tile()
            bt = self._tr_tile(self._pending_tr_id)
            if pt and bt and self._cheby(pt, bt) <= 1:
                self._open_menu(self._pending_tr_id)
                self._pending_tr_id = -1

        # ESC
        for ev in events:
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                if self._state != self.STATE_CLOSED:
                    self._close()
                return

        # Clique direito em Trainer (apenas quando fechado)
        if self._state == self.STATE_CLOSED and self._open_cooldown <= 0:
            # Usa world_surf (superfície lógica de zoom), não hud_surf (tela
            # real) — sem isso o clique calcula a posição mundial errada
            # quando self._zoom != 1.0 (ver ShopSystem._get_cam() em
            # systems.py, que já faz certo). Fallback pra hud_surf se
            # world_surf ainda não foi atribuído (1º frame, antes de
            # game.py::_assign_world_surf rodar — equivale a zoom=1.0).
            _wsurf = self.world_surf or self.hud_surf
            SW, SH = _wsurf.get_size()
            cx, cy = get_camera_offset(self.world, SW, SH)
            _sc = _wsurf.get_width() / max(1, self.hud_surf.get_width())

            for eid, pos, rend, _ in self.world.get_entities_with(
                    Position, Renderable, Trainer):
                for ev in events:
                    if ev.type != pygame.MOUSEBUTTONDOWN or ev.button != 3:
                        continue
                    wx = ev.pos[0] * _sc + cx
                    wy = ev.pos[1] * _sc + cy
                    hw = rend.width  / 2
                    hh = rend.height / 2
                    if abs(wx - pos.x) <= hw and abs(wy - pos.y) <= hh:
                        pt = self._player_tile()
                        bt = self._tr_tile(eid)
                        if pt and bt and self._cheby(pt, bt) <= 1:
                            suppress_chase(self.world, self.player_entity)
                            self._open_menu(eid)
                        else:
                            self._pending_tr_id = eid
                            walk_to_npc(self.world, self.player_entity, pt, bt)
                        self._right_click_consumed = True
                        break
                if self._right_click_consumed:
                    break

    # ------------------------------------------------------------------
    # handle_events() — quando is_open
    # ------------------------------------------------------------------
    def handle_events(self, events):
        """Atlas de UI: cliques/rolagem agem no SOLTAR dentro de render();
        aqui só se guardam os eventos do frame."""
        self._frame_events = list(events)

    # ------------------------------------------------------------------
    # Ações internas
    # ------------------------------------------------------------------
    def _compute_options(self, eid: int) -> list:
        """Opções de assunto disponíveis pra ESTE player com ESTE NPC —
        "Treinamento" só se a classe do player bate com a do treinador;
        "Quests" só se houver de fato algo pra essa classe (disponível, em
        progresso ou completável — não só "o NPC tem quest_ids").

        `self._tr_name` (setado por `_open_menu` antes de chamar isto) vai
        pra `_get_completable_quests` como `pending_npc_name` — mesmo
        motivo de `_open_menu` logo abaixo: sem isso, uma quest cujo ÚNICO
        objetivo pendente é justamente "falar com ESTE NPC" não conta como
        completável ainda (o talk_to_npc só é aplicado de verdade quando o
        diálogo de quest abre), e cai como "em progresso" por engano."""
        from engine.components import CharacterStats, QuestLog
        options = []
        cs = self.world.get_component(self.player_entity, CharacterStats)
        tr = self.world.get_component(eid, Trainer)
        tr_class = tr.class_id if tr else "guerreiro"
        # Trava de quest (04/10/2026, pedido do usuário; generalizada por
        # NPC individual em 06/10/2026, ver NPCUnlock/
        # quest_logic.is_npc_unlocked) — treino só entra como opção
        # depois que a trava do NPC estiver completa. Sem isso virar
        # opção, _open_menu abaixo já prioriza quest disponível/
        # completável sozinho (nenhuma mudança necessária lá) — é por
        # isso que nenhuma mensagem de "bloqueado" é preciso: o player
        # só vê o fluxo de quest, igual sempre foi pra NPC que só tem
        # quest pra oferecer.
        ql = self.world.get_component(self.player_entity, QuestLog)
        _unlocked = quest_logic.is_npc_unlocked(self.world, eid, ql)
        if cs and cs.class_id == tr_class and _unlocked:
            options.append(("treinamento", "Treinamento"))
        if (quest_logic.get_available_quests(self.world, self.player_entity, eid) or
                quest_logic.get_completable_quests(self.world, self.player_entity, eid, self._tr_name) or
                quest_logic.get_inprogress_quests(self.world, self.player_entity, eid)):
            options.append(("quest:0", "Quests"))
        return options

    def _open_menu(self, eid: int):
        self._tr_eid  = eid
        npc = self.world.get_component(eid, NPC)
        self._tr_name = npc.name if npc else "Treinador"
        tr  = self.world.get_component(eid, Trainer)
        self._tr_class_id = tr.class_id if tr else "guerreiro"

        # Prioridade: quest pronta pra entregar abre direto o diálogo de
        # quest, nunca o menu de assunto (mesmo com treinamento disponível).
        # `pending_npc_name=self._tr_name` (04/09/2026, bug real reportado
        # pelo usuário): sem isso, uma quest cujo único objetivo pendente é
        # "falar com ESTE NPC" nunca aparecia aqui como completável (o
        # talk_to_npc só conta depois que `_open_dialog` processa — que só
        # roda se a gente já tivesse decidido abrir o diálogo de quest, ciclo
        # vicioso). Resultado: o menu de "Quests/Treinamento" aparecia
        # primeiro, e a quest só se resolvia depois de já estar dentro do
        # diálogo — mesma checagem que `_open_dialog` já faz certo, só que
        # ANTES de decidir se pula o menu.
        if quest_logic.get_completable_quests(self.world, self.player_entity, eid, self._tr_name):
            self.world.add_component(self.player_entity, NPCInteractionRequest(
                kind="open_quest_dialog", target_eid=eid))
            self._state = self.STATE_CLOSED
            self._open_cooldown = 0.3
            return

        options = self._compute_options(eid)

        # Só 1 assunto (ou nenhum) pra essa classe — pula direto pro
        # destino, sem mostrar o menu de "o que deseja?".
        if len(options) <= 1:
            key = options[0][0] if options else "_none"
            if key == "treinamento":
                self._state = self.STATE_TRAINING
                self._scroll = aw.ScrollState()
            elif key.startswith("quest:"):
                self.world.add_component(self.player_entity, NPCInteractionRequest(
                    kind="open_quest_dialog", target_eid=eid))
                self._state = self.STATE_CLOSED
            else:
                from engine.components import CharacterStats
                cs = self.world.get_component(self.player_entity, CharacterStats)
                LOG.add(f"{self._tr_name}: nada para {cs.name if cs else 'você'} aqui.", _COL_GREY)
                self._state = self.STATE_CLOSED
            self._open_cooldown = 0.3
            return

        self._state = self.STATE_MENU
        self._open_cooldown = 0.3

    def _close(self):
        self._state         = self.STATE_CLOSED
        self._tr_eid        = -1
        self._pending_tr_id = -1
        self._open_cooldown = 0.2

    def _do_learn(self, skill_id: str):
        """Pedido de treino (04/10/2026 — antes era 100% local/client-
        trusted; ouro/skill concedidos de verdade só depois da
        confirmação do servidor, ver `_handle_msg_train_skill_result`,
        client/network_handlers.py). As checagens abaixo continuam
        client-side pra feedback INSTANTÂNEO nos casos óbvios (mesma
        mensagem de sempre) — o servidor repete TODAS elas de propósito
        (`WorldServer.process_train_skill`), nunca confia só nisto."""
        ps     = self._player_skills()
        wallet = self._player_wallet()
        if not ps or not wallet:
            return

        if skill_id in ps.learned_skill_ids:
            return

        req   = SKILL_LEVEL_REQUIREMENTS.get(skill_id, 99)
        cost  = SKILL_COSTS.get(skill_id, 9999)
        level = self._player_level()

        if level < req:
            LOG.add(f"Requer nível {req} para aprender.", _COL_RED)
            return
        if wallet.gold < cost:
            LOG.add(f"Ouro insuficiente. Necessário: {cost}g", _COL_RED)
            return

        from engine.components import CharacterStats
        cs = self.world.get_component(self.player_entity, CharacterStats)
        if cs and cs.class_id != self._tr_class_id:
            LOG.add(
                f"Apenas {self._tr_class_id.capitalize()}s podem aprender essas habilidades.",
                _COL_RED,
            )
            return

        if not self._net:
            return
        # self._tr_eid e o eid LOCAL do mirror (criado por
        # client/remote_entity_handlers.py::_spawn_remote_mob via
        # create_enemy() -- NAO reusa o eid do servidor). O servidor so
        # conhece seus proprios eids (process_train_skill faz
        # world.get_component(npc_id, Trainer) contra o World dele) --
        # mandar o eid local sempre resolvia pra "invalid_trainer", pra
        # QUALQUER player, sempre (bug real reportado por tester, 05/10/2026
        # -- nunca era sobre a trava de quest). Resolve pro server_eid
        # ANTES de mandar, mesmo padrao ja usado por
        # ui/skill_system.py (cast de skill em alvo) e
        # client/trade_handlers.py (abrir trade).
        from engine.components import RemoteEntityMeta as _REM_train
        meta = self.world.get_component(self._tr_eid, _REM_train)
        if meta is None:
            return
        from shared.messages import MsgType as _MTtrain
        self._net.send(_MTtrain.TRAIN_SKILL_REQUEST, {
            "npc_id": meta.server_eid, "skill_id": skill_id,
        })

    # ------------------------------------------------------------------
    # render_world() — indicador sobre NPC
    # ------------------------------------------------------------------
    def render_world(self, cam_x: float, cam_y: float):
        # Indicador "T" removido (11/07/2026) — o nome do NPC (NPC.name,
        # desenhado por ui/systems.py::RenderSystem pra QUALQUER NPC) já
        # identifica o treinador, mesmo padrão do merchant "LOJA" removido
        # antes (ui/systems.py::ShopSystem.render_world). Ícone por classe
        # (map_trainer_*) existe só pro mapa/minimapa (ui/map_markers.py).
        pass

    # ------------------------------------------------------------------
    # render() — atlas de UI (21/10/2026)
    # ------------------------------------------------------------------
    # Menu de assunto: aviso do kit (atlas_window.prompt) com a saudação.
    # Treinamento: janela escura com barra de título e X; 1 linha de 2
    # tiles por habilidade (slot com ícone, nome, descrição, nível, custo,
    # Aprender); lista rolável; ouro e nível do jogador embaixo. Cliques no
    # SOLTAR (atlas_window).

    _ROW_H     = aw.SLOT + 4  # px RAW por habilidade (slot de ícone 32x32 + folga)
    _VIEW_ROWS = 4           # habilidades visíveis antes de rolar
    _LIST_X    = 3           # slots encostados à esquerda (2px depois da borda)

    def _area(self) -> pygame.Rect:
        if self.safe_area_fn is not None:
            return self.safe_area_fn()
        SW, SH = self.hud_surf.get_size()
        return aw.safe_area(SW, SH, SH)

    def render(self):
        events, self._frame_events = self._frame_events, []
        self._hit_rects = {}
        if self._state == self.STATE_CLOSED:
            return
        if self._state == self.STATE_MENU:
            self._draw_action_menu(events)
        elif self._state == self.STATE_TRAINING:
            self._draw_training_modal(events)

    def _draw_action_menu(self, events):
        options = self._compute_options(self._tr_eid)
        from engine.components import CharacterStats
        cs = self.world.get_component(self.player_entity, CharacterStats)
        if not options:
            options = [("_none", "Fechar")]
        player_name = cs.name if cs else "Aventureiro"
        fired = aw.prompt(self.hud_surf, events, area=self._area(), press=self._press, font=self._font_md,
                          title=self._tr_name, message=f"Olá {player_name}, o que deseja?",
                          buttons=[lbl for _, lbl in options], hits=self._hit_rects, hit_prefix="tr:opt")
        if fired is None:
            return
        key = options[fired][0]
        if key == "treinamento":
            self._state = self.STATE_TRAINING
            self._scroll = aw.ScrollState()
        elif key.startswith("quest:"):
            self.world.add_component(self.player_entity, NPCInteractionRequest(
                kind="open_quest_dialog", target_eid=self._tr_eid))
            self._state = self.STATE_CLOSED
        else:
            self._close()

    @staticmethod
    def _truncate(text: str, font, max_px: int) -> str:
        if font.size(text)[0] <= max_px:
            return text
        while len(text) > 1 and font.size(text + "...")[0] > max_px:
            text = text[:-1]
        return text + "..."

    def _draw_training_modal(self, events):
        T, S = aw.T, aw.S
        ps     = self._player_skills()
        wallet = self._player_wallet()
        level  = self._player_level()
        order  = _skill_order_for(self._tr_class_id)
        title  = f"{self._tr_name} — Treinamento"

        lvl_w  = self._font_sm.size("Nível 99")[0] / S
        cost_w = self._font_sm.size("99999g")[0] / S
        bw     = aw.tiles_for(self._font_sm, ["Aprender", "Aprendido"], 4)
        sw_area = self._area().width // (T * S)
        tw = max(min(sw_area, 30), aw.tiles_for(self._font_md, [title], 1) + 3)
        row_h = self._ROW_H
        view_y0, view_h = 2 * T, self._VIEW_ROWS * row_h
        th = math.ceil((view_y0 + view_h + 26) / T)
        lw = (tw - 1) * T                                   # lista | rolagem (última coluna)
        btn_x = lw - 4 - bw * T                             # botão (px RAW, dentro da lista)
        cost_x = btn_x - 6
        lvl_x = cost_x - cost_w - 10
        text_x = self._LIST_X + aw.SLOT + 8
        text_w = (lvl_x - lvl_w - 10 - text_x) * S

        f = aw.Frame(self.hud_surf, events, tw, th, area=self._area(), press=self._press,
                     font=self._font_md, raw=ui_atlas.titled_dark_window_raw(tw, th))
        f.text(title, (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200))
        if f.close_button("tr:close", tw - 1):
            self._close()
            return
        self._hit_rects["tr:close"] = pygame.Rect(f.px + (tw - 1) * T * S, f.py, T * S, T * S)
        self._panel_rect = pygame.Rect(f.px, f.py, f.pw, f.ph)

        hdr = (160, 140, 100)
        f.text("Habilidade", text_x, 1.5 * T, hdr, self._font_sm, anchor="midleft")
        f.text("Nível", lvl_x, 1.5 * T, hdr, self._font_sm, anchor="midright")
        f.text("Custo", cost_x, 1.5 * T, hdr, self._font_sm, anchor="midright")

        content_h = max(1, len(order) * row_h)
        off = f.scrollbar(self._scroll, tw - 1, view_y0 // T, (view_y0 + view_h) // T - 1,
                          max(0, content_h - view_h), step=row_h)
        in_view = view_y0 <= f.ry < view_y0 + view_h and 0 <= f.rx < lw
        fc = f.child(pygame.Surface((lw, content_h), pygame.SRCALPHA),
                     f.ry - view_y0 + off if in_view else -10_000)
        learn = None
        ay0, ay1 = ui_atlas.button_art_y(False)
        for i, skill_id in enumerate(order):
            entry = SKILL_CATALOG.get(skill_id, {})
            name = entry.get("name", skill_id) if isinstance(entry, dict) else skill_id
            desc = entry.get("desc", "") if isinstance(entry, dict) else ""
            req = SKILL_LEVEL_REQUIREMENTS.get(skill_id, 99)
            cost = SKILL_COSTS.get(skill_id, 0)
            learned = ps is not None and skill_id in ps.learned_skill_ids
            enabled = (not learned) and level >= req and wallet is not None and wallet.gold >= cost
            y = i * row_h
            if i:
                fc.divider_h(y, 0, lw)
            icon = ICONS.get(f"skill_{skill_id}", aw.slot_icon_px())
            fc.slot(f"tr:icon:{i}", self._LIST_X, y + 2, icon, dim=learned)
            fc.text(name, text_x, y + 12, aw.DISABLED_COL if learned else aw.TEXT_COL,
                    self._font_md, anchor="midleft")
            fc.text(self._truncate(desc, self._font_xs, int(text_w)), text_x, y + 27,
                    (150, 140, 120), self._font_xs, anchor="midleft")
            fc.text(f"Nível {req}", lvl_x, y + row_h / 2, _COL_GREEN if level >= req else _COL_RED,
                    self._font_sm, anchor="midright")
            fc.text(f"{cost}g", cost_x, y + row_h / 2, _COL_GOLD, self._font_sm, anchor="midright")
            # Botão "baixo" em pixel livre (centrado na linha da skill)
            bx0 = btn_x
            bpy = int(y + (row_h - T) / 2)
            pressed, fired = fc.press(f"tr:learn:{i}", bx0, bpy + ay0, bx0 + bw * T, bpy + ay1) \
                if enabled else (False, False)
            self._blit_button_px(fc.raw, bx0, bpy, bw, pressed)
            fc.text("Aprendido" if learned else "Aprender", bx0 + bw * T / 2, bpy + (ay0 + ay1) / 2,
                    aw.BTN_TXT if enabled else aw.DISABLED_COL, self._font_sm)
            if fired:
                learn = skill_id
            ry = view_y0 + y - off
            if view_y0 <= ry and ry + row_h <= view_y0 + view_h:
                self._hit_rects[f"tr:learn:{i}"] = pygame.Rect(
                    f.px + bx0 * S, f.py + (view_y0 + bpy + ay0 - off) * S, bw * T * S, (ay1 - ay0) * S)

        if wallet:
            f.text(f"Saldo: {wallet.gold}g", T, (th - 1) * T, _COL_GOLD, self._font_sm, anchor="midleft")
        f.text(f"Seu nível: {level}", (tw - 1) * T - 4, (th - 1) * T, aw.TEXT_COL, self._font_sm,
               anchor="midright")
        f.end()
        content = ui_atlas.scale_up(fc.raw)
        aw.draw_texts(content, fc.texts, self._font_sm)
        aw.draw_overlays(content, fc.overlays)
        view = content.subsurface((0, off * S, content.get_width(), max(0, min(view_h, content_h - off)) * S))
        self.hud_surf.blit(view, (f.px, f.py + view_y0 * S))
        if learn is not None:
            self._do_learn(learn)

    @staticmethod
    def _blit_button_px(dst, x: int, y: int, w: int, pressed: bool) -> None:
        """Botão "baixo" do atlas em pixel RAW livre (linha de skill não
        alinha com a grade de tiles da janela)."""
        tmp = pygame.Surface((w * aw.T, aw.T), pygame.SRCALPHA)
        ui_atlas.blit_button(tmp, 0, 0, w, pressed=pressed, tall=False)
        dst.blit(tmp, (x, y))
