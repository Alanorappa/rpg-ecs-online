"""
save_sync_handlers.py — Mixin com persistência (save/load de personagem,
serialização de itens) e mensagens de sincronização ao servidor (talentos,
hotbar, stats de combate, loot, engage por proximidade). Separado de game.py
para manter GameEngine conciso. Esta classe NÃO deve ser instanciada
diretamente — ela é herdada por GameEngine, que fornece self.world,
self._my_eid, self._net e os demais atributos referenciados aqui.
"""
from engine.components import PlayerSkills

# Campos de Item que só passam direto (sem tratamento especial) entre
# serialize/deserialize — fonte única (achado 06 do benchmark arquitetural,
# PROBLEMAS_ARQUITETURA.md §12/§13: as duas listas viviam hardcoded
# separadamente neste mesmo arquivo e já tinham divergido de verdade —
# "damage_min"/"damage_max" existem em Item, estavam no loop de
# deserialize mas FALTAVAM no de serialize, perdendo silenciosamente o
# dano de arma de qualquer item reconstruído pelo caminho de fallback
# `_item_from_data`, ex: arma comprada em loja/forjada). "icon_key"
# removido — nunca foi atributo real de `Item`, só ficava sempre `None`.
_ITEM_STAT_FIELDS: tuple = (
    "attack_power", "armor", "spell_power", "stamina",
    "two_handed", "cast_range", "attack_speed",
    "damage_min", "damage_max",
    "item_level", "level_requirement", "description",
)


class SaveSyncHandlers:
    # ── Save de estado do personagem ─────────────────────────────────────────

    def _serialize_item(self, item) -> dict | None:
        """Serializa um Item para dict salvo no servidor.

        Inclui todos os campos necessários para restauração fiel,
        incluindo consumable, max_stack e stack (itens empilháveis).
        """
        if item is None:
            return None
        # item_id (débito C2, 10/08/2026) — identidade estável, sempre
        # primeiro campo por convenção (mesmo lugar de destaque de "name"
        # antes). "" pra item sem catálogo (fallback inerte do servidor).
        d = {"item_id": getattr(item, "item_id", ""), "name": getattr(item, "name", "")}
        # subtype: categoria da arma (ex: "Bow") — precisa estar aqui, não só
        # no bloco de quiver abaixo. Faltando isso, uma arma cujo nome não
        # bate com nenhum factory de loot_tables._T (comprada em loja com
        # nome próprio, ou forjada) reconstrói com subtype="" no relogin —
        # todo check `subtype == "Bow"` passa a falhar, disparando "Precisa
        # de um arco equipado" mesmo com o arco genuinamente equipado (bug
        # real reportado por testers). Ver PROBLEMAS_ARQUITETURA.md.
        for attr in (("item_type", "slot", "rarity", "value", "subtype")
                     + _ITEM_STAT_FIELDS):
            v = getattr(item, attr, None)
            if v is not None:
                d[attr] = v
        # Quiver: salva contagem atual de flechas (subtype já coberto acima)
        if getattr(item, "item_type", "") == "quiver":
            d["arrow_count"] = getattr(item, "arrow_count", 0)
            d["max_arrows"]  = getattr(item, "max_arrows",  0)
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
        if not d or not (d.get("item_id") or d.get("name")):
            return None
        from engine.components import Item as _Item, Modifier as _Mod
        mods = [_Mod(m["attribute"], float(m["value"]), m.get("type", "flat"))
                for m in d.get("modifiers", []) if "attribute" in m]
        item = _Item(
            name        = d.get("name", ""),
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
            item_id     = d.get("item_id", ""),
        )
        for f in _ITEM_STAT_FIELDS:
            if f in d:
                setattr(item, f, d[f])
        # Restaura stack salvo (default 1 para itens não empilháveis)
        item.stack = int(d.get("stack", 1))
        return item

    def _restore_item(self, d: dict):
        """Reconstrói Item a partir de dict salvo.

        Tenta primeiro no catálogo por `item_id` (débito C2, 10/08/2026 —
        O(1), preserva atributos ATUAIS do catálogo mesmo se balanceamento
        mudou desde o save) — nome só como fallback pra save ANTIGO sem
        item_id. Fallback final: _item_from_data (reconstrói dos dados —
        funciona para itens de loja/sem catálogo local do cliente).
        """
        if not d:
            return None

        def _apply_saved_bookkeeping(candidate):
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

        item_id = d.get("item_id", "")
        if item_id:
            from content.item_table import ITEMS as _IT_restore
            factory = _IT_restore.get(item_id)
            if factory is not None:
                try:
                    return _apply_saved_bookkeeping(factory())
                except Exception:
                    pass
        else:
            from content.loot_tables import _T
            name = d.get("name", "")
            # Tenta achar pelo nome no catálogo (loot drops) — save antigo
            # sem item_id salvo ainda.
            for key, factory in _T.items():
                try:
                    candidate = factory()
                except Exception:
                    continue
                if getattr(candidate, "name", "") == name:
                    return _apply_saved_bookkeeping(candidate)
        # Fallback: reconstrói dos dados (itens de loja, consumíveis, etc.)
        return self._item_from_data(d)

    def _collect_save_state(self) -> dict:
        """Coleta estado completo do personagem para enviar ao servidor."""
        import json
        from engine.components import (Inventory, Equipment, TalentTree,
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
                "spirit":           char.spirit,
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

        # Fog of War — tiles explorados por mapa (servidor faz union, é aditivo).
        # Bitmap comprimido (shared/fog_codec.py) — formato de coordenada crua
        # antigo passava de 1 MB numa conta bem explorada (ver
        # PROBLEMAS_ARQUITETURA.md, bug real 19/07/2026).
        from engine.components import FogOfWar as _FogCol
        from shared.fog_codec import encode_fog as _encode_fog_col
        fog_comp = self.world.get_component(self.player_entity, _FogCol)
        fog = {}
        if fog_comp:
            _normalized_maps = {
                map_key.replace("\\", "/"): tile_set
                for map_key, tile_set in fog_comp._explored_maps.items()
            }
            fog = _encode_fog_col(_normalized_maps)

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
        from engine.components import TalentTree as _TTu
        tt = self.world.get_component(self.player_entity, _TTu)
        if tt:
            self._net.send(_MT_tu.TALENT_UPDATE, {"talents": {
                "chosen_build":     tt.chosen_build,
                "allocated":        dict(tt.allocated),
                "available_points": tt.available_points,
            }})

    def _send_char_stats_request(self) -> None:
        """Pede o snapshot atual de CharStatsTracker ao abrir o modal de
        estatísticas (Fase E) — sob demanda, não um push contínuo."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_csr
        self._net.send(_MT_csr.CHAR_STATS_REQUEST, {})

    def _send_hotbar_update(self) -> None:
        """Envia apenas a barra de ações ao servidor quando ela é alterada."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_hbu
        from engine.components import PlayerSkills as _PSu, ConsumableBar as _CBu
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
            from engine.components import Wallet as _W_la
            wall = self.world.get_component(self.player_entity, _W_la)
            if wall:
                self._net.send(_MT_la.GOLD_UPDATE, {"gold": wall.gold})
        elif change_type == "item":
            from engine.components import Inventory as _Inv_la
            inv = self.world.get_component(self.player_entity, _Inv_la)
            if inv:
                inv_list = [self._serialize_item(it) for it in inv.items if it]
                inv_list = [s for s in inv_list if s]
                self._net.send(_MT_la.INV_SYNC, {"inventory": inv_list})

    def _send_equip_item(self, inv_index: int) -> None:
        """Manda ao servidor a INTENÇÃO de equipar o item na posição
        `inv_index` do Inventory local — débito A4 (10-11/08/2026, ver
        PROBLEMAS_ARQUITETURA.md). Substitui o antigo _send_equip_sync (que
        mandava o Equipment inteiro recalculado); o servidor lê o item de
        verdade no SEU PRÓPRIO Inventory ao vivo por posição, nunca confia
        em item mandado por aqui."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_es
        self._net.send(_MT_es.EQUIP_ITEM, {"inv_index": inv_index})

    def _send_unequip_item(self, slot: str) -> None:
        """Manda ao servidor a INTENÇÃO de desequipar `slot` — contraparte
        de _send_equip_item."""
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from shared.messages import MsgType as _MT_ues
        self._net.send(_MT_ues.UNEQUIP_ITEM, {"slot": slot})

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
        from engine.components import (Inventory, Equipment, TalentTree,
                                 Wallet, CharacterStats)
        from engine.stats_system import apply_char_stats_to_combat, sync_attack_interval
        from engine.components import CombatStats, PermanentStats
        from engine.components import Equipment as _EqC

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
        from engine.components import CombatStats as _CSEq
        from engine.stat_fns import add_modifier as _add_eq_mod
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
        from engine.components import SkillLevels as _SKLr
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
        from engine.components import QuestLog as _QLr
        ql_raw = char_data.get("quests_json", "{}")
        try:
            ql_dict = _jr.loads(ql_raw) if isinstance(ql_raw, str) else {}
        except Exception:
            ql_dict = {}
        ql = self.world.get_component(self.player_entity, _QLr)
        if ql and isinstance(ql_dict, dict):
            ql.active = {q: list(p) for q, p in (ql_dict.get("active") or {}).items()}
            ql.completed = set(ql_dict.get("completed") or [])

        # LearnedRecipes — débito A4 (11/08/2026, ver PROBLEMAS_ARQUITETURA.md,
        # bug real relatado pelo usuário): faltava esta parte — o servidor já
        # persistia certo, mas o cliente nunca CARREGAVA de volta no login,
        # então uma receita aprendida numa sessão anterior sumia da lista de
        # forja no próximo relog (só sobrevivia se aprendida na sessão atual,
        # via STATS_UPDATE de CONSUMABLE_USE).
        from engine.components import LearnedRecipes as _LRr
        lr_raw = char_data.get("learned_recipes_json", "[]")
        try:
            lr_list = _jr.loads(lr_raw) if isinstance(lr_raw, str) else lr_raw
        except Exception:
            lr_list = []
        lr = self.world.get_component(self.player_entity, _LRr)
        if lr is not None and isinstance(lr_list, list):
            lr.known = list(lr_list)

        # Skills — hotbar e learned_ids
        skills_raw = char_data.get("skills_json", "{}")
        try:
            skills_data = _jr.loads(skills_raw) if isinstance(skills_raw, str) else {}
        except Exception:
            skills_data = {}
        from engine.components import PlayerSkills as _PSR
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
                from content.skill_config import INITIAL_SKILLS_BY_CLASS as _ISC
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
        # decode_fog aceita formato novo (bitmap) OU antigo ([[x,y],...]) —
        # migração transparente entre os dois (ver shared/fog_codec.py).
        from engine.components import FogOfWar as _FogR
        from shared.fog_codec import decode_fog as _decode_fog_r
        fog_r = self.world.get_component(self.player_entity, _FogR)
        if fog_r:
            _fog_raw = char_data.get("fog_json", char_data.get("fog", {}))
            try:
                _fog_data = _jr.loads(_fog_raw) if isinstance(_fog_raw, str) else (_fog_raw or {})
            except Exception:
                _fog_data = {}
            for _mk, _tile_set in _decode_fog_r(_fog_data).items():
                _mk = _mk.replace("\\", "/")
                if _mk in fog_r._explored_maps:
                    fog_r._explored_maps[_mk].update(_tile_set)
                else:
                    fog_r._explored_maps[_mk] = _tile_set
            # Sincroniza ponteiro do mapa atual
            if fog_r._current_map in fog_r._explored_maps:
                fog_r.explored = fog_r._explored_maps[fog_r._current_map]

    def _player_world_pos(self) -> "tuple[float, float]":
        """Retorna posição pixel do player local (centro do tile)."""
        from engine.components import Position as _PosWP
        _p = self.world.get_component(self.player_entity, _PosWP)
        return (_p.x, _p.y) if _p else (0.0, 0.0)

    def _space_engage_online(self) -> None:
        """ESPAÇO: seleciona o mob remoto HOSTIL mais próximo e inicia
        perseguição (como offline). `is_hostile` (01/08/2026, bug real
        relatado pelo usuário) — antes iterava `self._remote_mobs` inteiro
        sem NENHUM filtro de facção, então podia selecionar/perseguir um
        minion/torre do PRÓPRIO time dentro de uma instância."""
        from engine.components import TileMovement, CombatState, PlayerAutoMove
        from engine.faction_system import is_hostile as _is_hostile_sp
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
            if not _is_hostile_sp(self.world, self.player_entity, local_eid):
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
        cs_p.chase_suppressed = False   # reengajamento reativa a perseguição
        if auto:
            auto.ground_target = None
            auto.path.clear()

    def _send_loot_request(self, corpse_id: int, take: str = "all",
                           item_id: str = "") -> None:
        """Envia LOOT_REQUEST ao servidor para o corpse_id.

        `take`: "gold"/"item"/"all" — granular desde 17/07/2026 (sacar só
        o ouro não deveria levar junto o resto do loot — ver
        server/loot_processor.py::request_loot). `item_id` (débito A4,
        11/08/2026, era `item_name` antes) — nome de exibição não
        distingue itens diferentes com o mesmo nome."""
        if not self._net or not self._net.connected:
            return
        from shared.messages import MsgType
        payload = {"corpse_id": corpse_id, "take": take}
        if item_id:
            payload["item_id"] = item_id
        self._net.send(MsgType.LOOT_REQUEST, payload)

    def _send_loot_request_for_local_corpse(self, local_corpse_eid: int,
                                            take: str = "all",
                                            item_id: str = "") -> None:
        """Ponte pro LootSystem (ui/systems.py) — ele só conhece o eid ECS
        LOCAL do corpse (a entidade Corpse criada por _handle_msg_loot_
        available), não o corpse_id do SERVIDOR (chave de
        self._available_loot). Resolve a tradução aqui antes de mandar
        LOOT_REQUEST — sem isso o servidor nunca acharia o corpse (espaços
        de id totalmente diferentes).

        Injetada em LootSystem SÓ em modo online (ver game.py) — o modo
        online passa a mandar LOOT_REQUEST em vez de creditar gold/item
        localmente na hora do clique (bug real relatado pelo usuário
        17/07/2026: grupo com loot free-for-all — cada membro recebia
        LOOT_AVAILABLE com os MESMOS itens/coins e processava a própria
        cópia local de forma independente, sem nenhuma validação do
        servidor — resultado: dois membros do grupo lootavam o MESMO ouro,
        cada um vendo o total "cheio" mesmo depois do outro já ter
        pegado). O crédito real agora só acontece em
        _handle_msg_loot_result, com o que o servidor confirma que ainda
        sobrava — se outro membro já pegou tudo, chega vazio."""
        corpse_id = next((cid for cid, data in self._available_loot.items()
                          if data.get("local_eid") == local_corpse_eid), None)
        if corpse_id is not None:
            self._send_loot_request(corpse_id, take, item_id)
