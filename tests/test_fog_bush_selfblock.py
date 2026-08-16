"""
tests/test_fog_bush_selfblock.py — Fog assimétrico ao ficar no mesmo
tile de bush (13/08/2026, achado do usuário validando a Fase F de
vision-blocking, §49).

Referência (wiki oficial de League of Legends, via WebSearch): "Brush
is opaque towards vision when viewed from the outside inwards and not
the reverse." — bush bloqueia de fora-pra-dentro, nunca de
dentro-pra-fora. `ui.fov.local_vision_blob` isola o blob conectado de
tiles bloqueantes que contém a origem do observador e o torna
transparente SÓ pro FOV calculado a partir dali — ver
PROBLEMAS_ARQUITETURA.md.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import unittest

from engine.fov import local_vision_blob, compute_fov
from engine.world import World
from engine.entity_factory import create_tilemap
from engine.components import Tilemap


def _blocking_from_set(blocked: set):
    def is_blocking(x, y):
        return (x, y) in blocked
    return is_blocking


class TestLocalVisionBlob(unittest.TestCase):

    def test_origem_fora_de_bloqueio_retorna_vazio(self):
        blocked = {(5, 5), (6, 5)}
        is_blocking = _blocking_from_set(blocked)
        self.assertEqual(local_vision_blob(0, 0, is_blocking), frozenset())

    def test_origem_em_blob_retorna_so_o_blob_conectado(self):
        # Blob A: 4 tiles conectados em L, contém a origem (2,2).
        blob_a = {(2, 2), (3, 2), (2, 3), (2, 4)}
        # Blob B: bush SEPARADA, não conectada a A (gap em (4,2)/(3,3)/(3,4)).
        blob_b = {(6, 2), (7, 2)}
        blocked = blob_a | blob_b
        is_blocking = _blocking_from_set(blocked)

        result = local_vision_blob(2, 2, is_blocking)
        self.assertEqual(result, frozenset(blob_a))
        self.assertTrue(blob_b.isdisjoint(result),
            "bush separada não deveria entrar no blob da origem")


class TestFovSymmetryOverBush(unittest.TestCase):
    """Ponta a ponta: is_blocking construído do mesmo jeito que
    `ui.systems.FogSystem._is_blocking_from` (blob exemptado). Blob em
    formato de "+" ao redor de (3,3) — origem cercada de bloqueio em 3
    das 4 direções (cima/baixo/direita), só a esquerda livre — reproduz
    de verdade o cone assimétrico do desenho do usuário (sem a
    exceção, o lado direito fica cego; com ela, os 2 lados enxergam)."""

    _BLOB_SHAPE = {(3, 3), (3, 2), (3, 4), (4, 3)}

    def _real_is_blocking(self, blob=frozenset()):
        def is_blocking(x, y):
            if (x, y) in blob:
                return False
            return (x, y) in self._BLOB_SHAPE
        return is_blocking

    def test_observador_em_cima_do_bloqueio_enxerga_dos_2_lados(self):
        blob = local_vision_blob(3, 3, self._real_is_blocking())
        is_blocking = self._real_is_blocking(blob)
        visible = compute_fov(3, 3, radius=5, is_blocking=is_blocking)
        self.assertIn((1, 3), visible, "deveria enxergar o lado esquerdo")
        self.assertIn((5, 3), visible,
            "deveria enxergar o lado direito também (sem o fix, o próprio "
            "blob cortava a visão nessa direção)")

    def test_observador_fora_do_bloqueio_continua_sem_ver_atras(self):
        # Regressão: jogador FORA da bush (não em cima) continua sem
        # enxergar o que está do outro lado — comportamento da Fase F
        # (§49) preservado, blob só isenta quem está literalmente em cima.
        blob = local_vision_blob(1, 3, self._real_is_blocking())
        self.assertEqual(blob, frozenset())
        is_blocking = self._real_is_blocking(blob)
        visible = compute_fov(1, 3, radius=5, is_blocking=is_blocking)
        self.assertNotIn((5, 3), visible,
            "de fora, não deveria enxergar o que está atrás da bush")


class TestWallNeverEntersBlob(unittest.TestCase):
    """Regressão de segurança: mesmo que uma bush esteja fisicamente
    ENCOSTADA numa parede/pedra/tronco sólido no mapa, o blob de exceção
    nunca "vaza" pra dentro do sólido — achado durante a escrita destes
    testes: sem o parâmetro `is_solid`, o BFS puramente geométrico
    atravessaria a parede inteira (já que ela também é `is_blocking`),
    deixando-a transparente por engano pra quem está na bush ao lado."""

    def test_parede_encostada_na_bush_nao_entra_no_blob(self):
        bush_blob = {(2, 2), (2, 3)}
        wall = {(3, 2), (3, 3)}  # parede colada, célula vizinha da bush
        blocked = bush_blob | wall

        is_blocking = _blocking_from_set(blocked)
        is_solid    = _blocking_from_set(wall)  # só a parede é sólida

        result = local_vision_blob(2, 2, is_blocking, is_solid)
        self.assertEqual(result, frozenset(bush_blob))
        self.assertTrue(wall.isdisjoint(result),
            "parede encostada na bush não deveria virar transparente")


if __name__ == "__main__":
    unittest.main()
