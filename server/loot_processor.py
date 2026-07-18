"""
server/loot_processor.py
Mixin para WorldServer: loot, corpses, notificações de drop.
"""
from __future__ import annotations
from server.log import log


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

    def request_loot(self, session_id: str, corpse_id: int,
                     take: str = "all", item_name: str = "") -> dict | None:
        """
        Retorna {items, coins} do corpse se o player for o dono OU membro do
        MESMO grupo do dono (free-for-all dentro do grupo — decisão do
        usuário 17/07/2026), None caso contrário.

        `take` decide o que sai do corpse nesta chamada (granular desde
        17/07/2026 — bug real relatado pelo usuário: sacar só o ouro
        também levava junto os itens que sobravam, corpo sumia com loot
        ainda dentro):
          - "gold": só as moedas, itens intocados.
          - "item": só o PRIMEIRO item da lista atual com name==item_name
            (nome, não índice — índice cru quebraria se outro membro do
            grupo já tivesse tirado um item antes, deslocando a lista).
          - "all" (default/compat): tudo, comportamento antigo.
        Após sacar: reduz timer pra 15s — primeiro do grupo a lootar leva
        o que pediu, os outros recebem {items:[],coins:0} se pedirem a
        MESMA coisa depois (mesmo comportamento de "free for all" de
        qualquer MMO). Fora do dono/grupo: None silenciosamente.
        """
        corpse = self._corpses.get(corpse_id)
        if not corpse:
            return None
        player_eid = self._player_eids.get(session_id, -1)
        owner_eid  = corpse["owner_eid"]
        if player_eid != owner_eid:
            owner_pid = self.get_party_id_of(owner_eid)
            if owner_pid == -1 or self.get_party_id_of(player_eid) != owner_pid:
                return None  # não é o dono nem está no mesmo grupo — ignora

        if take == "gold":
            coins = corpse.get("coins", 0)
            corpse["coins"] = 0
            items = []
        elif take == "item":
            items_list = corpse.get("items", [])
            items = []
            for i, it in enumerate(items_list):
                if it.get("name") == item_name:
                    items = [items_list.pop(i)]
                    break
            coins = 0
        else:
            items = corpse.pop("items", [])
            coins = corpse.pop("coins", 0)

        corpse["timer"] = min(corpse["timer"], 15.0)  # reduz timer após saque
        # Atualiza Wallet do servidor para persistência
        if coins > 0:
            from engine.components import Wallet as _W
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
            log.info(f"[Loot] corpse_id={corpse_id}  owner={owner_eid}  "
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
