"""
tests/test_character_animation.py — animação base de personagem
(09/09/2026, pedido do usuário, implementação incremental).

Cobre 4 peças: `engine.utils.facing_from_delta` (direção pura),
`engine.character_animation.get_character_frame` (carrega/corta/
espelha sprite, com fallback None quando o arquivo não existe),
`TileMovementSystem` gravando `TileMovement.facing` durante um passo
real (integração, não atalho), e o topo do nameplate/HUD em
`RenderSystem` acompanhando a altura REAL do sprite (não o
`renderable.height` antigo de 24px — bug real relatado pelo usuário:
nameplate cobria a metade de cima do sprite de 48px de altura).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()
pygame.display.set_mode((100, 100))

from engine.utils import facing_from_delta
from engine.world import World
from engine.components import Position, TileMovement
from engine.world_systems import TileMovementSystem
from shared.constants import TILE_SIZE


class TestFacingFromDelta(unittest.TestCase):

    def test_direcoes_cardinais(self):
        self.assertEqual(facing_from_delta(0, 1), "down")
        self.assertEqual(facing_from_delta(0, -1), "up")
        self.assertEqual(facing_from_delta(1, 0), "right")
        self.assertEqual(facing_from_delta(-1, 0), "left")

    def test_empate_na_diagonal_eixo_vertical_ganha(self):
        self.assertEqual(facing_from_delta(1, 1), "down")
        self.assertEqual(facing_from_delta(-1, 1), "down")
        self.assertEqual(facing_from_delta(1, -1), "up")
        self.assertEqual(facing_from_delta(-1, -1), "up")

    def test_delta_zero_usa_default_down(self):
        self.assertEqual(facing_from_delta(0, 0), "down")

    def test_vertical_domina_quando_maior_que_horizontal(self):
        self.assertEqual(facing_from_delta(1, 2), "down")
        self.assertEqual(facing_from_delta(2, 1), "right")


class TestGetCharacterFrame(unittest.TestCase):
    """`guerreiro_idle_down.png` é um asset real já commitado (309x48,
    8 frames de 32x48) — usado direto, sem fixture sintética, pra
    também servir de teste de regressão do arquivo em si."""

    def setUp(self):
        from engine import character_animation as _ca
        _ca._frame_cache.clear()

    def tearDown(self):
        from engine import character_animation as _ca
        _ca._frame_cache.clear()

    def test_combinacao_existente_retorna_frame(self):
        from engine.character_animation import get_character_frame
        frame = get_character_frame("guerreiro", "idle", "down", 0)
        self.assertIsNotNone(frame)
        w, h = frame.get_size()
        self.assertEqual(w, 32)

    def test_frame_avanca_com_o_tempo(self):
        from engine.character_animation import get_character_frame
        f0  = get_character_frame("guerreiro", "idle", "down", 0)
        f65 = get_character_frame("guerreiro", "idle", "down", 65)
        self.assertIsNot(f0, f65, "frame deveria avançar após frame_ms (65 default)")

    def test_combinacao_inexistente_retorna_none(self):
        """class_id fictício — não pode depender de quais sprites reais
        já existem em assets/sprites/ neste momento (o usuário vai
        adicionando aos poucos)."""
        from engine.character_animation import get_character_frame
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "run", "down", 0))
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "idle", "down", 0))

    def test_left_e_right_sem_arquivo_nenhum_retorna_none_sem_recursao_infinita(self):
        """Regressão: bug real pego testando — sem o guard, left tentava
        espelhar right, que tentava espelhar left de volta, infinitamente.
        Usa class_id fictício (nunca "guerreiro") — não pode depender de
        quais sprites reais já existem em assets/sprites/ neste momento."""
        from engine.character_animation import get_character_frame
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "idle", "left", 0))
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "idle", "right", 0))

    def test_espelha_lado_que_falta_a_partir_do_lado_que_existe(self):
        from engine.character_animation import get_character_frame
        path = os.path.join("assets", "sprites", "testclasse_idle_right.png")
        surf = pygame.Surface((64, 32), pygame.SRCALPHA)
        pygame.draw.rect(surf, (255, 0, 0, 255), (0, 0, 16, 32))
        pygame.draw.rect(surf, (0, 0, 255, 255), (16, 0, 16, 32))
        pygame.image.save(surf, path)
        try:
            left_frame = get_character_frame("testclasse", "idle", "left", 0)
            self.assertIsNotNone(left_frame)
            # Espelhado: o que era vermelho na borda esquerda de "right"
            # vira a borda DIREITA de "left".
            self.assertEqual(left_frame.get_at((31, 0))[:3], (255, 0, 0))
            self.assertEqual(left_frame.get_at((0, 0))[:3], (0, 0, 255))
        finally:
            os.remove(path)


class TestTileMovementSystemSetsFacing(unittest.TestCase):
    """Integração real: TileMovementSystem.update() (não um atalho de
    teste) deve gravar `facing` a partir do delta target-current durante
    um passo, e preservar o último valor depois que o passo termina."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, Position(x=0, y=0))
        self.tm = TileMovement()
        self.world.add_component(self.eid, self.tm)
        self.sys = TileMovementSystem(self.world)

    def _start_step(self, dx: int, dy: int) -> None:
        self.tm.current_tile_x = 5
        self.tm.current_tile_y = 5
        self.tm.target_tile_x  = 5 + dx
        self.tm.target_tile_y  = 5 + dy
        self.tm.start_pixel_x  = 5 * TILE_SIZE
        self.tm.start_pixel_y  = 5 * TILE_SIZE
        self.tm.target_pixel_x = (5 + dx) * TILE_SIZE
        self.tm.target_pixel_y = (5 + dy) * TILE_SIZE
        self.tm.progress    = 0.0
        self.tm.is_moving   = True
        self.tm.move_duration = 0.2

    def test_passo_pra_direita_grava_facing_right(self):
        self._start_step(dx=1, dy=0)
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "right")

    def test_passo_pra_baixo_grava_facing_down(self):
        self._start_step(dx=0, dy=1)
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "down")

    def test_facing_permanece_apos_passo_completar_parado(self):
        self._start_step(dx=-1, dy=0)
        self.sys.update(dt=1.0)   # dt grande o bastante pra completar o passo (progress>=1)
        self.assertFalse(self.tm.is_moving)
        self.assertEqual(self.tm.facing, "left",
            "facing do último passo real deve continuar valendo enquanto parado")


class TestRenderSystemNameplateTopFollowsSpriteHeight(unittest.TestCase):
    """`guerreiro_idle_down.png` (32x48, real) é quase o dobro da altura
    do retângulo antigo (24px) — o topo do nameplate precisa subir pra
    acompanhar, não ficar fixo em `renderable.height/2`."""

    def _make_player(self, world):
        from engine.components import (Position, Renderable, PlayerControlled,
                                       CharacterStats, CombatStats, TileMovement)
        eid = world.create_entity()
        world.add_component(eid, Position(x=100, y=100, prev_x=100, prev_y=100))
        world.add_component(eid, Renderable(color=(40, 50, 60), width=24, height=24))
        world.add_component(eid, PlayerControlled())
        world.add_component(eid, CharacterStats(name="Herói", class_id="guerreiro"))
        cs = CombatStats()
        cs.max_hp = 100
        cs.current_hp = 100
        world.add_component(eid, cs)
        tm = TileMovement()
        tm.is_moving = False
        tm.facing = "down"
        world.add_component(eid, tm)
        return eid

    def test_topo_do_hud_usa_altura_do_sprite_nao_do_retangulo_antigo(self):
        from engine.world import World
        from ui.systems import RenderSystem
        from ui.world_labels import WORLD_LABELS

        world = World()
        eid = self._make_player(world)
        screen = pygame.display.get_surface()
        rs = RenderSystem(world, screen)

        WORLD_LABELS._pending.clear()
        WORLD_LABELS._stack_offset.clear()
        rs.render(0, 0, nameplate_mode=0)

        # Todo elemento empilhado pra este entity_id usa o MESMO
        # world_y de topo (stack_key=eid) — a barra de HP é o 1º.
        entries = [e for e in WORLD_LABELS._pending]
        self.assertTrue(entries, "deveria ter enfileirado pelo menos a barra de HP")
        hud_top_world_y = entries[0][1]

        # Sprite real é 32x48; âncora de pé é position.y+12 (mesmo
        # offset usado pro blit) — topo esperado = position.y+12-48.
        self.assertAlmostEqual(hud_top_world_y, 100 + 12 - 48)
        # Regressão: NÃO pode ser o cálculo antigo (baseado em
        # renderable.height=24), que ficaria bem mais baixo — dentro do
        # próprio sprite.
        self.assertNotAlmostEqual(hud_top_world_y, 100 - 24 / 2)


if __name__ == "__main__":
    unittest.main()
