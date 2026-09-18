"""
tests/test_talent_node_icon.py — 17/09/2026, ver
arquitetura/PROBLEMAS_ARQUITETURA.md §182: nó de talento
(`ui/talent_system.py::_render_node`) desenhava só um retângulo
colorido + as 2 letras iniciais do nome, mesmo pra talentos que
desbloqueiam uma skill (que já tem ícone de verdade no atlas). Cada
talento ganhou um campo `"icon"` (`content/talent_data.py`) — os 13
que desbloqueiam skill foram auto-preenchidos reaproveitando o ícone
da própria skill; os outros 31 continuam `None` (fallback de letra).

Prova: (1) `_render_node` usa `ICONS.get(icon_key, ...)` quando o
talento tem `icon` setado, e NUNCA chama `ICONS.get()` quando não tem
(cai pro fallback de letra de sempre); (2) talento bloqueado escurece
o ícone (mesmo tratamento que a hotbar já usa pra skill indisponível),
talento desbloqueado mostra o ícone "puro"; (3) os 13 talentos com
`unlocks_skill` têm `icon` apontando pro ícone da skill certa, E esse
ícone está catalogado de verdade no atlas (não um nome inventado que
nunca resolve pra nada).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import TalentTree
from content.talent_data import TALENTS
from ui.talent_system import TalentSystem
import ui.talent_system as talent_system_mod


def _make_system_and_tree():
    world = World()
    player = world.create_entity()
    tt = TalentTree()
    world.add_component(player, tt)
    screen = pygame.Surface((1280, 720))
    sys_ = TalentSystem(world, player, screen)
    return sys_, tt


class TestTalentNodeIconLookup(unittest.TestCase):

    def setUp(self):
        self.sys_, self.tt = _make_system_and_tree()
        self._orig_get = talent_system_mod.ICONS.get
        self.calls = []
        def _spy(name, size):
            self.calls.append((name, size))
            return self._orig_get(name, size)
        talent_system_mod.ICONS.get = _spy

    def tearDown(self):
        talent_system_mod.ICONS.get = self._orig_get

    def test_talento_com_icon_chama_icons_get_com_a_chave_certa(self):
        talent_id = "cav_golpe_debilitante"
        self.assertEqual(TALENTS[talent_id]["icon"], "skill_golpe_debilitante")
        panel  = self.sys_._panel_rect()
        node_r = self.sys_._node_rect(panel, talent_id)
        self.sys_._render_node(panel, talent_id, self.tt, (80, 200, 100))
        self.assertIn(("skill_golpe_debilitante", node_r.width), self.calls,
                      "tamanho pedido ao ICONS.get() deveria ser exatamente o do slot do "
                      "talento (18/09/2026, pedido do usuário: ícone preenche o slot inteiro)")

    def test_talento_sem_icon_nunca_chama_icons_get(self):
        # cav_reflexos não desbloqueia skill nenhuma -> icon=None
        talent_id = "cav_reflexos"
        self.assertIsNone(TALENTS[talent_id]["icon"])
        panel = self.sys_._panel_rect()
        self.sys_._render_node(panel, talent_id, self.tt, (80, 200, 100))
        self.assertEqual(self.calls, [], "talento sem ícone catalogado não deveria consultar ICONS.get")


class TestTalentNodeIconLockedDimming(unittest.TestCase):
    """Isola o efeito do escurecimento controlando a COR do ícone (mock
    de ICONS.get devolvendo vermelho puro opaco) — sem isso, um teste
    que só compara pixel locked-vs-desbloqueado poderia passar por
    causa de qualquer OUTRA diferença visual entre os 2 estados (cor de
    fundo do nó, barra de progresso), não necessariamente pelo
    escurecimento do ícone em si."""

    def setUp(self):
        self.sys_, self.tt = _make_system_and_tree()
        self._orig_get = talent_system_mod.ICONS.get
        red = pygame.Surface((talent_system_mod.NODE_W,) * 2, pygame.SRCALPHA)
        red.fill((255, 0, 0, 255))
        talent_system_mod.ICONS.get = lambda name, size: red

    def tearDown(self):
        talent_system_mod.ICONS.get = self._orig_get

    def test_icone_desbloqueado_aparece_puro_bloqueado_aparece_escurecido(self):
        talent_id = "cav_punho_queixo"   # tem icon, requer outro talento (começa locked)
        panel  = self.sys_._panel_rect()
        node_r = self.sys_._node_rect(panel, talent_id)

        # bloqueado: sem requisito cumprido, available_points=0
        self.tt.available_points = 0
        self.sys_._render_node(panel, talent_id, self.tt, (80, 200, 100))
        pixel_locked = tuple(self.sys_.screen.get_at(node_r.center))

        # desbloqueia de verdade: aloca 1 ponto (current>0 -> não é mais "locked")
        self.tt.allocated[talent_id] = 1
        self.sys_._render_node(panel, talent_id, self.tt, (80, 200, 100))
        pixel_unlocked = tuple(self.sys_.screen.get_at(node_r.center))

        self.assertEqual(pixel_unlocked[:3], (255, 0, 0),
                         "ícone desbloqueado deveria aparecer com a cor pura (sem escurecer)")
        self.assertLess(pixel_locked[0], 255,
                        "ícone bloqueado deveria estar mais escuro que o vermelho puro")
        self.assertNotEqual(pixel_locked, pixel_unlocked)


class TestUnlocksSkillTalentsHaveCatalogedIcon(unittest.TestCase):

    def test_todo_talento_que_desbloqueia_skill_tem_icon_da_propria_skill_catalogado(self):
        from ui.icon_atlas_catalog import ICON_ATLAS
        for talent_id, t in TALENTS.items():
            unlocks = t.get("unlocks_skill")
            if not unlocks:
                continue
            expected_icon = f"skill_{unlocks}"
            self.assertEqual(t.get("icon"), expected_icon,
                             f"{talent_id} desbloqueia {unlocks} mas 'icon' não bate")
            self.assertIn(expected_icon, ICON_ATLAS,
                          f"{expected_icon} (usado por {talent_id}) não está catalogado no atlas")


if __name__ == "__main__":
    unittest.main()
