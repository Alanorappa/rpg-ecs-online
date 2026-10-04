"""
tests/test_portal_remote_mirror.py — bug real relatado pelo usuário
(03/10/2026): ao usar um portal, nem o som nem a animação do personagem
("entrando no portal") apareciam pra quem estava observando de fora —
só o próprio caster, porque `PORTAL_CAST_START` (servidor→cliente) é
privado de propósito (confirmado: caster ouvia/via normal, só quem
observava não via/ouvia nada).

Fix: `PORTAL_CAST_BEGIN`/`PORTAL_CAST_ABORT` (broadcast AOI, server/
session.py) + `PortalHandlers._update_remote_portal_casts` (client/
portal_handlers.py) espelham som posicional + animação "portal_in" na
entidade REMOTA, reaproveitando os mesmos helpers (`_begin_portal_in_anim`/
`_clear_portal_in_anim`) que o caster local já usava.

Este arquivo testa só o lado CLIENTE (handlers de rede + update por
frame) — o broadcast em si (server/session.py) seria testado end-to-end
só com um harness de SessionManager completo, fora do escopo de um
teste unitário rápido; a validação real do broadcast é manual (usuário
testando com 2 contas).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((100, 100))

import unittest

from client.portal_handlers import (PortalHandlers, _PORTAL_SFX_START_S,
                                    _PORTAL_CHAR_ANIM_START_S)
from engine.world import World
from engine.components import Position, TileMovement, RemoteControlled


class _Fixture(PortalHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = 1
        self._remote_players = {}

    def _player_world_pos(self):
        return (0.0, 0.0)


def _make_fixture():
    world = World()
    me = world.create_entity()
    world.add_component(me, TileMovement(current_tile_x=0, current_tile_y=0))
    remote_local_eid = world.create_entity()
    world.add_component(remote_local_eid, Position(x=50.0, y=50.0))
    world.add_component(remote_local_eid, TileMovement(current_tile_x=1, current_tile_y=1))
    world.add_component(remote_local_eid, RemoteControlled(
        server_eid=42, name="Fulano", hp=100, hp_max=100, level=5))
    fx = _Fixture(world, me)
    fx._remote_players = {42: remote_local_eid}
    fx._remote_portal_casts = {}   # normalmente inicializado por _build_portal_tiles
    return fx, remote_local_eid


class TestPortalCastBeginHandler(unittest.TestCase):

    def test_registra_cast_remoto(self):
        fx, _ = _make_fixture()
        fx._handle_msg_portal_cast_begin({"eid": 42, "duration": 5.0})
        self.assertIn(42, fx._remote_portal_casts)
        cast = fx._remote_portal_casts[42]
        self.assertEqual(cast["duration"], 5.0)
        self.assertEqual(cast["elapsed"], 0.0)
        self.assertFalse(cast["sfx_played"])
        self.assertFalse(cast["char_anim_triggered"])

    def test_ignora_o_proprio_eid_local(self):
        """Guard defensivo -- _sessions_in_aoi inclui a origem, mas o
        próprio caster já tem seu caminho privado via PORTAL_CAST_START."""
        fx, _ = _make_fixture()
        fx._handle_msg_portal_cast_begin({"eid": fx._my_eid, "duration": 5.0})
        self.assertNotIn(fx._my_eid, fx._remote_portal_casts)


class TestUpdateRemotePortalCasts(unittest.TestCase):

    def setUp(self):
        self.fx, self.remote_local_eid = _make_fixture()
        self.fx._handle_msg_portal_cast_begin({"eid": 42, "duration": 5.0})

    def test_toca_sfx_ao_cruzar_o_instante_certo(self):
        cast = self.fx._remote_portal_casts[42]
        # Ainda não cruzou _PORTAL_SFX_START_S (1.317s)
        self.fx._update_remote_portal_casts(dt=1.0)
        self.assertFalse(cast["sfx_played"])
        self.fx._update_remote_portal_casts(dt=1.0)   # total 2.0s > 1.317s
        self.assertTrue(cast["sfx_played"])

    def test_liga_animacao_portal_in_no_ultimo_segundo(self):
        tm = self.fx.world.get_component(self.remote_local_eid, TileMovement)
        self.fx._update_remote_portal_casts(dt=4.5)   # restam 0.5s (< 1.0s)
        self.assertEqual(tm.anim_attack_state, "portal_in")
        self.assertFalse(tm.anim_attack_hold_last_frame,
                         "observador remoto nunca segura a última frame "
                         "pra sempre -- diferente do caster local")

    def test_nao_liga_animacao_antes_do_ultimo_segundo(self):
        tm = self.fx.world.get_component(self.remote_local_eid, TileMovement)
        self.fx._update_remote_portal_casts(dt=1.0)   # restam 4.0s
        self.assertEqual(tm.anim_attack_state, "")

    def test_remove_entrada_ao_completar_a_duracao(self):
        self.fx._update_remote_portal_casts(dt=5.5)
        self.assertNotIn(42, self.fx._remote_portal_casts)

    def test_remove_entrada_se_entidade_remota_nao_existe_mais(self):
        """Saiu do AOI/despawnou -- ENTITY_DESPAWN já limpa
        _remote_players, detectado aqui sem handler dedicado."""
        del self.fx._remote_players[42]
        self.fx._update_remote_portal_casts(dt=1.0)
        self.assertNotIn(42, self.fx._remote_portal_casts)


class TestPortalCastAbortHandler(unittest.TestCase):

    def test_remove_do_tracking(self):
        fx, _ = _make_fixture()
        fx._handle_msg_portal_cast_begin({"eid": 42, "duration": 5.0})
        fx._handle_msg_portal_cast_abort({"eid": 42})
        self.assertNotIn(42, fx._remote_portal_casts)

    def test_desliga_animacao_se_ja_tinha_ligado(self):
        fx, remote_local_eid = _make_fixture()
        fx._handle_msg_portal_cast_begin({"eid": 42, "duration": 5.0})
        fx._update_remote_portal_casts(dt=4.5)   # liga a animação (último segundo)
        tm = fx.world.get_component(remote_local_eid, TileMovement)
        self.assertEqual(tm.anim_attack_state, "portal_in")

        fx._handle_msg_portal_cast_abort({"eid": 42})
        self.assertEqual(tm.anim_attack_state, "")

    def test_idempotente_sem_cast_ativo(self):
        """Não deveria crashar se chegar um abort pra um eid que o
        cliente não estava rastreando (corrida de rede/duplicata)."""
        fx, _ = _make_fixture()
        fx._handle_msg_portal_cast_abort({"eid": 999})   # não crasha


if __name__ == "__main__":
    unittest.main(verbosity=2)
