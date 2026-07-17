"""
tests/test_party.py — Sistema de Party/Grupo (convite/aceite/recusa/
sair/expulsar/promoção de líder) + XP compartilhado — ver
server/party_processor.py e ARQUITETURA_ONLINE.md §34.19.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from shared.constants import PARTY_MAX_SIZE


class TestPartyLifecycle(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)
        self.b = spawn_player(self.ws, "s2", 131, 374)

    def _group(self, requester, target):
        self.assertIsNone(self.ws.request_party_invite(requester, target))
        req, started = self.ws.respond_party_invite(target, accept=True)
        self.assertEqual(req, requester)
        self.assertTrue(started)

    def test_convite_e_aceite_cria_grupo_de_2_com_lider_correto(self):
        self._group(self.a, self.b)
        pid = self.ws.get_party_id_of(self.a)
        self.assertNotEqual(pid, -1)
        self.assertEqual(self.ws.get_party_id_of(self.b), pid)
        snap = self.ws.get_party_snapshot(pid)
        self.assertEqual(snap["leader_eid"], self.a)
        self.assertEqual(sorted(m["eid"] for m in snap["members"]), sorted([self.a, self.b]))

    def test_convite_de_membro_existente_faz_grupo_crescer(self):
        self._group(self.a, self.b)
        c = spawn_player(self.ws, "s3", 132, 374)
        # "b" (NÃO é líder) convida — qualquer membro pode convidar.
        self._group(self.b, c)
        pid = self.ws.get_party_id_of(self.a)
        self.assertEqual(self.ws.get_party_id_of(c), pid,
                         "convite de um membro existente deveria crescer o MESMO grupo")
        self.assertEqual(len(self.ws.get_party_members(self.a)), 3)

    def test_decline_nao_cria_grupo(self):
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        req, started = self.ws.respond_party_invite(self.b, accept=False)
        self.assertEqual(req, self.a)
        self.assertFalse(started)
        self.assertEqual(self.ws.get_party_id_of(self.a), -1)
        self.assertEqual(self.ws.get_party_id_of(self.b), -1)

    def test_limite_de_tamanho(self):
        self._group(self.a, self.b)
        members = [self.a, self.b]
        for i in range(2, PARTY_MAX_SIZE):
            newp = spawn_player(self.ws, f"s{i+1}", 130 + i, 374)
            self._group(self.a, newp)
            members.append(newp)
        self.assertEqual(len(self.ws.get_party_members(self.a)), PARTY_MAX_SIZE)

        overflow = spawn_player(self.ws, "s_overflow", 150, 374)
        self.assertEqual(self.ws.request_party_invite(self.a, overflow), "invalid",
                         f"grupo já tem {PARTY_MAX_SIZE} membros — convite deveria ser recusado")

    def test_membro_comum_sai_grupo_continua(self):
        self._group(self.a, self.b)
        c = spawn_player(self.ws, "s3", 132, 374)
        self._group(self.a, c)

        self.ws.leave_party(self.b)

        self.assertEqual(self.ws.get_party_id_of(self.b), -1)
        pid = self.ws.get_party_id_of(self.a)
        self.assertEqual(sorted(self.ws.get_party_members(self.a)), sorted([self.a, c]))
        self.assertEqual(self.ws._parties[pid]["leader_eid"], self.a,
                         "líder não deveria mudar quando um membro comum sai")

    def test_lider_sai_promove_proximo(self):
        self._group(self.a, self.b)
        c = spawn_player(self.ws, "s3", 132, 374)
        self._group(self.a, c)

        self.ws.leave_party(self.a)   # líder original sai

        self.assertEqual(self.ws.get_party_id_of(self.a), -1)
        pid = self.ws.get_party_id_of(self.b)
        self.assertEqual(self.ws._parties[pid]["leader_eid"], self.b,
                         "próximo membro (b, mais antigo restante) deveria virar líder")
        self.assertEqual(sorted(self.ws.get_party_members(self.b)), sorted([self.b, c]))

    def test_grupo_de_2_desfaz_quando_um_sai(self):
        self._group(self.a, self.b)
        self.ws.leave_party(self.a)
        self.assertEqual(self.ws.get_party_id_of(self.a), -1)
        self.assertEqual(self.ws.get_party_id_of(self.b), -1,
                         "grupo de 2 vira grupo de 1 — não faz sentido, deveria desfazer")
        self.assertEqual(len(self.ws._parties), 0)

    def test_expulsao_so_lider_pode(self):
        self._group(self.a, self.b)
        c = spawn_player(self.ws, "s3", 132, 374)
        self._group(self.a, c)

        # "b" (não é líder) tentando expulsar "c" — recusado
        self.assertFalse(self.ws.kick_from_party(self.b, c))
        self.assertNotEqual(self.ws.get_party_id_of(c), -1, "expulsão de não-líder não deveria funcionar")

        # "a" (líder) expulsa "c" — funciona
        self.assertTrue(self.ws.kick_from_party(self.a, c))
        self.assertEqual(self.ws.get_party_id_of(c), -1)
        self.assertEqual(sorted(self.ws.get_party_members(self.a)), sorted([self.a, self.b]))

    def test_desconexao_limpa_grupo(self):
        self._group(self.a, self.b)
        c = spawn_player(self.ws, "s3", 132, 374)
        self._group(self.a, c)

        self.ws.end_parties_of(self.a)   # líder desconecta

        self.assertEqual(self.ws.get_party_id_of(self.a), -1)
        pid = self.ws.get_party_id_of(self.b)
        self.assertNotEqual(pid, -1)
        self.assertEqual(self.ws._parties[pid]["leader_eid"], self.b)

    def test_desconexao_limpa_convites_pendentes(self):
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        self.ws.end_parties_of(self.a)
        self.assertEqual(len(self.ws._pending_party_invites), 0)

    def test_convidar_alvo_ja_em_outro_grupo_falha(self):
        self._group(self.a, self.b)
        c = spawn_player(self.ws, "s3", 132, 374)
        d = spawn_player(self.ws, "s4", 133, 374)
        self._group(c, d)

        self.assertEqual(self.ws.request_party_invite(self.a, c), "invalid",
                         "alvo já em outro grupo — convite deveria ser recusado")


class TestPartySharedXp(unittest.TestCase):
    """XP compartilhado (decisão do usuário 17/07/2026): fatia por
    proporção de dano continua entre atacantes sem grupo em comum, mas
    entre membros do MESMO grupo a soma vira um pool redistribuído
    igualmente entre quem está dentro do raio da morte — mesmo quem não
    bateu."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)
        self.b = spawn_player(self.ws, "s2", 131, 374)
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        self.ws.respond_party_invite(self.b, accept=True)

    def _spawn_mob_for_death(self, tx: int, ty: int) -> int:
        from engine.entity_factory import create_enemy
        from engine.components import MapLocation
        mob = create_enemy(self.ws.world, tx, ty, faction="monstros_hostis")
        self.ws.world.add_component(mob, MapLocation(self.ws._map_file))
        return mob

    def _kill_and_collect_xp(self, mob: int, killer_eid: int) -> dict:
        from engine.components import PendingDeath
        self.ws.world.add_component(mob, PendingDeath(killer_entity_id=killer_eid))
        self.ws._death_handler.update()
        entries = self.ws._death_handler.consume_xp()
        result: dict = {}
        for e in entries:
            if e.get("xp", 0) > 0:
                result[e["player_eid"]] = result.get(e["player_eid"], 0) + e["xp"]
        return result

    def test_membro_que_nao_bateu_recebe_fatia_igual(self):
        """Só "a" bate no mob; "b" (grupo, por perto) não bateu nada —
        os 2 devem receber a mesma fatia do pool."""
        mob = self._spawn_mob_for_death(130, 374)
        set_entity_tile(self.ws, mob, 130, 374)
        self.ws._mob_damage_log[mob] = {self.a: 100}

        xp = self._kill_and_collect_xp(mob, self.a)

        self.assertIn(self.a, xp)
        self.assertIn(self.b, xp, "membro do grupo que não bateu deveria receber XP mesmo assim")
        self.assertEqual(xp[self.a], xp[self.b])

    def test_membro_fora_do_raio_nao_recebe_nada(self):
        from shared.constants import PARTY_XP_SHARE_RADIUS_TILES
        mob = self._spawn_mob_for_death(130, 374)
        set_entity_tile(self.ws, mob, 130, 374)
        set_entity_tile(self.ws, self.b, 130 + PARTY_XP_SHARE_RADIUS_TILES + 10, 374)
        self.ws._mob_damage_log[mob] = {self.a: 100}

        xp = self._kill_and_collect_xp(mob, self.a)

        self.assertIn(self.a, xp)
        self.assertNotIn(self.b, xp, "membro fora do raio da morte não deveria ganhar XP")

    def test_atacante_de_fora_do_grupo_mantem_fatia_individual(self):
        """base_xp=50 (tier normal, mob genérico). "a" e "c" batem igual
        (50/50 dano) — proporção original é 25 cada. "c" (sem grupo)
        mantém 25 intocado; "a" (grupo com "b") tem sua fatia de 25
        pooled e dividida com "b": 12 cada (25 // 2, arredondado pra
        baixo)."""
        c = spawn_player(self.ws, "s3", 132, 374)   # sem grupo
        mob = self._spawn_mob_for_death(130, 374)
        set_entity_tile(self.ws, mob, 130, 374)
        set_entity_tile(self.ws, c, 130, 374)
        self.ws._mob_damage_log[mob] = {self.a: 50, c: 50}

        xp = self._kill_and_collect_xp(mob, self.a)

        self.assertEqual(xp[c], 25, "fatia individual de 'c' não deveria ser tocada pelo grupo alheio")
        self.assertEqual(xp[self.a], 12)
        self.assertEqual(xp[self.b], 12)


class TestPartyLootFreeForAll(unittest.TestCase):
    """Loot free-for-all dentro do grupo (bug real relatado pelo usuário
    17/07/2026: só o "dono" do corpo conseguia lootear mesmo com o
    resto do grupo do lado — server/loot_processor.py::request_loot só
    checava `player_eid == corpse["owner_eid"]`, sem noção de grupo)."""

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)
        self.b = spawn_player(self.ws, "s2", 131, 374)

    def _make_corpse(self, owner_eid: int) -> int:
        cid = self.ws._next_corpse_id
        self.ws._next_corpse_id += 1
        self.ws._corpses[cid] = {
            "tx": 130, "ty": 374, "owner_eid": owner_eid,
            "items": [{"name": "Item Teste"}], "coins": 10,
            "timer": 120.0, "map": self.ws._map_file,
        }
        return cid

    def test_membro_do_grupo_pode_lootear_corpo_do_dono(self):
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        self.ws.respond_party_invite(self.b, accept=True)
        cid = self._make_corpse(owner_eid=self.a)

        result = self.ws.request_loot("s2", cid)

        self.assertIsNotNone(result, "membro do grupo deveria conseguir lootear o corpo do dono")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["coins"], 10)

    def test_fora_do_grupo_continua_bloqueado(self):
        spawn_player(self.ws, "s3", 132, 374)   # sem grupo com "a"
        cid = self._make_corpse(owner_eid=self.a)

        result = self.ws.request_loot("s3", cid)

        self.assertIsNone(result, "player fora do grupo do dono não deveria conseguir lootear")

    def test_grupos_diferentes_nao_se_misturam(self):
        c = spawn_player(self.ws, "s3", 132, 374)
        d = spawn_player(self.ws, "s4", 133, 374)
        self.assertIsNone(self.ws.request_party_invite(c, d))
        self.ws.respond_party_invite(d, accept=True)   # "c"/"d" em outro grupo
        cid = self._make_corpse(owner_eid=self.a)      # "a" sem grupo

        self.assertIsNone(self.ws.request_loot("s3", cid))
        self.assertIsNone(self.ws.request_loot("s4", cid))

    def test_segundo_membro_recebe_vazio_apos_primeiro_lootear(self):
        """Free-for-all = primeiro que clicar leva tudo — mesmo
        comportamento padrão de qualquer MMO."""
        self.assertIsNone(self.ws.request_party_invite(self.a, self.b))
        self.ws.respond_party_invite(self.b, accept=True)
        cid = self._make_corpse(owner_eid=self.a)

        first  = self.ws.request_loot("s1", cid)
        second = self.ws.request_loot("s2", cid)

        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(second["items"], [])
        self.assertEqual(second["coins"], 0)


if __name__ == "__main__":
    unittest.main()
