"""
save_sync_handlers.py — Mixin com persistência (save/load de personagem,
serialização de itens) e mensagens de sincronização ao servidor (talentos,
hotbar, stats de combate, loot, engage por proximidade). Separado de game.py
para manter GameEngine conciso. Esta classe NÃO deve ser instanciada
diretamente — ela é herdada por GameEngine, que fornece self.world,
self._my_eid, self._net e os demais atributos referenciados aqui.
"""
from components import PlayerSkills


class SaveSyncHandlers:
    # ── Save de estado do personagem ─────────────────────────────────────────

    def _serialize_item(self, item) -> dict | None:
        """Serializa um Item para dict salvo no servidor.

        Inclui todos os campos necessários para restauração fiel,
        incluindo consumable, max_stack e stack (itens empilháveis).
        """
        if item is None:
            return None
        d = {"name": getattr(item, "name", "")}
        for attr in ("icon_key","item_type","slot","rarity","value",
                     "attack_power","armor","spell_power","stamina",
                     "two_handed","cast_range","attack_speed"):
            v = getattr(item, attr, None)
            if v is not None:
                d[attr] = v
        # Quiver: salva contagem atual de flechas
        if getattr(item, "item_type", "") == "quiver":
            d["arrow_count"] = getattr(item, "arrow_count", 0)
            d["max_arrows"]  = getattr(item, "max_arrows",  0)
            _subtype = getattr(item, "subtype", "")
            if _subtype:
                d["subtype"] = _subtype
        # Campos de itens empilháveis (consumíveis, munição, etc.)
        max_stack = getattr(item, "max_stack", 1)
        stack     = getattr(item, "stack", 1)
        if max_stack > 1:
            d["max_stack"] = max_stack
            d["stack"]     = stack
        # Consumível: salva o dict completo para restaurar funcionalidade
        consumable = getattr(item, "consumable", None)
        if consumable:
            d["consumable"] = consumable
        mods = []
        for mod in getattr(item, "modifiers", []):
            mods.append({"attribute": mod.attribute, "value": mod.value,
                         "type": getattr(mod, "type", "flat")})
        if mods:
            d["modifiers"] = mods
        return d

    def _item_from_data(self, d: dict):
        """Reconstrói Item diretamente dos dados serializados (sem lookup em catálogo).

        Usado para itens de loja que podem não estar em loot_tables._T.
        Todos os campos necessários vêm no próprio dict.
        """
        if not d or not d.get("name"):
            return None
        from components import Item as _Item, Modifier as _Mod
        mods = [_Mod(m["attribute"], float(m["value"]), m.get("type", "flat"))
                for m in d.get("modifiers", []) if "attribute" in m]
        item = _Item(
            name        = d["name"],
            item_type   = d.get("item_type", ""),
            slot        = d.get("slot", ""),
            rarity      = d.get("rarity", "common"),
            value       = int(d.get("value", 0)),
            consumable  = d.get("consumable"),
            max_stack   = int(d.get("max_stack", 1)),
            modifiers   = mods,
            arrow_count = int(d.get("arrow_count", 0)),
            max_arrows  = int(d.get("max_arrows",  0)),
            subtype     = d.get("subtype", ""),
        )
        for f in ("attack_power", "armor", "spell_power", "stamina",
                  "two_handed", "attack_speed", "damage_min", "damage_max"):
            if f in d:
                setattr(item, f, d[f])
        # Restaura stack salvo (default 1 para itens não empilháveis)
        item.stack = int(d.get("stack", 1))
        return item

    def _restore_item(self, d: dict):
        """Reconstrói Item a partir de dict salvo.

        Tenta primeiro no catálogo _T (preserva atributos do original).
        Fallback: _item_from_data (reconstrói dos dados — funciona para itens de loja).
        """
        if not d:
            return None
        from loot_tables import _T
        name = d.get("name", "")
        # Tenta achar pelo nome no catálogo (loot drops)
        for key, factory in _T.items():
            try:
                candidate = factory()
            except Exception:
                continue
            if getattr(candidate, "name", "") == name:
                # Restaura campos variáveis que o factory não preserva
                if "arrow_count" in d:
                    candidate.arrow_count = int(d["arrow_count"])
                if "max_arrows" in d:
                    candidate.max_arrows  = int(d["max_arrows"])
                if "subtype" in d:
                    candidate.subtype     = d["subtype"]
                if "stack" in d:
                    candidate.stack     = int(d["stack"])
                if "max_stack" in d:
                    candidate.max_stack = int(d["max_stack"])
                return candidate
        # Fallback: reconstrói dos dados (itens de loja, consumíveis, etc.)
        return self._item_from_data(d)

    def _collect_save_state(self) -> dict:
        """Coleta estado completo do personagem para enviar ao servidor."""
        import json
        from components import (Inventory, Equipment, TalentTree,
                                 Wallet, CharacterStats, CombatStats, PlayerSkills as _PSCol)

        char  = self.world.get_component(self.player_entity, CharacterStats)
        cs    = self.world.get_component(self.player_entity, CombatStats)
        inv   = self.world.get_component(self.player_entity, Inventory)
        equip = self.world.get_component(self.player_entity, Equipment)
        tt    = self.world.get_component(self.player_entity, TalentTree)
        wall  = self.world.get_component(self.player_entity, Wallet)

        stats = {}
        if char:
            stats = {
                "level":            char.level,
                "current_xp":       char.current_xp,
                "xp_to_next_level": char.xp_to_next_level,
                "strength":         char.strength,
                "intelligence":     char.intelligence,
                "agility":          char.agility,
                "vitality":         char.vitality,
                "defense":          char.defense,
                "max_hp":           cs.max_hp if cs else 210,
                "current_hp":       cs.current_hp if cs else 210,
            }
        if wall:
            stats["gold"] = wall.gold

        inv_list = []
        if inv:
            for item in inv.items:
                s = self._serialize_item(item)
                if s:
                    inv_list.append(s)

        equipment = {}
        if equip:
            for slot, item in equip.slots.items():
                if item is not None:
                    s = self._serialize_item(item)
                    if s:
                        equipment[slot] = s

        talents = {}
        if tt:
            talents = {
                "chosen_build":     tt.chosen_build,
                "allocated":        dict(tt.allocated),
                "available_points": tt.available_points,
            }

        skills = {}
        ps_col = self.world.get_component(self.player_entity, _PSCol)
        if ps_col and ps_col.learned_skill_ids:
            # Apenas quais skills foram aprendidas — layout da hotbar é UI local (config.json).
            # Não envia lista vazia: o servidor interpretaria {} como "não enviado"
            # mas {"learned":[]} como truthy e sobrescreveria o DB com lista vazia.
            skills = {
                "learned": list(ps_col.learned_skill_ids),
            }

        # Fog of War — tiles explorados por mapa (servidor faz union, é aditivo)
        from components import FogOfWar as _FogCol
        fog_comp = self.world.get_component(self.player_entity, _FogCol)
        fog = {}
        if fog_comp:
            for map_key, tile_set in fog_comp._explored_maps.items():
                normalized = map_key.replace("\\", "/")
                if tile_set:
                    fog[normalized] = [[x, y] for x, y in tile_set]

        return {
            "stats":     stats,
            "inventory": inv_list,
            "equipment": equipment,
            "talents":   talents,
            "skills":    skills,
            "fog":       fog,
        }

    def _send_talent_update(self) -> None:
        """Envia apenas os talentos ao servidor quando um ponto é alocado/desalocado."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_tu
        from components import TalentTree as _TTu
        tt = self.world.get_component(self.player_entity, _TTu)
        if tt:
            self._net.send(_MT_tu.TALENT_UPDATE, {"talents": {
                "chosen_build":     tt.chosen_build,
                "allocated":        dict(tt.allocated),
                "available_points": tt.available_points,
            }})

    def _send_hotbar_update(self) -> None:
        """Envia apenas a barra de ações ao servidor quando ela é alterada."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_hbu
        from components import PlayerSkills as _PSu, ConsumableBar as _CBu
        ps   = self.world.get_component(self.player_entity, _PSu)
        cbar = self.world.get_component(self.player_entity, _CBu)
        self._net.send(_MT_hbu.HOTBAR_UPDATE, {
            "skills":      [sk.skill_id if sk else None for sk in ps.skills] if ps else [],
            "consumables": list(cbar.slots) if cbar else [],
        })

    def _on_loot_action(self, change_type: str = "item") -> None:
        """Envia ao servidor apenas a consequência da ação de loot, não o estado completo."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_la
        if change_type == "gold":
            from components import Wallet as _W_la
            wall = self.world.get_component(self.player_entity, _W_la)
            if wall:
                self._net.send(_MT_la.GOLD_UPDATE, {"gold": wall.gold})
        elif change_type == "item":
            from components import Inventory as _Inv_la
            inv = self.world.get_component(self.player_entity, _Inv_la)
            if inv:
                inv_list = [self._serialize_item(it) for it in inv.items if it]
                inv_list = [s for s in inv_list if s]
                self._net.send(_MT_la.INV_SYNC, {"inventory": inv_list})

    def _get_equip_snapshot(self) -> dict:
        """Retorna snapshot do equipamento atual como dict slot→item_name (para comparação)."""
        from components import Equipment as _EqSnap
        equip = self.world.get_component(self.player_entity, _EqSnap)
        if not equip:
            return {}
        snapshot = {}
        for slot, item in equip.slots.items():
            if item is not None:
                key = getattr(item, "name", "") or ""
                ac  = getattr(item, "arrow_count", None)
                snapshot[slot] = f"{key}:{ac}" if ac is not None else key
        return snapshot

    def _send_equip_sync(self) -> None:
        """Sincroniza o equipamento atual com o servidor após equip/unequip.

        Atualiza _equip_snapshot para que a detecção passiva em game.py
        não envie duplicata no mesmo frame.
        """
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_es
        from components import Equipment as _EqES
        equip = self.world.get_component(self.player_entity, _EqES)
        if not equip:
            return
        equipment = {}
        for slot, item in equip.slots.items():
            if item is not None:
                s = self._serialize_item(item)
                if s:
                    equipment[slot] = s
        self._net.send(_MT_es.EQUIP_SYNC, {"equipment": equipment})
        self._equip_snapshot = self._get_equip_snapshot()

    def _on_recarregar_changed(self) -> None:
        """Recarregar mudou bag (flechas consumidas) e aljava (arrow_count) —
        sincroniza o Inventory do servidor (Recarregar futuro/inventário cheio)
        e persiste o estado completo, senão a recarga se perde se o jogador
        deslogar antes do próximo evento de save."""
        self._on_loot_action("item")
        self._send_save_state()

    def _send_save_state(self) -> None:
        """Envia estado completo do personagem ao servidor para persistência."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType
        state = self._collect_save_state()
        self._net.send(MsgType.SAVE_STATE, state)

    def _restore_save_state(self, char_data: dict) -> None:
        """Restaura inventário, equipment e talentos recebidos do servidor no LOGIN_OK."""
        import json as _jr
        from components import (Inventory, Equipment, TalentTree,
                                 Wallet, CharacterStats)
        from stats_system import apply_char_stats_to_combat, sync_attack_interval
        from components import CombatStats, PermanentStats
        from components import Equipment as _EqC

        # Inventário
        inv_raw = char_data.get("inventory_json", "[]")
        try:
            inv_list = _jr.loads(inv_raw) if isinstance(inv_raw, str) else inv_raw
        except Exception:
            inv_list = []
        inv = self.world.get_component(self.player_entity, Inventory)
        if inv and isinstance(inv_list, list):
            inv.items.clear()
            for item_d in inv_list:
                item = self._restore_item(item_d)
                if item:
                    inv.items.append(item)

        # Equipment
        equip_raw = char_data.get("inventory_json", "[]")  # equipment salvo separado
        equip_data = char_data.get("equipment_json", "{}")
        try:
            equip_dict = _jr.loads(equip_data) if isinstance(equip_data, str) else {}
        except Exception:
            equip_dict = {}
        equip = self.world.get_component(self.player_entity, Equipment)
        if equip and isinstance(equip_dict, dict):
            for slot, item_d in equip_dict.items():
                if slot in equip.slots:
                    equip.slots[slot] = self._restore_item(item_d)

        # Re-aplica modificadores de todos os itens equipados
        from components import CombatStats as _CSEq
        from stat_fns import add_modifier as _add_eq_mod
        cs_eq = self.world.get_component(self.player_entity, _CSEq)
        if equip and cs_eq:
            for _slot_eq, _item_eq in equip.slots.items():
                if _item_eq is None:
                    continue
                if _slot_eq == "mainhand" and getattr(_item_eq, "attack_speed", 0.0) > 0:
                    cs_eq.base_attack_interval = _item_eq.attack_speed
                for _mod_eq in getattr(_item_eq, "modifiers", []):
                    _add_eq_mod(cs_eq, _mod_eq)

        # Talentos
        tal_raw = char_data.get("talents_json", "{}")
        try:
            tal_dict = _jr.loads(tal_raw) if isinstance(tal_raw, str) else {}
        except Exception:
            tal_dict = {}
        tt = self.world.get_component(self.player_entity, TalentTree)
        if tt and isinstance(tal_dict, dict) and tal_dict:
            tt.chosen_build     = tal_dict.get("chosen_build", tt.chosen_build)
            tt.allocated        = dict(tal_dict.get("allocated", {}))
            tt.available_points = int(tal_dict.get("available_points", 0))
        # Sempre aplica efeitos quando TalentTree existe — o reset de cs_flags garante
        # valores base (ex: concentration_regen_idle = 5.0) mesmo sem talentos alocados.
        if tt:
            try:
                self._talent_system.apply_talent_effects()
            except Exception:
                pass

        # SkillLevels — progressão Tibia-like por uso. Componente local é
        # SÓ EXIBIÇÃO (painel de skills): nunca enviado de volta ao servidor,
        # nunca usado em cálculo de dano no cliente (server-autoritativo,
        # ver stats_system.grant_skill_xp / PROBLEMAS_ARQUITETURA.md).
        from components import SkillLevels as _SKLr
        skl_raw = char_data.get("skill_levels_json", "{}")
        try:
            skl_dict = _jr.loads(skl_raw) if isinstance(skl_raw, str) else {}
        except Exception:
            skl_dict = {}
        skl = self.world.get_component(self.player_entity, _SKLr)
        if skl and isinstance(skl_dict, dict):
            for _sid_skl, _lvl_skl in (skl_dict.get("levels") or {}).items():
                if _sid_skl in skl.levels:
                    skl.levels[_sid_skl] = int(_lvl_skl)
            for _sid_skl, _xp_skl in (skl_dict.get("xp") or {}).items():
                if _sid_skl in skl.xp:
                    skl.xp[_sid_skl] = int(_xp_skl)

        # QuestLog — progresso/entrega de quest. Componente local é SÓ
        # EXIBIÇÃO (HUD/diálogo/diário): nunca enviado de volta ao servidor,
        # nunca muta sozinho no modo online (server-autoritativo, ver
        # quest_logic.py/PROBLEMAS_ARQUITETURA.md). Mudanças subsequentes
        # chegam via QUEST_UPDATE (client/network_handlers.py).
        from components import QuestLog as _QLr
        ql_raw = char_data.get("quests_json", "{}")
        try:
            ql_dict = _jr.loads(ql_raw) if isinstance(ql_raw, str) else {}
        except Exception:
            ql_dict = {}
        ql = self.world.get_component(self.player_entity, _QLr)
        if ql and isinstance(ql_dict, dict):
            ql.active = {q: list(p) for q, p in (ql_dict.get("active") or {}).items()}
            ql.completed = set(ql_dict.get("completed") or [])

        # Skills — hotbar e learned_ids
        skills_raw = char_data.get("skills_json", "{}")
        try:
            skills_data = _jr.loads(skills_raw) if isinstance(skills_raw, str) else {}
        except Exception:
            skills_data = {}
        from components import PlayerSkills as _PSR
        ps_r = self.world.get_component(self.player_entity, _PSR)
        if ps_r:
            # Layout da hotbar é UI local (config.json) — não sincronizado com servidor.
            # Servidor só guarda quais skills foram aprendidas (gameplay autoritativo).
            learned_ids = (skills_data.get("learned", [])
                           if isinstance(skills_data, dict) else [])
            if learned_ids:
                ps_r.learned_skill_ids.clear()
                for _sid_r in learned_ids:
                    ps_r.learned_skill_ids.add(_sid_r)
            elif not ps_r.learned_skill_ids:
                # Novo personagem ou save corrompido (skills_json vazio/learned=[]):
                # adiciona skills iniciais da classe (igual ao servidor em spawn_player).
                from skill_config import INITIAL_SKILLS_BY_CLASS as _ISC
                _cls_rs = char_data.get("class_id", "guerreiro")
                for _isid in _ISC.get(_cls_rs, []):
                    ps_r.learned_skill_ids.add(_isid)

        # Re-adiciona skills de talento a learned_skill_ids após o restore as ter limpado.
        # apply_talent_effects() também as adiciona, mas é chamado ANTES do clear de learned.
        _tt_rs = self.world.get_component(self.player_entity, TalentTree)
        if _tt_rs and _tt_rs._unlocked_skill_ids:
            _ps_rs = self.world.get_component(self.player_entity, PlayerSkills)
            if _ps_rs:
                for _tsid in _tt_rs._unlocked_skill_ids:
                    _ps_rs.learned_skill_ids.add(_tsid)

        # Fog of War — restaura tiles explorados por mapa a partir do servidor.
        # char_data pode ter "fog_json" (coluna do DB) ou "fog" (já parseado pelo merge).
        from components import FogOfWar as _FogR
        fog_r = self.world.get_component(self.player_entity, _FogR)
        if fog_r:
            _fog_raw = char_data.get("fog_json", char_data.get("fog", {}))
            try:
                _fog_data = _jr.loads(_fog_raw) if isinstance(_fog_raw, str) else (_fog_raw or {})
            except Exception:
                _fog_data = {}
            for _mk, _coords in _fog_data.items():
                _mk = _mk.replace("\\", "/")
                _tile_set = {(int(x), int(y)) for x, y in _coords}
                if _mk in fog_r._explored_maps:
                    fog_r._explored_maps[_mk].update(_tile_set)
                else:
                    fog_r._explored_maps[_mk] = _tile_set
            # Sincroniza ponteiro do mapa atual
            if fog_r._current_map in fog_r._explored_maps:
                fog_r.explored = fog_r._explored_maps[fog_r._current_map]

    def _player_world_pos(self) -> "tuple[float, float]":
        """Retorna posição pixel do player local (centro do tile)."""
        from components import Position as _PosWP
        _p = self.world.get_component(self.player_entity, _PosWP)
        return (_p.x, _p.y) if _p else (0.0, 0.0)

    def _space_engage_online(self) -> None:
        """ESPAÇO: seleciona o mob remoto mais próximo e inicia perseguição (como offline)."""
        from components import TileMovement, CombatState, PlayerAutoMove
        tm   = self.world.get_component(self.player_entity, TileMovement)
        cs_p = self.world.get_component(self.player_entity, CombatState)
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        if not tm or not cs_p:
            return
        pl_x, pl_y = tm.current_tile_x, tm.current_tile_y
        best_local = -1
        best_dist  = float("inf")
        for server_eid, local_eid in self._remote_mobs.items():
            _meta_am = self._meta_from_local(local_eid)
            if _meta_am is None or _meta_am.hp <= 0:
                continue
            mob_tm = self.world.get_component(local_eid, TileMovement)
            if not mob_tm:
                continue
            dist = abs(mob_tm.current_tile_x - pl_x) + abs(mob_tm.current_tile_y - pl_y)
            if dist < best_dist:
                best_dist  = dist
                best_local = local_eid
        if best_local == -1:
            return
        cs_p.target_entity_id = best_local
        cs_p.is_pursuing = True
        if auto:
            auto.ground_target = None
            auto.path.clear()

    def _send_loot_request(self, corpse_id: int) -> None:
        """Envia LOOT_REQUEST ao servidor para o corpse_id."""
        if not self._net or not self._net.connected:
            return
        from shared.messages import MsgType
        self._net.send(MsgType.LOOT_REQUEST, {"corpse_id": corpse_id})
