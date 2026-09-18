"""
tests/test_tiro_multiplo_reticle_cache.py — 17/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #7: `SpellCastSystem.render()`
(mira de Tiro Múltiplo, `ui/spell_system.py`) recriava uma Surface
rotacionada via `pygame.transform.rotate()` TODO frame, mesmo quando o
mouse não se move o bastante pra mudar o ângulo de forma perceptível —
custo de CPU desnecessário, sem bug de comportamento (a mira sempre
apontou certo).

Prova: (1) 2 chamadas de render() com o mouse na MESMA posição (mesmo
bucket de 5°) reusam a MESMA Surface do cache (id idêntico), sem crescer
o cache; (2) mover o mouse pra um ângulo bem diferente cria uma 2ª
entrada no cache; (3) o cache nunca ultrapassa 360/5=72 entradas mesmo
girando o mouse em volta do player inteiro (espaço de chaves
naturalmente limitado).

Mouse simulado via monkeypatch de `pygame.mouse.get_pos` (mesmo padrão
de `tests/test_quest_reward_ui.py`) — NUNCA `pygame.mouse.set_pos()`:
esse depende do tamanho REAL da janela SDL corrente, que outro módulo
de teste (importado antes deste na mesma sessão de pytest) pode ter
deixado menor que as coordenadas usadas aqui, clampando o "mouse" pra
dentro dos limites e silenciosamente igualando posições que deveriam
ser diferentes — achado real rodando a suíte inteira (passava isolado,
falhava dentro de `pytest tests/`, sem depender do fix em si).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import Position, SpellCast, PlayerControlled
from ui.spell_system import SpellCastSystem


def _make_system_and_caster():
    world = World()
    screen = pygame.Surface((320, 240))
    sys_ = SpellCastSystem(world, screen)

    caster = world.create_entity()
    world.add_component(caster, Position(x=160, y=120))
    world.add_component(caster, PlayerControlled())
    world.add_component(caster, SpellCast(spell_id="tiro_multiplo"))
    return sys_, caster


class TestTiroMultiploReticleCache(unittest.TestCase):

    def setUp(self):
        self._orig_get_pos = pygame.mouse.get_pos
        self._mouse_pos = (0, 0)
        pygame.mouse.get_pos = lambda: self._mouse_pos

    def tearDown(self):
        pygame.mouse.get_pos = self._orig_get_pos

    def test_mesma_posicao_de_mouse_reusa_surface_do_cache(self):
        sys_, _caster = _make_system_and_caster()
        self._mouse_pos = (200, 120)
        sys_.render()
        self.assertEqual(len(sys_._tiro_aim_rot_cache), 1)
        cached_surface = next(iter(sys_._tiro_aim_rot_cache.values()))

        sys_.render()  # 2º frame, mouse não se moveu
        self.assertEqual(len(sys_._tiro_aim_rot_cache), 1,
                         "mesmo ângulo não deveria criar 2ª entrada no cache")
        self.assertIs(next(iter(sys_._tiro_aim_rot_cache.values())), cached_surface,
                      "deveria reusar o MESMO objeto Surface, não recriar")

    def test_angulo_bem_diferente_cria_nova_entrada(self):
        sys_, _caster = _make_system_and_caster()
        self._mouse_pos = (200, 120)   # aponta pra direita
        sys_.render()
        self._mouse_pos = (160, 40)    # aponta pra cima — ângulo bem diferente
        sys_.render()
        self.assertEqual(len(sys_._tiro_aim_rot_cache), 2)

    def test_cache_nunca_ultrapassa_espaco_de_angulos_possiveis(self):
        sys_, _caster = _make_system_and_caster()
        import math
        for deg in range(0, 360, 3):  # mais granular que o bucket de 5°
            rad = math.radians(deg)
            self._mouse_pos = (160 + int(50 * math.cos(rad)),
                               120 + int(50 * math.sin(rad)))
            sys_.render()
        max_buckets = 360 // sys_._TIRO_AIM_ANGLE_BUCKET_DEG
        self.assertLessEqual(len(sys_._tiro_aim_rot_cache), max_buckets)


if __name__ == "__main__":
    unittest.main()
