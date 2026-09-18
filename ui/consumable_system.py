# consumable_system.py
"""
ConsumableSystem — barra de consumíveis (keybinds + uso) e ActiveRegen
(HoT). Extraído de ui/systems.py (18/09/2026, ver
PROBLEMAS_ARQUITETURA.md §185, item #2 do backlog de 14).
"""
from __future__ import annotations
import pygame
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui.ui_sizes import UI

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from engine.core_systems import apply_effect
try:
    from debug.mob_combat_debug import MCL as _MCL
except ImportError:
    _MCL = None  # não disponível no ambiente do cliente

from engine.components import CombatStats, CombatState, Inventory, CharacterStats, ActiveRegen, ConsumableBar
from engine.world import World
# Hoisted pro topo (14/09/2026, revisão de performance — ver
# arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 2): estavam sendo
# reimportados dentro de RenderSystem.render(), no loop de desenho mais
# quente do cliente (uma vez por ENTIDADE visível, todo frame). Módulos
# puros (`from __future__ import annotations` só), sem ciclo de import
# com ui/systems.py — seguro no topo.
from ui.floating_text import WARN
from engine.quest_events import fire as quest_fire

# Lookup reverso skill_id → (talent_id, min_points) — fonte única em
# world_systems (_TALENT_SKILL_REQS, usado também por is_skill_authorized
# no gate autoritativo do servidor). Alias mantido pro gate de UI local.



# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import System, EnemyAISystem


class ConsumableSystem(System):
    """Processa a barra de consumíveis (keybinds + uso) e ActiveRegen (HoT).

    Online: injete `system._net = self._net` após criação para que o
    uso de consumíveis seja comunicado ao servidor via CONSUMABLE_USE.
    O servidor é autoritativo — cura/mana/HoT e a remoção do item da bag
    só se aplicam no cliente APÓS a confirmação (STATS_UPDATE com
    `item_id`+`consumable_ok`, ver client/network_handlers.py). Sem
    isso, um consumível bloqueado no servidor (ex: HP já cheio lá, mesmo
    que o cliente ache que não está) era perdido em silêncio: cliente já
    tinha curado localmente e consumido o item antes de saber que o
    servidor não fez nada (bug real reportado por testers — ver
    PROBLEMAS_ARQUITETURA.md). Offline: aplica tudo localmente, sem espera.
    """

    def __init__(self, world: World):
        self.world = world
        self._net  = None   # injetado pelo GameEngine no modo online
        # Item aguardando confirmação do servidor (online) — resolvido por
        # network_handlers.py._handle_msg_stats_update ao chegar a resposta.
        self.pending_item_id: str = ""

    def update(self, events=None, dt: float = 0) -> None:
        # ── Barra de consumíveis: cooldown + keybinds ─────────────────────
        for eid, cbar in self.world.get_entities_with(ConsumableBar):
            if cbar.global_cooldown > 0:
                cbar.global_cooldown = max(0.0, cbar.global_cooldown - dt)

            if events:
                for ev in events:
                    if ev.type != pygame.KEYDOWN:
                        continue
                    for slot_i, kb in enumerate(cbar.keybinds):
                        if ev.key == kb:
                            item_id = cbar.slots[slot_i]
                            if item_id and cbar.global_cooldown <= 0:
                                self._use_consumable(eid, item_id, cbar)
                            break

        # ── ActiveRegen: ticks de regeneração de HP ───────────────────────
        to_remove = []
        for eid, regen in self.world.get_entities_with(ActiveRegen):
            cs     = self.world.get_component(eid, CombatStats)
            if not cs:
                to_remove.append(eid)
                continue

            regen.tick_timer -= dt
            if regen.tick_timer <= 0:
                regen.tick_timer += regen.interval
                regen.ticks_remaining -= 1
                # Servidor aplica e envia STATS_UPDATE autoritativo — não
                # alterar HP local aqui, ou desync e "correção" visual ao
                # tomar dano.
                if regen.ticks_remaining <= 0:
                    to_remove.append(eid)

        for eid in to_remove:
            self.world.remove_component(eid, ActiveRegen)

        # ── ActiveManaRegen: ticks de regeneração de mana (odres) ─────────
        from engine.components import ActiveManaRegen as _AMR, CharacterStats as _CHSr
        _mana_remove = []
        for eid, mregen in self.world.get_entities_with(_AMR):
            char_r = self.world.get_component(eid, _CHSr)
            if not char_r or char_r.max_mana <= 0:
                _mana_remove.append(eid)
                continue

            mregen.tick_timer -= dt
            if mregen.tick_timer <= 0:
                mregen.tick_timer += mregen.interval
                mregen.ticks_remaining -= 1
                # Servidor aplica e envia STATS_UPDATE — não alterar mana local aqui.
                if mregen.ticks_remaining <= 0:
                    _mana_remove.append(eid)

        for eid in _mana_remove:
            self.world.remove_component(eid, _AMR)

    def _use_consumable(self, entity_id: int, item_id: str, cbar) -> None:
        inv = self.world.get_component(entity_id, Inventory)
        cs  = self.world.get_component(entity_id, CombatStats)
        if not inv or not cs:
            return

        item = next((it for it in inv.items
                     if it is not None and it.item_id == item_id and it.consumable), None)
        if not item:
            return

        cons   = item.consumable
        cstate = self.world.get_component(entity_id, CombatState)

        # Consumíveis ooc_only (comida, ensopados) não podem ser usados em combate
        if cons.get("ooc_only", False) and cstate and cstate.in_combat:
            WARN.add("Não pode usar em combate")
            return

        # Determina que recursos o consumível restaura
        _has_hp   = bool(cons.get("heal_instant", 0) or cons.get("heal_per_tick", 0))
        _has_mana = bool(cons.get("mana_restore", 0) or cons.get("mana_per_tick", 0))

        # Bloqueia se o recurso relevante já está cheio (checagem LOCAL —
        # feedback rápido; o servidor, online, faz a checagem real e pode
        # rejeitar mesmo que passe aqui, ver abaixo)
        from engine.components import CharacterStats as _CHSu
        _char_u = self.world.get_component(entity_id, _CHSu)
        if _has_hp and not _has_mana and cs.current_hp >= cs.max_hp:
            WARN.add("HP já está cheio")
            return
        if _has_mana and not _has_hp and _char_u and _char_u.max_mana > 0:
            if _char_u.mana >= _char_u.max_mana:
                WARN.add("Mana já está cheia")
                return

        heal_per_tick = cons.get("heal_per_tick", 0)
        ticks         = cons.get("ticks", 0)
        interval      = cons.get("interval", 2.0)
        mana_per_tick = cons.get("mana_per_tick", 0)
        heal_instant  = cons.get("heal_instant", 0)
        mana_restore  = cons.get("mana_restore", 0)

        if not self._net:
            return
        # SÓ manda o pedido — nada é mutado aqui (nem HP/mana, nem HoT, nem
        # o item). O servidor é quem decide se aceita, e só ao confirmar
        # (STATS_UPDATE com item_id+consumable_ok, ver network_handlers.py)
        # o item é removido e os efeitos aplicados. Sem isso, um consumível
        # bloqueado no SERVIDOR (drift natural entre os dois lados — ex: HP5
        # regen que o cliente ainda não viu) era perdido em silêncio: cliente
        # já tinha curado local e consumido o item antes de saber que nada
        # aconteceu de verdade.
        if cbar is not None:
            cbar.global_cooldown = ConsumableBar.GCD_DURATION
        self.pending_item_id = item_id
        from shared.messages import MsgType as _MTC
        _hot = {"heal_per_tick": heal_per_tick, "interval": interval, "ticks": ticks} \
               if heal_per_tick > 0 and ticks > 0 else None
        _mana_hot = {"mana_per_tick": mana_per_tick, "interval": interval, "ticks": ticks} \
                    if mana_per_tick > 0 and ticks > 0 else None
        self._net.send(_MTC.CONSUMABLE_USE, {
            "item_id":      item_id,
            "heal_instant": heal_instant,
            "mana_restore": mana_restore,
            "hot":          _hot,
            "mana_hot":     _mana_hot,
            "ooc_only":     cons.get("ooc_only", False),
            "buffs":        [],
        })

    def _finalize_consumable(self, entity_id: int, item_id: str) -> None:
        """Chamado por network_handlers.py ao chegar consumable_ok do servidor
        — SÓ AGORA remove 1 unidade do item da bag local (online). Aplica os
        HoTs locais (ActiveRegen/ActiveManaRegen) — a cura/mana instantânea já
        chega via heal_amount/mana_amount no mesmo STATS_UPDATE, tratada em
        network_handlers.py."""
        inv = self.world.get_component(entity_id, Inventory)
        if not inv:
            return
        item = next((it for it in inv.items
                     if it is not None and it.item_id == item_id and it.consumable), None)
        if not item:
            return
        cons = item.consumable
        heal_per_tick = cons.get("heal_per_tick", 0)
        ticks         = cons.get("ticks", 0)
        interval      = cons.get("interval", 2.0)
        mana_per_tick = cons.get("mana_per_tick", 0)
        if heal_per_tick > 0 and ticks > 0:
            self.world.add_component(entity_id, ActiveRegen(
                heal_per_tick=heal_per_tick, interval=interval, ticks_total=ticks,
            ))
        from engine.components import ActiveManaRegen as _AMRu2
        if mana_per_tick > 0 and ticks > 0:
            try:
                self.world.remove_component(entity_id, _AMRu2)
            except Exception:
                pass
            self.world.add_component(entity_id, _AMRu2(
                mana_per_tick=mana_per_tick, interval=interval, ticks_total=ticks,
            ))
        item.stack -= 1
        if item.stack <= 0:
            inv.items.remove(item)
        quest_fire("use_consumable", item_id=item_id)


