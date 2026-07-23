"""
tests/test_taunt.py — Brado Provocativo vira taunt de verdade (Fase D,
23/07/2026, referência trazida pelo usuário: hard-CC de LoL — Rammus/
Galio/Shen). Alvo é forçado a andar até o taunter e autoatacá-lo por 3s,
sem poder usar habilidades. Ver TauntSystem (engine/world_systems.py) e
o design completo em ARQUITETURA_ONLINE.md.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import (make_world_server, spawn_player, run_ticks, authorize_skill,
                           first_mob, set_entity_tile)
from engine.components import (StatusEffects, CombatState, TileMovement, AIControlled,
                               CombatStats, SpellCast)


def _cast_brado(ws, caster_eid):
    ws._pending_skill_requests.append({
        "player_eid": caster_eid, "sid": "brado_provocativo", "tid": -1,
    })
    ws._process_skill_requests()


class TestTauntMob(unittest.TestCase):
    """Mob: AIControlled.state="CHASING"/target_eid é escrito na hora pelo
    próprio handler — a RETENÇÃO de alvo já existente em EnemyAISystem
    (mob em estado de combate mantém o alvo retido, ignora reavaliação)
    já garante o "travado por 3s", sem precisar de TauntSystem."""

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 50)  # spawn mobs
        self.caster = spawn_player(self.ws, "taunt_mob_c", 130, 374, class_id="guerreiro")
        authorize_skill(self.ws, self.caster, "brado_provocativo")
        self.mob = first_mob(self.ws)
        if not self.mob:
            self.skipTest("Sem mobs")
        set_entity_tile(self.ws, self.mob, 131, 374)

    def test_mob_forca_chasing_e_recebe_status_taunted(self):
        _cast_brado(self.ws, self.caster)
        ai = self.ws.world.get_component(self.mob, AIControlled)
        sfx = self.ws.world.get_component(self.mob, StatusEffects)
        self.assertEqual(ai.state, "CHASING")
        self.assertEqual(ai.target_eid, self.caster)
        self.assertTrue(sfx and sfx.has("taunted"))

    def test_mob_fora_do_raio_nao_e_afetado(self):
        set_entity_tile(self.ws, self.mob, 200, 374)  # bem longe, fora do raio 3
        _cast_brado(self.ws, self.caster)
        ai = self.ws.world.get_component(self.mob, AIControlled)
        self.assertNotEqual(ai.target_eid, self.caster)


class TestTauntPlayerPvP(unittest.TestCase):
    """Player: TauntSystem pilota o movimento forçado (sem AIControlled)."""

    def setUp(self):
        self.ws = make_world_server()
        self.caster = spawn_player(self.ws, "taunt_pvp_c", 130, 374, class_id="guerreiro")
        authorize_skill(self.ws, self.caster, "brado_provocativo")
        self.victim = spawn_player(self.ws, "taunt_pvp_v", 132, 374, class_id="mago")
        self.assertIsNone(self.ws.request_duel(self.caster, self.victim))
        self.ws.respond_duel_invite(self.victim, accept=True)

    def test_aplica_taunted_com_magnitude_do_taunter(self):
        _cast_brado(self.ws, self.caster)
        sfx = self.ws.world.get_component(self.victim, StatusEffects)
        fx = sfx.get("taunted") if sfx else None
        self.assertIsNotNone(fx)
        self.assertEqual(int(fx.magnitude), self.caster)

    def test_bloqueia_move_player_livre(self):
        _cast_brado(self.ws, self.caster)
        sid = self.ws._player_eid_to_sid.get(self.victim)
        ok = self.ws.move_player(sid, 133, 374)
        self.assertFalse(ok, "MOVE livre deveria ser recusado enquanto taunted")

    def test_bloqueia_cast_skill_do_taunted(self):
        authorize_skill(self.ws, self.victim, "bola_de_fogo")
        _cast_brado(self.ws, self.caster)
        self.ws._pending_skill_requests.append({
            "player_eid": self.victim, "sid": "bola_de_fogo", "tid": self.caster,
        })
        self.ws._process_skill_requests()
        self.assertIsNone(self.ws.world.get_component(self.victim, SpellCast),
                          "taunted não deveria conseguir iniciar outra skill")

    def test_sistema_conduz_ate_adjacente_e_autoataca(self):
        _cast_brado(self.ws, self.caster)
        for _ in range(60):  # 3s
            self.ws._tick(0.05)
        vtm = self.ws.world.get_component(self.victim, TileMovement)
        ctm = self.ws.world.get_component(self.caster, TileMovement)
        from engine.utils import chebyshev
        dist = chebyshev(vtm.current_tile_x, vtm.current_tile_y,
                         ctm.current_tile_x, ctm.current_tile_y)
        self.assertLessEqual(dist, 1, "vítima deveria estar adjacente ao taunter")
        vcst = self.ws.world.get_component(self.victim, CombatState)
        self.assertEqual(vcst.target_entity_id, self.caster)
        self.assertTrue(vcst.is_pursuing)

    def test_expira_apos_duracao(self):
        _cast_brado(self.ws, self.caster)
        for _ in range(70):  # > 3s
            self.ws._tick(0.05)
        sfx = self.ws.world.get_component(self.victim, StatusEffects)
        self.assertFalse(sfx.has("taunted"), "taunted deveria ter expirado")

    def test_libera_na_hora_se_taunter_morre(self):
        _cast_brado(self.ws, self.caster)
        sfx_before = self.ws.world.get_component(self.victim, StatusEffects)
        self.assertTrue(sfx_before.has("taunted"))
        caster_cs = self.ws.world.get_component(self.caster, CombatStats)
        caster_cs.current_hp = 0
        self.ws._tick(0.05)
        sfx_after = self.ws.world.get_component(self.victim, StatusEffects)
        self.assertFalse(sfx_after.has("taunted"),
                         "taunt deveria liberar na hora com o taunter morto/inválido")


if __name__ == "__main__":
    unittest.main()
