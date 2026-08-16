"""
modal_stack_handlers.py — Mixin com o registro centralizado de prioridade de
modais (ModalStack). Único ponto de verdade pra "qual modal está no topo" —
usado tanto por _close_top_modal (ESC fecha o de maior prioridade) quanto
pelo filtro de eventos que bloqueia input dos sistemas ECS quando algum modal
está aberto (ver game.py, bloco de systems_events). Antes desta extração, a
mesma ordem de prioridade só existia hardcoded dentro de _close_top_modal,
sem reuso — ver PROBLEMAS_ARQUITETURA.md item IU3.

Separado de game.py para manter GameEngine conciso. Esta classe NÃO deve ser
instanciada diretamente — ela é herdada por GameEngine, que fornece
self.world, self.player_entity, self._mkb_rebind, self._show_habilidades,
self._map_overlay, self._loot_system, self._crafting_system,
self._trainer_system, self._quest_dialog, self._quest_journal,
self._shop_system, self._show_hotbar_editor, self._show_debug,
self._show_talents, self._show_skills, self._show_inventory, self._show_pause,
self._pause_submenu, self._sound_drag, self._selected_inv_idx,
self._close_hotbar_editor (já existe em hotbar_editor_handlers.py),
self._trade_is_open/_close_trade (trade_handlers.py) e, desde 10/08/2026,
os modais de PvP: self._arena_pending_match/_bg_pending_match/_arena_result/
_bg_result/_arena_modal_open (arena_handlers.py/bg_queue_handlers.py/
battleground_handlers.py) + self._send_arena_forfeit/_send_bg_leave.
"""
from ui.sound_manager import SOUNDS


class ModalStackHandlers:

    def _modal_registry(self) -> list:
        """Lista (nome, is_open_fn, close_fn) em ordem de prioridade — topo =
        primeiro match. Mesma ordem que _close_top_modal já usava antes desta
        extração; qualquer novo modal deve ser inserido aqui na posição certa
        em vez de criar mais um if/elif solto em outro lugar."""
        return [
            ("mkb_rebind",    lambda: self._mkb_rebind is not None,           self._close_mkb_rebind),
            ("chat",          lambda: self._chat_active,                      self._close_chat_input),
            ("habilidades",   lambda: self._show_habilidades,                 self._close_habilidades),
            ("map_overlay",   lambda: self._map_overlay.is_open,              self._close_map_overlay),
            ("loot",          lambda: self._loot_system.open_corpse_id != -1, self._loot_system._close_modal),
            ("crafting",      lambda: self._crafting_system.is_open,          self._crafting_system._close),
            ("trainer",       lambda: self._trainer_system.is_open,           self._trainer_system._close),
            ("quest_dialog",  lambda: self._quest_dialog.is_open,              self._quest_dialog._close),
            ("quest_journal", lambda: self._quest_journal.is_open,             self._quest_journal.close),
            ("shop_qty",      lambda: self._shop_system.qty_modal_open,        self._shop_system._close_qty_modal),
            ("shop",          lambda: self._shop_system.is_open,               self._shop_system._close),
            ("trade_qty",     lambda: self._trade_qty_modal is not None,       self._close_trade_qty_modal),
            ("trade",         lambda: self._trade_is_open,                     self._close_trade),
            # Fila/partida de PvP (Arena + Battleground, 10/08/2026 — achado
            # real do usuário: nenhum destes 5 estava no registro, ESC não
            # fechava nenhum). Modal de resultado fecha mandando a mesma ação
            # do botão "Sair da Arena"/"Voltar" (mesmo padrão de "trade"
            # acima, que também manda TRADE_DECLINE/CANCEL ao fechar via ESC)
            # — não é só esconder UI, é a ação real de sair da partida.
            ("arena_accept",  lambda: self._arena_pending_match is not None,   self._close_arena_accept),
            ("bg_accept",     lambda: self._bg_pending_match is not None,      self._close_bg_accept),
            ("arena_result",  lambda: self._arena_result is not None,          self._close_arena_result),
            ("bg_result",     lambda: self._bg_result is not None,             self._close_bg_result),
            ("arena_queue",   lambda: self._arena_modal_open,                  self._close_arena_queue_modal),
            ("hotbar_editor", lambda: self._show_hotbar_editor,                self._close_hotbar_editor),
            ("debug",         lambda: self._show_debug,                       self._close_debug),
            ("talents",       lambda: self._show_talents,                     self._close_talents),
            ("skill_level",   lambda: self._show_skills,                      self._close_skill_level),
            ("inventory",     lambda: self._show_inventory,                   self._close_inventory),
            ("pause",         lambda: self._show_pause,                       self._close_pause),
        ]

    def _topmost_open_modal(self) -> "str | None":
        """Nome do modal de maior prioridade atualmente aberto, ou None."""
        for name, is_open, _close in self._modal_registry():
            if is_open():
                return name
        return None

    def _any_modal_open(self) -> bool:
        return self._topmost_open_modal() is not None

    def _close_top_modal(self) -> bool:
        """Fecha o modal de maior prioridade atualmente aberto.
        Retorna True se fechou algo, False se nada estava aberto."""
        for _name, is_open, close in self._modal_registry():
            if is_open():
                close()
                return True
        return False

    # ── Closers pequenos — extraídos do antigo if/elif de _close_top_modal,
    # comportamento idêntico. Modais que já tinham um close próprio no seu
    # sistema (_loot_system._close_modal, _crafting_system._close, etc.) são
    # referenciados direto no registry acima, sem wrapper aqui. ──────────────

    def _close_mkb_rebind(self) -> None:
        self._mkb_rebind = None

    def _close_habilidades(self) -> None:
        self._show_habilidades = False
        _drag = self._get_drag()
        if _drag.kind == "skill" and _drag.source == "habilidades":
            _drag.reset()

    def _close_map_overlay(self) -> None:
        SOUNDS.play_ui("map_close")
        self._map_overlay.is_open = False

    def _close_debug(self) -> None:
        self._show_debug = False

    def _close_talents(self) -> None:
        SOUNDS.play_ui("talent_close")
        self._show_talents = False

    def _close_skill_level(self) -> None:
        self._show_skills = False

    def _close_inventory(self) -> None:
        SOUNDS.play_ui("inventory_close")
        self._show_inventory   = False
        self._selected_inv_idx = -1

    def _close_pause(self) -> None:
        if self._pause_submenu:
            self._pause_submenu = ""
            self._sound_drag    = ""
        else:
            self._show_pause = False

    def _close_arena_accept(self) -> None:
        """ESC na janela "Partida encontrada!" (Arena) — só esconde
        localmente, sem mandar nada ao servidor: o convite pendente lá
        expira sozinho no `accept_deadline` de qualquer forma (mesmo
        comportamento de perder o timer sem clicar Aceitar), então não
        existe ação de "recusar" separada a mandar."""
        self._arena_pending_match_val = None

    def _close_bg_accept(self) -> None:
        """Mesma lógica de `_close_arena_accept`, pro modal da BG."""
        self._bg_pending_match_val = None

    def _close_arena_result(self) -> None:
        """ESC na tela de fim de partida da Arena — mesma ação do botão
        "Sair da Arena" (mesmo padrão de `_close_trade`, que também manda
        uma mensagem real ao servidor em vez de só esconder UI). O modal
        só some de fato quando o ARENA_MATCH_END chegar de volta
        (`_arena_result_val = None` em arena_handlers.py), não aqui."""
        self._send_arena_forfeit()

    def _close_bg_result(self) -> None:
        """Mesma lógica de `_close_arena_result`, pro modal da BG (botão
        "Voltar")."""
        self._send_bg_leave()

    def _close_arena_queue_modal(self) -> None:
        self._arena_modal_open_val = False
