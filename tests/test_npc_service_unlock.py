"""
tests/test_npc_service_unlock.py — trava de quest pra desbloquear
serviço de NPC (loja/treinador), pedido do usuário (04/10/2026):
"a loja só destravar depois que o player fizer uma quest... a mesma
coisa serve para qualquer outro NPC que interage, como treinamento."

Generalizada em 06/10/2026 (pedido do usuário, investigando por que
`turn_in_ids`/`unlock_quest_id` não funcionavam pro mercador "Fabian
Hardek"): a trava deixou de ser um campo específico de `Trainer` (ou um
mecanismo paralelo por `shop_id` em `content/merchant_data.py`) e virou
um componente único (`NPCUnlock`), anexável a QUALQUER NPC
(Merchant/Blacksmith/Mailbox/Trainer/QuestGiver/futuro), pra não
precisar reimplementar a trava toda vez que um sistema de interação
novo for criado.

Decisões fechadas com o usuário (AskUserQuestion):
- Treino de skill ganha validação REAL no servidor nesta mesma tarefa
  (antes era 100% client-trusted — `ui/trainer_system.py::_do_learn`
  mutava ouro/learned_skill_ids local, sem nenhuma mensagem de rede).
- "Finalizar várias quests" = só a ÚLTIMA quest de uma chain já
  existente (`QuestDef.requires`) — por isso a trava é 1 quest_id só
  (`NPCUnlock.quest_id`), nunca uma lista.
- Sem feedback de "bloqueado" — clicar no NPC travado abre o diálogo
  de quest direto (mesma priorização que `ui/trainer_system.py::
  _open_menu` já fazia pra quest completável; replicada em loja/
  ferreiro/correio, que não tinham menu intermediário).
- Trava por NPC INDIVIDUAL (não por shop_id/catálogo) — pedido
  explícito do usuário, pra valer igual em qualquer sistema futuro.

Cobre:
  1. `engine.quest_logic.has_unlocked`/`is_npc_unlocked` — ponto único
     de verdade.
  2. `WorldServer.process_shop_buy` — trava por NPC (`npc_eid`),
     autoritativa.
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
    NPCUnlock,
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
# 1b) is_npc_unlocked — ponto único de verdade GENÉRICO (06/10/2026),
#     lê NPCUnlock do NPC em vez de um campo próprio de cada capacidade.
# ─────────────────────────────────────────────────────────────────────────

class TestIsNpcUnlocked(unittest.TestCase):

    def setUp(self):
        from engine.world import World
        self.world = World()
        self.npc = self.world.create_entity()

    def test_sem_npcunlock_sempre_liberado(self):
        ql = QuestLog()
        self.assertTrue(quest_logic.is_npc_unlocked(self.world, self.npc, ql))
        self.assertTrue(quest_logic.is_npc_unlocked(self.world, self.npc, None))

    def test_com_npcunlock_exige_completed(self):
        self.world.add_component(self.npc, NPCUnlock(quest_id="alguma_quest"))
        ql = QuestLog()
        self.assertFalse(quest_logic.is_npc_unlocked(self.world, self.npc, ql))
        ql.completed.add("alguma_quest")
        self.assertTrue(quest_logic.is_npc_unlocked(self.world, self.npc, ql))

    def test_npcunlock_com_quest_id_vazio_sempre_liberado(self):
        self.world.add_component(self.npc, NPCUnlock(quest_id=""))
        self.assertTrue(quest_logic.is_npc_unlocked(self.world, self.npc, None))


# ─────────────────────────────────────────────────────────────────────────
# 2) Loja — trava autoritativa no servidor, por NPC individual
# ─────────────────────────────────────────────────────────────────────────

class TestShopBuyUnlockGate(unittest.TestCase):
    UNLOCK_QID = "nsu_quest_loja"

    def setUp(self):
        QUESTS[self.UNLOCK_QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        self.ws     = make_world_server()
        self.eid    = spawn_player(self.ws, "s1", 130, 374)
        self.inv    = self.ws.world.get_component(self.eid, Inventory)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.wallet.gold = 10_000
        self.inv.items.clear()

        self.merchant = self.ws.world.create_entity()
        self.ws.world.add_component(self.merchant, Merchant(shop_id="general"))
        self.ws.world.add_component(self.merchant, NPCUnlock(quest_id=self.UNLOCK_QID))

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)

    def test_compra_recusada_sem_completar_a_quest(self):
        result = self.ws.process_shop_buy("s1", "general", "Espada de Ferro", 1,
                                          item_id="iron_sword", npc_eid=self.merchant)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "locked")
        self.assertEqual(self.wallet.gold, 10_000, "ouro não deveria ter sido debitado")
        self.assertEqual(len(self.inv.items), 0)

    def test_compra_liberada_apos_completar_a_quest(self):
        ql = self.ws.world.get_component(self.eid, QuestLog)
        ql.completed.add(self.UNLOCK_QID)
        result = self.ws.process_shop_buy("s1", "general", "Espada de Ferro", 1,
                                          item_id="iron_sword", npc_eid=self.merchant)
        self.assertTrue(result["success"])
        self.assertEqual(self.wallet.gold, 10_000 - 55)

    def test_mercador_sem_npcunlock_continua_liberado(self):
        """Regressão -- mercador que nunca configurou a trava (todos, por
        padrão) continua funcionando exatamente como antes."""
        merchant2 = self.ws.world.create_entity()
        self.ws.world.add_component(merchant2, Merchant(shop_id="general"))
        result = self.ws.process_shop_buy("s1", "general", "Espada de Ferro", 1,
                                          item_id="iron_sword", npc_eid=merchant2)
        self.assertTrue(result["success"])

    def test_sem_npc_eid_conhecido_continua_liberado(self):
        """Compatibilidade -- cliente antigo/sem NPC conhecido (npc_eid
        default -1) nunca trava uma compra que antes passava."""
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
        self.ws.world.add_component(self.trainer_eid, Trainer(class_id="guerreiro"))
        self.ws.world.add_component(self.trainer_eid, NPCUnlock(quest_id=self.UNLOCK_QID))

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
        self.world.add_component(self.npc, Trainer(class_id="guerreiro"))
        self.world.add_component(self.npc, NPCUnlock(quest_id=self.UNLOCK_QID))

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
        self.world.add_component(self.merchant, NPCUnlock(quest_id=self.UNLOCK_QID))

        screen = pygame.Surface((800, 600))
        self.shop = ShopSystem(self.world, self.player, screen)
        self.shop.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)

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


# ─────────────────────────────────────────────────────────────────────────
# 7) Ferreiro — forja/reciclagem ganham a MESMA trava genérica (06/10/2026,
#    pedido explícito do usuário: "para trainer, shopping, forja,
#    reciclagem, correio e todos os outros sistemas"). Antes desta tarefa
#    nenhum dos dois tinha gate nenhum.
# ─────────────────────────────────────────────────────────────────────────

class TestForgeRecycleUnlockGate(unittest.TestCase):
    UNLOCK_QID = "nsu_quest_ferreiro"

    def setUp(self):
        QUESTS[self.UNLOCK_QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.wallet.gold = 10_000

        self.blacksmith = self.ws.world.create_entity()
        from engine.components import Blacksmith
        self.ws.world.add_component(self.blacksmith, Blacksmith(shop_id="blacksmith"))
        self.ws.world.add_component(self.blacksmith, NPCUnlock(quest_id=self.UNLOCK_QID))

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)

    def test_forja_recusada_sem_completar_a_quest(self):
        result = self.ws.craft_item("s1", "espada_afiada", npc_eid=self.blacksmith)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "locked")

    def test_forja_passa_da_trava_apos_completar_a_quest(self):
        """Só confirma que a trava LIBERA (reason != locked) -- o resto
        da validação (ouro/material reais) já é coberto por outros
        testes de craft_item, fora de escopo aqui."""
        ql = self.ws.world.get_component(self.eid, QuestLog)
        ql.completed.add(self.UNLOCK_QID)
        result = self.ws.craft_item("s1", "espada_afiada", npc_eid=self.blacksmith)
        self.assertNotEqual(result.get("reason"), "locked")

    def test_reciclagem_recusada_sem_completar_a_quest(self):
        result = self.ws.recycle_item("s1", 0, npc_eid=self.blacksmith)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "locked")

    def test_forja_sem_npc_eid_conhecido_passa_da_trava(self):
        """Compatibilidade -- sem npc_eid (cliente antigo), nunca trava
        uma forja que antes passava (gate é opt-in)."""
        result = self.ws.craft_item("s1", "espada_afiada")
        self.assertNotEqual(result.get("reason"), "locked")


# ─────────────────────────────────────────────────────────────────────────
# 8) Ferreiro — ui/crafting_system.py::_open_menu redireciona pra quest
#    em vez de abrir o menu (mesmo padrão de ShopSystem).
# ─────────────────────────────────────────────────────────────────────────

class TestBlacksmithOpenMenuRedirecionaParaQuest(unittest.TestCase):
    UNLOCK_QID = "nsu_quest_bs_client"

    def setUp(self):
        from engine.world import World
        from ui.crafting_system import BlacksmithSystem
        from engine.components import Blacksmith

        QUESTS[self.UNLOCK_QID] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, QuestLog())
        self.world.add_component(self.player, Inventory())
        self.world.add_component(self.player, Wallet(gold=0))

        self.blacksmith = self.world.create_entity()
        self.world.add_component(self.blacksmith, NPC(name="Ferreiro"))
        self.world.add_component(self.blacksmith, Blacksmith(shop_id="blacksmith"))
        self.world.add_component(self.blacksmith, NPCUnlock(quest_id=self.UNLOCK_QID))

        screen = pygame.Surface((800, 600))
        self.bs = BlacksmithSystem(self.world, self.player, screen)
        self.bs.set_ui_scale(1.0)

    def tearDown(self):
        QUESTS.pop(self.UNLOCK_QID, None)

    def test_travado_nao_abre_menu_e_pede_quest_dialog(self):
        self.bs._open_menu(self.blacksmith)
        self.assertEqual(self.bs._state, self.bs.STATE_CLOSED)
        req = self.world.get_component(self.player, NPCInteractionRequest)
        self.assertIsNotNone(req)
        self.assertEqual(req.kind, "open_quest_dialog")
        self.assertEqual(req.target_eid, self.blacksmith)

    def test_destravado_abre_o_menu_normalmente(self):
        ql = self.world.get_component(self.player, QuestLog)
        ql.completed.add(self.UNLOCK_QID)
        self.bs._open_menu(self.blacksmith)
        self.assertEqual(self.bs._state, self.bs.STATE_MENU)
        self.assertEqual(self.bs._bs_eid, self.blacksmith)


# ─────────────────────────────────────────────────────────────────────────
# 9) Mercador aceita entrega de quest (bug original que motivou esta
#    generalização, 06/10/2026 — "Fabian Hardek" não aceitava entrega de
#    "merchant_greeting" porque create_merchant nunca anexava QuestGiver).
# ─────────────────────────────────────────────────────────────────────────

class TestMerchantComQuestGiver(unittest.TestCase):

    def test_create_merchant_com_turn_in_ids_anexa_questgiver(self):
        from engine.entity_factory import create_merchant
        from engine.components import QuestGiver
        from engine.world import World
        world = World()
        eid = create_merchant(world, 10, 10, shop_id="general", name="Fabian Teste",
                              quest_ids=("merchant_greeting",),
                              turn_in_ids=("merchant_greeting",))
        qg = world.get_component(eid, QuestGiver)
        self.assertIsNotNone(qg, "mercador com quest_ids deveria ganhar QuestGiver")
        self.assertEqual(qg.turn_in_ids, ("merchant_greeting",))

    def test_create_merchant_so_com_turn_in_ids_sem_quest_ids_anexa_questgiver(self):
        """Bug real achado em produção (06/10/2026): o caso REAL do
        Fabian Hardek é só `turn_in_ids` (ele não DÁ nenhuma quest, só
        aceita a entrega de uma quest dada por outro NPC) — `quest_ids`
        fica vazio. O teste acima (com os 2 juntos) nunca pegou esse
        caso porque sempre passava `quest_ids` também. Guard antigo
        (`if quest_ids:`) deixava Fabian sem QuestGiver, sem marcador
        "?" e sem diálogo nenhum ao clicar com o direito."""
        from engine.entity_factory import create_merchant
        from engine.components import QuestGiver
        from engine.world import World
        world = World()
        eid = create_merchant(world, 10, 10, shop_id="general", name="Fabian Teste",
                              turn_in_ids=("merchant_greeting",))
        qg = world.get_component(eid, QuestGiver)
        self.assertIsNotNone(qg,
            "mercador só com turn_in_ids (sem quest_ids) também deveria ganhar QuestGiver")
        self.assertEqual(qg.turn_in_ids, ("merchant_greeting",))
        self.assertEqual(qg.quest_ids, ())

    def test_create_merchant_sem_quest_ids_nao_anexa_questgiver(self):
        """Regressão -- mercador comum (sem quest_ids) continua sem
        QuestGiver, exatamente como antes desta mudança."""
        from engine.entity_factory import create_merchant
        from engine.components import QuestGiver
        from engine.world import World
        world = World()
        eid = create_merchant(world, 10, 10, shop_id="general")
        self.assertIsNone(world.get_component(eid, QuestGiver))

    def test_mercador_com_questgiver_aparece_em_get_completable_quests(self):
        """Cobre o bug original fim-a-fim: um mercador que aceita a
        entrega de uma quest completável precisa aparecer como giver
        válido pro quest_logic, igual qualquer outro NPC."""
        from engine.entity_factory import create_merchant
        from engine.world import World
        import engine.quest_logic as ql_logic

        qid = "nsu_merchant_turn_in"
        QUESTS[qid] = QuestDef(
            title="Teste", description="d",
            objectives=(ObjectiveDef(type="kill", target="*", count=1),),
            reward=QuestReward(xp=1),
        )
        try:
            world = World()
            player = world.create_entity()
            ql = QuestLog()
            ql.active[qid] = [1]   # objetivo já cumprido (count=1 atingido)
            world.add_component(player, ql)

            merchant_eid = create_merchant(world, 10, 10, shop_id="general",
                                           name="Fabian Teste",
                                           quest_ids=(qid,), turn_in_ids=(qid,))
            completable = ql_logic.get_completable_quests(world, player, merchant_eid)
            self.assertIn(qid, completable)
        finally:
            QUESTS.pop(qid, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
