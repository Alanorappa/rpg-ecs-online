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

    def consume_harvestable_refills(self) -> list[dict]:
        """Retorna e limpa harvestables de posição fixa reabastecidos
        neste tick (Fase M2, 25/07/2026): list de {hid, tx, ty, map} —
        consumido pelo SessionManager pra mandar LOOT_AVAILABLE
        personalizado pra quem já conhece a entidade."""
        result = list(self._pending_harvestable_refill)
        self._pending_harvestable_refill.clear()
        return result

    def _resolve_conditional_loot_for(self, corpse: dict, player_eid: int) -> list:
        """Resolve (1x, cacheado) o loot condicional de quest (ex.: Pelo de
        Urso) PARA ESTE JOGADOR ESPECÍFICO — Fase L1, 25/07/2026, pedido do
        usuário. Antes, esse loot era decidido 1x na morte do mob contra a
        QuestLog do first-attacker e ficava FIXO no corpse — qualquer
        membro do MESMO GRUPO que saqueasse depois via request_loot (regra
        "free-for-all dentro do grupo") via/pegava o item mesmo sem a
        quest. Agora: cada jogador que interage com o corpse (LOOT_AVAILABLE
        pra ele, server/session.py, OU o fallback aqui em request_loot pra
        quem entrou no grupo depois) tem seu PRÓPRIO sorteio, cacheado em
        `corpse["quest_rolls"][player_eid]` — nunca re-sorteado pro MESMO
        jogador depois (reabrir o modal não dá nova chance). Retorna a
        lista (já serializada) do que esse jogador ganha — [] se ele não
        tem a quest ou o sorteio de chance não favoreceu."""
        quest_rolls = corpse.setdefault("quest_rolls", {})
        if player_eid not in quest_rolls:
            quest_rolls[player_eid] = []
            mob_name = corpse.get("mob_name", "")
            if mob_name:
                from engine.components import QuestLog as _QL_loot
                ql = self.world.get_component(player_eid, _QL_loot)
                if ql is not None:
                    import engine.quest_logic as _quest_logic_loot
                    from server.server_death_handler import _serialize_item as _ser_loot
                    extras = _quest_logic_loot.roll_conditional_loot(
                        ql, mob_name, corpse.get("mob_race", ""))
                    quest_rolls[player_eid] = [_ser_loot(it) for it in extras]
        return quest_rolls[player_eid]

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
            Procura primeiro no pote comum (compartilhado), depois no
            pessoal (condicional de quest, Fase L1) deste jogador.
          - "all" (default/compat): tudo — comum + o que sobrar do
            condicional pessoal deste jogador.
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
        # owner_eid == -1: harvestable de mapa (Fase M1) — público, sem
        # dono, qualquer jogador pode saquear, sem checagem de grupo.
        if owner_eid != -1 and player_eid != owner_eid:
            owner_pid = self.get_party_id_of(owner_eid)
            if owner_pid == -1 or self.get_party_id_of(player_eid) != owner_pid:
                return None  # não é o dono nem está no mesmo grupo — ignora

        # Fallback: garante que este jogador já tem seu próprio sorteio
        # condicional resolvido, mesmo se ele não estava no grupo/AOI na
        # hora do LOOT_AVAILABLE original (server/session.py já resolve o
        # caso comum lá; isto aqui só cobre quem chega depois).
        personal_items = self._resolve_conditional_loot_for(corpse, player_eid)

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
            if not items:
                for i, it in enumerate(personal_items):
                    if it.get("name") == item_name:
                        items = [personal_items.pop(i)]
                        break
            coins = 0
        else:
            items = corpse.pop("items", [])
            items.extend(personal_items)
            corpse["quest_rolls"][player_eid] = []   # já retirado — não reaparece
            coins = corpse.pop("coins", 0)

        corpse["timer"] = min(corpse["timer"], 15.0)  # reduz timer após saque
        # Atualiza Wallet do servidor para persistência
        if coins > 0:
            from engine.components import Wallet as _W
            wallet = self.world.get_component(player_eid, _W)
            if wallet:
                wallet.gold += coins

        # Fase M4 (25/07/2026, REVISADA 25/07/2026 duas vezes — usuário
        # pediu fluxo de decisão, depois pediu que o modal fosse o MESMO
        # do NPC): item saqueado que concede quest NÃO inicia mais
        # automaticamente aqui — fica só na bag até o jogador clicar direito
        # nele e escolher "Aceitar" no modal de quest (ui/quest_system.py::
        # QuestDialogSystem.open_for_item, via client/inventory_handlers.py::
        # _try_open_item_quest_dialog), que manda o MESMO QUEST_ACCEPT que o
        # diálogo de NPC usa (server/session.py::_handle_quest_accept).
        # ITEM_GRANTS_QUEST (content/quests_data.py) continua existindo,
        # agora só como METADADO consultado pelo cliente (tag do tooltip +
        # gatilho do popup) — não é mais lido aqui.
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
                # Fase L1 (25/07/2026) — loot condicional de quest resolvido
                # por jogador, não mais 1x na morte (ver
                # _resolve_conditional_loot_for).
                "mob_name":    loot_entry.get("mob_name", ""),
                "mob_race":    loot_entry.get("mob_race", ""),
                "quest_rolls": {},
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

        # Decay de corpses (timer baseado em tempo real via dt).
        # no_decay=True (harvestable de mapa, Fase M1) nunca expira aqui —
        # é permanente até M2 trazer respawn de verdade.
        for cid in list(self._corpses.keys()):
            c = self._corpses[cid]
            if c.get("no_decay"):
                continue
            c["timer"] -= dt
            if c["timer"] <= 0:
                self._expired_corpses_this_tick.append(
                    {"cid": cid, "tx": c["tx"], "ty": c["ty"], "map": c.get("map")})
                del self._corpses[cid]
