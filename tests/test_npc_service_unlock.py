"""
tests/test_npc_service_unlock.py — trava de quest pra desbloquear
serviço de NPC (loja/treinador), pedido do usuário (04/10/2026):
"a loja só destravar depois que o player fizer uma quest... a mesma
coisa serve para qualquer outro NPC que interage, como treinamento."

Decisões fechadas com o usuário (AskUserQuestion):
- Treino de skill ganha validação REAL no servidor nesta mesma tarefa
  (antes era 100% client-trusted — `ui/trainer_system.py::_do_learn`
  mutava ouro/learned_skill_ids local, sem nenhuma mensagem de rede).
- "Finalizar várias quests" = só a ÚLTIMA quest de uma chain já
  existente (`QuestDef.requires`) — por isso a trava é 1 quest_id só
  (`unlock_quest_id`), nunca uma lista.
- Sem feedback de "bloqueado" — clicar no NPC travado abre o diálogo
  de quest direto (mesma priorização que `ui/trainer_system.py::
  _open_menu` já fazia pra quest completável; replicada aqui pra loja,
  que não tinha menu intermediário).

Cobre:
  1. `engine.quest_logic.has_unlocked` — ponto único de verdade.
  2. `WorldServer.process_shop_buy` — trava por loja (`SHOPS[shop_id]
     ["unlock_quest_id"]`), autoritativa.
  3. `WorldServer.process_train_skill` — fluxo NOVO inteiro
     (autoridade real: classe, nível, ouro, trava de quest, concessão).
  4. `ui/trainer_system.py::_compute_options` — "Treinamento" não
     aparece como opção enquanto travado.
  5. `ui/shop_system.py::_try_open_merchant` — redireciona pra quest
     em vez de abrir a loja, enquanto travada.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from tests.helpers import make_world_server, spawn_player
from engine.components import (
    Inventory, Wallet, QuestLog, Trainer, Merchant, NPC, CharacterStats,
    PlayerSkills, CombatStats, PlayerControlled, NPCInteractionRequest,
)
from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
import engine.quest_logic as quest_logic


# ─────────────────────────────────────────────────────────────────────────
# 1) has_unlocked — ponto único de verdade
# ─────────────────────────────────────────────────────────────────────────

class TestHasUnlocked(unittest.TestCase):

    def test_sem_unlock_quest_id_sempre_liberado(self):
        ql = QuestLog()
        self.assertTrue(quest_logic.has_unlocked(ql, ""))
        self.assertTrue(quest_logic.has_unlocked(None, ""))

    def test_com_unlock_quest_id_exige_completed(self):
        ql = QuestLog()
        self.assertFalse(quest_logic.has_unlocked(ql, "alguma_quest"))
        ql.active["alguma_quest"] = [0]
        self.assertFalse(quest_logic.has_unlocked(ql, "alguma_quest"),
            "ativa mas não completa ainda não desbloqueia")
        ql.completed.add("alguma_quest")
        self.assertTrue(quest_logic.has_unlocked(ql, "alguma_quest"))

    def test_sem_questlog_e_trava_configurada_fica_bloqueado(self):
        self.assertFalse(quest_logic.has_unlocked(None, "alguma_quest"))


# ─────────────────────────────────────────────────────────────────────────
# 2) Loja — trava autoritativa no servidor
# ─────────────────────────────────────────────────────────────────────────

class TestShopBuyUnlockGate(unittest.TestCase):
    UNLOCK_QID = "nsu_quest_loja"

    def setUp(self):
        QUESTS[self.UNLOCK_QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        from content.merchant_data import SHOPS
        self._orig_general = dict(SHOPS["general"])
        SHOPS["general"]["unlock_quest_id"] = self.UNLOCK_QID

        self.ws     = make_world_server()
        self.eid    = spawn_player(self.ws, "s1", 130, 374)
        self.inv    = self.ws.world.get_component(self.eid, Inventory)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.wallet.gold = 10_000
        self.inv.items.clear()

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)
        from content.merchant_data import SHOPS
        SHOPS["general"] = self._orig_general

    def test_compra_recusada_sem_completar_a_quest(self):
        result = self.ws.process_shop_buy("s1", "general", "Espada de Ferro", 1,
                                          item_id="iron_sword")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "locked")
        self.assertEqual(self.wallet.gold, 10_000, "ouro não deveria ter sido debitado")
        self.assertEqual(len(self.inv.items), 0)

    def test_compra_liberada_apos_completar_a_quest(self):
        ql = self.ws.world.get_component(self.eid, QuestLog)
        ql.completed.add(self.UNLOCK_QID)
        result = self.ws.process_shop_buy("s1", "general", "Espada de Ferro", 1,
                                          item_id="iron_sword")
        self.assertTrue(result["success"])
        self.assertEqual(self.wallet.gold, 10_000 - 55)

    def test_loja_sem_unlock_quest_id_continua_liberada(self):
        """Regressão -- lojas que nunca configuraram a trava (todas, por
        padrão) continuam funcionando exatamente como antes."""
        from content.merchant_data import SHOPS
        SHOPS["general"].pop("unlock_quest_id", None)
        result = self.ws.process_shop_buy("s1", "general", "Espada de Ferro", 1,
                                          item_id="iron_sword")
        self.assertTrue(result["success"])


# ─────────────────────────────────────────────────────────────────────────
# 3) Treino de skill — fluxo NOVO inteiro, autoritativo
# ─────────────────────────────────────────────────────────────────────────

class TestTrainSkillAuthoritative(unittest.TestCase):
    UNLOCK_QID = "nsu_quest_treino"

    def setUp(self):
        QUESTS[self.UNLOCK_QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.char   = self.ws.world.get_component(self.eid, CharacterStats)
        self.ps     = self.ws.world.get_component(self.eid, PlayerSkills)
        self.wallet.gold = 10_000
        self.char.level  = 99   # alto o bastante pra nunca bater no gate de nível por acaso

        self.trainer_eid = self.ws.world.create_entity()
        self.ws.world.add_component(self.trainer_eid, NPC(name="Treinador de Teste"))
        self.ws.world.add_component(self.trainer_eid,
            Trainer(class_id="guerreiro", unlock_quest_id=self.UNLOCK_QID))

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)

    def _unlock(self):
        ql = self.ws.world.get_component(self.eid, QuestLog)
        ql.completed.add(self.UNLOCK_QID)

    def test_recusado_sem_completar_a_quest(self):
        result = self.ws.process_train_skill("s1", self.trainer_eid, "golpe_poderoso")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "locked")
        self.assertNotIn("golpe_poderoso", self.ps.learned_skill_ids)

    def test_sucesso_apos_completar_a_quest(self):
        self._unlock()
        gold_before = self.wallet.gold
        # "impacto" (custo 100g no catálogo) em vez de golpe_poderoso
        # (custo 0, primeira skill da classe) -- precisa de um custo
        # real > 0 pra provar que o ouro foi cobrado de verdade.
        result = self.ws.process_train_skill("s1", self.trainer_eid, "impacto")
        self.assertTrue(result["success"])
        self.assertIn("impacto", self.ps.learned_skill_ids)
        self.assertLess(self.wallet.gold, gold_before, "deveria ter cobrado o custo real")
        self.assertTrue(any(s and s.skill_id == "impacto" for s in self.ps.skills),
            "skill deveria ter entrado num slot da hotbar")

    def test_recusa_classe_errada_mesmo_desbloqueado(self):
        self._unlock()
        self.char.class_id = "mago"
        result = self.ws.process_train_skill("s1", self.trainer_eid, "golpe_poderoso")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "class")

    def test_recusa_nivel_insuficiente_mesmo_desbloqueado(self):
        self._unlock()
        self.char.level = 1
        from content.skill_config import SKILL_LEVEL_REQUIREMENTS
        # golpe_poderoso normalmente não tem requisito alto -- usa uma
        # skill com requisito real pra garantir o gate dispara.
        _high_req_skill = next((sid for sid, req in SKILL_LEVEL_REQUIREMENTS.items() if req > 1), None)
        if _high_req_skill is None:
            self.skipTest("nenhuma skill com level_req>1 no catálogo pra testar")
        result = self.ws.process_train_skill("s1", self.trainer_eid, _high_req_skill)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "level")

    def test_recusa_ouro_insuficiente_mesmo_desbloqueado(self):
        self._unlock()
        self.wallet.gold = 0
        result = self.ws.process_train_skill("s1", self.trainer_eid, "impacto")   # custo 100g
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "insufficient_gold")
        self.assertEqual(self.wallet.gold, 0)

    def test_recusa_skill_ja_aprendida(self):
        self._unlock()
        self.ps.learned_skill_ids.add("golpe_poderoso")
        gold_before = self.wallet.gold
        result = self.ws.process_train_skill("s1", self.trainer_eid, "golpe_poderoso")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "already_learned")
        self.assertEqual(self.wallet.gold, gold_before, "não deveria cobrar de novo")

    def test_recusa_trainer_invalido(self):
        self._unlock()
        _fake_eid = self.ws.world.create_entity()
        result = self.ws.process_train_skill("s1", _fake_eid, "golpe_poderoso")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "invalid_trainer")

    def test_trainer_sem_unlock_quest_id_continua_liberado(self):
        """Regressão -- treinador sem trava configurada (comportamento
        de sempre) continua funcionando sem precisar de nenhuma quest."""
        trainer2 = self.ws.world.create_entity()
        self.ws.world.add_component(trainer2, NPC(name="Treinador Livre"))
        self.ws.world.add_component(trainer2, Trainer(class_id="guerreiro"))
        result = self.ws.process_train_skill("s1", trainer2, "impacto")
        self.assertTrue(result["success"])


# ─────────────────────────────────────────────────────────────────────────
# 4) Cliente — TrainerSystem._compute_options respeita a trava
# ─────────────────────────────────────────────────────────────────────────

class TestTrainerComputeOptionsRespeitaTrava(unittest.TestCase):
    UNLOCK_QID = "nsu_quest_menu"

    def setUp(self):
        from engine.world import World
        from ui.quest_system import QuestSystem, QuestDialogSystem
        from ui.trainer_system import TrainerSystem

        QUESTS[self.UNLOCK_QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, CharacterStats(name="Testchar", class_id="guerreiro"))
        self.world.add_component(self.player, QuestLog())
        self.world.add_component(self.player, PlayerControlled())

        self.npc = self.world.create_entity()
        self.world.add_component(self.npc, NPC(name="Treinador"))
        self.world.add_component(self.npc,
            Trainer(class_id="guerreiro", unlock_quest_id=self.UNLOCK_QID))

        screen = pygame.Surface((800, 600))
        self.qs = QuestSystem(self.world, self.player)
        self.qs.set_ui_scale(1.0)
        self.dlg = QuestDialogSystem(self.world, self.player, screen, self.qs)
        self.dlg.set_ui_scale(1.0)
        self.trainer = TrainerSystem(self.world, self.player, screen)
        self.trainer.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)

    def test_treinamento_fora_das_opcoes_enquanto_travado(self):
        options = self.trainer._compute_options(self.npc)
        keys = {k for k, _ in options}
        self.assertNotIn("treinamento", keys)

    def test_treinamento_aparece_apos_completar_a_quest(self):
        ql = self.world.get_component(self.player, QuestLog)
        ql.completed.add(self.UNLOCK_QID)
        options = self.trainer._compute_options(self.npc)
        keys = {k for k, _ in options}
        self.assertIn("treinamento", keys)


# ─────────────────────────────────────────────────────────────────────────
# 5) Cliente — ShopSystem redireciona pra quest em vez de abrir a loja
# ─────────────────────────────────────────────────────────────────────────

class TestShopTryOpenMerchantRedirecionaParaQuest(unittest.TestCase):
    UNLOCK_QID = "nsu_quest_shop_client"

    def setUp(self):
        from engine.world import World
        from ui.shop_system import ShopSystem

        QUESTS[self.UNLOCK_QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        from content.merchant_data import SHOPS
        self._orig_general = dict(SHOPS["general"])
        SHOPS["general"]["unlock_quest_id"] = self.UNLOCK_QID

        from ui.ui_components import ShopUIState

        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, QuestLog())
        self.world.add_component(self.player, Inventory())
        self.world.add_component(self.player, Wallet(gold=0))
        self.world.add_component(self.player, ShopUIState())

        self.merchant = self.world.create_entity()
        self.world.add_component(self.merchant, NPC(name="Mercador"))
        self.world.add_component(self.merchant, Merchant(shop_id="general"))

        screen = pygame.Surface((800, 600))
        self.shop = ShopSystem(self.world, self.player, screen)
        self.shop.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)
        from content.merchant_data import SHOPS
        SHOPS["general"] = self._orig_general

    def test_travado_nao_abre_loja_e_pede_quest_dialog(self):
        self.shop._try_open_merchant(self.merchant)
        self.assertEqual(self.shop.open_merchant_id, -1,
            "loja não deveria ter aberto enquanto travada")
        req = self.world.get_component(self.player, NPCInteractionRequest)
        self.assertIsNotNone(req)
        self.assertEqual(req.kind, "open_quest_dialog")
        self.assertEqual(req.target_eid, self.merchant)

    def test_destravado_abre_a_loja_normalmente(self):
        ql = self.world.get_component(self.player, QuestLog)
        ql.completed.add(self.UNLOCK_QID)
        self.shop._try_open_merchant(self.merchant)
        self.assertEqual(self.shop.open_merchant_id, self.merchant)
        self.assertIsNone(self.world.get_component(self.player, NPCInteractionRequest))


# ─────────────────────────────────────────────────────────────────────────
# 6) Cliente — TrainerSystem._do_learn manda o eid do SERVIDOR, não o local
# ─────────────────────────────────────────────────────────────────────────
# Bug real reportado por tester (ERUDOM, 05/10/2026): "Interceptar aparece
# na lista, clico e não acontece nada" — nada a ver com a trava de quest
# (ele já tinha completado). Causa real: client/remote_entity_handlers.py::
# _spawn_remote_mob cria o NPC com um eid LOCAL novo (create_enemy()),
# diferente do eid que o SERVIDOR usa pra essa mesma entidade — o eid real
# do servidor fica só em RemoteEntityMeta.server_eid. _do_learn mandava
# self._tr_eid (local) direto como "npc_id" — o servidor nunca reconhecia
# esse id (process_train_skill faz world.get_component(npc_id, Trainer)
# contra o PRÓPRIO World dele), sempre recusava com reason="invalid_trainer",
# pra QUALQUER player, sempre — log de produção confirmou isso em 3
# sessões/eids diferentes do mesmo tester, inclusive após relogar.

class TestDoLearnMandaServerEidNaoLocal(unittest.TestCase):

    class _FakeNet:
        def __init__(self):
            self.sent = []

        def send(self, msg_type, payload):
            self.sent.append((msg_type, payload))

    def setUp(self):
        from engine.world import World
        from ui.trainer_system import TrainerSystem
        from engine.components import RemoteEntityMeta

        self.world = World()
        self.player = self.world.create_entity()
        _cs = CharacterStats(name="Testchar", class_id="guerreiro")
        _cs.level = 8
        self.world.add_component(self.player, _cs)
        self.world.add_component(self.player, PlayerSkills())
        self.world.add_component(self.player, Wallet(gold=1000))
        self.world.add_component(self.player, PlayerControlled())

        # eid LOCAL do NPC (o que create_enemy() teria criado) é
        # propositalmente DIFERENTE do server_eid simulado abaixo — é
        # exatamente essa diferença que o bug ignorava.
        self.npc_local_eid = self.world.create_entity()
        self.world.add_component(self.npc_local_eid, NPC(name="Avido Faseo"))
        self.world.add_component(self.npc_local_eid, Trainer(class_id="guerreiro"))
        self.world.add_component(self.npc_local_eid, RemoteEntityMeta(server_eid=9999))

        screen = pygame.Surface((800, 600))
        self.trainer = TrainerSystem(self.world, self.player, screen)
        self.trainer.set_ui_scale(1.0)
        self.trainer._net = self._FakeNet()
        self.trainer._tr_eid = self.npc_local_eid
        self.trainer._tr_class_id = "guerreiro"

    def test_do_learn_manda_server_eid_nao_o_eid_local(self):
        self.trainer._do_learn("interceptar")
        self.assertEqual(len(self.trainer._net.sent), 1)
        _msg_type, payload = self.trainer._net.sent[0]
        self.assertEqual(payload["npc_id"], 9999,
            "deveria mandar RemoteEntityMeta.server_eid, não o eid local do mirror")
        self.assertNotEqual(payload["npc_id"], self.npc_local_eid)

    def test_do_learn_sem_remote_entity_meta_nao_manda_nada(self):
        # Guarda de regressão: sem RemoteEntityMeta (ex: NPC local nunca
        # chegou via rede), nunca manda um npc_id "chutado" (-1/local) —
        # melhor não mandar nada do que mandar id errado de novo.
        from engine.components import RemoteEntityMeta
        self.world.remove_component(self.npc_local_eid, RemoteEntityMeta)
        self.trainer._do_learn("interceptar")
        self.assertEqual(len(self.trainer._net.sent), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
