"""
tests/test_in_range_euclidean_attack.py — 14/09/2026, bug real relatado
pelo usuário: alcance de ataque (skill/auto-attack/torre/minion/mob)
medido em Chebyshev "esticava" na diagonal — diagonal conta igual a
reto, então N tiles na diagonal cobrem ~41% mais distância REAL do que
N tiles em linha reta. Usuário testou em jogo: 5 tiles na diagonal
(distância real ~7.07) ainda conseguia lançar Bola de Fogo com
cast_range=6.

Fix: `engine/utils.py::in_range()` — Euclidiana em tile (círculo de
verdade), ponto único de verdade pra alcance de ataque de alvo único.
EXCEÇÃO deliberada: alcance melee (<=1 tile, adjacência de 8 direções)
continua Chebyshev — com Euclidiana, adjacência diagonal (distância
real ~1.41) ficaria fora de um alcance=1, quebrando golpe corpo-a-corpo
em 4 das 8 direções. Ver PROBLEMAS_ARQUITETURA.md §135.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import CombatState, TileMovement, CombatStats, Position
from engine.skill_handlers import HeadlessSkillHandler
from engine.utils import in_range, chebyshev


class TestInRangeFuncaoPura(unittest.TestCase):

    def test_reto_exatamente_na_borda_entra(self):
        self.assertTrue(in_range(0, 0, 6, 0, 6))

    def test_reto_um_tile_alem_da_borda_fica_fora(self):
        self.assertFalse(in_range(0, 0, 7, 0, 6))

    def test_diagonal_mesmo_chebyshev_da_borda_fica_fora(self):
        # chebyshev(0,0,5,5) == 5 (dentro do quadrado antigo de alcance 6),
        # mas distância real = 5*sqrt(2) ~= 7.07 — fora do círculo real de 6.
        self.assertEqual(chebyshev(0, 0, 5, 5), 5)
        self.assertFalse(in_range(0, 0, 5, 5, 6))

    def test_diagonal_dentro_do_circulo_real_entra(self):
        # 4,4 -> distância real = 4*sqrt(2) ~= 5.66, dentro do círculo de 6.
        self.assertTrue(in_range(0, 0, 4, 4, 6))

    def test_mesma_posicao_sempre_em_alcance(self):
        self.assertTrue(in_range(3, 3, 3, 3, 0))


def _make_world_with_target(px: int, py: int, tx: int, ty: int):
    world = World()
    player = world.create_entity()
    ptm = TileMovement(current_tile_x=px, current_tile_y=py)
    world.add_component(player, ptm)
    cs = CombatState()
    world.add_component(player, cs)

    target = world.create_entity()
    ttm = TileMovement(current_tile_x=tx, current_tile_y=ty)
    world.add_component(target, ttm)
    tcs = CombatStats()
    world.add_component(target, tcs)
    world.add_component(target, Position(x=tx * 32, y=ty * 32))

    return world, player, ptm, cs, target


class TestResolveTargetAlcanceRangedUsaEuclidiana(unittest.TestCase):
    """`SkillHandlers._resolve_target` (engine/skill_handlers.py) é o gate
    de alcance de skill compartilhado cliente+servidor — reproduz aqui o
    cenário exato relatado pelo usuário (Bola de Fogo, cast_range=6)."""

    def test_alvo_diagonal_fora_do_circulo_real_e_rejeitado(self):
        world, player, ptm, cs, target = _make_world_with_target(0, 0, 5, 5)
        cs.target_entity_id = target
        handler = HeadlessSkillHandler(world, player)
        result = handler._resolve_target(cs, ptm, 6)
        self.assertEqual(result, -1,
            "alvo a 5 tiles na diagonal (dist real ~7.07) devia ficar fora do alcance real de 6")

    def test_alvo_reto_na_borda_e_aceito(self):
        world, player, ptm, cs, target = _make_world_with_target(0, 0, 6, 0)
        cs.target_entity_id = target
        handler = HeadlessSkillHandler(world, player)
        result = handler._resolve_target(cs, ptm, 6)
        self.assertEqual(result, target,
            "alvo a 6 tiles em linha reta devia continuar em alcance")

    def test_alvo_diagonal_dentro_do_circulo_real_e_aceito(self):
        world, player, ptm, cs, target = _make_world_with_target(0, 0, 4, 4)
        cs.target_entity_id = target
        handler = HeadlessSkillHandler(world, player)
        result = handler._resolve_target(cs, ptm, 6)
        self.assertEqual(result, target,
            "alvo a 4 tiles na diagonal (dist real ~5.66) devia continuar em alcance de 6")


class TestResolveTargetMeleeContinuaChebyshev(unittest.TestCase):
    """Exceção deliberada: alcance melee (_max_range<=1) precisa continuar
    aceitando as 8 direções igualmente — regressão que a 1ª versão deste
    fix introduziu e foi corrigida antes de commitar."""

    def test_melee_diagonal_adjacente_continua_aceito(self):
        world, player, ptm, cs, target = _make_world_with_target(5, 5, 6, 6)
        cs.target_entity_id = target
        handler = HeadlessSkillHandler(world, player)
        result = handler._resolve_target(cs, ptm, 1)
        self.assertEqual(result, target,
            "adjacência diagonal (melee) precisa continuar válida — regressão real, já corrigida")

    def test_melee_ortogonal_adjacente_continua_aceito(self):
        world, player, ptm, cs, target = _make_world_with_target(5, 5, 6, 5)
        cs.target_entity_id = target
        handler = HeadlessSkillHandler(world, player)
        result = handler._resolve_target(cs, ptm, 1)
        self.assertEqual(result, target, "adjacência ortogonal (melee) precisa continuar válida")


if __name__ == "__main__":
    unittest.main()
