"""
server/loot_processor.py
Mixin para WorldServer: loot, corpses, notificações de drop.
"""
from __future__ import annotations


class LootProcessorMixin:

    def consume_loot_notifications(self) -> list[dict]:
        """Retorna e limpa notificações de loot pendentes (para o SessionManager)."""
        result = list(self._pending_loot_notifications)
        self._pending_loot_notifications.clear()
        return result

    def consume_expired_corpses(self) -> list[dict]:
        """Retorna e limpa corpses que expiraram neste tick: list de {cid, tx, ty}."""
        result = list(self._expired_corpses_this_tick)
        self._expired_corpses_this_tick.clear()
        return result

    def request_loot(self, session_id: str, corpse_id: int) -> dict | None:
        """
        Retorna {items, coins} do corpse se o player for o dono, None caso contrário.
        Após sacar: esvazia items/coins e reduz timer para 15s.
        Outros players recebem None silenciosamente (regra de negócio: ignorar).
        """
        corpse = self._corpses.get(corpse_id)
        if not corpse:
            return None
        player_eid = self._player_eids.get(session_id, -1)
        if player_eid != corpse["owner_eid"]:
            return None  # não é o dono — ignora silenciosamente
        items = corpse.pop("items", [])
        coins = corpse.pop("coins", 0)
        corpse["timer"] = min(corpse["timer"], 15.0)  # reduz timer após saque
        # Atualiza Wallet do servidor para persistência
        if coins > 0:
            from components import Wallet as _W
            wallet = self.world.get_component(player_eid, _W)
            if wallet:
                wallet.gold += coins
        return {"items": items, "coins": coins}

    def _process_loot_drops(self, dt: float) -> None:
        """Registra corpses de mobs mortos e faz decay dos existentes."""
        # Processa loot dos mobs mortos — registra corpse e agenda notificações
        for loot_entry in self._death_handler.consume_loot():
            owner_eid  = loot_entry["owner_eid"]
            corpse_id  = self._next_corpse_id
            self._next_corpse_id += 1
            self._corpses[corpse_id] = {
                "tx":        loot_entry["tx"],
                "ty":        loot_entry["ty"],
                "owner_eid": owner_eid,
                "items":     loot_entry["items"],
                "coins":     loot_entry.get("coins", 0),
                "timer":     120.0,
                "map":       loot_entry.get("map"),
            }
            self._pending_loot_notifications.append({
                "corpse_id": corpse_id,
                "owner_eid": owner_eid,
                "tx":        loot_entry["tx"],
                "ty":        loot_entry["ty"],
                "items":     loot_entry["items"],
                "coins":     loot_entry.get("coins", 0),
                "map":       loot_entry.get("map"),
            })
            print(f"[Loot] corpse_id={corpse_id}  owner={owner_eid}  "
                  f"items={len(loot_entry['items'])}  coins={loot_entry.get('coins', 0)}  "
                  f"tile=({loot_entry['tx']},{loot_entry['ty']})")

        # Decay de corpses (timer baseado em tempo real via dt)
        for cid in list(self._corpses.keys()):
            c = self._corpses[cid]
            c["timer"] -= dt
            if c["timer"] <= 0:
                self._expired_corpses_this_tick.append(
                    {"cid": cid, "tx": c["tx"], "ty": c["ty"], "map": c.get("map")})
                del self._corpses[cid]
