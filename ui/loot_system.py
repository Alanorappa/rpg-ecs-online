# loot_system.py
"""
LootSystem — clique direito em cadáver abre modal de loot; clique
esquerdo num item move pro inventário do jogador. Extraído de
ui/systems.py (18/09/2026, ver PROBLEMAS_ARQUITETURA.md §185, item #2
do backlog de 14).
"""
from __future__ import annotations
import pygame
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui.ui_scale_mixin import UIScaleMixin
from ui.ui_sizes import UI

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from engine.core_systems import apply_effect
try:
    from debug.mob_combat_debug import MCL as _MCL
except ImportError:
    _MCL = None  # não disponível no ambiente do cliente

from engine.components import Position, Renderable, PlayerControlled, TileMovement, CombatStats, CombatState, PlayerAutoMove, Corpse, Inventory, Equipment, Wallet, CharacterStats, NPC
from engine.world import World
from engine.tileset import TILE_SIZE
from engine.utils import get_camera_offset
# Hoisted pro topo (14/09/2026, revisão de performance — ver
# arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 2): estavam sendo
# reimportados dentro de RenderSystem.render(), no loop de desenho mais
# quente do cliente (uma vez por ENTIDADE visível, todo frame). Módulos
# puros (`from __future__ import annotations` só), sem ciclo de import
# com ui/systems.py — seguro no topo.
from ui.combat_log import LOG
from ui.sound_manager import SOUNDS
from ui.icon_manager import ICONS
from ui.ui_helpers import item_tooltip_lines, fill_surf
from content.item_table import RARITY_COLORS as _SHARED_RARITY_COLORS
from engine.quest_events import fire as quest_fire
from engine.stat_fns import add_modifier, remove_modifier

# Lookup reverso skill_id → (talent_id, min_points) — fonte única em
# world_systems (_TALENT_SKILL_REQS, usado também por is_skill_authorized
# no gate autoritativo do servidor). Alias mantido pro gate de UI local.



# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import System, EnemyAISystem, is_tile_walkable
# _live_target_at_world_pos mora em ui/click_rect_helpers.py, junto com
# _corpse_click_rect (18/09/2026, achado da revisão multiagente do item
# #2 do backlog de 14) — LootSystem só CONSOME pra manter consistência
# com a decisão de alvo que MouseTargetingSystem já tomou pro mesmo
# clique (ver comentário no ponto de uso abaixo); os dois Systems são
# independentes, sem referência um ao outro.
from ui.click_rect_helpers import _corpse_click_rect, _live_target_at_world_pos


class LootSystem(UIScaleMixin, System):
    """
    Detecta clique direito em cadáveres e exibe modal de loot.
    Clique esquerdo em item no modal → move para o inventário do jogador.
    Hover → tooltip com detalhes do item.
    Shift + hover → painel de comparação com item equipado no mesmo slot.
    """

    _FONT_BASES = {"font_sm": 20, "font_md": 24}

    # Layout do modal em lista vertical (tamanho fixo) — ui_sizes.py (UI.LOOT_*)
    MODAL_W   = UI.LOOT_MODAL_W    # largura fixa do modal
    ROW_H     = UI.LOOT_ROW_H      # altura de cada linha (ícone + texto)
    ICON_S    = UI.LOOT_ICON_S     # tamanho do ícone dentro da linha
    MAX_ROWS  = UI.LOOT_MAX_ROWS   # linhas visíveis (scroll se houver mais)
    PAD       = UI.LOOT_PAD
    TITLE_H   = UI.LOOT_TITLE_H
    SCROLL_W  = UI.LOOT_SCROLL_W   # largura da barra de rolagem
    MODAL_H   = TITLE_H + MAX_ROWS * (ROW_H + PAD // 2) + PAD  # altura fixa

    # Cores
    BG_COLOR     = (20, 14, 8, 220)
    BORDER_COLOR = (140, 100, 60)
    HOVER_COLOR  = (60, 45, 20)

    # Fonte única em content/item_table.py (achado 06 do benchmark
    # arquitetural, PROBLEMAS_ARQUITETURA.md §12/§13).
    RARITY_COLORS = _SHARED_RARITY_COLORS

    def __init__(self, world: World, screen: pygame.Surface, player_entity: int = -1):
        super().__init__()
        self.world         = world
        self.player_entity = player_entity
        self.world_surf = screen
        self.hud_surf   = screen
        # open_corpse_id → LootUIState component (via property abaixo)
        self.pending_loot_corpse_id: int = -1
        self.pending_tooltip             = None  # lido por GameEngine no fim do frame
        # Callback chamado após cada ação de loot (moeda ou item) — injetado pelo GameEngine.
        # Online: aponta para _send_save_state() para salvar imediatamente na ação.
        self._on_loot_collected = None
        # SÓ setado em modo online (game.py) — quando presente, clicar em
        # qualquer linha do loot (ouro OU item) NÃO credita nada localmente:
        # manda LOOT_REQUEST(local_corpse_eid) e espera LOOT_RESULT (client/
        # network_handlers.py::_handle_msg_loot_result) creditar o que o
        # servidor confirmar que ainda sobra. Sem isso, cada cliente
        # processava sua PRÓPRIA cópia local do corpse (criada em
        # LOOT_AVAILABLE) de forma independente — em grupo, dois membros
        # lootavam o MESMO ouro (bug real relatado pelo usuário
        # 17/07/2026). fn(local_corpse_eid: int) -> None.
        self._online_loot_requester = None
        self._online_loot_pending: set = set()  # local_corpse_eid com request em voo
        self._modal_x       = 0   # posição X do modal (definida ao abrir)
        self._modal_y       = 0   # posição Y do modal
        self._scroll_offset = 0   # índice da primeira linha visível
        self._pending_cursor = (0, 0)  # cursor quando o loot foi solicitado

    def set_online_loot_requester(self, fn) -> None:
        """GameEngine chama isto (só em modo online) com
        self._send_loot_request_for_local_corpse — ver docstring dela."""
        self._online_loot_requester = fn

    def _try_send_online_loot_request(self, take: str, item_id: str = "") -> bool:
        """True = modo online, request enfileirado (ou já em voo pra este
        corpse) — chamador deve consumir o clique sem creditar nada
        localmente. False = modo offline (sem requester setado), segue o
        fluxo local de sempre.

        `take`: "gold" ou "item" (com `item_id` — débito A4, 11/08/2026,
        era `item_name` antes; nome de exibição não distingue itens
        diferentes com o mesmo nome) — granular desde 17/07/2026, sacar
        só o ouro não deveria levar junto o resto do loot. Um request em
        voo bloqueia OUTROS cliques no MESMO corpse (não por linha) —
        próximo clique só depois do LOOT_RESULT responder, evita spam de
        request duplicado."""
        if self._online_loot_requester is None:
            return False
        corpse_id = self.open_corpse_id
        if corpse_id not in self._online_loot_pending:
            self._online_loot_pending.add(corpse_id)
            self._online_loot_requester(corpse_id, take, item_id)
        return True

    @property
    def open_corpse_id(self) -> int:
        from ui.ui_components import LootUIState
        ui = self.world.get_component(self.player_entity, LootUIState)
        return ui.open_corpse_id if ui else -1

    @open_corpse_id.setter
    def open_corpse_id(self, value: int) -> None:
        from ui.ui_components import LootUIState
        ui = self.world.get_component(self.player_entity, LootUIState)
        if ui:
            ui.open_corpse_id = value

    # ------------------------------------------------------------------ #
    def update(self, events: list = None, dt: float = 0) -> None:
        if events is None:
            return

        # Se o cadáver aberto foi removido, fecha o modal
        if self.open_corpse_id != -1:
            _open_corpse_chk = self.world.get_component(self.open_corpse_id, Corpse)
            # Fecha se a entidade sumiu OU se esvaziou por completo — antes
            # (25/07/2026) isso "funcionava" só pra harvestable por
            # ACIDENTE: o bug do despawn genérico removia a entidade ao
            # esvaziar (corrigido em §34.54), e a checagem de "componente
            # sumiu" fechava o modal como efeito colateral. Harvestable
            # agora persiste corretamente ao esvaziar (por design — fica
            # visível vazio), então precisa de checagem própria de "vazio"
            # aqui, senão o modal nunca fecha sozinho (bug real relatado
            # pelo usuário: "irritante").
            if (_open_corpse_chk is None or
                    (not _open_corpse_chk.loot and _open_corpse_chk.coins <= 0)):
                self.open_corpse_id = -1

        # Verifica se o jogador chegou ao cadáver pendente
        if self.pending_loot_corpse_id != -1:
            p_corpse = self.world.get_component(self.pending_loot_corpse_id, Corpse)
            if not p_corpse:
                self.pending_loot_corpse_id = -1
            else:
                _tm_pc_first = self.world.get_first(TileMovement, PlayerControlled)
                player_tm = _tm_pc_first[1] if _tm_pc_first is not None else None
                corpse_pos = None
                for eid, pos, _ in self.world.get_entities_with(Position, Corpse):
                    if eid == self.pending_loot_corpse_id:
                        corpse_pos = pos
                        break
                if player_tm and corpse_pos:
                    c_tile_x = int(corpse_pos.x / TILE_SIZE)
                    c_tile_y = int(corpse_pos.y / TILE_SIZE)
                    dist = max(abs(player_tm.current_tile_x - c_tile_x),
                               abs(player_tm.current_tile_y - c_tile_y))
                    if dist <= 1:
                        corpse_id = self.pending_loot_corpse_id
                        self.pending_loot_corpse_id = -1
                        if not p_corpse.loot and p_corpse.coins <= 0:
                            return
                        p_corpse.is_open = True
                        p_corpse.looted  = True
                        ax, ay = self._pending_cursor
                        self._open_modal(corpse_id, ax, ay)

        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN:
                mx, my = event.pos

                if event.button == 3:
                    if self.open_corpse_id != -1 and self._point_in_modal(mx, my):
                        # Clique direito dentro do modal aberto → equipa item
                        self._try_equip_item(mx, my)
                    else:
                        # Clique direito fora → tenta abrir cadáver
                        self._pending_cursor = (mx, my)
                        self._try_open_corpse(mx, my)

                elif event.button == 1:  # clique esquerdo → tenta pegar item
                    if self.open_corpse_id != -1:
                        modal = self._modal_rect()
                        if self._close_btn_rect(modal).collidepoint(mx, my):
                            self._close_modal()
                        else:
                            taken = self._try_take_item(mx, my)
                            if not taken:
                                # Clique fora do modal → fecha
                                if not self._point_in_modal(mx, my):
                                    self._close_modal()

            elif event.type == pygame.MOUSEWHEEL:
                if self.open_corpse_id != -1:
                    modal_r = self._modal_rect()
                    if modal_r.collidepoint(pygame.mouse.get_pos()):
                        corpse = self.world.get_component(self.open_corpse_id, Corpse)
                        if corpse:
                            total = (1 if corpse.coins > 0 else 0) + len(corpse.loot)
                            max_scroll = max(0, total - self.MAX_ROWS)
                            self._scroll_offset = max(0, min(self._scroll_offset - event.y, max_scroll))

    def _get_camera_offset(self):
        return get_camera_offset(self.world, self.world_surf.get_width(), self.world_surf.get_height())

    def _try_open_corpse(self, mx: int, my: int) -> None:
        cam_x, cam_y = self._get_camera_offset()
        scale = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
        world_x = mx * scale + cam_x
        world_y = my * scale + cam_y

        # Prioridade de clique (pedido do usuário, 03/08/2026): um alvo
        # vivo (NPC/Player/Mob) no MESMO tile de um cadáver sempre vence —
        # nunca anda pro loot nem cancela o combate que MouseTargetingSystem
        # já decidiu pra este MESMO clique (ver _live_target_at_world_pos).
        if _live_target_at_world_pos(self.world, self.player_entity, world_x, world_y):
            return

        # Coleta todos os cadáveres no alcance; prioriza os que ainda têm loot
        candidates = []
        for entity_id, pos, corpse in self.world.get_entities_with(Position, Corpse):
            left, top, right, bottom = _corpse_click_rect(self.world, entity_id, pos)
            if left <= world_x <= right and top <= world_y <= bottom:
                has_loot = bool(corpse.loot) or corpse.coins > 0
                candidates.append((entity_id, pos, corpse, has_loot))

        if not candidates:
            self.open_corpse_id = -1
            return

        # Cadáveres com loot primeiro; dentro do grupo, qualquer ordem serve
        candidates.sort(key=lambda c: 0 if c[3] else 1)
        entity_id, pos, corpse, _ = candidates[0]

        _tm_auto_first = self.world.get_first(TileMovement, PlayerControlled, PlayerAutoMove)
        player_tm   = _tm_auto_first[1] if _tm_auto_first is not None else None
        player_auto = _tm_auto_first[3] if _tm_auto_first is not None else None

        c_tile_x = int(pos.x / TILE_SIZE)
        c_tile_y = int(pos.y / TILE_SIZE)
        if player_tm:
            dist = max(abs(player_tm.current_tile_x - c_tile_x),
                       abs(player_tm.current_tile_y - c_tile_y))
            if dist > 1:
                self.pending_loot_corpse_id = entity_id
                # Cancela combate para o movimento ao corpo não ser interrompido
                _pc_cs_first = self.world.get_first(PlayerControlled, CombatState)
                if _pc_cs_first is not None:
                    p_cs = _pc_cs_first[2]
                    p_cs.target_entity_id = -1
                    p_cs.is_pursuing = False
                if player_auto:
                    # Anda pro tile ADJACENTE mais próximo, não pro tile do
                    # corpse em si (Fase M1, revisão 3, 25/07/2026 — bug
                    # real relatado pelo usuário: cliques de longe na caixa
                    # nunca abriam o loot). Corpse de mob morto não tem
                    # colisão, então mirar o próprio tile sempre funcionou;
                    # harvestable AGORA tem colisão real (TileMovement) —
                    # o próprio tile é SÓLIDO, então o auto-move nunca
                    # conseguia chegar lá e a fila de movimento ficava presa
                    # pra sempre, sem abrir o modal. Mesmo padrão já usado
                    # por _walk_to_merchant (linha ~2546) pra NPC sólido.
                    #
                    # Fix 2 (25/07/2026, 2º bug real — caixa de teste M2 em
                    # (111,383)): a escolha do adjacente "mais próximo" não
                    # filtrava por walkability — se o adjacente geometricamente
                    # mais perto do player fosse ele mesmo sólido (parede ao
                    # lado da caixa), o auto-move mirava um tile inalcançável
                    # e nunca chegava, travando o loot pra sempre (mesma
                    # classe de bug do fix acima, só que no tile ADJACENTE em
                    # vez do próprio). Filtra pra só walkable antes de
                    # escolher o mais próximo; se os 4 estiverem bloqueados
                    # (caso raro), cai no comportamento antigo (best-effort).
                    adj = [(c_tile_x + dx, c_tile_y + dy)
                          for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
                    try:
                        walkable_adj = [t for t in adj
                                       if is_tile_walkable(self.player_entity, t[0], t[1])]
                    except KeyError:
                        walkable_adj = []  # sem tile_validation registrado (teste isolado)
                    if walkable_adj:
                        adj = walkable_adj
                    ptx, pty = player_tm.current_tile_x, player_tm.current_tile_y
                    target = min(adj, key=lambda t: abs(t[0]-ptx) + abs(t[1]-pty))
                    player_auto.ground_target = target
                    player_auto.active = True
                    player_auto.path.clear()
                    player_auto.path_recalc_timer = 0.0
                return

        if not corpse.loot and corpse.coins <= 0:
            return
        corpse.is_open = True
        corpse.looted  = True
        self._open_modal(entity_id, mx, my)

    def _open_modal(self, entity_id: int, ax: int, ay: int) -> None:
        """Abre o modal ancorado próximo ao ponto (ax, ay), clamped à tela."""
        self.open_corpse_id = entity_id
        self._scroll_offset = 0
        self._set_panel_scale(self.MODAL_W, self.MODAL_H)
        sw, sh = self.hud_surf.get_size()
        modal_w = self._u(self.MODAL_W)
        modal_h = self._u(self.MODAL_H)
        x = max(4, min(ax + 16, sw - modal_w - 4))
        y = max(4, min(ay - self._u(self.TITLE_H), sh - modal_h - 4))
        self._modal_x, self._modal_y = x, y

    def _modal_rect(self) -> pygame.Rect:
        """Tamanho sempre fixo — não depende do conteúdo."""
        self._set_panel_scale(self.MODAL_W, self.MODAL_H)
        return pygame.Rect(self._modal_x, self._modal_y, self._u(self.MODAL_W), self._u(self.MODAL_H))

    def _close_btn_rect(self, modal: pygame.Rect) -> pygame.Rect:
        """Botão X no canto superior direito da barra de título."""
        sz = self._u(self.TITLE_H) - self._u(6)
        return pygame.Rect(modal.right - sz - self._u(4), modal.y + self._u(3), sz, sz)

    def _row_rect(self, modal: pygame.Rect, row: int) -> pygame.Rect:
        """Rect de uma linha da lista (row 0 = primeira linha)."""
        y = modal.y + self._u(self.TITLE_H) + row * (self._u(self.ROW_H) + self._u(self.PAD) // 2)
        return pygame.Rect(modal.x + self._u(self.PAD), y, self._u(self.MODAL_W) - self._u(self.PAD) * 2, self._u(self.ROW_H))

    def _point_in_modal(self, mx: int, my: int) -> bool:
        return self._modal_rect().collidepoint(mx, my)

    def _try_take_item(self, mx: int, my: int) -> bool:
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return False

        modal     = self._modal_rect()
        has_coins = corpse.coins > 0

        # Constrói a lista virtual de linhas considerando scroll
        # Linha virtual 0 = moedas (se houver), depois itens
        virtual_row = 0
        screen_row  = 0  # linha visível (0 = primeira visível)

        if has_coins:
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                if self._row_rect(modal, screen_row).collidepoint(mx, my):
                    if self._try_send_online_loot_request("gold"):
                        return True
                    wallet = self.world.get_component(self.player_entity, Wallet)
                    if wallet:
                        wallet.gold += corpse.coins
                        LOG.add(f"+{corpse.coins} moedas coletadas!", (255, 215, 0))
                        corpse.coins = 0
                        SOUNDS.play_ui("loot_gold")
                        if self._on_loot_collected:
                            self._on_loot_collected("gold")
                    self._check_auto_close(corpse)
                    return True
                screen_row += 1
            virtual_row += 1

        for i, item in enumerate(corpse.loot):
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                if self._row_rect(modal, screen_row).collidepoint(mx, my):
                    if self._try_send_online_loot_request("item", item.item_id):
                        return True
                    inv = self.world.get_component(self.player_entity, Inventory)
                    if inv:
                        # Tenta empilhar em stack existente
                        stacked = False
                        if item.max_stack > 1:
                            for existing in inv.items:
                                if existing is None:
                                    continue
                                if existing.item_id == item.item_id and existing.stack < existing.max_stack:
                                    existing.stack += item.stack
                                    stacked = True
                                    break
                        _inv_full = False
                        if stacked:
                            corpse.loot.pop(i)
                        elif len(inv.items) < inv.max_slots:
                            inv.items.append(item)
                            corpse.loot.pop(i)
                        else:
                            LOG.add("Inventario cheio!", (255, 160, 0))
                            _inv_full = True
                        if not _inv_full:
                            col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                            LOG.add(f"Coletado: {item.name} ({item.rarity})", col)
                            SOUNDS.play_ui("loot_item")
                            quest_fire("collect_item", item_id=item.item_id)
                            if self._on_loot_collected:
                                self._on_loot_collected("item")
                            # Corrige scroll se necessário
                            total = (1 if corpse.coins > 0 else 0) + len(corpse.loot)
                            self._scroll_offset = min(self._scroll_offset, max(0, total - self.MAX_ROWS))
                    self._check_auto_close(corpse)
                    return True
                screen_row += 1
            virtual_row += 1

        return False

    def _try_equip_item(self, mx: int, my: int) -> bool:
        """Clique direito num item do loot: equipa diretamente, sem passar pelo inventário."""
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return False

        modal     = self._modal_rect()
        has_coins = corpse.coins > 0
        virtual_row = 0
        screen_row  = 0

        # Pula linha de moedas (não é equipável)
        if has_coins:
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                screen_row += 1
            virtual_row += 1

        for i, item in enumerate(corpse.loot):
            if virtual_row >= self._scroll_offset and screen_row < self.MAX_ROWS:
                if self._row_rect(modal, screen_row).collidepoint(mx, my):
                    # Modo online: mesma proteção de _try_take_item — sem
                    # isso, equipar direto do loot creditava o item da
                    # cópia LOCAL do corpse sem checar com o servidor se
                    # outro membro do grupo já pegou (mesma classe de bug
                    # do ouro duplicado). Cai pro inventário (LOOT_RESULT),
                    # não equipa direto — trade-off aceitável por segurança.
                    if self._try_send_online_loot_request("item", item.item_id):
                        return True
                    equip        = self.world.get_component(self.player_entity, Equipment)
                    inv          = self.world.get_component(self.player_entity, Inventory)
                    combat_stats = self.world.get_component(self.player_entity, CombatStats)

                    if not (equip and combat_stats):
                        return False

                    target_slot = getattr(item, 'slot', None)
                    if target_slot is None or target_slot not in equip.slots:
                        # Item sem slot (consumível etc.) → vai para inventário
                        if inv and len(inv.items) < inv.max_slots:
                            inv.items.append(item)
                            corpse.loot.pop(i)
                            col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                            LOG.add(f"Coletado: {item.name} ({item.rarity})", col)
                            SOUNDS.play_ui("loot_item")
                            self._check_auto_close(corpse)
                        else:
                            LOG.add("Inventario cheio!", (255, 160, 0))
                        return True

                    # Restrição de armor_class por classe
                    if getattr(item, "armor_class", "") and item.item_type == "armor":
                        from engine.stats_system import CLASS_ARMOR_ALLOWED
                        from engine.components import CharacterStats as _CST
                        _char = self.world.get_component(self.player_entity, _CST)
                        _allowed = CLASS_ARMOR_ALLOWED.get(_char.class_id if _char else "", frozenset())
                        if item.armor_class not in _allowed:
                            _names = {"placa": "Placa", "couro": "Couro", "tecido": "Tecido"}
                            LOG.add(f"Sua classe não pode usar armadura de {_names.get(item.armor_class, item.armor_class)}.", (255, 100, 80))
                            if inv and len(inv.items) < inv.max_slots:
                                inv.items.append(item)
                                corpse.loot.pop(i)
                            return True

                    # Arma de duas mãos → desequipa offhand
                    if getattr(item, 'two_handed', False) and target_slot == "mainhand":
                        old_oh = equip.slots.get("offhand")
                        if old_oh and inv and len(inv.items) < inv.max_slots:
                            for mod in old_oh.modifiers:
                                remove_modifier(combat_stats, mod)
                            inv.items.append(old_oh)
                            equip.slots["offhand"] = None

                    # Offhand bloqueado por arma de duas mãos
                    if target_slot == "offhand" and equip.is_offhand_locked():
                        LOG.add("Desequipe a arma de duas maos primeiro.", (255, 160, 0))
                        return True

                    # Devolve item já equipado ao inventário (se houver e houver espaço)
                    old_item = equip.slots[target_slot]
                    if old_item:
                        if inv and len(inv.items) < inv.max_slots:
                            for mod in old_item.modifiers:
                                remove_modifier(combat_stats, mod)
                            inv.items.append(old_item)
                        else:
                            LOG.add("Inventario cheio para trocar o item equipado!", (255, 160, 0))
                            return True

                    # Equipa o item do loot
                    equip.slots[target_slot] = item
                    corpse.loot.pop(i)
                    if target_slot == "mainhand" and getattr(item, "attack_speed", 0.0) > 0:
                        combat_stats.base_attack_interval = item.attack_speed
                    for mod in item.modifiers:
                        add_modifier(combat_stats, mod)
                    col = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                    LOG.add(f"Equipado: {item.name} ({item.rarity})", col)
                    SOUNDS.play_ui("equip_item")
                    quest_fire("equip_item", item_id=item.item_id, item_type=item.item_type)
                    total = (1 if corpse.coins > 0 else 0) + len(corpse.loot)
                    self._scroll_offset = min(self._scroll_offset, max(0, total - self.MAX_ROWS))
                    self._check_auto_close(corpse)
                    return True
                screen_row += 1
            virtual_row += 1

        return False

    def _check_auto_close(self, corpse) -> None:
        """Fecha o modal automaticamente se o cadáver estiver vazio."""
        if corpse.coins == 0 and not corpse.loot:
            # Todo o loot foi retirado — reduz o timer para 30s
            corpse.timer = min(corpse.timer, Corpse.LOOTED_DECAY_TIME)
            self._close_modal()

    def _close_modal(self) -> None:
        if self.open_corpse_id != -1:
            corpse = self.world.get_component(self.open_corpse_id, Corpse)
            if corpse:
                corpse.is_open = False
        self.open_corpse_id = -1

    # ------------------------------------------------------------------ #
    def render_world(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        """Desenha cadáveres no mundo: vazios primeiro (embaixo — hoje
        não desenham nada de qualquer forma, 11/10/2026, mas o sort
        continua útil pra ordenar os que TÊM loot entre si), com loot
        por cima."""
        all_corpses = list(self.world.get_entities_with(Position, Corpse))
        all_corpses.sort(key=lambda c: 0 if (not c[2].loot and c[2].coins <= 0) else 1)
        for entity_id, pos, corpse in all_corpses:
            # Harvestable (Fase M1, revisão 2, 25/07/2026) TAMBÉM tem
            # Renderable — já é desenhado, Y-sorted, por RenderSystem.
            # render() (sprite do catálogo de objeto de mapa). Desenhar de
            # novo aqui duplicava a marca (bug relatado pelo usuário: a
            # elipse aparecia por cima da sprite) — corpse de mob morto
            # nunca tem Renderable, comportamento antigo intacto pra eles.
            if self.world.get_component(entity_id, Renderable) is not None:
                continue
            draw_x = pos.x - camera_offset_x
            draw_y = pos.y - camera_offset_y
            # Sprite de morte congelado (11/10/2026) no lugar da elipse
            # — mesma âncora pelo CENTRO do frame que o mob vivo usa
            # (ui/render_system.py, §269), senão o cadáver "pularia" de
            # posição no instante em que vira corpse. corpse.mob_key=""
            # (sem sprite de morte conhecido/ainda não desenhado) cai na
            # elipse de sempre — sem regressão nenhuma.
            _death_frame = None
            if corpse.mob_key:
                from engine.mob_animation import get_mob_death_last_frame
                _death_frame = get_mob_death_last_frame(corpse.mob_key, corpse.facing)
            if _death_frame is not None:
                _dfw, _dfh = _death_frame.get_size()
                self.world_surf.blit(_death_frame, (int(draw_x - _dfw / 2), int(draw_y - _dfh / 2)))
                continue
            # Sem sprite de morte conhecido (mob sem arte ainda, ou
            # fonte não-mob) — marcador genérico de "tem loot aqui"
            # (11/10/2026, pedido do usuário): animação de
            # assets/sprites/loot.png (ui/world_icon_animator.py, mesmo
            # módulo do marcador animado de quest — categoria de asset
            # "ícone de mundo", não precisa de módulo novo), MESMA
            # animação pra gold e pra item-só (sem tingimento — decisão
            # do usuário, mais simples que replicar o esquema de 2
            # cores antigo). Cadáver SEM loot nenhum (já saqueado por
            # completo, ainda dentro do timer de decay curto) não
            # desenha NADA — nem elipse nem animação.
            has_loot = corpse.coins > 0 or bool(corpse.loot)
            if not has_loot:
                continue
            from ui.world_icon_animator import get_frame as _get_loot_anim_frame
            _anim_frame = _get_loot_anim_frame("loot", 32, pygame.time.get_ticks())
            if _anim_frame is not None:
                self.world_surf.blit(_anim_frame, (int(draw_x - 16), int(draw_y - 16)))
                continue
            # Fallback se loot.png sumir/falhar ao carregar — mesma
            # elipse de sempre, só 2 cores (vazio nunca chega aqui, já
            # saiu acima).
            color = (180, 150, 30) if corpse.coins > 0 else (120, 80, 40)
            pygame.draw.ellipse(self.world_surf, color,
                                (int(draw_x - 10), int(draw_y - 6), 20, 12))
            pygame.draw.ellipse(self.world_surf, (80, 55, 25),
                                (int(draw_x - 10), int(draw_y - 6), 20, 12), 1)

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0) -> None:
        """Renderiza apenas o modal de loot (chamado por cima de tudo)."""
        self.pending_tooltip = None

        if self.open_corpse_id == -1:
            return
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return
        _char_loot  = self.world.get_component(self.player_entity, CharacterStats)
        _viewer_cls = _char_loot.class_id if _char_loot else None

        modal = self._modal_rect()
        self.hud_surf.blit(fill_surf((modal.w, modal.h), self.BG_COLOR), modal.topleft)
        pygame.draw.rect(self.hud_surf, self.BORDER_COLOR, modal, 2, border_radius=4)

        # --- Barra de título ---
        title = self.font_sm.render("Loot", False, self.BORDER_COLOR)
        self.hud_surf.blit(title, (modal.x + self._u(self.PAD), modal.y + (self._u(self.TITLE_H) - title.get_height()) // 2))

        # Botão X
        close_r = self._close_btn_rect(modal)
        pygame.draw.rect(self.hud_surf, (90, 30, 30), close_r, border_radius=2)
        x_surf = self.font_sm.render("X", False, (220, 100, 100))
        self.hud_surf.blit(x_surf, (close_r.centerx - x_surf.get_width() // 2,
                                  close_r.centery - x_surf.get_height() // 2))

        mx, my = pygame.mouse.get_pos()
        has_coins = corpse.coins > 0

        # Lista virtual: [("coin",) se has_coins] + [("item", item) ...]
        virtual: list = []
        if has_coins:
            virtual.append(("coin",))
        for item in corpse.loot:
            virtual.append(("item", item))

        total = len(virtual)

        # --- Barra de rolagem (se necessário) ---
        need_scroll = total > self.MAX_ROWS
        scroll_w = self._u(self.SCROLL_W)
        row_w = self._u(self.MODAL_W) - self._u(self.PAD) * 2 - (scroll_w + self._u(4) if need_scroll else 0)

        if need_scroll:
            # Trilho
            track_x = modal.right - scroll_w - self._u(4)
            track_y = modal.y + self._u(self.TITLE_H) + self._u(self.PAD) // 2
            track_h = self.MAX_ROWS * (self._u(self.ROW_H) + self._u(self.PAD) // 2) - self._u(self.PAD) // 2
            pygame.draw.rect(self.hud_surf, (40, 30, 18),
                             (track_x, track_y, scroll_w, track_h), border_radius=3)
            # Thumb
            thumb_h = max(20, track_h * self.MAX_ROWS // total)
            max_scroll = total - self.MAX_ROWS
            thumb_y = track_y + (track_h - thumb_h) * self._scroll_offset // max(1, max_scroll)
            pygame.draw.rect(self.hud_surf, (130, 100, 55),
                             (track_x, thumb_y, scroll_w, thumb_h), border_radius=3)

        # --- Renderiza linhas visíveis ---
        visible = virtual[self._scroll_offset: self._scroll_offset + self.MAX_ROWS]
        hovered_item = None

        for screen_row, entry in enumerate(visible):
            # Rect da linha (largura ajustada se há scrollbar)
            base_rr = self._row_rect(modal, screen_row)
            rr = pygame.Rect(base_rr.x, base_rr.y, row_w, base_rr.h)
            hovered = rr.collidepoint(mx, my)

            if entry[0] == "coin":
                # --- Linha de moedas ---
                icon_s = self._u(self.ICON_S)
                pygame.draw.rect(self.hud_surf, (70, 55, 15) if hovered else (28, 20, 8), rr, border_radius=3)
                pygame.draw.rect(self.hud_surf, (200, 170, 50) if hovered else (100, 80, 20), rr, 1, border_radius=3)
                icon_r = pygame.Rect(rr.x + self._u(4), rr.centery - icon_s // 2, icon_s, icon_s)
                r_out = icon_s // 2
                r_in  = max(1, r_out - self._u(4))
                pygame.draw.circle(self.hud_surf, (180, 140, 0),  icon_r.center, r_out)
                pygame.draw.circle(self.hud_surf, (255, 215, 0),  icon_r.center, r_in)
                pygame.draw.circle(self.hud_surf, (120, 90, 0),   icon_r.center, r_out, 1)
                g_surf = self.font_sm.render("G", False, (120, 90, 0))
                self.hud_surf.blit(g_surf, (icon_r.centerx - g_surf.get_width() // 2,
                                          icon_r.centery - g_surf.get_height() // 2))
                tx = icon_r.right + self._u(8)
                ty = rr.centery - self.font_md.get_height() // 2
                self.hud_surf.blit(self.font_md.render(f"{corpse.coins} moedas", False, (255, 215, 0)), (tx, ty))
                if hovered:
                    self.pending_tooltip = (mx, my, "Moedas",
                                            [(f"{corpse.coins} moedas disponíveis", (255, 215, 0)),
                                             ("Clique p/ coletar tudo", (140, 140, 140))])

            else:
                # --- Linha de item ---
                item = entry[1]
                icon_s = self._u(self.ICON_S)
                bg_col = self.HOVER_COLOR if hovered else (28, 20, 8)
                pygame.draw.rect(self.hud_surf, bg_col, rr, border_radius=3)

                icon_r = pygame.Rect(rr.x + self._u(4), rr.centery - icon_s // 2, icon_s, icon_s)
                icon_surf = ICONS.get(ICONS.item_key(item), icon_s)
                if icon_surf:
                    self.hud_surf.blit(icon_surf, icon_r)
                else:
                    fb = self.RARITY_COLORS.get(item.rarity, (100, 100, 100))
                    pygame.draw.rect(self.hud_surf, fb, icon_r, border_radius=2)
                from ui.ui_helpers import draw_stack_count as _dsc2
                _dsc2(self.hud_surf, item, icon_r, self.font_sm)

                rc = self.RARITY_COLORS.get(item.rarity, (200, 200, 200))
                tx = icon_r.right + self._u(8)
                _stack = getattr(item, "stack", 1)
                _max_s = getattr(item, "max_stack", 1)
                _name_lbl = f"{item.name} x{_stack}" if _max_s > 1 else item.name
                name_surf = self.font_md.render(_name_lbl, False, rc)
                sub_surf  = self.font_sm.render(f"{item.item_type}  •  {item.slot}", False, (130, 115, 95))
                total_h   = name_surf.get_height() + 2 + sub_surf.get_height()
                ty = rr.centery - total_h // 2
                self.hud_surf.blit(name_surf, (tx, ty))
                self.hud_surf.blit(sub_surf,  (tx, ty + name_surf.get_height() + 2))

                border_col = (180, 140, 60) if hovered else (55, 40, 22)
                pygame.draw.rect(self.hud_surf, border_col, rr, 1, border_radius=3)

                if hovered:
                    hovered_item = item

        # Vazio
        if not virtual:
            empty = self.font_sm.render("(vazio)", False, (120, 100, 80))
            rr = self._row_rect(modal, 0)
            self.hud_surf.blit(empty, (rr.x + self._u(4), rr.centery - empty.get_height() // 2))

        # Tooltip + comparação no hover
        if hovered_item is not None:
            lines = item_tooltip_lines(hovered_item, _viewer_cls)
            lines.append(("Clique p/ pegar | Shift p/ comparar", (140, 140, 140)))
            equip = self.world.get_component(self.player_entity, Equipment)
            equipped_item = equip.slots.get(hovered_item.slot) if equip else None
            name_col = self.RARITY_COLORS.get(hovered_item.rarity, (255, 220, 100))
            self.pending_tooltip = (mx, my, hovered_item.name, lines, name_col,
                                    hovered_item, equipped_item)

