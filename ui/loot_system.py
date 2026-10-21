# loot_system.py
"""
LootSystem — clique direito em cadáver abre modal de loot; clique
esquerdo num item move pro inventário do jogador. Extraído de
ui/systems.py (18/09/2026, ver PROBLEMAS_ARQUITETURA.md §185, item #2
do backlog de 14).
"""
from __future__ import annotations
import math
import pygame
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui import atlas_window as aw
from ui import ui_atlas
from ui.ui_scale_mixin import UIScaleMixin

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
from ui.ui_helpers import item_tooltip_lines
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


# Ícone de moeda do modal de loot, por (tamanho, fonte) — ver _coin_icon.
_COIN_ICON_CACHE: dict = {}
_COIN_ICON_CACHE_MAX = 8


class LootSystem(UIScaleMixin, System):
    """
    Detecta clique direito em cadáveres e exibe modal de loot.
    Clique esquerdo em item no modal → move para o inventário do jogador.
    Hover → tooltip com detalhes do item.
    Shift + hover → painel de comparação com item equipado no mesmo slot.
    """

    _FONT_BASES = {"font_xs": 15, "font_sm": 20, "font_md": 24}

    # Modal no atlas de UI (21/10/2026): janela escura com barra de título
    # e X, lista rolável (slot + nome) — ui/atlas_window.py.
    MAX_ROWS  = 5        # linhas visíveis (rola se houver mais)
    ROW_H     = aw.SLOT + 2   # px RAW por linha (slot de ícone 32x32 + folga)
    LIST_X    = 3        # slots encostados à esquerda (2px depois da borda)

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
        self._anchor        = (0, 0)  # ponto de tela perto do qual o modal abre
        self._pending_cursor = (0, 0)  # cursor quando o loot foi solicitado
        # Atlas: botão afundado, rolagem, eventos do frame (guardados em
        # update, consumidos em render), rect de tela do modal desenhado e
        # rects clicáveis (testes).
        self._press = aw.PressState()
        self._scroll = aw.ScrollState()
        self._frame_events: list = []
        self._modal_screen_rect = pygame.Rect(0, 0, 0, 0)
        self._hit_rects: dict = {}

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

        # Ações dentro do modal (pegar = esquerdo, equipar = direito, X,
        # rolagem) agem no SOLTAR, em render(); aqui só o clique direito no
        # MUNDO (abrir outro cadáver) e o clique fora do modal (fecha).
        self._frame_events = list(events)
        for event in events:
            if event.type != pygame.MOUSEBUTTONDOWN:
                continue
            in_modal = self.open_corpse_id != -1 and self._point_in_modal(*event.pos)
            if event.button == 3 and not in_modal:
                self._pending_cursor = event.pos
                self._try_open_corpse(*event.pos)
            elif event.button == 1 and self.open_corpse_id != -1 and not in_modal:
                self._close_modal()

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
        """Abre o modal perto do ponto de tela (ax, ay) — posição final
        (presa à tela) é calculada no render, a partir do tamanho real."""
        self.open_corpse_id = entity_id
        self._scroll = aw.ScrollState()
        self._anchor = (ax, ay)

    def _point_in_modal(self, mx: int, my: int) -> bool:
        return self._modal_screen_rect.collidepoint(mx, my)

    def _virtual_rows(self, corpse) -> list:
        """[("coin",)] se houver ouro + [("item", índice_no_loot)]."""
        rows = [("coin",)] if corpse.coins > 0 else []
        rows += [("item", i) for i in range(len(corpse.loot))]
        return rows

    def _try_take_item(self, vi: int) -> bool:
        """Clique esquerdo na linha `vi` da lista (0 = ouro, se houver):
        pega pro inventário. Online: manda LOOT_REQUEST e NÃO credita nada
        localmente (crédito real só no LOOT_RESULT)."""
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return False
        rows = self._virtual_rows(corpse)
        if not 0 <= vi < len(rows):
            return False
        if rows[vi][0] == "coin":
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

        i = rows[vi][1]
        item = corpse.loot[i]
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
        self._check_auto_close(corpse)
        return True

    def _try_equip_item(self, vi: int) -> bool:
        """Clique direito na linha `vi`: equipa direto, sem passar pelo
        inventário (ouro não é equipável)."""
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return False
        rows = self._virtual_rows(corpse)
        if not 0 <= vi < len(rows) or rows[vi][0] != "item":
            return False
        i = rows[vi][1]
        item = corpse.loot[i]
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
        self._check_auto_close(corpse)
        return True

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
        """Modal de loot (atlas de UI) — chamado por cima de tudo."""
        events, self._frame_events = self._frame_events, []
        self.pending_tooltip = None
        self._hit_rects = {}
        if self.open_corpse_id == -1:
            self._modal_screen_rect = pygame.Rect(0, 0, 0, 0)
            return
        corpse = self.world.get_component(self.open_corpse_id, Corpse)
        if not corpse:
            return
        T, S = aw.T, aw.S
        char = self.world.get_component(self.player_entity, CharacterStats)
        viewer_cls = char.class_id if char else None
        rows = self._virtual_rows(corpse)

        def _label(row):
            if row[0] == "coin":
                return f"{corpse.coins} moedas"
            it = corpse.loot[row[1]]
            return f"{it.name} x{it.stack}" if getattr(it, "max_stack", 1) > 1 else it.name

        labels = [_label(r) for r in rows] or ["(vazio)"]
        text_w = max(self.font_sm.size(l)[0] for l in labels) / S
        text_x = self.LIST_X + aw.SLOT + 6
        need_scroll = len(rows) > self.MAX_ROWS
        # Barra de rolagem só quando os itens não cabem — na última coluna,
        # encostada na borda direita; sem ela a lista vai até a borda.
        tw = max(6, math.ceil((text_x + text_w + 8) / T) + (1 if need_scroll else 0))
        view_rows = max(1, min(len(rows), self.MAX_ROWS))
        view_y0 = T + 1
        th = math.ceil((view_y0 + view_rows * self.ROW_H + 3) / T)
        sw, sh = self.hud_surf.get_size()
        pw, ph = tw * T * S, th * T * S
        ax, ay = self._anchor
        x = max(4, min(ax + 16, sw - pw - 4))
        y = max(4, min(ay - T * S, sh - ph - 4))
        f = aw.Frame(self.hud_surf, events, tw, th, area=pygame.Rect(x, y, pw, ph), press=self._press,
                     font=self.font_md, raw=ui_atlas.titled_dark_window_raw(tw, th), overlay=False)
        self._modal_screen_rect = pygame.Rect(f.px, f.py, f.pw, f.ph)
        mx, my = pygame.mouse.get_pos()
        f.text("Loot", (tw - 1) * T / 2, aw.TITLE_BAR_Y, (240, 228, 200), self.font_sm)
        close = f.close_button("loot:close", tw - 1)
        self._hit_rects["loot:close"] = pygame.Rect(f.px + (tw - 1) * T * S, f.py, T * S, T * S)

        view_h = view_rows * self.ROW_H
        content_h = max(1, len(rows) * self.ROW_H)
        off = f.scrollbar(self._scroll, tw - 1, 1, th - 1, max(0, content_h - view_h), step=self.ROW_H)
        lw = (tw - 1) * T if need_scroll else tw * T - 2
        in_view = view_y0 <= f.ry < view_y0 + view_h and 0 <= f.rx < lw
        fc = f.child(pygame.Surface((lw, content_h), pygame.SRCALPHA),
                     f.ry - view_y0 + off if in_view else -10_000)
        ip = aw.slot_icon_px()
        mid = aw.SLOT / 2
        take = equip = None
        for vi, row in enumerate(rows):
            y = vi * self.ROW_H + 1
            if row[0] == "coin":
                icon = self._coin_icon(ip)
                color = (255, 215, 0)
            else:
                it = corpse.loot[row[1]]
                icon = aw.item_icon(it, self.font_sm, ip)
                color = self.RARITY_COLORS.get(it.rarity, (200, 200, 200))
            fc.slot(f"loot:{vi}", self.LIST_X, y, icon, dark=True)
            fc.text(labels[vi], text_x, y + mid, color, self.font_sm, anchor="midleft")
            ry = view_y0 + y - 1 - off
            if view_y0 <= ry + 1 and ry + 1 + aw.SLOT <= view_y0 + view_h:
                self._hit_rects[f"loot:{vi}"] = pygame.Rect(f.px, f.py + ry * S, lw * S, self.ROW_H * S)
            if fc.hover(0, y - 1, lw, y - 1 + self.ROW_H):
                if fc.press(f"loot:row:{vi}", 0, y - 1, lw, y - 1 + self.ROW_H)[1]:
                    take = vi
                if fc.rreleased:
                    equip = vi
                if row[0] == "coin":
                    self.pending_tooltip = (mx, my, "Moedas",
                                            [(f"{corpse.coins} moedas disponíveis", (255, 215, 0)),
                                             ("Clique p/ coletar tudo", (140, 140, 140))])
                else:
                    lines = item_tooltip_lines(it, viewer_cls)
                    lines.append(("Clique p/ pegar | Dir. p/ equipar | Shift p/ comparar", (140, 140, 140)))
                    eq = self.world.get_component(self.player_entity, Equipment)
                    self.pending_tooltip = (mx, my, it.name, lines, color, it,
                                            eq.slots.get(it.slot) if eq else None)
        if not rows:
            fc.text("(vazio)", self.LIST_X + 4, mid, (120, 100, 80), self.font_sm, anchor="midleft")

        f.end()
        content = ui_atlas.scale_up(fc.raw)
        aw.draw_texts(content, fc.texts, self.font_sm)
        aw.draw_overlays(content, fc.overlays)
        view = content.subsurface((0, off * S, content.get_width(),
                                   max(0, min(view_h, content_h - off)) * S))
        self.hud_surf.blit(view, (f.px, f.py + view_y0 * S))

        if close:
            self._close_modal()
        elif take is not None:
            self._try_take_item(take)
        elif equip is not None:
            self._try_equip_item(equip)

    def _coin_icon(self, size: int) -> pygame.Surface:
        """Moeda desenhada (sem ícone no atlas): círculo dourado com "G".
        Cache de MÓDULO com teto (`_COIN_ICON_CACHE`), nunca dict no corpo
        da classe."""
        key = (size, id(self.font_md))
        surf = _COIN_ICON_CACHE.get(key)
        if surf is None:
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            c, r = (size // 2, size // 2), size // 2 - 2
            pygame.draw.circle(surf, (180, 140, 0), c, r)
            pygame.draw.circle(surf, (255, 215, 0), c, max(1, r - 3))
            g = self.font_md.render("G", False, (120, 90, 0))
            surf.blit(g, g.get_rect(center=c))
            if len(_COIN_ICON_CACHE) >= _COIN_ICON_CACHE_MAX:
                _COIN_ICON_CACHE.clear()   # troca de escala de UI recria fontes
            _COIN_ICON_CACHE[key] = surf
        return surf
