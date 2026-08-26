"""
tests/test_pack_aggro.py — Puxão de grupo (16/08/2026, pedido do usuário,
estilo WoW/LoL jungle camp): quando um mob entra em combate (por dano OU
por proximidade), outro mob da MESMA Faction dentro de
core_systems.PACK_AGGRO_RADIUS_TILES também entra em combate, mesmo que
ele mesmo seja neutro (nunca aggraria sozinho por proximidade) — só 1
salto, sem corrente. Mecanismo GERAL (core_systems.trigger_mob_aggro),
não específico de jungle — testado aqui tanto com facção neutra
(monstros_jungle) quanto hostil clássica (monstros_hostis), pra provar
que não é hardcoded pra BG.

Ver arquitetura/PROBLEMAS_ARQUITETURA.md e o plano em
C:\\Users\\l4nce\\.claude\\plans\\expressive-wondering-starlight.md.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from engine.components import AIControlled, CombatState, CombatStats, Visible, MapLocation
from engine.entity_factory import create_enemy


class _PackAggroBase(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        self.peid = self.ws._player_eids["s1"]
        pcs = self.ws.world.get_component(self.peid, CombatStats)
        pcs.acerto = 100.0  # hit garantido — aggro por dano só dispara se acertar

    def _spawn_mob(self, tx: int, ty: int, faction: str) -> int:
        eid = create_enemy(self.ws.world, tx, ty, faction=faction)
        self.ws._mob_eids.add(eid)
        self.ws.world.add_component(eid, CombatState())
        self.ws.world.add_component(eid, Visible())
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        set_entity_tile(self.ws, eid, tx, ty)
        cs = self.ws.world.get_component(eid, CombatStats)
        cs.dodge_rating = 0.0
        cs.parry_rating = 0.0
        return eid

    def _attack(self, target_eid: int) -> None:
        cst = self.ws.world.get_component(self.peid, CombatState)
        cst.target_entity_id = target_eid
        cst.is_pursuing = True
        self.ws._attack_timers["s1"] = 0.0
        mob_cs = self.ws.world.get_component(target_eid, CombatStats)
        snap = {target_eid: mob_cs.current_hp}
        self.ws._process_player_attacks(0.05, snap)


class TestPackAggroByDamage(_PackAggroBase):
    """Mecanismo genérico via facção NEUTRA (monstros_jungle — nunca
    aggraria por proximidade sozinho)."""

    def test_ataque_puxa_vizinho_da_mesma_faccao_dentro_do_raio(self):
        a = self._spawn_mob(131, 374, faction="monstros_jungle")
        b = self._spawn_mob(133, 374, faction="monstros_jungle")  # 2 tiles de a
        self._attack(a)

        ai_a = self.ws.world.get_component(a, AIControlled)
        ai_b = self.ws.world.get_component(b, AIControlled)
        self.assertNotEqual(ai_a.state, "IDLE", "mob atacado deveria aggrar")
        self.assertNotEqual(ai_b.state, "IDLE", "vizinho a 2 tiles deveria ser puxado junto")
        self.assertEqual(ai_b.target_eid, self.peid, "puxado deveria mirar o MESMO atacante")

    def test_vizinho_fora_do_raio_nao_e_puxado(self):
        a = self._spawn_mob(131, 374, faction="monstros_jungle")
        c = self._spawn_mob(140, 374, faction="monstros_jungle")  # 9 tiles de a
        self._attack(a)

        ai_c = self.ws.world.get_component(c, AIControlled)
        self.assertEqual(ai_c.state, "IDLE", "vizinho fora do raio de 3 tiles não deveria ser puxado")

    def test_faccao_diferente_no_raio_nao_e_puxada(self):
        a = self._spawn_mob(131, 374, faction="monstros_jungle")
        d = self._spawn_mob(133, 374, faction="monstros_hostis")  # 2 tiles, facção diferente
        self._attack(a)

        ai_d = self.ws.world.get_component(d, AIControlled)
        self.assertEqual(ai_d.state, "IDLE", "mob de OUTRA facção não deveria ser puxado")

    def test_puxao_e_so_1_salto_sem_corrente(self):
        # a --2t-- b --2t-- e  (b puxado por a; e NÃO deveria ser puxado por b)
        a = self._spawn_mob(131, 374, faction="monstros_jungle")
        b = self._spawn_mob(133, 374, faction="monstros_jungle")
        e = self._spawn_mob(135, 374, faction="monstros_jungle")  # 4 tiles de a, 2 de b
        self._attack(a)

        ai_b = self.ws.world.get_component(b, AIControlled)
        ai_e = self.ws.world.get_component(e, AIControlled)
        self.assertNotEqual(ai_b.state, "IDLE", "vizinho direto deveria ser puxado")
        self.assertEqual(ai_e.state, "IDLE",
                         "vizinho do vizinho (fora do raio de quem apanhou) não deveria ser puxado — só 1 salto")


class TestPackAggroGenericoFaccaoHostil(_PackAggroBase):
    """Confirma que o mecanismo NÃO é hardcoded pra jungle/BG — funciona
    igual com a facção hostil clássica do mundo aberto, disparado pelo
    aggro de PROXIMIDADE (não só por dano)."""

    def test_aggro_de_proximidade_tambem_puxa_vizinho_da_mesma_faccao(self):
        from tests.helpers import run_ticks
        a = self._spawn_mob(131, 374, faction="monstros_hostis")   # adjacente ao player — aggra por proximidade
        b = self._spawn_mob(133, 374, faction="monstros_hostis")   # 2 tiles de a, longe do player
        run_ticks(self.ws, 5)

        ai_a = self.ws.world.get_component(a, AIControlled)
        ai_b = self.ws.world.get_component(b, AIControlled)
        self.assertNotEqual(ai_a.state, "IDLE", "mob hostil deveria aggrar por proximidade (regressão)")
        self.assertNotEqual(ai_b.state, "IDLE",
                            "vizinho deveria ser puxado mesmo quando o aggro original foi por proximidade")


if __name__ == "__main__":
    unittest.main()
