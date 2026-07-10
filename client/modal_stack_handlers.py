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
self._pause_submenu, self._sound_drag, self._selected_inv_idx e
self._close_hotbar_editor (já existe em hotbar_editor_handlers.py).
"""
from sound_manager import SOUNDS


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
            ("trade",         lambda: self._trade_is_open,                     self._close_trade),
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
