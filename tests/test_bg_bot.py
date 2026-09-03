"""
tests/test_bg_bot.py — Bot 100% servidor da fila BG (16/08/2026, pedido
do usuário: testar a BG sem precisar de 4 humanos, e deixar players
reais jogarem entre si quando possível). Ver `server/
bg_queue_processor.py` (docstring do módulo), `server/bot_ai_system.py`,
`arquitetura/PROBLEMAS_ARQUITETURA.md`.

Escopo: só modo 2v2 (decisão do usuário) — 3v3/5v5 continuam com
`_bg_try_pack` de sempre, cobertos em test_bg_queue.py.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import time

from tests.helpers import make_world_server, spawn_player
from tests.test_bg_queue import _make_group
from server.bg_queue_processor import BG_BOT_BACKFILL_WAIT_S, BG_BOT_LEVEL
from server.bot_ai_system import BotAISystem, BOT_ENGAGE_RANGE_TILES
from engine.components import (BotControlled, Faction, CombatStats, PlayerSkills,
                               TileMovement, CombatState, GhostState)


def _age_token(ws, mode_id: str, val, kind: str, seconds_ago: float) -> None:
    """Reescreve o join_ts do token na fila pra simular espera (16/08/2026
    — token virou 3-tupla com timestamp, ver server/bg_queue_processor.py)."""
    q = ws._bg_queues[mode_id]
    for i, t in enumerate(q):
        if t[0] == kind and t[1] == val:
            q[i] = (t[0], t[1], time.time() - seconds_ago)
            return
    raise AssertionError("token não encontrado na fila")


class TestBgTryPackWithBots(unittest.TestCase):
    """`_bg_try_pack_with_bots` — a política nova de pareamento do 2v2."""

    def setUp(self):
        self.ws = make_world_server()

    def test_solo_sozinho_nao_fecha_antes_do_timeout(self):
        solo = spawn_player(self.ws, "b_solo1", 130, 374)
        self.ws.request_bg_queue_join(solo, "2v2")
        self.assertIsNone(self.ws._bg_try_pack_with_bots("2v2", time.time()))

    def test_solo_sozinho_fecha_com_bots_apos_o_timeout(self):
        solo = spawn_player(self.ws, "b_solo2", 130, 374)
        self.ws.request_bg_queue_join(solo, "2v2")
        _age_token(self.ws, "2v2", solo, "solo", BG_BOT_BACKFILL_WAIT_S + 1)
        packed = self.ws._bg_try_pack_with_bots("2v2", time.time())
        self.assertIsNotNone(packed)
        side_a, side_b = packed
        self.assertEqual(side_a[0][1], solo)
        self.assertEqual(side_b, [])

    def test_2_solos_fecham_imediato_um_contra_o_outro_sem_esperar(self):
        """O pedido central do usuário: 2 solos estranhos NUNCA no mesmo
        time — sempre adversários, cada um com parceiro bot."""
        s1 = spawn_player(self.ws, "b_solo3", 130, 374)
        s2 = spawn_player(self.ws, "b_solo4", 131, 374)
        self.ws.request_bg_queue_join(s1, "2v2")
        self.ws.request_bg_queue_join(s2, "2v2")
        packed = self.ws._bg_try_pack_with_bots("2v2", time.time())
        self.assertIsNotNone(packed)
        side_a, side_b = packed
        self.assertEqual(side_a[0][1], s1)
        self.assertEqual(side_b[0][1], s2)

    def test_grupo_de_2_sozinho_tambem_espera_o_timeout_antes_de_bots(self):
        """Grupo já é time completo, mas ainda assim espera a MESMA
        janela que um solo esperaria — dá chance de outro humano/grupo
        aparecer antes de desistir e fechar só com bots."""
        duo = _make_group(self.ws, "b_duo", 2)
        self.ws.request_bg_queue_join(duo[0], "2v2")
        party_id = self.ws.get_party_id_of(duo[0])
        self.assertIsNone(self.ws._bg_try_pack_with_bots("2v2", time.time()))
        _age_token(self.ws, "2v2", party_id, "party", BG_BOT_BACKFILL_WAIT_S + 1)
        packed = self.ws._bg_try_pack_with_bots("2v2", time.time())
        self.assertIsNotNone(packed)
        side_a, side_b = packed
        self.assertEqual(side_a[0][1], party_id)
        self.assertEqual(side_b, [])


class TestSpawnBotPlayer(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_bot_tem_componentes_de_combate_esperados(self):
        """Classe sorteada (16/08/2026) — a skill inicial varia por
        classe (INSTANCE_SKILL_UNLOCK_ORDER[class_id][0]), não mais
        sempre "golpe_poderoso"."""
        from content.skill_config import INSTANCE_SKILL_UNLOCK_ORDER
        from engine.components import CharacterStats
        eid = self.ws._spawn_bot_player()
        self.assertIsNotNone(self.ws.world.get_component(eid, BotControlled))
        self.assertIsNotNone(self.ws.world.get_component(eid, CombatStats))
        self.assertIsNotNone(self.ws.world.get_component(eid, CombatState))
        char = self.ws.world.get_component(eid, CharacterStats)
        self.assertIn(char.class_id, ("guerreiro", "arqueiro", "mago"))
        ps = self.ws.world.get_component(eid, PlayerSkills)
        self.assertIsNotNone(ps)
        skill_ids = {s.skill_id for s in ps.skills if s is not None}
        self.assertIn(INSTANCE_SKILL_UNLOCK_ORDER[char.class_id][0], skill_ids)
        self.assertIn(eid, self.ws._player_eid_to_sid)

    def test_bot_registra_session_falsa_quando_session_manager_existe(self):
        from server.session import SessionManager
        self.ws._session_manager = SessionManager(self.ws)
        eid = self.ws._spawn_bot_player()
        sid = self.ws._player_eid_to_sid[eid]
        bot_sess = self.ws._session_manager._sessions.get(sid)
        self.assertIsNotNone(bot_sess,
            "sem Session falsa registrada, o bot fica invisível pros outros players "
            "(_build_update_for_session só descobre players via self._sessions)")
        self.assertTrue(bot_sess.authenticated)
        self.assertEqual(bot_sess.entity_id, eid)

    def test_despawn_remove_entidade_e_session_falsa(self):
        from server.session import SessionManager
        self.ws._session_manager = SessionManager(self.ws)
        eid = self.ws._spawn_bot_player()
        sid = self.ws._player_eid_to_sid[eid]
        self.ws._despawn_bot_player(eid)
        self.assertNotIn(eid, self.ws._player_eid_to_sid)
        self.assertNotIn(sid, self.ws._session_manager._sessions)
        self.assertIsNone(self.ws.world.get_component(eid, BotControlled))


class TestTickBgQueueBotBackfill(unittest.TestCase):
    """Ponta-a-ponta: `_tick_bg_queue` de verdade cria e aceita os bots."""

    def setUp(self):
        self.ws = make_world_server()

    def test_solo_sozinho_apos_timeout_forma_partida_com_3_bots(self):
        solo = spawn_player(self.ws, "e2e_solo", 130, 374)
        self.ws.request_bg_queue_join(solo, "2v2")
        _age_token(self.ws, "2v2", solo, "solo", BG_BOT_BACKFILL_WAIT_S + 1)
        self.ws._tick_bg_queue()

        self.assertEqual(len(self.ws._bg_active_matches), 1)
        match = next(iter(self.ws._bg_active_matches.values()))
        self.assertEqual(len(match["invited_a"]) + len(match["invited_b"]), 4)
        self.assertIn(solo, match["invited_a"] + match["invited_b"])
        # O humano ainda não aceitou (precisa mandar BG_MATCH_ACCEPT) --
        # só os 3 bots (1 parceiro + 2 adversários) já entraram sozinhos.
        accepted = match["team_a"] + match["team_b"]
        self.assertNotIn(solo, accepted)
        bot_eids = [eid for eid in accepted
                    if self.ws.world.get_component(eid, BotControlled) is not None]
        self.assertEqual(len(bot_eids), 3)
        self.assertEqual(len(accepted), 3)

    def test_2_solos_formam_partida_1_bot_cada(self):
        s1 = spawn_player(self.ws, "e2e_s1", 130, 374)
        s2 = spawn_player(self.ws, "e2e_s2", 131, 374)
        self.ws.request_bg_queue_join(s1, "2v2")
        self.ws.request_bg_queue_join(s2, "2v2")
        self.ws._tick_bg_queue()

        self.assertEqual(len(self.ws._bg_active_matches), 1)
        match = next(iter(self.ws._bg_active_matches.values()))
        self.assertEqual(set(match["invited_a"] + match["invited_b"]) & {s1, s2}, {s1, s2})
        self.assertNotEqual(s1 in match["invited_a"], s2 in match["invited_a"],
            "s1 e s2 nunca podem estar do MESMO lado")
        bot_eids = [eid for eid in match["invited_a"] + match["invited_b"]
                    if self.ws.world.get_component(eid, BotControlled) is not None]
        self.assertEqual(len(bot_eids), 2)


class TestBotSkillReady(unittest.TestCase):
    """`WorldServer._skill_ready` (25/08/2026): bot consulta o cooldown
    AUTORITATIVO do servidor antes de decidir usar uma skill — nunca o
    campo local `PlayerSkills.skill.current_cooldown` (bot não tem
    cliente pra atualizá-lo via SKILL_RESULT, então ficava retentando a
    MESMA skill em cooldown real pra sempre, sem cair pro auto-attack;
    bug confirmado em log real de playtest — arqueiro martelando
    `picada_escorpiao` a cada ~0,35s por minutos, sempre rejeitado pelo
    servidor)."""

    def setUp(self):
        self.ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(self.ws, 8, 8, 60, 60)
        self.bot = self.ws._spawn_bot_player(class_id="guerreiro")
        from tests.helpers import clear_login_immunity
        clear_login_immunity(self.ws, self.bot)
        self.ws.world.add_component(self.bot, Faction(faction_id="arena_time_a"))
        from engine.utils import snap_to_tile
        snap_to_tile(self.ws.world, self.bot, 20, 20, carry_prev=False)
        self.enemy = spawn_player(self.ws, "enemy_sr", 21, 20)
        self.ws.world.add_component(self.enemy, Faction(faction_id="arena_time_b"))

    def test_bot_nao_reenvia_skill_em_cooldown_real_e_cai_pro_auto_attack(self):
        import time as _t
        # Servidor marca golpe_poderoso como usado agora com CD efetivo de
        # 5s (mesmo mecanismo real de talento/uso anterior) —
        # PlayerSkills.skill.current_cooldown do bot continua 0, exatamente
        # como um bot de verdade em produção (nunca processa SKILL_RESULT).
        self.ws._skill_last_used[(self.bot, "golpe_poderoso")] = _t.time()
        self.ws._skill_effective_cd[(self.bot, "golpe_poderoso")] = 5.0

        BotAISystem.update(self.ws, dt=999.0)

        pending = [r for r in self.ws._pending_skill_requests if r["player_eid"] == self.bot]
        self.assertFalse(any(r["sid"] == "golpe_poderoso" for r in pending),
                         "bot não deveria reenviar uma skill que o servidor sabe estar em cooldown")
        cs = self.ws.world.get_component(self.bot, CombatState)
        self.assertEqual(cs.target_entity_id, self.enemy,
                         "sem skill disponível, bot deveria cair pro auto-attack")


class TestBotAISystem(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(self.ws, 8, 8, 60, 60)
        # class_id="guerreiro" fixo (16/08/2026) — classe do bot agora é
        # sorteada em produção, mas estes testes precisam de skill/
        # comportamento determinístico ("golpe_poderoso" especificamente).
        self.bot = self.ws._spawn_bot_player(class_id="guerreiro")
        from tests.helpers import clear_login_immunity
        clear_login_immunity(self.ws, self.bot)
        self.ws.world.add_component(self.bot, Faction(faction_id="arena_time_a"))
        from engine.utils import snap_to_tile
        snap_to_tile(self.ws.world, self.bot, 20, 20, carry_prev=False)

    def _spawn_enemy(self, tx, ty):
        enemy = spawn_player(self.ws, f"enemy_{tx}_{ty}", tx, ty)
        self.ws.world.add_component(enemy, Faction(faction_id="arena_time_b"))
        return enemy

    def test_bot_engaja_alvo_em_alcance_com_skill(self):
        enemy = self._spawn_enemy(21, 20)
        BotAISystem.update(self.ws, dt=999.0)
        ps = self.ws.world.get_component(self.bot, PlayerSkills)
        # golpe_poderoso foi enfileirado (queue_skill) -- confirma via
        # _pending_skill_requests, ponto único de verdade do request.
        pending = [r for r in self.ws._pending_skill_requests if r["player_eid"] == self.bot]
        self.assertTrue(any(r["sid"] == "golpe_poderoso" and r["tid"] == enemy for r in pending))

    def test_bot_persegue_alvo_fora_de_alcance(self):
        # Dentro de BOT_LANE_AWARENESS_RADIUS_TILES (8) mas fora do
        # alcance efetivo do guerreiro (1 tile, `_effective_engage_range`
        # — 26/08/2026, antes era 1 constante genérica de 2 pra todas as
        # classes) — perto o bastante pro bot perceber e perseguir, longe
        # o bastante pra não engajar direto.
        enemy = self._spawn_enemy(26, 20)
        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        pos_after = (tm_after.current_tile_x, tm_after.current_tile_y)
        self.assertNotEqual(pos_before, pos_after, "bot deveria ter andado 1 tile em direção ao inimigo")
        dist_before = max(abs(pos_before[0]-26), abs(pos_before[1]-20))
        dist_after  = max(abs(pos_after[0]-26),  abs(pos_after[1]-20))
        self.assertLess(dist_after, dist_before)

    def test_bot_sem_inimigo_segue_a_propria_lane(self):
        # "target_tile" já chega normalizado por engine/map_loader.py
        # (SEMPRE list[tuple[int,int]], nunca o par cru `[x,y]` do JSON
        # — ver docstring de _push_lane) — usar a forma crua aqui
        # escondia o bug real relatado pelo usuário (17/08/2026, ver
        # tests abaixo), porque nunca exercitava a forma de verdade que
        # world_server._minion_lanes guarda em produção.
        from engine.components import BotControlled as _BCLane
        self.ws.world.get_component(self.bot, _BCLane).lane_id = "mid"
        self.ws._minion_lanes[self.ws.get_player_map(
            self.ws._player_eid_to_sid[self.bot])] = [{
            "faction": "arena_time_a", "lane_id": "mid",
            "spawn_tile": (20, 20), "target_tile": [(40, 20)],
        }]
        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        pos_after = (tm_after.current_tile_x, tm_after.current_tile_y)
        self.assertNotEqual(pos_before, pos_after)
        self.assertGreater(pos_after[0], pos_before[0], "deveria ter andado em direção a x=40")

    def test_bot_segue_lane_com_curva_varios_waypoints(self):
        """Bug real relatado pelo usuário (17/08/2026): lane com curva
        (2+ waypoints, ex: top/bot no mapa real) travava o tick inteiro
        com TypeError — `_lane_goal` (v1) reimplementava um parse
        próprio que nunca batia com a forma normalizada de verdade
        (`list[tuple[int,int]]`), devolvendo uma tupla-de-tuplas como
        goal em vez do ÚLTIMO waypoint. v2 (`_push_lane`) usa
        `WorldServer._compute_lane_route_tail`, que já consome essa
        forma corretamente."""
        from engine.components import BotControlled as _BCLane2
        self.ws.world.get_component(self.bot, _BCLane2).lane_id = "top"
        self.ws._minion_lanes[self.ws.get_player_map(
            self.ws._player_eid_to_sid[self.bot])] = [{
            "faction": "arena_time_a", "lane_id": "top",
            "spawn_tile": (20, 20), "target_tile": [(25, 20), (40, 20)],
        }]
        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)  # não deveria levantar TypeError
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        pos_after = (tm_after.current_tile_x, tm_after.current_tile_y)
        self.assertNotEqual(pos_before, pos_after)
        self.assertGreater(pos_after[0], pos_before[0], "deveria andar em direção ao ÚLTIMO waypoint (40,20)")

    def test_bot_nao_age_morto(self):
        gst = self.ws.world.get_component(self.bot, GhostState)
        gst.is_dead = True
        enemy = self._spawn_enemy(21, 20)
        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        self.assertEqual(pos_before, (tm_after.current_tile_x, tm_after.current_tile_y))
        pending = [r for r in self.ws._pending_skill_requests if r["player_eid"] == self.bot]
        self.assertEqual(pending, [])

    def test_guerreiro_a_2_tiles_nao_engaja_ainda(self):
        """Bug real relatado pelo usuário (26/08/2026): guerreiro parava a
        2 tiles do alvo e nunca acertava golpe_poderoso/auto-attack (os
        dois exigem Chebyshev ≤1 de verdade — `_resolve_target`/
        `_resolve_auto_attack`). Antes, `BOT_ENGAGE_RANGE_TILES=2`
        genérico fazia o guerreiro parar 1 tile longe demais e ficar
        parado pra sempre. Agora `_effective_engage_range` devolve 1 pro
        guerreiro (sem skill de longo alcance) — a 2 tiles ele CONTINUA
        avançando em vez de tentar brigar."""
        enemy = self._spawn_enemy(22, 20)  # dist=2
        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)
        pending = [r for r in self.ws._pending_skill_requests if r["player_eid"] == self.bot]
        self.assertEqual(pending, [], "a 2 tiles o guerreiro não deveria tentar nenhuma skill ainda")
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        pos_after = (tm_after.current_tile_x, tm_after.current_tile_y)
        self.assertNotEqual(pos_before, pos_after, "deveria continuar avançando, não parar a 2 tiles")

    def test_mago_engaja_a_6_tiles_sem_precisar_chegar_mais_perto(self):
        """Mago não devia andar até quase melee pra usar uma skill de
        alcance 6 (`bola_de_fogo.cast_range`, content/skill_config.py) —
        `_effective_engage_range` lê o cast_range da 1ª skill ofensiva
        aprendida em vez do raio genérico de 1/2 tiles."""
        from server.bot_ai_system import BotAISystem as _BotAI2
        mago = self.ws._spawn_bot_player(class_id="mago")
        from tests.helpers import clear_login_immunity as _cli2
        _cli2(self.ws, mago)
        self.ws.world.add_component(mago, Faction(faction_id="arena_time_a"))
        from engine.utils import snap_to_tile as _stt2
        _stt2(self.ws.world, mago, 20, 20, carry_prev=False)

        enemy = self._spawn_enemy(26, 20)  # dist=6, exatamente o cast_range da Bola de Fogo
        tm_before = self.ws.world.get_component(mago, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        _BotAI2.update(self.ws, dt=999.0)
        pending = [r for r in self.ws._pending_skill_requests
                   if r["player_eid"] == mago and r["sid"] == "bola_de_fogo"]
        self.assertTrue(any(r["tid"] == enemy for r in pending),
                        "mago deveria ter tentado Bola de Fogo já a 6 tiles")
        tm_after = self.ws.world.get_component(mago, TileMovement)
        pos_after = (tm_after.current_tile_x, tm_after.current_tile_y)
        self.assertEqual(pos_before, pos_after,
                         "mago não devia ter andado — já estava dentro do alcance da skill")


class TestBotAdvanceTowardRejectedMove(unittest.TestCase):
    """Rota em cache desincronizava da posição real quando move_player
    rejeitava o passo (bug real relatado pelo usuário: bot "travando" e
    depois "pulando" vários tiles de uma vez) — ver server/
    bot_ai_system.py::_advance_toward, `bot.path.pop(0)` acontecia ANTES
    de checar o retorno de move_player."""

    def setUp(self):
        self.ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(self.ws, 8, 8, 60, 60)
        self.bot = self.ws._spawn_bot_player()
        from tests.helpers import clear_login_immunity
        clear_login_immunity(self.ws, self.bot)
        from engine.utils import snap_to_tile
        snap_to_tile(self.ws.world, self.bot, 20, 20, carry_prev=False)

    def test_passo_rejeitado_limpa_a_rota_em_vez_de_desincronizar(self):
        bot_comp = self.ws.world.get_component(self.bot, BotControlled)
        # Rota em cache cujo PRIMEIRO passo não é adjacente à posição real
        # (move_player rejeita por orçamento de distância) — mesmo efeito
        # de um tile ocupado/bloqueado no instante do MOVE.
        bot_comp.path = [(25, 20), (26, 20)]
        bot_comp.path_goal = (26, 20)
        tm = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm.current_tile_x, tm.current_tile_y)

        BotAISystem._advance_toward(self.ws, self.bot, bot_comp, tm, (26, 20))

        self.assertEqual((tm.current_tile_x, tm.current_tile_y), pos_before,
                         "posição real não deveria mudar num passo rejeitado")
        self.assertIsNone(bot_comp.path,
                          "rota deveria ser descartada pra recalcular do zero, "
                          "não continuar desincronizada com a posição real")


class TestBotInitialShopping(unittest.TestCase):
    """`BotAISystem.do_initial_shopping` — kit por classe, comprado E
    equipado (16/08/2026, pedido do usuário)."""

    def setUp(self):
        self.ws = make_world_server()

    def _shop_for_class(self, class_id: str):
        from server.instance_progression import enter_normalized_progression
        from engine.components import Wallet, Equipment
        eid = spawn_player(self.ws, f"shop_{class_id}", 20, 20, class_id=class_id)
        enter_normalized_progression(self.ws, eid)
        BotAISystem.do_initial_shopping(self.ws, eid)
        wallet = self.ws.world.get_component(eid, Wallet)
        eq = self.ws.world.get_component(eid, Equipment)
        equipped_names = {slot: item.name for slot, item in eq.slots.items() if item}
        return wallet.gold, equipped_names

    def test_guerreiro_compra_e_equipa_espada_e_escudo_sem_estourar_gold(self):
        gold_left, equipped = self._shop_for_class("guerreiro")
        self.assertGreaterEqual(gold_left, 0)
        self.assertEqual(equipped.get("mainhand"), "Espada de Ferro")
        self.assertEqual(equipped.get("offhand"), "Escudo de Ferro")

    def test_arqueiro_compra_e_equipa_arco_e_aljava_sem_estourar_gold(self):
        gold_left, equipped = self._shop_for_class("arqueiro")
        self.assertGreaterEqual(gold_left, 0)
        self.assertEqual(equipped.get("mainhand"), "Arco do Caçador")
        self.assertEqual(equipped.get("offhand"), "Aljava de Batalha")

    def test_mago_compra_e_equipa_varinha_e_manto_sem_estourar_gold(self):
        gold_left, equipped = self._shop_for_class("mago")
        self.assertGreaterEqual(gold_left, 0)
        self.assertEqual(equipped.get("mainhand"), "Varinha Arcana")
        self.assertEqual(equipped.get("chest"), "Manto de Seda")

    def test_nada_sobra_desequipado_na_mochila(self):
        from engine.components import Inventory
        for class_id in ("guerreiro", "arqueiro", "mago"):
            eid = spawn_player(self.ws, f"noinv_{class_id}", 20, 20, class_id=class_id)
            from server.instance_progression import enter_normalized_progression
            enter_normalized_progression(self.ws, eid)
            BotAISystem.do_initial_shopping(self.ws, eid)
            inv = self.ws.world.get_component(eid, Inventory)
            self.assertEqual([i for i in inv.items if i], [],
                             f"{class_id}: item comprado deveria estar equipado, não na mochila")


class TestBotLaneDraw(unittest.TestCase):
    """Sorteio de lane por time (16/08/2026) — 2 bots do MESMO time
    nunca caem na mesma lane."""

    def test_bots_do_mesmo_time_nunca_sorteiam_a_mesma_lane(self):
        ws = make_world_server()
        s1 = spawn_player(ws, "ld_s1", 130, 374)
        s2 = spawn_player(ws, "ld_s2", 131, 374)
        ws.request_bg_queue_join(s1, "2v2")
        ws.request_bg_queue_join(s2, "2v2")
        # 2 solos viram adversários (1 bot parceiro cada) — mas rodar
        # várias partidas garante pelo menos 1 caso de 2 bots no MESMO
        # time (backfill: se só 1 solo aparece, o outro time vira 2
        # bots). Força esse caso reaproveitando o backfill isolado.
        ws2 = make_world_server()
        solo = spawn_player(ws2, "ld_solo", 130, 374)
        ws2.request_bg_queue_join(solo, "2v2")
        from server.bg_queue_processor import BG_BOT_BACKFILL_WAIT_S
        _age_token(ws2, "2v2", solo, "solo", BG_BOT_BACKFILL_WAIT_S + 1)
        ws2._tick_bg_queue()

        match = next(iter(ws2._bg_active_matches.values()))
        for side_eids in (match["invited_a"], match["invited_b"]):
            lanes = [ws2.world.get_component(eid, BotControlled).lane_id
                    for eid in side_eids
                    if ws2.world.get_component(eid, BotControlled) is not None]
            self.assertEqual(len(lanes), len(set(lanes)),
                             f"time com bots repetindo lane: {lanes}")
            for lane_id in lanes:
                self.assertIn(lane_id, ("top", "mid", "bot"))


class TestBotPushLaneV2(unittest.TestCase):
    """Espera 10s após a wave + segue a MESMA rota que o minion usa
    (16/08/2026, pedido do usuário)."""

    def setUp(self):
        self.ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(self.ws, 8, 8, 60, 60)
        self.bot = self.ws._spawn_bot_player(class_id="guerreiro")
        from tests.helpers import clear_login_immunity
        clear_login_immunity(self.ws, self.bot)
        self.ws.world.add_component(self.bot, Faction(faction_id="arena_time_a"))
        from engine.utils import snap_to_tile
        snap_to_tile(self.ws.world, self.bot, 20, 20, carry_prev=False)
        bot_c = self.ws.world.get_component(self.bot, BotControlled)
        bot_c.lane_id = "mid"
        self.map_file = self.ws.get_player_map(self.ws._player_eid_to_sid[self.bot])
        self.ws._minion_lanes[self.map_file] = [{
            "faction": "arena_time_a", "lane_id": "mid",
            "spawn_tile": (20, 20), "target_tile": [(40, 20)],
        }]

    def test_bot_nao_anda_antes_do_push_start_at(self):
        bot_c = self.ws.world.get_component(self.bot, BotControlled)
        bot_c.push_start_at = time.time() + 999.0  # bem no futuro
        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        self.assertEqual(pos_before, (tm_after.current_tile_x, tm_after.current_tile_y),
                         "bot não deveria empurrar lane antes de push_start_at")

    def test_bot_anda_depois_do_push_start_at_pela_rota_calculada(self):
        bot_c = self.ws.world.get_component(self.bot, BotControlled)
        bot_c.push_start_at = time.time() - 1.0  # já passou
        expected_tail = self.ws._compute_lane_route_tail(
            self.map_file, self.ws._minion_lanes[self.map_file][0])
        self.assertTrue(expected_tail, "rota esperada não deveria ser vazia/None neste setup")

        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        pos_after = (tm_after.current_tile_x, tm_after.current_tile_y)

        self.assertNotEqual(pos_before, pos_after)
        # bot.route == [posição inicial] + a MESMA cauda que um minion
        # dessa lane usaria — prova "mesmo caminho", não só "mesmo destino".
        self.assertEqual(bot_c.route, [pos_before] + expected_tail)
        self.assertEqual(pos_after, bot_c.route[1])

    def test_rota_do_bot_desvia_do_pe_de_2_tiles_da_torre(self):
        """Bug real de playtest (25/08/2026): bot travava permanentemente
        do lado da PRÓPRIA torre logo após spawnar. Causa:
        `WorldServer._get_tower_tiles_for_map` (dynamic_obstacles da
        rota compartilhada com o minion) só registrava o tile ÂNCORA da
        torre, não o pé de colisão REAL de 2 tiles (`entity_footprint_
        tiles`, §42) — a rota calculada podia cruzar o 2º tile
        (invisível pro A*), e o fallback do bot (`_advance_toward`)
        recalculava com o MESMO obstáculo incompleto, travando pra
        sempre no mesmo lugar (minion nunca mostrava o sintoma — seu
        fallback próprio, `_get_occupied_tiles`, já usava o pé real).
        Mesma técnica de `tests/test_minions.py::
        test_rota_desvia_do_pe_de_2_tiles_da_torre_nao_so_do_ancora`:
        âncora da torre FORA do caminho reto, 2º tile do pé EM CIMA."""
        from engine.entity_factory import create_tower
        from engine.components import MapLocation
        from engine.world_systems import entity_footprint_tiles

        lane_path_x = 20  # caminho reto vertical (20,20) -> (20,40)
        tower_tile = (lane_path_x, 30)
        tower_anchor = (lane_path_x - 1, 30)  # 1 tile a oeste, FORA do caminho
        tower_eid = create_tower(self.ws.world, tower_anchor[0], tower_anchor[1],
                                 "torre_de_fogo", faction_id="arena_time_b")
        self.ws.world.add_component(tower_eid, MapLocation(self.map_file))
        tower_tm = self.ws.world.get_component(tower_eid, TileMovement)
        footprint = entity_footprint_tiles(self.ws.world, tower_eid, tower_tm)
        self.assertIn(tower_tile, footprint,
                      "pré-condição do teste: o pé da torre precisa cobrir o tile do caminho")

        self.ws._minion_lanes[self.map_file] = [{
            "faction": "arena_time_a", "lane_id": "mid",
            "spawn_tile": (lane_path_x, 20), "target_tile": [(lane_path_x, 40)],
        }]
        bot_c = self.ws.world.get_component(self.bot, BotControlled)
        bot_c.push_start_at = time.time() - 1.0

        BotAISystem.update(self.ws, dt=999.0)

        self.assertIsNotNone(bot_c.route, "bot deveria ter calculado uma rota")
        self.assertNotIn(tower_tile, bot_c.route,
                         "rota do bot não deveria atravessar o 2º tile do pé da torre")


class TestBotAttacksMinion(unittest.TestCase):
    """Bot mira minion inimigo, não só player (16/08/2026 — v1 ignorava
    minion por completo)."""

    def setUp(self):
        self.ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(self.ws, 8, 8, 60, 60)
        self.bot = self.ws._spawn_bot_player(class_id="guerreiro")
        from tests.helpers import clear_login_immunity
        clear_login_immunity(self.ws, self.bot)
        self.ws.world.add_component(self.bot, Faction(faction_id="arena_time_a"))
        from engine.utils import snap_to_tile
        snap_to_tile(self.ws.world, self.bot, 20, 20, carry_prev=False)

    def test_bot_engaja_minion_inimigo_em_alcance(self):
        # Mesmo padrão de test_bot_engaja_alvo_em_alcance_com_skill (bot
        # guerreiro tem "golpe_poderoso" pronta e off-cooldown — usa
        # skill, não auto-attack puro, então o sinal certo é
        # _pending_skill_requests, não CombatState.target_entity_id).
        from engine.entity_factory import create_minion
        from engine.components import MapLocation
        map_file = self.ws.get_player_map(self.ws._player_eid_to_sid[self.bot])
        minion = create_minion(self.ws.world, 21, 20, "minion_melee",
                               faction_id="arena_time_b", route=[(21, 20)])
        self.ws.world.add_component(minion, MapLocation(map_file))

        BotAISystem.update(self.ws, dt=999.0)

        pending = [r for r in self.ws._pending_skill_requests if r["player_eid"] == self.bot]
        self.assertTrue(any(r["sid"] == "golpe_poderoso" and r["tid"] == minion for r in pending),
                        "bot deveria ter mirado a skill no minion inimigo em alcance")

    def test_bot_persegue_minion_fora_de_alcance(self):
        from engine.entity_factory import create_minion
        from engine.components import MapLocation
        map_file = self.ws.get_player_map(self.ws._player_eid_to_sid[self.bot])
        minion = create_minion(self.ws.world, 26, 20, "minion_melee",
                               faction_id="arena_time_b", route=[(26, 20)])
        self.ws.world.add_component(minion, MapLocation(map_file))

        tm_before = self.ws.world.get_component(self.bot, TileMovement)
        pos_before = (tm_before.current_tile_x, tm_before.current_tile_y)
        BotAISystem.update(self.ws, dt=999.0)
        tm_after = self.ws.world.get_component(self.bot, TileMovement)
        pos_after = (tm_after.current_tile_x, tm_after.current_tile_y)
        self.assertNotEqual(pos_before, pos_after,
                            "bot deveria andar em direção ao minion fora de alcance")


class TestBotDiveGate(unittest.TestCase):
    """Dive gate (16/08/2026, pedido do usuário; condição (a) reajustada
    25/08/2026): só entra no alcance de torre inimiga com minion aliado
    PERTO DO PRÓPRIO BOT agora, ou alvo <30% HP. Antes exigia minion
    dentro do alcance da MESMA torre — bug real de playtest (bot travava
    perto de torre mesmo com a leva por perto, porque a leva raramente
    fica parada EXATAMENTE dentro do alcance da torre)."""

    def setUp(self):
        self.ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(self.ws, 8, 8, 60, 60)
        self.map_file = self.ws._map_file
        from engine.entity_factory import create_tower
        self.tower = create_tower(self.ws.world, 30, 20, "torre_de_fogo",
                                  faction_id="arena_time_b")
        from engine.components import MapLocation, Tower as _TwrDG
        self.ws.world.add_component(self.tower, MapLocation(self.map_file))
        tower_comp = self.ws.world.get_component(self.tower, _TwrDG)
        # tile candidato (próximo passo do bot) coberto pela torre, e a
        # posição ATUAL do bot 1 tile antes dele (mesmo padrão de
        # _push_lane/_advance_toward: cur = posição atual, tile = próximo passo).
        self.covered_tile = (30 - tower_comp.attack_range_tiles, 20)
        self.my_tile = (self.covered_tile[0] - 1, self.covered_tile[1])

    def test_tile_fora_do_alcance_sempre_permitido(self):
        self.assertTrue(BotAISystem._dive_allowed(
            self.ws, self.map_file, (0, 0), "arena_time_a", None, (0, 0)))

    def test_tile_no_alcance_bloqueado_sem_minion_aliado_e_sem_alvo_baixo(self):
        self.assertFalse(BotAISystem._dive_allowed(
            self.ws, self.map_file, self.covered_tile, "arena_time_a", None, self.my_tile))

    def test_tile_no_alcance_permitido_com_minion_aliado_perto_do_bot(self):
        from engine.entity_factory import create_minion
        from engine.components import MapLocation
        ally_minion = create_minion(self.ws.world, *self.my_tile, "minion_melee",
                                    faction_id="arena_time_a", route=[self.my_tile])
        self.ws.world.add_component(ally_minion, MapLocation(self.map_file))
        self.assertTrue(BotAISystem._dive_allowed(
            self.ws, self.map_file, self.covered_tile, "arena_time_a", None, self.my_tile))

    def test_tile_no_alcance_bloqueado_com_minion_aliado_longe_do_bot(self):
        """Regressão do bug real: minion aliado dentro do alcance DA
        TORRE (cenário que a condição ANTIGA liberava), mas o BOT está
        longe dali — a condição nova exige perto do bot, não da torre,
        então continua bloqueado."""
        from engine.entity_factory import create_minion
        from engine.components import MapLocation
        ally_minion = create_minion(self.ws.world, *self.covered_tile, "minion_melee",
                                    faction_id="arena_time_a", route=[self.covered_tile])
        self.ws.world.add_component(ally_minion, MapLocation(self.map_file))
        my_tile_far = (self.covered_tile[0] - 10, self.covered_tile[1])
        self.assertFalse(BotAISystem._dive_allowed(
            self.ws, self.map_file, self.covered_tile, "arena_time_a", None, my_tile_far))

    def test_tile_no_alcance_permitido_com_alvo_abaixo_de_30_por_cento(self):
        from tests.helpers import spawn_player as _sp_dg
        from engine.components import CombatStats as _CSDG
        low_hp_target = _sp_dg(self.ws, "dg_target", 25, 25)
        cs = self.ws.world.get_component(low_hp_target, _CSDG)
        cs.current_hp = int(cs.max_hp * 0.20)
        self.assertTrue(BotAISystem._dive_allowed(
            self.ws, self.map_file, self.covered_tile, "arena_time_a", low_hp_target, self.my_tile))

    def test_tile_no_alcance_ainda_bloqueado_com_alvo_acima_de_30_por_cento(self):
        from tests.helpers import spawn_player as _sp_dg2
        from engine.components import CombatStats as _CSDG2
        healthy_target = _sp_dg2(self.ws, "dg_target2", 25, 25)
        cs = self.ws.world.get_component(healthy_target, _CSDG2)
        cs.current_hp = cs.max_hp  # 100%
        self.assertFalse(BotAISystem._dive_allowed(
            self.ws, self.map_file, self.covered_tile, "arena_time_a", healthy_target, self.my_tile))


class TestBotProjectileAutohit(unittest.TestCase):
    """Bot não tem cliente pra mandar PROJECTILE_HIT_CS — sem confirmação
    de rede, skill de projétil (Bola de Fogo, e as skills de projétil do
    arqueiro) completava o cast mas NUNCA aplicava dano (a entrada em
    `_spells_in_flight_queue` expirava sozinha 3s depois). Bug real
    relatado pelo usuário (26/08/2026), confirmado por simulação real
    antes do fix: 7 casts completos de Bola de Fogo em 20s, 0 dano.
    `_schedule_bot_projectile_autohit`/`_tick_bot_projectile_autohits`
    (server/spell_completion_processor.py) resolvem chamando a MESMA
    `_apply_spell_on_projectile_hit` que uma confirmação de cliente real
    usaria — sem duplicar lógica de dano."""

    def setUp(self):
        self.ws = make_world_server()
        from tests.test_tile_los_visibility import _clear_area
        _clear_area(self.ws, 8, 8, 60, 60)

    def test_bola_de_fogo_bot_causa_dano_real_de_ponta_a_ponta(self):
        """Fluxo completo, tick real (`run_ticks`, inclui BotAISystem):
        bot mago decide, casta, cast completa, entra em voo, e o novo
        auto-hit aplica o dano — sem precisar de nenhum cliente."""
        from tests.helpers import clear_login_immunity, run_ticks
        from engine.utils import snap_to_tile
        from engine.components import CombatStats

        mago = self.ws._spawn_bot_player(class_id="mago")
        clear_login_immunity(self.ws, mago)
        self.ws.world.add_component(mago, Faction(faction_id="arena_time_a"))
        snap_to_tile(self.ws.world, mago, 20, 20, carry_prev=False)

        enemy = spawn_player(self.ws, "bdf_target", 22, 20, hp=500)
        clear_login_immunity(self.ws, enemy)
        self.ws.world.add_component(enemy, Faction(faction_id="arena_time_b"))
        enemy_cs = self.ws.world.get_component(enemy, CombatStats)
        hp_before = enemy_cs.current_hp

        # cast_time=1.5s (bola_de_fogo) + tempo de voo (curto, alvo perto)
        # + folga — dt=0.05, 60 ticks = 3s, sobra margem.
        run_ticks(self.ws, 60, dt=0.05)

        self.assertLess(enemy_cs.current_hp, hp_before,
                        "Bola de Fogo do bot deveria ter causado dano real ao alvo")

    def test_schedule_bot_projectile_autohit_generico_por_spell_id(self):
        """Mesma mecânica de agendamento, mas testada isolada (sem
        depender de autorização/equipamento de arqueiro) pra provar que
        NÃO é hardcoded pra Bola de Fogo — funciona pra qualquer spell_id
        em _PROJECTILE_SPELLS, aqui com flecha_reiterada."""
        from tests.helpers import clear_login_immunity
        from engine.components import BotControlled, CombatStats, Equipment, Item
        import time as _t

        bot = spawn_player(self.ws, "fr_bot", 20, 20, class_id="arqueiro")
        clear_login_immunity(self.ws, bot)
        self.ws.world.add_component(bot, BotControlled())
        self.ws.world.add_component(bot, Faction(faction_id="arena_time_a"))
        equip = self.ws.world.get_component(bot, Equipment)
        equip.slots["mainhand"] = Item("Arco Teste", "weapon", "mainhand",
                                       subtype="Bow", cast_range=8,
                                       damage_min=10, damage_max=15)
        equip.slots["offhand"]  = Item("Aljava Teste", "quiver", "offhand",
                                       arrow_count=50, max_arrows=50)
        target = spawn_player(self.ws, "fr_target", 21, 20, hp=200)
        clear_login_immunity(self.ws, target)
        self.ws.world.add_component(target, Faction(faction_id="arena_time_b"))
        target_cs = self.ws.world.get_component(target, CombatStats)
        target_cs.dodge_rating = 0.0
        target_cs.parry_rating = 0.0
        hp_before = target_cs.current_hp

        # Simula o que _process_spell_cast_completions já faria ao
        # completar um cast de flecha_reiterada (entrada em voo real).
        self.ws._spells_in_flight_queue.append({
            "player_eid":     bot,
            "spell_id":       "flecha_reiterada",
            "target_id":      target,
            "entry":          {},
            "expires_at":     _t.time() + 3.0,
            "remaining_hits": 1,
        })
        self.ws._schedule_bot_projectile_autohit(bot, "flecha_reiterada", target)
        self.assertEqual(len(self.ws._bot_projectile_autohits), 1)

        # Avança tempo suficiente pro "voo" (1 tile de distância, bem
        # menos que qualquer velocidade de projétil cadastrada).
        for _ in range(20):
            self.ws._tick_bot_projectile_autohits(dt=0.05)

        self.assertLess(target_cs.current_hp, hp_before,
                        "flecha_reiterada deveria ter aplicado dano via auto-hit do bot")
        self.assertEqual(self.ws._bot_projectile_autohits, [],
                         "agendamento deveria ter sido consumido (1 hit só)")


if __name__ == "__main__":
    unittest.main()
