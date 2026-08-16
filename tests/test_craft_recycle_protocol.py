"""
tests/test_craft_recycle_protocol.py — Débito A4 (11/08/2026, ver
PROBLEMAS_ARQUITETURA.md): 2 bugs reais achados numa sessão de discussão
de performance, mais 2 bloqueadores encontrados no meio do desenho —
todos pequenos o bastante, e um deles uma duplicação de ouro já ativa,
pra corrigir junto:

1. Forjar/reciclar nunca descontava ouro/material de verdade no servidor
   — `ui/crafting_system.py` mutava só a cópia LOCAL do cliente. Fix:
   `CRAFT_REQUEST`/`RECYCLE_REQUEST`, servidor autoritativo
   (`WorldServer.craft_item`/`recycle_item`), mesmo modelo já provado em
   `process_shop_buy`/`process_shop_sell`.
2. Aprender receita por pergaminho nunca funcionava online — `apply_
   consumable` nunca olhava pra `LearnedRecipes`.
3. Bloqueador do #2: `_item_factory_by_id` nunca resolvia o item_id de
   um pergaminho de receita (prefixo "recipe_" vs chave crua de
   RECIPE_ITEMS) — pré-requisito, corrigido primeiro.
4. 2º bloqueador do #2, achado ao testar: `LearnedRecipes` nunca era
   criado pro personagem no `spawn_player` do SERVIDOR (só o do cliente
   tinha), e não existia coluna no banco pra persistir isso — sistema de
   receita nunca tinha sido migrado pro banco online de verdade. Fix:
   componente no spawn + coluna `learned_recipes_json` + save/load
   (mesmo padrão já usado 4x nesta sessão pra equipamento/inventário/
   talentos/hotbar).

Playtest do usuário (11/08/2026) achou mais 3 bugs reais nos fixes
acima — os 3 já corrigidos, testados aqui:

5. Forjar não tirava os materiais da bag LOCAL do cliente — só o
   resultado craftado chegava (`_grant_items_to_inventory`); os
   materiais consumidos nunca eram informados de volta, só sumiam da
   bag no próximo relog. Fix: `craft_item()` retorna
   `materials_consumed` (mesmo formato do "removed" de
   INVENTORY_UPDATE), cliente chama `_remove_items_from_inventory`.
6. `apply_consumable` nunca decrementava o item no Inventory AO VIVO
   do SERVIDOR — só a cópia do cliente sumia. Sintoma relatado: um
   pergaminho de receita reaparecia na bag a cada relog (o servidor
   nunca soube que foi consumido). Mesma classe de bug do #5/#1, só
   que pra QUALQUER consumível, não só forja.
7. O cliente nunca CARREGAVA `learned_recipes_json` de volta no login
   — `LearnedRecipes` já era salvo certo (bug #4), mas nunca lido de
   volta em `client/save_sync_handlers.py::_restore_save_state`, então
   uma receita aprendida numa sessão anterior sumia da lista de forja
   no relog seguinte.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import Inventory, Wallet, Item, LearnedRecipes
from content.crafting_data import RARITY_FORGE_COST, RARITY_RECYCLE_COST


def _mat(mat_id: str, qty: int) -> Item:
    from content.crafting_data import MATERIALS
    it = MATERIALS[mat_id]()
    it.stack = qty
    return it


class TestRecipeItemIdResolution(unittest.TestCase):
    """Bug #3 (pré-requisito do #2): item_id de pergaminho de receita
    nunca resolvia em _item_factory_by_id."""

    def test_item_factory_by_id_resolve_pergaminho_de_receita(self):
        from server.world_server import WorldServer
        item = WorldServer._item_factory_by_id("recipe_espada_afiada")
        self.assertIsNotNone(item)
        self.assertEqual(item.item_id, "recipe_espada_afiada")
        self.assertEqual(item.consumable, {"learn_recipe": "espada_afiada"})

    def test_item_factory_by_id_prefixo_sem_receita_correspondente_e_none(self):
        from server.world_server import WorldServer
        self.assertIsNone(WorldServer._item_factory_by_id("recipe_nao_existe"))

    def test_item_factory_by_id_ainda_resolve_resultado_craftado(self):
        """Garante que o fix do prefixo não quebrou a busca do item
        CRAFTADO (RECIPES[chave], sem prefixo, item diferente)."""
        from server.world_server import WorldServer
        item = WorldServer._item_factory_by_id("espada_afiada")
        self.assertIsNotNone(item)
        self.assertEqual(item.item_id, "espada_afiada")
        self.assertEqual(item.name, "Espada Afiada")


class TestLearnRecipeOnline(unittest.TestCase):
    """Bug #2: consumir pergaminho de receita nunca aprendia a receita
    de verdade no modo online."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.lr  = self.ws.world.get_component(self.eid, LearnedRecipes)

    def test_consumir_pergaminho_aprende_receita_e_avisa_cliente(self):
        self.ws.apply_consumable("s1", {"item_id": "recipe_espada_afiada"})
        updates = self.ws.consume_stats_updates()
        ok = [u for u in updates if u.get("consumable_ok")]
        self.assertEqual(len(ok), 1)
        self.assertEqual(ok[0]["learned_recipe"], "espada_afiada")

    def test_consumir_pergaminho_remove_do_inventory_ao_vivo_do_servidor(self):
        """Bug #6 (playtest 11/08/2026): sem isso, o pergaminho reaparecia
        na bag a cada relog — o servidor nunca soube que foi consumido."""
        inv = self.ws.world.get_component(self.eid, Inventory)
        scroll = self.ws._item_factory_by_id("recipe_espada_afiada")
        scroll.stack = 1
        inv.items.append(scroll)

        self.ws.apply_consumable("s1", {"item_id": "recipe_espada_afiada"})

        self.assertNotIn(scroll, inv.items)

    def test_consumir_potion_empilhada_decrementa_so_1_no_servidor(self):
        """Mesma classe de bug do pergaminho, mas pra QUALQUER consumível
        empilhável — prova que só 1 unidade sai, não a stack inteira."""
        inv = self.ws.world.get_component(self.eid, Inventory)
        potion = self.ws._item_factory_by_id("hp_potion")
        potion.stack = 5
        inv.items.append(potion)

        self.ws.apply_consumable("s1", {"item_id": "hp_potion", "heal_instant": 50})

        self.assertIn(potion, inv.items)
        self.assertEqual(potion.stack, 4)

    def test_pergaminho_ja_conhecido_nao_manda_learned_recipe_de_novo(self):
        self.lr.known.append("espada_afiada")
        self.ws.apply_consumable("s1", {"item_id": "recipe_espada_afiada"})
        updates = self.ws.consume_stats_updates()
        ok = [u for u in updates if u.get("consumable_ok")]
        self.assertEqual(len(ok), 1)
        self.assertNotIn("learned_recipe", ok[0])
        self.assertEqual(self.lr.known.count("espada_afiada"), 1)

    def test_consumivel_normal_nao_aprende_receita_nenhuma(self):
        self.ws.apply_consumable("s1", {"item_id": "hp_potion", "heal_instant": 50})
        updates = self.ws.consume_stats_updates()
        ok = [u for u in updates if u.get("consumable_ok")]
        self.assertEqual(len(ok), 1)
        self.assertNotIn("learned_recipe", ok[0])
        self.assertEqual(self.lr.known, [])


class TestLearnedRecipesPersistence(unittest.TestCase):
    """Bug #4: LearnedRecipes nunca era criado no spawn_player do
    servidor, e não existia persistência online pra ele."""

    def test_spawn_player_sempre_cria_learned_recipes(self):
        ws  = make_world_server()
        eid = spawn_player(ws, "s1", 130, 374)
        lr = ws.world.get_component(eid, LearnedRecipes)
        self.assertIsNotNone(lr)
        self.assertEqual(lr.known, [])

    def test_spawn_player_carrega_receitas_salvas(self):
        import json
        ws = make_world_server()
        char = {
            "tile_x": 130, "tile_y": 374, "name": "s1", "class_id": "guerreiro",
            "hp": 200, "level": 1,
            "stats_json": '{"attack_power": 50, "max_hp": 200}',
            "learned_recipes_json": json.dumps(["espada_afiada", "cota_reforcada"]),
        }
        eid = ws.spawn_player("s1", char)
        lr = ws.world.get_component(eid, LearnedRecipes)
        self.assertEqual(lr.known, ["espada_afiada", "cota_reforcada"])

    def test_get_player_learned_recipes_data_reflete_o_componente_ao_vivo(self):
        ws  = make_world_server()
        eid = spawn_player(ws, "s1", 130, 374)
        lr = ws.world.get_component(eid, LearnedRecipes)
        lr.known.append("espada_runica")
        self.assertEqual(ws.get_player_learned_recipes_data("s1"), ["espada_runica"])

    def test_get_player_learned_recipes_data_sessao_invalida_e_none(self):
        ws = make_world_server()
        self.assertIsNone(ws.get_player_learned_recipes_data("sessao_inexistente"))

    def test_build_save_merge_live_learned_recipes_vence_cache_do_cliente(self):
        """Mesma régua já usada pra equipamento/hotbar/talentos/
        inventário: o componente AO VIVO é sempre a fonte de verdade no
        save, nunca um cache de payload do cliente (que nem chega a
        mandar learned_recipes de propósito)."""
        from server.session import SessionManager
        merged = SessionManager._build_save_merge(
            srv_data={}, client_payload={"learned_recipes": ["receita_velha_do_cache"]},
            live_learned_recipes=["espada_afiada"])
        self.assertEqual(merged["learned_recipes"], ["espada_afiada"])

    def test_build_save_merge_sem_live_cai_no_cache_do_cliente(self):
        from server.session import SessionManager
        merged = SessionManager._build_save_merge(
            srv_data={}, client_payload={"learned_recipes": ["fallback"]},
            live_learned_recipes=None)
        self.assertEqual(merged["learned_recipes"], ["fallback"])


class TestCraftItem(unittest.TestCase):
    """Bug #1 (metade forja): craft_item() autoritativo."""

    RECIPE = "espada_afiada"  # 5 fragmento_ferro, 2 fibra_madeira, 1 tira_couro; uncommon

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv    = self.ws.world.get_component(self.eid, Inventory)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.wallet.gold = 10_000

    def _give_materials(self):
        self.inv.items.extend([
            _mat("fragmento_ferro", 5),
            _mat("fibra_madeira", 2),
            _mat("tira_couro", 1),
        ])

    def test_craft_sucesso_desconta_ouro_e_material_credita_resultado(self):
        self._give_materials()
        result = self.ws.craft_item("s1", self.RECIPE)

        self.assertTrue(result["success"])
        self.assertEqual(result["item"]["item_id"], "espada_afiada")
        self.assertEqual(self.wallet.gold, 10_000 - RARITY_FORGE_COST["uncommon"])
        self.assertEqual(result["new_gold"], self.wallet.gold)
        # materiais consumidos por completo
        self.assertEqual(sum(it.stack for it in self.inv.items
                             if it is not None and it.item_id == "fragmento_ferro"), 0)
        # resultado está de verdade no Inventory AO VIVO
        self.assertTrue(any(it is not None and it.item_id == "espada_afiada"
                            for it in self.inv.items))
        # Bug #5 (playtest 11/08/2026): sem isso, a cópia LOCAL do
        # cliente nunca ficava sabendo quais materiais consumir.
        self.assertEqual(
            {(m["item_id"], m["stack"]) for m in result["materials_consumed"]},
            {("fragmento_ferro", 5), ("fibra_madeira", 2), ("tira_couro", 1)})

    def test_craft_receita_invalida_nao_muda_nada(self):
        self._give_materials()
        gold_before = self.wallet.gold
        result = self.ws.craft_item("s1", "receita_forjada_que_nao_existe")
        self.assertEqual(result, {"success": False, "reason": "invalid_recipe"})
        self.assertEqual(self.wallet.gold, gold_before)

    def test_craft_ouro_insuficiente_nao_consome_material(self):
        self._give_materials()
        self.wallet.gold = 0
        result = self.ws.craft_item("s1", self.RECIPE)
        self.assertEqual(result["reason"], "insufficient_gold")
        # nenhum material foi tocado
        self.assertEqual(sum(it.stack for it in self.inv.items
                             if it is not None and it.item_id == "fragmento_ferro"), 5)

    def test_craft_material_insuficiente_nao_desconta_ouro(self):
        # só metade do fragmento_ferro necessário
        self.inv.items.append(_mat("fragmento_ferro", 2))
        gold_before = self.wallet.gold
        result = self.ws.craft_item("s1", self.RECIPE)
        self.assertEqual(result["reason"], "insufficient_materials")
        self.assertEqual(self.wallet.gold, gold_before)

    def test_craft_mochila_cheia_recusa_sem_consumir_nada(self):
        self._give_materials()
        self.inv.max_slots = len(self.inv.items)  # cheio, resultado não empilha (item novo)
        gold_before = self.wallet.gold
        result = self.ws.craft_item("s1", self.RECIPE)
        self.assertEqual(result["reason"], "inventory_full")
        self.assertEqual(self.wallet.gold, gold_before)
        self.assertEqual(sum(it.stack for it in self.inv.items
                             if it is not None and it.item_id == "fragmento_ferro"), 5)

    def test_craft_sessao_invalida_nao_crasha(self):
        result = self.ws.craft_item("sessao_inexistente", self.RECIPE)
        self.assertEqual(result, {"success": False, "reason": "not_logged_in"})


class TestRecycleItem(unittest.TestCase):
    """Bug #1 (metade reciclagem): recycle_item() autoritativo."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv    = self.ws.world.get_component(self.eid, Inventory)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.wallet.gold = 10_000

    def _sword(self, rarity="common"):
        return Item("Espada de Teste", "weapon", "mainhand",
                    rarity=rarity, subtype="Sword", value=10)

    def test_recycle_sucesso_remove_item_credita_materiais(self):
        sword = self._sword()
        self.inv.items.append(sword)
        result = self.ws.recycle_item("s1", 0)

        self.assertTrue(result["success"])
        self.assertEqual(self.wallet.gold, 10_000 - RARITY_RECYCLE_COST["common"])
        self.assertNotIn(sword, self.inv.items)
        self.assertTrue(len(result["materials"]) > 0)
        self.assertTrue(any(it is not None and it.item_id == "fragmento_ferro"
                            for it in self.inv.items))

    def test_recycle_item_nao_reciclavel_mantem_item(self):
        potion = Item("Poção", "consumable", "", rarity="common")
        self.inv.items.append(potion)
        result = self.ws.recycle_item("s1", 0)
        self.assertEqual(result["reason"], "not_recyclable")
        self.assertIn(potion, self.inv.items)

    def test_recycle_ouro_insuficiente_mantem_item(self):
        sword = self._sword()
        self.inv.items.append(sword)
        self.wallet.gold = 0
        result = self.ws.recycle_item("s1", 0)
        self.assertEqual(result["reason"], "insufficient_gold")
        self.assertIn(sword, self.inv.items)

    def test_recycle_inv_index_forjado_nao_crasha(self):
        result = self.ws.recycle_item("s1", 99)
        self.assertEqual(result, {"success": False, "reason": "invalid_item"})

    def test_recycle_inv_index_negativo_nao_crasha(self):
        result = self.ws.recycle_item("s1", -1)
        self.assertEqual(result["success"], False)


class TestShopSellRemovesFromLiveInventory(unittest.TestCase):
    """Bug real relatado pelo usuário (12/08/2026): vendeu item na loja,
    gold ficou certo, mas o item voltava pra bag no relog seguinte —
    e pior, `EQUIP_ITEM` (posicional contra o Inventory AO VIVO,
    `equip_item_from_inventory`) passou a equipar/rejeitar o item
    errado depois de vender algo, porque a posição real no servidor
    divergia da que o cliente via. `process_shop_sell` mutava só a
    Wallet, nunca o Inventory — mesma classe de bug já corrigida pra
    forja/reciclagem/consumível nesta mesma sessão (ver docstring do
    topo do arquivo, itens #5/#6), só que faltou aplicar pra venda."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv    = self.ws.world.get_component(self.eid, Inventory)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.wallet.gold = 0

    def _iron_sword(self, stack=1):
        from content.item_table import resolve_item_by_id
        it = resolve_item_by_id("iron_sword")
        it.stack = stack
        return it

    def test_vender_remove_o_item_do_inventory_vivo(self):
        sword = self._iron_sword()
        self.inv.items.append(sword)

        result = self.ws.process_shop_sell(
            "s1", item_name="Espada de Ferro", client_value=35,
            stack_sold=1, current_gold=0, item_id="iron_sword")

        self.assertTrue(result["success"])
        self.assertGreater(self.wallet.gold, 0)
        self.assertNotIn(sword, self.inv.items,
                         "item vendido deveria ter sido removido do Inventory AO VIVO")

    def test_vender_1_de_uma_pilha_decrementa_sem_remover_o_slot(self):
        arrows = self._iron_sword(stack=1)
        # Empilhável de verdade — usa Flecha, que tem max_stack>1
        from content.item_table import resolve_item_by_id
        arrows = resolve_item_by_id("arrow")
        arrows.stack = 5
        self.inv.items.append(arrows)

        result = self.ws.process_shop_sell(
            "s1", item_name=arrows.name, client_value=arrows.value,
            stack_sold=1, current_gold=0, item_id="arrow")

        self.assertTrue(result["success"])
        self.assertIn(arrows, self.inv.items, "pilha não deveria sumir, só diminuir")
        self.assertEqual(arrows.stack, 4)

    def test_vender_item_que_nao_esta_no_inventory_ainda_sucede_sem_remover_nada(self):
        """Mesmo espírito conservador do fallback de client_value — não
        bloqueia a venda por item_id não encontrado ao vivo (evita
        regressão pra qualquer chamador existente que já dependia de
        vender sempre suceder)."""
        result = self.ws.process_shop_sell(
            "s1", item_name="Espada de Ferro", client_value=35,
            stack_sold=1, current_gold=0, item_id="iron_sword")
        self.assertTrue(result["success"])
        self.assertEqual(self.inv.items, [])


class TestCraftRecycleProtocolDispatch(unittest.IsolatedAsyncioTestCase):
    """Fim-a-fim via protocolo real (encode/on_message)."""

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login
        self.ws, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(self.mgr, "s1", "user_craft_a", 130, 374)
        self.inv    = self.ws.world.get_component(self.session.entity_id, Inventory)
        self.wallet = self.ws.world.get_component(self.session.entity_id, Wallet)
        self.wallet.gold = 10_000

    async def _send(self, msg_type, payload):
        from shared.messages import encode
        await self.mgr.on_message(self.session, encode(msg_type, payload))

    async def test_craft_request_sucesso_via_protocolo(self):
        from tests.test_session import get_msgs_of_type
        from shared.messages import MsgType
        self.inv.items.extend([
            _mat("fragmento_ferro", 5), _mat("fibra_madeira", 2), _mat("tira_couro", 1),
        ])
        self.fw.sent.clear()

        await self._send(MsgType.CRAFT_REQUEST, {"recipe_id": "espada_afiada"})

        results = get_msgs_of_type(self.fw, MsgType.CRAFT_RESULT)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])
        self.assertTrue(any(it is not None and it.item_id == "espada_afiada"
                            for it in self.inv.items))

    async def test_craft_request_receita_forjada_nao_crasha_nem_credita_nada(self):
        from tests.test_session import get_msgs_of_type
        from shared.messages import MsgType
        self.fw.sent.clear()

        await self._send(MsgType.CRAFT_REQUEST, {"recipe_id": "receita_que_nao_existe_no_catalogo"})

        results = get_msgs_of_type(self.fw, MsgType.CRAFT_RESULT)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertEqual(self.wallet.gold, 10_000)

    async def test_recycle_request_sucesso_via_protocolo(self):
        from tests.test_session import get_msgs_of_type
        from shared.messages import MsgType
        sword = Item("Espada de Teste", "weapon", "mainhand", rarity="common", subtype="Sword")
        self.inv.items.append(sword)
        self.fw.sent.clear()

        await self._send(MsgType.RECYCLE_REQUEST, {"inv_index": 0})

        results = get_msgs_of_type(self.fw, MsgType.RECYCLE_RESULT)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])
        self.assertNotIn(sword, self.inv.items)


class TestClientLoadsLearnedRecipesOnLogin(unittest.TestCase):
    """Bug #7 (playtest 11/08/2026): client/save_sync_handlers.py::
    _restore_save_state nunca carregava `learned_recipes_json` de volta
    — uma receita aprendida numa sessão anterior sumia da lista de forja
    no relog seguinte, mesmo já persistida certo no banco (bug #4)."""

    def setUp(self):
        import json
        from engine.world import World
        from engine.entity_factory import create_player
        from client.network_handlers import NetworkHandlers
        from client.save_sync_handlers import SaveSyncHandlers
        from client.inventory_handlers import InventoryHandlers

        class _FakeNet:
            connected = True
            def send(self, *a, **k): pass

        class _FakeClient(NetworkHandlers, SaveSyncHandlers, InventoryHandlers):
            def __init__(self, world, player_entity):
                self.world = world
                self.player_entity = player_entity
                self._my_eid = player_entity
                self._net = _FakeNet()

        self.world  = World()
        self.eid    = create_player(self.world, 10, 10)
        self.client = _FakeClient(self.world, self.eid)
        self._json  = json

    def test_restore_save_state_carrega_receitas_aprendidas_anteriormente(self):
        char_data = {
            "learned_recipes_json": self._json.dumps(["espada_afiada", "cota_reforcada"]),
            "class_id": "guerreiro",
        }
        self.client._restore_save_state(char_data)
        lr = self.world.get_component(self.eid, LearnedRecipes)
        self.assertEqual(lr.known, ["espada_afiada", "cota_reforcada"])

    def test_restore_save_state_sem_receitas_nao_crasha(self):
        self.client._restore_save_state({"class_id": "guerreiro"})
        lr = self.world.get_component(self.eid, LearnedRecipes)
        self.assertEqual(lr.known, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
