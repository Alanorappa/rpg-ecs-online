"""
tests/test_loot_authoritative.py — Débito A4 (11/08/2026, ver
PROBLEMAS_ARQUITETURA.md): redesign de saque, última peça client-
authoritative do item A4. Antes, `request_loot()` só tirava o item do
dict do corpse e devolvia pro cliente aplicar por conta própria na sua
cópia LOCAL do Inventory — o servidor nunca sabia se o item realmente
coube na bag, nunca empilhava de verdade, e "item" era casado por NOME
de exibição (`item_name`), que não distingue itens diferentes com o
mesmo nome.

Fix: `request_loot()` agora credita direto no Inventory AO VIVO do
servidor antes de responder (mesmo modelo já provado em `craft_item`);
`take="item"` casa por `item_id`; item que não cabe na bag continua no
corpse (decisão do usuário: isso não é comportamento de loot, é só "não
tira do corpse o que não coube" — sem lógica nova de expiração, o timer
normal do corpse já cobre); progresso de quest `collect_item` se move
pra dentro do próprio fluxo de saque (server/session.py::
_handle_loot_request), já que o cliente não manda mais INV_SYNC depois
de lootear. Guarda de integridade nova: dois item_ids diferentes com o
mesmo nome de exibição recusam o boot do servidor (decisão do usuário,
11/08/2026 — "trava para que isso não possa acontecer").
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, AuthPoolCleanupMixin
from engine.components import Inventory, Item


def _loot_dict(item_id: str, stack: int = 1) -> dict:
    from content.item_table import ITEMS
    from server.server_death_handler import _serialize_item
    d = _serialize_item(ITEMS[item_id]())
    d["stack"] = stack
    return d


def _make_corpse(ws, owner_eid: int, items: list, coins: int = 0) -> int:
    cid = ws._next_corpse_id
    ws._next_corpse_id += 1
    ws._corpses[cid] = {
        "tx": 130, "ty": 374, "owner_eid": owner_eid,
        "items": items, "coins": coins,
        "timer": 120.0, "map": ws._map_file, "quest_rolls": {},
    }
    return cid


class TestLootCreditaDiretoNoInventoryAoVivo(unittest.TestCase):
    """request_loot() agora muta o Inventory AO VIVO do jogador antes de
    responder — nunca mais um pedido pro cliente aplicar sozinho."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv = self.ws.world.get_component(self.eid, Inventory)

    def test_item_stackavel_empilha_em_stack_existente(self):
        existing = Item("Poção de Vida", "consumable", slot=None,
                        max_stack=20, item_id="hp_potion")
        existing.stack = 2
        self.inv.items.append(existing)
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("hp_potion", stack=3)])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(self.inv.items), 1, "não deveria ter criado slot novo")
        self.assertEqual(self.inv.items[0].stack, 5)

    def test_item_nao_stackavel_ocupa_slot_novo(self):
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("iron_sword")])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(self.inv.items), 1)
        self.assertEqual(self.inv.items[0].item_id, "iron_sword")

    def test_take_item_casa_por_item_id_nao_por_nome(self):
        cid = _make_corpse(self.ws, self.eid,
                           [_loot_dict("hp_potion"), _loot_dict("iron_sword")])

        result = self.ws.request_loot("s1", cid, take="item", item_id="iron_sword")

        self.assertEqual([i["item_id"] for i in result["items"]], ["iron_sword"])
        remaining = [i["item_id"] for i in self.ws._corpses[cid]["items"]]
        self.assertEqual(remaining, ["hp_potion"])
        self.assertEqual(len(self.inv.items), 1)
        self.assertEqual(self.inv.items[0].item_id, "iron_sword")

    def test_dado_invalido_no_reconstruct_nao_desaparece_do_cadaver(self):
        """Dict sem item_id e sem name (não deveria acontecer em produção —
        corpse só é populado por lógica server-side) não pode simplesmente
        sumir do corpse em silêncio — fica lá, não concedido, mesma regra
        de 'sem espaço'."""
        bad = {"stack": 1}
        cid = _make_corpse(self.ws, self.eid, [bad])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(result["items"], [])
        self.assertEqual(self.ws._corpses[cid]["items"], [bad],
                         "dado inválido deveria permanecer no corpse, não sumir")


class TestLootItemSemEspacoFicaNoCadaver(unittest.TestCase):
    """Decisão do usuário (11/08/2026): bag cheia não é comportamento de
    loot — o item simplesmente continua no corpse, sem lógica nova de
    expiração (o timer normal do corpse já cobre)."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.inv = self.ws.world.get_component(self.eid, Inventory)
        # Enche a bag com slots não-empilháveis distintos.
        for i in range(self.inv.max_slots):
            filler = Item(f"Enchimento {i}", "material", slot=None,
                         max_stack=1, item_id=f"filler_{i}")
            self.inv.items.append(filler)

    def test_take_all_com_bag_cheia_nao_perde_o_item(self):
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("iron_sword")], coins=5)

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(result["items"], [])
        self.assertTrue(result["no_space"])
        self.assertEqual(result["coins"], 5, "ouro não depende de espaço na bag")
        self.assertEqual(len(self.ws._corpses[cid]["items"]), 1,
                         "item que não coube deveria continuar no corpse")
        self.assertEqual(len(self.inv.items), self.inv.max_slots,
                         "bag não deveria ter sido alterada")

    def test_take_item_com_bag_cheia_retorna_no_space(self):
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("iron_sword")])

        result = self.ws.request_loot("s1", cid, take="item", item_id="iron_sword")

        self.assertEqual(result["items"], [])
        self.assertTrue(result["no_space"])
        self.assertEqual(len(self.ws._corpses[cid]["items"]), 1)

    def test_take_item_ja_pego_por_outro_nao_e_no_space(self):
        """Corpse já vazio pro item pedido (outro membro do grupo já
        levou) é um caso DIFERENTE de 'não coube' — no_space deve ficar
        False (cliente mostra 'já foi saqueado', não 'mochila cheia')."""
        cid = _make_corpse(self.ws, self.eid, [])

        result = self.ws.request_loot("s1", cid, take="item", item_id="iron_sword")

        self.assertEqual(result["items"], [])
        self.assertFalse(result["no_space"])

    def test_item_stackavel_ainda_empilha_mesmo_com_bag_cheia_de_outros_itens(self):
        """Bag "cheia" (max_slots atingido) ainda aceita empilhar num slot
        JÁ existente do mesmo item_id — só precisa de slot livre quando
        for criar um NOVO."""
        existing = Item("Poção de Vida", "consumable", slot=None,
                        max_stack=20, item_id="hp_potion")
        existing.stack = 1
        self.inv.items[0] = existing  # substitui 1 filler por uma poção já estocada
        cid = _make_corpse(self.ws, self.eid, [_loot_dict("hp_potion", stack=2)])

        result = self.ws.request_loot("s1", cid, take="all")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(existing.stack, 3)
        self.assertFalse(result["no_space"])


class TestLootQuestProgressViaSessionFlow(AuthPoolCleanupMixin, unittest.IsolatedAsyncioTestCase):
    """Progresso de collect_item precisa disparar DIRETO no fluxo de
    saque agora — o cliente não manda mais INV_SYNC depois de lootear
    (débito A4, request_loot já é quem muta o Inventory ao vivo)."""

    QID = "qcl_loot_flow_teste"

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login, get_msgs_of_type
        self._fake_login = fake_login
        self._get_msgs = get_msgs_of_type
        self.ws_server, self.mgr = make_session_manager()
        from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
        QUESTS[self.QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="collect_item", target="*",
                                     loot_item="arrow", count=2),),
            reward=QuestReward(xp=1),
        )

    async def asyncTearDown(self):
        await super().asyncTearDown()
        from content.quests_data import QUESTS
        QUESTS.pop(self.QID, None)

    async def test_saquear_item_de_quest_atualiza_progresso_sem_inv_sync(self):
        from shared.messages import encode, MsgType
        from engine.components import QuestLog
        session, fw = await self._fake_login(self.mgr, "s1", "user_loot_qp_a", 130, 374)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        cid = _make_corpse(self.ws_server, session.entity_id,
                           [_loot_dict("arrow", stack=2)])

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))

        updates = self._get_msgs(fw, MsgType.QUEST_UPDATE)
        self.assertEqual(len(updates), 1,
                         "saque de item de quest deveria disparar QUEST_UPDATE direto, sem INV_SYNC")
        self.assertEqual(updates[0]["active"][self.QID], [2])

    async def test_saque_sem_item_de_quest_nao_dispara_quest_update(self):
        from shared.messages import encode, MsgType
        from engine.components import QuestLog
        session, fw = await self._fake_login(self.mgr, "s1", "user_loot_qp_b", 130, 374)
        ql = self.ws_server.world.get_component(session.entity_id, QuestLog)
        ql.active[self.QID] = [0]
        cid = _make_corpse(self.ws_server, session.entity_id,
                           [_loot_dict("iron_sword")])   # não é o item da quest

        fw.sent.clear()
        await self.mgr.on_message(session, encode(
            MsgType.LOOT_REQUEST, {"corpse_id": cid, "take": "all"}))

        updates = self._get_msgs(fw, MsgType.QUEST_UPDATE)
        self.assertEqual(updates, [], "item alheio à quest não deveria disparar QUEST_UPDATE")


class TestNameCollisionGuard(unittest.TestCase):
    """Trava de integridade (11/08/2026, pedido do usuário): dois
    item_ids diferentes com o mesmo nome de exibição recusam o boot do
    servidor — decisão explícita do usuário (hard-fail, não só log)."""

    def test_catalogo_real_atual_nao_tem_colisao(self):
        from server.world_server import WorldServer
        WorldServer._check_item_name_collisions()  # não deveria levantar

    def test_colisao_sintetica_levanta_runtime_error(self):
        from server.world_server import WorldServer
        from content.crafting_data import MATERIALS

        MATERIALS["__teste_colisao__"] = lambda: Item(
            "Poção de Vida", "material", slot=None, max_stack=1,
            item_id="__fake_colisao_id__")
        try:
            with self.assertRaises(RuntimeError):
                WorldServer._check_item_name_collisions()
        finally:
            MATERIALS.pop("__teste_colisao__", None)


class TestCorpseTimerSemLootNaoAparece(unittest.TestCase):
    """Pedido do usuário (02/10/2026): "quando não trava nenhum loot,
    não aparece o corpo; quando tem loot, aparece por 45s; se o player
    lootear o corpo inteiro some na hora; se não saqueou tudo aguarda
    mais 30 segundos" — substitui o par antigo "120s mob / 4s minion".
    `_process_loot_drops` é quem decide se um corpse nasce; testado
    direto via `_death_handler.pending_loot`, sem precisar simular uma
    morte de mob inteira."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)

    def _drop(self, items: list, coins: int = 0) -> None:
        self.ws._death_handler.pending_loot.append({
            "mob_eid": -1, "owner_eid": self.eid,
            "items": items, "coins": coins,
            "tx": 130, "ty": 374, "map": self.ws._map_file,
            "mob_name": "Teste", "mob_race": "",
        })

    def test_sem_loot_nenhum_nao_cria_corpse(self):
        antes = set(self.ws._corpses.keys())
        self._drop(items=[], coins=0)
        self.ws._process_loot_drops(dt=0.1)
        self.assertEqual(set(self.ws._corpses.keys()), antes,
                         "mob sem item nem gold não deveria gerar corpse nenhum")

    def test_com_item_cria_corpse_com_timer_de_45s(self):
        antes = set(self.ws._corpses.keys())
        self._drop(items=[_loot_dict("hp_potion")], coins=0)
        # dt=0.0 — só registra o corpse novo, sem decair nada ainda (a
        # mesma chamada já faz as 2 coisas: registra E decai os
        # existentes, ver _process_loot_drops).
        self.ws._process_loot_drops(dt=0.0)
        novos = set(self.ws._corpses.keys()) - antes
        self.assertEqual(len(novos), 1)
        corpse = self.ws._corpses[novos.pop()]
        self.assertEqual(corpse["timer"], self.ws.CORPSE_TIMER_S)
        self.assertEqual(corpse["timer"], 45.0)

    def test_so_com_gold_tambem_cria_corpse(self):
        antes = set(self.ws._corpses.keys())
        self._drop(items=[], coins=10)
        self.ws._process_loot_drops(dt=0.1)
        self.assertEqual(len(set(self.ws._corpses.keys()) - antes), 1,
                         "gold sozinho (sem item nenhum) já é loot suficiente pra criar corpse")

    def test_saquear_tudo_faz_corpse_sumir_na_hora(self):
        antes = set(self.ws._corpses.keys())
        self._drop(items=[_loot_dict("hp_potion")], coins=5)
        self.ws._process_loot_drops(dt=0.0)
        cid = (set(self.ws._corpses.keys()) - antes).pop()

        result = self.ws.request_loot("s1", cid, take="all")
        self.assertIsNotNone(result)
        self.assertEqual(self.ws._corpses[cid]["timer"], self.ws.INSTANT_EXPIRE_S)
        self.assertEqual(self.ws._corpses[cid]["timer"], 0.0)

        # Confirma que de fato expira no tick seguinte (decay real).
        self.ws._process_loot_drops(dt=0.1)
        self.assertNotIn(cid, self.ws._corpses)

    def test_saquear_parcialmente_da_mais_30s(self):
        antes = set(self.ws._corpses.keys())
        self._drop(items=[_loot_dict("hp_potion"), _loot_dict("iron_sword")], coins=0)
        self.ws._process_loot_drops(dt=0.0)
        cid = (set(self.ws._corpses.keys()) - antes).pop()

        # Pega só 1 dos 2 itens -- sobra o outro.
        result = self.ws.request_loot("s1", cid, take="item", item_id="hp_potion")
        self.assertIsNotNone(result)
        self.assertTrue(self.ws._corpses[cid]["items"], "ainda devia sobrar 1 item")
        self.assertEqual(self.ws._corpses[cid]["timer"], self.ws.PARTIAL_LOOT_TIMER_S)
        self.assertEqual(self.ws._corpses[cid]["timer"], 30.0)


class TestCorpseNasceParaLootCondicionalSemLootComum(unittest.TestCase):
    """Bug real relatado pelo usuário (06/10/2026, achado investigando o
    §236 acima): quest "spider_venom" tem 70% de chance de dropar
    "Veneno de Aranha" ao matar uma Aranha -- mas a Aranha quase nunca
    dropa nada da tabela de loot COMUM dela (luvas/braçadeiras/capuz,
    cada um na faixa de 2-5%, 0% de ouro). Sem loot comum, nenhum corpse
    nascia (regra de 02/10/2026), e o sorteio dos 70% nunca chegava a
    RODAR -- a chance real observada de conseguir o item de quest era
    muito menor que 70%, limitada pela chance (baixa) de loot comum.

    Fix: `_process_loot_drops` cria o corpse mesmo com loot comum vazio
    quando o dono (ou algum membro do grupo dele) tem uma quest ativa
    que aceita este mob -- decisão explícita do usuário: o corpse pode
    abrir vazio se o sorteio falhar (30% de chance), é o preço de 70%
    valerem 70% de verdade."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)

    def _drop_vazio(self, mob_name: str = "Aranha", mob_race: str = "Fera") -> None:
        self.ws._death_handler.pending_loot.append({
            "mob_eid": -1, "owner_eid": self.eid,
            "items": [], "coins": 0,
            "tx": 130, "ty": 374, "map": self.ws._map_file,
            "mob_name": mob_name, "mob_race": mob_race,
        })

    def _give_spider_venom_quest(self) -> None:
        from engine.components import QuestLog
        ql = self.ws.world.get_component(self.eid, QuestLog)
        ql.active["spider_venom"] = [0]

    def test_sem_quest_ativa_continua_sem_criar_corpse(self):
        # Controle -- preserva a regra de 02/10/2026 pra quem não tem
        # nenhuma quest que aceite este mob.
        antes = set(self.ws._corpses.keys())
        self._drop_vazio()
        self.ws._process_loot_drops(dt=0.1)
        self.assertEqual(set(self.ws._corpses.keys()), antes)

    def test_com_quest_ativa_cria_corpse_mesmo_sem_loot_comum(self):
        self._give_spider_venom_quest()
        antes = set(self.ws._corpses.keys())
        self._drop_vazio()
        self.ws._process_loot_drops(dt=0.0)
        novos = set(self.ws._corpses.keys()) - antes
        self.assertEqual(len(novos), 1, "deveria criar corpse pro sorteio dos 70% poder rodar")
        cid = novos.pop()
        self.assertEqual(self.ws._corpses[cid]["items"], [])
        self.assertEqual(self.ws._corpses[cid]["coins"], 0)

    def test_quest_de_membro_do_grupo_tambem_conta(self):
        eid2 = spawn_player(self.ws, "s2", 130, 375)
        pid = self.ws._next_party_id
        self.ws._next_party_id += 1
        self.ws._parties[pid] = {"leader_eid": self.eid, "members": [self.eid, eid2]}
        self.ws._player_party_id[self.eid] = pid
        self.ws._player_party_id[eid2] = pid

        from engine.components import QuestLog
        ql2 = self.ws.world.get_component(eid2, QuestLog)
        ql2.active["spider_venom"] = [0]

        antes = set(self.ws._corpses.keys())
        self._drop_vazio()  # owner_eid = self.eid, que NÃO tem a quest
        self.ws._process_loot_drops(dt=0.0)
        self.assertEqual(len(set(self.ws._corpses.keys()) - antes), 1,
            "membro do grupo do dono com a quest ativa também deveria contar")

    def test_quest_com_objetivo_ja_completo_nao_forca_corpse(self):
        self._give_spider_venom_quest()
        from engine.components import QuestLog
        ql = self.ws.world.get_component(self.eid, QuestLog)
        ql.active["spider_venom"] = [3]  # count=3, já bateu a meta
        antes = set(self.ws._corpses.keys())
        self._drop_vazio()
        self.ws._process_loot_drops(dt=0.1)
        self.assertEqual(set(self.ws._corpses.keys()), antes)


class TestCorpseTimerRespeitaLootCondicionalDeQuest(unittest.TestCase):
    """Bug real relatado pelo usuário (06/10/2026): quest "spider_venom"
    (item condicional, Fase L1 25/07/2026, `quest_rolls[player_eid]` —
    separado do pote comum "items"/"coins") só aparecia quando a aranha
    também dropava outro item comum JUNTO. Looteando o item comum
    primeiro, o corpse "sumia" antes de conseguir pegar o item de quest.

    Causa raiz: o timer pós-saque (`request_loot`) decide "saqueou tudo,
    expira na hora" olhando só items/coins -- nunca quest_rolls. Com um
    item comum + 1 item condicional no mesmo corpse, saquear o comum
    esvaziava items/coins e expirava na hora, apagando o dict inteiro
    (quest_rolls incluso) antes do jogador clicar no item de quest."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.cid = _make_corpse(self.ws, self.eid, items=[_loot_dict("hp_potion")], coins=0)
        # Mesma mecânica de _resolve_conditional_loot_for: dict por
        # player_eid, plantado direto (sem precisar simular o roll de
        # verdade) -- mesmo padrão já usado por _make_corpse pro resto
        # do corpse. "veneno_aranha" (item da quest "spider_venom") vive
        # em content.quests_data.QUEST_ITEMS, não em item_table.ITEMS --
        # resolve_item_by_id() é o mesmo caminho que o servidor real usa
        # (_reconstruct_item), cobre os dois catálogos.
        from content.item_table import resolve_item_by_id
        from server.server_death_handler import _serialize_item
        _quest_item = _serialize_item(resolve_item_by_id("veneno_aranha"))
        self.ws._corpses[self.cid]["quest_rolls"][self.eid] = [_quest_item]

    def test_saquear_item_comum_nao_apaga_item_de_quest_pendente(self):
        result = self.ws.request_loot("s1", self.cid, take="item", item_id="hp_potion")
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ws._corpses[self.cid]["timer"], self.ws.PARTIAL_LOOT_TIMER_S,
            "ainda sobra o item de quest no pote pessoal -- não devia expirar na hora")

        # Confirma que o corpse de fato sobrevive ao tick seguinte...
        self.ws._process_loot_drops(dt=0.1)
        self.assertIn(self.cid, self.ws._corpses)

        # ...e que o item de quest continua lootável de verdade.
        result2 = self.ws.request_loot("s1", self.cid, take="item", item_id="veneno_aranha")
        self.assertIsNotNone(result2)
        self.assertTrue(result2["items"], "item de quest deveria ter sido concedido")

    def test_saquear_os_dois_ai_sim_expira_na_hora(self):
        self.ws.request_loot("s1", self.cid, take="item", item_id="hp_potion")
        self.ws.request_loot("s1", self.cid, take="item", item_id="veneno_aranha")
        self.assertEqual(self.ws._corpses[self.cid]["timer"], self.ws.INSTANT_EXPIRE_S)
        self.ws._process_loot_drops(dt=0.1)
        self.assertNotIn(self.cid, self.ws._corpses)


if __name__ == "__main__":
    unittest.main()
