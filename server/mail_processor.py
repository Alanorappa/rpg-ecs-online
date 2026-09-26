"""
server/mail_processor.py
Mixin para WorldServer: correio (NPC "Caixa de Correio", 25/09/2026) — a
PRIMEIRA feature do projeto com entrega ASSÍNCRONA de verdade (destinatário
pode estar offline). Persistência real via `server/auth.py` (único ponto de
acesso ao banco); item/gold em custódia na LINHA do banco (não em memória,
diferente de trade_processor.py) até reclamado.

Entrega de anexo reaproveita canais já existentes — ver shared/messages.py:
item concedido → `grant_items_to_player` + INVENTORY_UPDATE (mesmo caminho
de recompensa de quest); gold → mutação direta de Wallet.gold + STATS_UPDATE
(mesmo caminho de sempre). Este módulo só cuida da parte que esses canais
não sabem: validar posse real, resolver destinatário, e persistir/consultar
o banco.
"""
from __future__ import annotations


class MailProcessorMixin:

    def _find_online_player_eid_by_name(self, name: str) -> "int | None":
        """Busca um player ONLINE agora por nome de personagem (case-
        insensitive) — usado só pra notificar o indicador de correspondência
        nova em tempo real; se o destinatário não estiver online, a
        correspondência já foi persistida de qualquer forma (ele vê no
        próximo login, via `count_unread_mail` em `_spawn_and_start`)."""
        from engine.components import EntityIdentity, PlayerControlled
        name_lower = name.lower()
        for eid, ident, _ in self.world.get_entities_with(EntityIdentity, PlayerControlled):
            if ident.name.lower() == name_lower:
                return eid
        return None

    async def process_mail_open_inbox(self, char_id: int) -> list:
        from server import auth
        return await auth.list_inbox(char_id)

    async def process_mail_open_item(self, mail_id: int, char_id: int) -> "dict | None":
        from server import auth
        return await auth.get_mail_detail(mail_id, char_id)

    async def process_mail_send(self, player_eid: int, char_id: int, sender_name: str,
                                to_name: str, subject: str, body: str,
                                items: list, gold: int) -> dict:
        """Autoritativo — valida destinatário e posse real dos itens/gold no
        Inventory/Wallet AO VIVO (nunca confia no que o cliente reporta ter).
        Tudo-ou-nada: um índice inválido no meio da lista rejeita a
        correspondência INTEIRA, sem remover nada (mesmo princípio de
        `process_shop_buy` — nunca entrega/debita parcial)."""
        from server import auth
        from engine.components import Inventory, Wallet

        to_name = str(to_name).strip()
        if not to_name:
            return {"success": False, "reason": "destinatario_invalido"}
        recipient_char_id = await auth.get_character_id_by_name(to_name)
        if recipient_char_id is None:
            return {"success": False, "reason": "destinatario_nao_encontrado"}
        if recipient_char_id == char_id:
            return {"success": False, "reason": "nao_pode_mandar_pra_si_mesmo"}
        if len(items) > 6:
            return {"success": False, "reason": "muitos_itens"}

        inv    = self.world.get_component(player_eid, Inventory)
        wallet = self.world.get_component(player_eid, Wallet)
        if inv is None or wallet is None:
            return {"success": False, "reason": "erro_interno"}

        gold = max(0, int(gold))
        if gold > wallet.gold:
            return {"success": False, "reason": "ouro_insuficiente"}
        if gold == 0 and not items:
            return {"success": False, "reason": "correspondencia_vazia"}

        # Valida TODOS os slots antes de remover qualquer um. `quantity`
        # (25/09/2026, pedido do usuário — Shift+clique direito fatia a
        # pilha, mesmo padrão de trade_processor.py::add_trade_item)
        # nunca é confiada cegamente: clampada contra o stack REAL do
        # item nesse índice, no Inventory AO VIVO.
        seen_indices: set = set()
        ordered: list = []   # [(idx, qty), ...] em ordem de chegada
        for it in items:
            idx = int(it.get("inv_index", -1))
            if idx in seen_indices or not (0 <= idx < len(inv.items)):
                return {"success": False, "reason": "item_invalido"}
            seen_indices.add(idx)
            real_stack = inv.items[idx].stack
            qty = int(it.get("quantity", real_stack))
            qty = max(1, min(qty, real_stack))
            ordered.append((idx, qty))

        entries         = [(inv.items[i].item_id, q) for i, q in ordered]
        # "item_id" (não "name") — client/network_handlers.py::
        # _remove_items_from_inventory espera esse campo (migrado
        # 10/08/2026, débito C2); usar "name" faria o item nunca sumir
        # de verdade da bag LOCAL do remetente (a remoção real no
        # Inventory do SERVIDOR, acima, já está correta de qualquer
        # forma — isso só afeta o espelho client-side).
        removed_display = [{"name": inv.items[i].name, "item_id": inv.items[i].item_id, "stack": q}
                           for i, q in ordered]

        subject_final = str(subject).strip()
        if not subject_final:
            # Fallback aplicado no SERVIDOR também (não só no cliente, que
            # já pré-preenche por UX) — não depender de um cliente
            # modificado preencher certo.
            if removed_display:
                subject_final = removed_display[0]["name"]
            elif gold > 0:
                subject_final = str(gold)

        # Remove/fatia de verdade só AGORA que tudo validou — índices em
        # ordem DECRESCENTE pra popar (quando qty == stack inteiro) sem
        # bagunçar os índices anteriores da lista; fatiar (qty < stack)
        # só decrementa in-place, não desloca nenhum índice.
        for idx, qty in sorted(ordered, key=lambda p: p[0], reverse=True):
            item = inv.items[idx]
            if qty >= item.stack:
                inv.items.pop(idx)
            else:
                item.stack -= qty
        wallet.gold -= gold

        await auth.create_mail(recipient_char_id, sender_name, subject_final,
                               str(body)[:2000], entries, gold)

        if gold > 0:
            self.queue_stats_update({"player_eid": player_eid, "gold": wallet.gold})

        recipient_eid = self._find_online_player_eid_by_name(to_name)
        if recipient_eid is not None:
            await self._push_mail_unread(recipient_eid, recipient_char_id)

        return {"success": True, "removed": removed_display}

    async def process_mail_claim_item(self, player_eid: int, char_id: int,
                                      mail_id: int, item_index: int) -> dict:
        from server import auth
        claimed = await auth.claim_mail_item(mail_id, char_id, item_index)
        if claimed is None:
            return {"success": False}
        granted, _not_granted = self.grant_items_to_player(
            player_eid, [(claimed["item_key"], claimed["stack"])])
        if not granted:
            # Mochila cheia — desfaz o claim no banco pra não perder o item
            # (idempotência: jogador tenta de novo depois de abrir espaço).
            await auth.unclaim_mail_item(mail_id, char_id, item_index)
            return {"success": False, "reason": "mochila_cheia"}
        await self._push_mail_unread(player_eid, char_id)
        return {"success": True, "granted": granted}

    async def process_mail_claim_gold(self, player_eid: int, char_id: int,
                                      mail_id: int) -> dict:
        from server import auth
        from engine.components import Wallet
        amount = await auth.claim_mail_gold(mail_id, char_id)
        if amount is None:
            return {"success": False}
        wallet = self.world.get_component(player_eid, Wallet)
        if wallet is None:
            return {"success": False}
        wallet.gold += amount
        self.queue_stats_update({"player_eid": player_eid, "gold": wallet.gold})
        await self._push_mail_unread(player_eid, char_id)
        return {"success": True}

    async def process_mail_delete(self, player_eid: int, char_id: int, mail_id: int) -> bool:
        from server import auth
        deleted = await auth.delete_mail(mail_id, char_id)
        if deleted:
            await self._push_mail_unread(player_eid, char_id)
        return deleted

    async def _push_mail_unread(self, player_eid: int, char_id: int) -> None:
        """Recalcula e avisa o indicador de correspondência não reclamada
        — bug real relatado pelo usuário 25/09/2026: a exclamação sobre a
        caixa de correio só sumia relogando (só existia o gatilho de
        login, `_spawn_and_start`); reclamar item/gold ou deletar uma
        correspondência nunca recalculava. Reaproveita STATS_UPDATE
        (campo `mail_unread`), mesmo canal do login — nenhum buffer/
        mensagem nova."""
        from server import auth
        count = await auth.count_unread_mail(char_id)
        self.queue_stats_update({"player_eid": player_eid, "mail_unread": count})
