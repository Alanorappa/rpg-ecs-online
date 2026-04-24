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
import random
import pygame
from fonts import make as _font

from quest_events import QUEST_EVENTS
from quests_data import QUESTS, QUEST_ITEMS, ObjectiveDef
from combat_log import LOG
from floating_text import PROC


# ═══════════════════════════════════════════════════════════════════════════════
# QuestSystem — progressão e recompensas
# ═══════════════════════════════════════════════════════════════════════════════

class QuestSystem:
    # ── HUD layout ──────────────────────────────────────────────────────────
    MAX_HUD_QUESTS   = 3
    HUD_MARGIN_RIGHT = 10
    HUD_MARGIN_TOP   = 196   # abaixo do minimap: MARGIN_TOP(68)+SIZE(120)+gap(6)

    # Cores
    COL_TITLE  = (220, 200, 120)
    COL_PROG   = (170, 170, 170)
    COL_DONE   = ( 80, 200, 100)
    COL_REWARD = (255, 215,   0)

    def __init__(self, world, player_entity: int) -> None:
        self.world         = world
        self.player_entity = player_entity
        self._font_title:  "pygame.font.Font | None" = None
        self._font_obj:    "pygame.font.Font | None" = None
        self._hud_cache_key:  "tuple | None"          = None
        self._hud_cache_surf: "pygame.Surface | None" = None
        self._current_map: str = ""
        self._last_reach_tile: tuple = (-1, -1, "")  # (tx, ty, map) — evita disparo por frame

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

        # Dispara reach_tile com posição atual do player a cada frame
        self._fire_reach_tile(ql)

        while QUEST_EVENTS:
            event_type, data = QUEST_EVENTS.popleft()
            for qid in list(ql.active.keys()):
                qdef = QUESTS.get(qid)
                if qdef is None:
                    continue
                prog = ql.active[qid]
                for i, obj in enumerate(qdef.objectives):
                    if prog[i] >= obj.count:
                        continue
                    if self._matches(event_type, data, obj):
                        # reach_level: obj.count é o nível-alvo, não uma contagem cumulativa
                        if obj.type == "reach_level":
                            prog[i] = obj.count
                        else:
                            prog[i] += 1
                        self._hud_cache_key = None   # invalida cache HUD

        # collect_item: sincroniza progresso com inventário real (cobre itens já na bag)
        self._sync_collect_progress(ql)

    def _process_talk_to_npc(self, npc_name: str) -> None:
        """Processa imediatamente um evento talk_to_npc sem passar pela fila.
        Chamado por QuestDialogSystem._open_dialog antes de calcular o estado do diálogo."""
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return
        data = {"npc_name": npc_name}
        for qid in list(ql.active.keys()):
            qdef = QUESTS.get(qid)
            if qdef is None:
                continue
            prog = ql.active[qid]
            for i, obj in enumerate(qdef.objectives):
                if prog[i] >= obj.count:
                    continue
                if self._matches("talk_to_npc", data, obj):
                    prog[i] += 1
                    self._hud_cache_key = None

    # ── API para QuestDialogSystem ───────────────────────────────────────────

    def can_turn_in(self, qid: str) -> bool:
        """True se a quest está ativa e todos os objetivos concluídos."""
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None or qid not in ql.active:
            return False
        qdef = QUESTS.get(qid)
        if qdef is None:
            return False
        return self._all_done(ql.active[qid], qdef)

    def turn_in(self, qid: str) -> None:
        """Completa e recompensa a quest. Chamado pelo QuestDialogSystem."""
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql:
            self._complete_quest(ql, qid)

    # ── Drop condicional ─────────────────────────────────────────────────────

    def get_conditional_loot(self, enemy_name: str, enemy_race: str) -> list:
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return []
        extras = []
        for qid, prog in ql.active.items():
            qdef = QUESTS.get(qid)
            if qdef is None:
                continue
            for i, obj in enumerate(qdef.objectives):
                if obj.type != "collect_item":
                    continue
                if obj.target not in ("*", enemy_name, enemy_race):
                    continue
                if prog[i] >= obj.count:
                    continue
                if not obj.loot_item:
                    continue
                if random.random() <= obj.loot_chance:
                    factory = QUEST_ITEMS.get(obj.loot_item)
                    if factory:
                        extras.append(factory())
        return extras

    # ── HUD ─────────────────────────────────────────────────────────────────

    def render_hud(self, screen: pygame.Surface) -> None:
        from components import QuestLog
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None or not ql.active:
            self._hud_cache_key  = None
            self._hud_cache_surf = None
            return

        if self._font_title is None:
            self._font_title = _font(18)
            self._font_obj   = _font(16)

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
                    (sw - self.HUD_MARGIN_RIGHT - self._hud_cache_surf.get_width(),
                     self.HUD_MARGIN_TOP))

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
            y += 17
            for i, obj in enumerate(qdef.objectives):
                done  = prog[i] >= obj.count
                color = self.COL_DONE if done else self.COL_PROG
                mark  = "v " if done else "- "
                surf  = self._font_obj.render(mark + self._obj_label(obj, prog[i]),
                                              True, color)
                lines.append((surf, y))
                y += 14
            y += 5
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

    def _try_start(self, ql, qid: str) -> bool:
        if qid in ql.active or qid in ql.completed:
            return False
        qdef = QUESTS.get(qid)
        if qdef is None:
            return False
        if not all(r in ql.completed for r in qdef.requires):
            return False
        if qdef.level_req > 0 and self._player_level() < qdef.level_req:
            LOG.add(f'Nivel {qdef.level_req} necessario para "{qdef.title}".', (200, 80, 80))
            return False
        # Inicializa progresso; reach_level verifica nível atual no momento do início
        plvl = self._player_level()
        prog = []
        for obj in qdef.objectives:
            if obj.type == "reach_level" and plvl >= obj.count:
                prog.append(obj.count)   # já satisfeito
            else:
                prog.append(0)
        ql.active[qid] = prog
        self._hud_cache_key = None
        LOG.add(f'Quest: "{qdef.title}" iniciada.', self.COL_TITLE)
        return True

    def _all_done(self, prog: list, qdef) -> bool:
        return all(prog[i] >= obj.count for i, obj in enumerate(qdef.objectives))

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
        QUEST_EVENTS.append(("reach_tile", {
            "tx":  tm.current_tile_x,
            "ty":  tm.current_tile_y,
            "map": self._current_map,
        }))

    def _sync_collect_progress(self, ql) -> None:
        """Sincroniza objetivos collect_item com o inventário real do player."""
        from components import Inventory
        inv = self.world.get_component(self.player_entity, Inventory)
        if inv is None:
            return
        for qid, prog in ql.active.items():
            qdef = QUESTS.get(qid)
            if qdef is None:
                continue
            for i, obj in enumerate(qdef.objectives):
                if obj.type != "collect_item" or not obj.loot_item:
                    continue
                owned = sum(item.stack for item in inv.items if item.name == obj.loot_item)
                new_prog = min(owned, obj.count)
                if prog[i] != new_prog:
                    prog[i] = new_prog
                    self._hud_cache_key = None

    def _complete_quest(self, ql, qid: str) -> None:
        from components import Wallet, CharacterStats, CombatStats, \
                               PermanentStats, PlayerControlled
        from stats_system import apply_char_stats_to_combat
        from save_system import request_autosave

        qdef = QUESTS.get(qid)
        if qdef is None:
            return

        del ql.active[qid]
        if not qdef.repeatable:
            ql.completed.add(qid)
        self._hud_cache_key = None

        reward = qdef.reward

        # Remove itens de quest do inventário para objetivos collect_item
        from components import Inventory, PlayerControlled as _PC
        for _, inv, _ in self.world.get_entities_with(Inventory, _PC):
            for obj in qdef.objectives:
                if obj.type != "collect_item" or not obj.loot_item:
                    continue
                needed = obj.count
                i = 0
                while i < len(inv.items) and needed > 0:
                    item = inv.items[i]
                    if item.name == obj.loot_item:
                        if item.stack <= needed:
                            needed -= item.stack
                            inv.items.pop(i)
                        else:
                            item.stack -= needed
                            needed = 0
                            i += 1
                    else:
                        i += 1
            break

        if reward.xp > 0:
            from stats_system import process_levelups
            for eid, char, cs, _ in self.world.get_entities_with(
                    CharacterStats, CombatStats, PlayerControlled):
                perm = self.world.get_component(eid, PermanentStats)
                char.current_xp += reward.xp
                process_levelups(self.world, eid, char, cs, perm)
                break

        if reward.gold > 0:
            for _, wlt, _ in self.world.get_entities_with(Wallet, PlayerControlled):
                wlt.gold += reward.gold
                break

        parts = []
        if reward.xp:   parts.append(f"+{reward.xp} XP")
        if reward.gold: parts.append(f"+{reward.gold} ouro")
        reward_str = f" ({', '.join(parts)})" if parts else ""
        LOG.add(f'Quest completa: "{qdef.title}"{reward_str}!', self.COL_REWARD)
        PROC.add("Quest Completa!", self.COL_REWARD)

        # Desbloqueia quests auto_start com pré-requisitos agora satisfeitos
        for qid2, qdef2 in QUESTS.items():
            if qid2 not in ql.active and qid2 not in ql.completed and qdef2.auto_start:
                self._try_start(ql, qid2)

        request_autosave()

    # ── Match de evento ──────────────────────────────────────────────────────

    @staticmethod
    def _matches(event_type: str, data: dict, obj: ObjectiveDef) -> bool:
        if event_type != obj.type:
            return False
        t = obj.target
        if obj.type == "kill":
            return t in ("*", data.get("name", ""), data.get("race", ""))
        if obj.type == "collect_item":
            # compara pelo nome do item (loot_item), não pelo mob alvo
            return data.get("item_name", "") == obj.loot_item
        if obj.type == "reach_tile":
            loc = obj.location
            if not loc:
                return False
            # Se target especifica um mapa, verifica se o player está nele
            if t and t != "*" and data.get("map", "") != t:
                return False
            tx, ty = data.get("tx", -1), data.get("ty", -1)
            if len(loc) == 2:
                return tx == loc[0] and ty == loc[1]
            if len(loc) == 4:
                return loc[0] <= tx <= loc[2] and loc[1] <= ty <= loc[3]
            return False
        if obj.type == "use_skill":
            return t in ("*", data.get("skill_id", ""))
        if obj.type == "use_consumable":
            return t in ("*", data.get("item_name", ""))
        if obj.type == "reach_level":
            return data.get("level", 0) >= obj.count
        if obj.type == "talk_to_npc":
            return t in ("*", data.get("npc_name", ""))
        if obj.type == "equip_item":
            return t in ("*", data.get("item_name", ""), data.get("item_type", ""))
        return False

    @staticmethod
    def _obj_label(obj: ObjectiveDef, progress: int) -> str:
        if obj.type == "kill":
            alvo = obj.target if obj.target != "*" else "inimigo"
            return f"Matar {alvo}: {progress}/{obj.count}"
        if obj.type == "collect_item":
            nome = obj.loot_item or obj.target
            return f"Coletar {nome}: {progress}/{obj.count}"
        if obj.type == "reach_tile":
            return f"Chegar ao destino: {'sim' if progress > 0 else 'nao'}"
        if obj.type == "use_skill":
            return f"Usar {obj.target}: {progress}/{obj.count}"
        if obj.type == "use_consumable":
            alvo = obj.target if obj.target != "*" else "consumivel"
            return f"Usar {alvo}: {progress}/{obj.count}"
        if obj.type == "reach_level":
            return f"Alcançar nivel {obj.count}: {'sim' if progress >= obj.count else 'nao'}"
        if obj.type == "talk_to_npc":
            alvo = obj.target if obj.target != "*" else "NPC"
            return f"Falar com {alvo}: {'sim' if progress > 0 else 'nao'}"
        if obj.type == "equip_item":
            return f"Equipar {obj.target}: {'sim' if progress > 0 else 'nao'}"
        return f"{obj.type}: {progress}/{obj.count}"


# ═══════════════════════════════════════════════════════════════════════════════
# QuestDialogSystem — modal WoW-style
# ═══════════════════════════════════════════════════════════════════════════════

class QuestDialogSystem:
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

    PANEL_W = 480
    PANEL_H = 400
    PAD     = 16

    COL_GOLD   = (255, 215,   0)
    COL_GREY   = (150, 150, 150)
    COL_WHITE  = (220, 220, 220)
    COL_TITLE  = (220, 200, 120)
    COL_GREEN  = ( 80, 200, 100)
    COL_BG     = ( 15,  10,   5, 235)
    COL_BORDER = (140, 100,  60)

    def __init__(self, world, player_entity: int,
                 screen: pygame.Surface, quest_system: QuestSystem) -> None:
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

        # Fontes (lazy)
        self._font_lg:    "pygame.font.Font | None" = None
        self._font_body:  "pygame.font.Font | None" = None
        self._font_sm:    "pygame.font.Font | None" = None

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
                wx, wy = mx + cam_x, my + cam_y
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
                    from components import QuestLog
                    ql = self.world.get_component(self.player_entity, QuestLog)
                    if ql:
                        self._qs._try_start(ql, self._dialog_selected_qid)
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
        self._lazy_fonts()

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
        self._lazy_fonts()

        giver = self.world.get_component(self._dialog_npc_id, _QG)
        if giver is None:
            self._close()
            return
        _npc_comp = self.world.get_component(self._dialog_npc_id, _NPC)
        npc_name  = _npc_comp.name if _npc_comp else "NPC"

        SW, SH = self.hud_surf.get_size()
        ov = pygame.Surface((SW, SH), pygame.SRCALPHA)
        ov.fill((0, 0, 0, 160))
        self.hud_surf.blit(ov, (0, 0))

        x0, y0 = self._panel_origin()
        W, H   = self.PANEL_W, self.PANEL_H
        bg = pygame.Surface((W, H), pygame.SRCALPHA)
        bg.fill(self.COL_BG)
        self.hud_surf.blit(bg, (x0, y0))
        pygame.draw.rect(self.hud_surf, self.COL_BORDER, (x0, y0, W, H), 2, border_radius=4)

        # Header: nome do NPC
        npc_surf = self._font_lg.render(npc_name, True, self.COL_TITLE)
        self.hud_surf.blit(npc_surf, (x0 + self.PAD, y0 + self.PAD))
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (x0 + 4, y0 + 42), (x0 + W - 4, y0 + 42))

        # Botão fechar (X)
        close_r = pygame.Rect(x0 + W - 36, y0 + 4, 32, 32)
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
        y = y0 + 52

        hint = self._font_sm.render("Escolha uma missao:", True, self.COL_GREY)
        self.hud_surf.blit(hint, (x0 + self.PAD, y))
        y += 22

        for qid, is_turnin in [(q, True) for q in comp] + [(q, False) for q in avail]:
            qdef = QUESTS.get(qid)
            if qdef is None:
                continue
            symbol = "?" if is_turnin else "!"
            color  = self.COL_GOLD
            row_r  = pygame.Rect(x0 + self.PAD, y, self.PANEL_W - self.PAD * 2, 30)
            if row_r.collidepoint(mx, my):
                pygame.draw.rect(self.hud_surf, (45, 38, 18), row_r, border_radius=3)
                pygame.draw.rect(self.hud_surf, self.COL_BORDER, row_r, 1, border_radius=3)
            label = self._font_body.render(f"[{symbol}]  {qdef.title}", True, color)
            self.hud_surf.blit(label, (x0 + self.PAD + 6, y + 6))
            self._list_rects[qid] = row_r
            y += 34

    def _render_detail(self, x0: int, y0: int) -> None:
        self._accept_rect  = None
        self._decline_rect = None
        qdef = QUESTS.get(self._dialog_selected_qid)
        if qdef is None:
            return

        W, PAD = self.PANEL_W, self.PAD
        mx, my = pygame.mouse.get_pos()
        y = y0 + 52

        # Título da quest
        ts = self._font_lg.render(qdef.title, True, self.COL_TITLE)
        self.hud_surf.blit(ts, (x0 + PAD, y))
        y += 28

        # Descrição
        for line in self._wrap(qdef.description, W - PAD * 2, self._font_body):
            self.hud_surf.blit(self._font_body.render(line, True, self.COL_WHITE),
                             (x0 + PAD, y))
            y += 20
        y += 8

        # Objetivos
        self.hud_surf.blit(self._font_sm.render("Objetivos:", True, self.COL_GREY),
                         (x0 + PAD, y))
        y += 18
        for obj in qdef.objectives:
            s = self._font_sm.render(f"  - {QuestSystem._obj_label(obj, 0)}",
                                     True, self.COL_GREY)
            self.hud_surf.blit(s, (x0 + PAD, y))
            y += 16
        y += 8

        # Recompensas
        parts = []
        if qdef.reward.xp:   parts.append(f"+{qdef.reward.xp} XP")
        if qdef.reward.gold: parts.append(f"+{qdef.reward.gold} ouro")
        if parts:
            rew = self._font_sm.render("Recompensa: " + ", ".join(parts),
                                       True, self.COL_GOLD)
            self.hud_surf.blit(rew, (x0 + PAD, y))

        # Botões na base do painel — alinhados à direita: [Recusar] [Aceitar]
        btn_y  = y0 + self.PANEL_H - 48
        acc_r  = pygame.Rect(x0 + self.PANEL_W - PAD - 150,       btn_y, 150, 32)
        dec_r  = pygame.Rect(x0 + self.PANEL_W - PAD - 150 - 158, btn_y, 150, 32)
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

        PAD      = self.PAD
        max_w    = self.PANEL_W - PAD * 2
        mx, my   = pygame.mouse.get_pos()
        btn_y    = y0 + self.PANEL_H - 48
        y        = y0 + 52

        # Cabeçalho
        hdr = self._font_lg.render("Missao Completa!", True, self.COL_GOLD)
        self.hud_surf.blit(hdr, (x0 + PAD, y))
        y += 28

        # Título da quest
        ts = self._font_body.render(qdef.title, True, self.COL_TITLE)
        self.hud_surf.blit(ts, (x0 + PAD, y))
        y += 22

        # Linha separadora
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (x0 + PAD, y), (x0 + self.PANEL_W - PAD, y))
        y += 10

        # Texto de conclusão do NPC (ou fallback genérico)
        completion_text = getattr(qdef, "completion", "") or "Bom trabalho. Aqui esta sua recompensa."
        for line in self._wrap(completion_text, max_w, self._font_body):
            s = self._font_body.render(line, True, self.COL_WHITE)
            self.hud_surf.blit(s, (x0 + PAD, y))
            y += s.get_height() + 2
        y += 12

        # Recompensas
        parts = []
        if qdef.reward.xp:   parts.append(f"+{qdef.reward.xp} XP")
        if qdef.reward.gold: parts.append(f"+{qdef.reward.gold} ouro")
        if parts:
            rew_hdr = self._font_sm.render("Recompensa:", True, (160, 140, 80))
            self.hud_surf.blit(rew_hdr, (x0 + PAD, y))
            y += rew_hdr.get_height() + 4
            rew = self._font_body.render("  " + "  |  ".join(parts), True, self.COL_GOLD)
            self.hud_surf.blit(rew, (x0 + PAD, y))

        # Botão Concluir
        comp_r = pygame.Rect(x0 + self.PANEL_W - PAD - 180, btn_y, 180, 32)
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
        # Re-calcula completáveis APÓS processar talk_to_npc
        comp  = self._get_completable_quests(npc_id)
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
        """Quests que o NPC pode oferecer (não iniciadas, pré-req ok, nível ok)."""
        from components import QuestLog, QuestGiver as _QG
        giver = self.world.get_component(npc_id, _QG)
        if giver is None:
            return []
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return []
        plvl = self._qs._player_level()
        result = []
        for qid in giver.quest_ids:
            if qid in ql.active or qid in ql.completed:
                continue
            qdef = QUESTS.get(qid)
            if qdef and all(r in ql.completed for r in qdef.requires):
                if qdef.level_req <= plvl:
                    result.append(qid)
        return result

    def _get_locked_quests(self, npc_id: int) -> list:
        """Quests com level_req acima do nível atual (pré-req ok mas bloqueadas)."""
        from components import QuestLog, QuestGiver as _QG
        giver = self.world.get_component(npc_id, _QG)
        if giver is None:
            return []
        ql = self.world.get_component(self.player_entity, QuestLog)
        if ql is None:
            return []
        plvl = self._qs._player_level()
        result = []
        for qid in giver.quest_ids:
            if qid in ql.active or qid in ql.completed:
                continue
            qdef = QUESTS.get(qid)
            if qdef and all(r in ql.completed for r in qdef.requires):
                if qdef.level_req > plvl:
                    result.append(qid)
        return result

    def _get_completable_quests(self, npc_id: int) -> list:
        """Quests que o NPC aceita para entrega e estão 100% concluídas."""
        from components import QuestGiver as _QG
        giver = self.world.get_component(npc_id, _QG)
        if giver is None:
            return []
        ids = giver.turn_in_ids if giver.turn_in_ids else giver.quest_ids
        return [qid for qid in ids if self._qs.can_turn_in(qid)]

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
        from components import Camera, Position as _Pos
        SW, SH = self.hud_surf.get_size()
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
        SW, SH = self.hud_surf.get_size()
        return (SW - self.PANEL_W) // 2, (SH - self.PANEL_H) // 2

    def _lazy_fonts(self) -> None:
        if self._font_lg is None:
            self._font_lg   = _font(26)
            self._font_body = _font(21)
            self._font_sm   = _font(18)

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

class QuestJournalSystem:
    """
    Diário de quests acessível pela tecla J.

    Layout:
        Painel 700×520 centralizado.
        Coluna esquerda (220px): lista de quests ativas (amarelo) e completas (verde).
        Coluna direita (440px): detalhes da quest selecionada.

    Nenhuma ação pode ser executada aqui — é somente leitura.
    Aceitar / entregar quests é feito pelo diálogo com o NPC.
    """

    PANEL_W  = 700
    PANEL_H  = 520
    LIST_W   = 210
    PAD      = 14

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

    def __init__(self, world, player_entity: int,
                 screen: pygame.Surface, quest_system: QuestSystem) -> None:
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

        self._font_title: "pygame.font.Font | None" = None
        self._font_body:  "pygame.font.Font | None" = None
        self._font_sm:    "pygame.font.Font | None" = None

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
                self._scroll_offset = max(0, self._scroll_offset - event.y * 20)
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
        self._lazy_fonts()
        self._rebuild_list()

        SW, SH = self.hud_surf.get_size()
        x0 = (SW - self.PANEL_W) // 2
        y0 = (SH - self.PANEL_H) // 2

        # Overlay
        overlay = pygame.Surface((SW, SH), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.hud_surf.blit(overlay, (0, 0))

        # Painel fundo
        panel = pygame.Surface((self.PANEL_W, self.PANEL_H), pygame.SRCALPHA)
        panel.fill(self.COL_BG)
        self.hud_surf.blit(panel, (x0, y0))
        pygame.draw.rect(self.hud_surf, self.COL_BORDER,
                         (x0, y0, self.PANEL_W, self.PANEL_H), 2, border_radius=6)

        # Titulo
        title_s = self._font_title.render("Diario de Quests", True, self.COL_HEADER)
        self.hud_surf.blit(title_s, (x0 + self.PAD, y0 + self.PAD))

        # Botão X
        close_r = pygame.Rect(x0 + self.PANEL_W - 28, y0 + 8, 22, 22)
        pygame.draw.rect(self.hud_surf, (80, 40, 30), close_r, border_radius=3)
        pygame.draw.rect(self.hud_surf, self.COL_BORDER, close_r, 1, border_radius=3)
        xs = self._font_body.render("X", True, (220, 180, 140))
        self.hud_surf.blit(xs, (close_r.centerx - xs.get_width() // 2,
                               close_r.centery - xs.get_height() // 2))
        self._close_rect = close_r

        # Divisória vertical
        lx = x0 + self.LIST_W
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (lx, y0 + 40), (lx, y0 + self.PANEL_H - self.PAD))

        # Renderiza lista e detalhe
        self._render_list(x0, y0)
        self._render_detail(x0, y0)

    def _render_list(self, x0: int, y0: int) -> None:
        self._entry_rects = []
        mx, my = pygame.mouse.get_pos()

        list_area = pygame.Rect(x0 + 2, y0 + 42, self.LIST_W - 4, self.PANEL_H - 50)
        clip_surf = pygame.Surface((list_area.width, list_area.height), pygame.SRCALPHA)
        clip_surf.fill((0, 0, 0, 0))

        row_h = 24
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
                self.hud_surf.blit(sep_s, (row_abs.x + 4, row_abs.centery - sep_s.get_height() // 2))
                continue

            is_sel = (qid == self._selected_qid)
            is_hov = row_abs.collidepoint(mx, my)
            bg = self.COL_SEL_BG if is_sel else (self.COL_HOVER_BG if is_hov else None)
            if bg:
                pygame.draw.rect(self.hud_surf, bg, row_abs)
            if is_sel:
                pygame.draw.rect(self.hud_surf, self.COL_BORDER, row_abs, 1)

            txt_s = self._font_sm.render(label, True, color)
            self.hud_surf.blit(txt_s, (row_abs.x + 8, row_abs.centery - txt_s.get_height() // 2))
            self._entry_rects.append((row_abs, qid))

    def _render_detail(self, x0: int, y0: int) -> None:
        from components import QuestLog
        if not self._selected_qid:
            hint = self._font_body.render("Selecione uma quest na lista.", True, self.COL_GREY)
            dx = x0 + self.LIST_W + self.PAD
            dy = y0 + self.PANEL_H // 2
            self.hud_surf.blit(hint, (dx, dy))
            return

        qdef = QUESTS.get(self._selected_qid)
        if qdef is None:
            return

        ql = self.world.get_component(self.player_entity, QuestLog)
        is_completed = ql and self._selected_qid in ql.completed
        prog = ql.active.get(self._selected_qid) if ql else None

        dx = x0 + self.LIST_W + self.PAD
        dy = y0 + 42
        max_w = self.PANEL_W - self.LIST_W - self.PAD * 2

        # Título
        col_t = self.COL_GREY if is_completed else self.COL_ACTIVE
        title_text = f"{qdef.title} (Done)" if is_completed else qdef.title
        title_s = self._font_title.render(title_text, True, col_t)
        self.hud_surf.blit(title_s, (dx, dy))
        dy += title_s.get_height() + 4

        # Nível requerido
        if qdef.level_req > 0:
            plvl = self._qs._player_level()
            lvl_col = self.COL_GREY if plvl >= qdef.level_req else (200, 80, 80)
            lvl_s = self._font_sm.render(f"Nivel minimo: {qdef.level_req}", True, lvl_col)
            self.hud_surf.blit(lvl_s, (dx, dy))
            dy += lvl_s.get_height() + 6
        else:
            dy += 2

        # Linha separadora
        pygame.draw.line(self.hud_surf, self.COL_BORDER,
                         (dx, dy), (dx + max_w, dy))
        dy += 8

        # Descrição
        for line in self._wrap(qdef.description, max_w, self._font_body):
            s = self._font_body.render(line, True, self.COL_WHITE)
            self.hud_surf.blit(s, (dx, dy))
            dy += s.get_height() + 2
        dy += 8

        # Objetivos
        obj_hdr = self._font_sm.render("Objetivos:", True, self.COL_HEADER)
        self.hud_surf.blit(obj_hdr, (dx, dy))
        dy += obj_hdr.get_height() + 4

        for i, obj in enumerate(qdef.objectives):
            if is_completed:
                done, cur = True, obj.count
            elif prog:
                cur  = prog[i]
                done = cur >= obj.count
            else:
                cur, done = 0, False

            col = self.COL_DONE_OBJ if done else self.COL_PROG
            mark = "v" if done else "-"
            label = QuestSystem._obj_label(obj, cur)
            obj_s = self._font_body.render(f"  {mark} {label}", True, col)
            self.hud_surf.blit(obj_s, (dx, dy))
            dy += obj_s.get_height() + 2
        dy += 8

        # Recompensas
        rew_hdr = self._font_sm.render("Recompensas:", True, self.COL_HEADER)
        self.hud_surf.blit(rew_hdr, (dx, dy))
        dy += rew_hdr.get_height() + 4

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
            btn_y  = y0 + self.PANEL_H - 46
            abn_r  = pygame.Rect(dx, btn_y, 140, 28)
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

    def _lazy_fonts(self) -> None:
        if self._font_title is None:
            self._font_title = _font(24)
            self._font_body  = _font(21)
            self._font_sm    = _font(18)

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
