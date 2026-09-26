"""Tracejado de seleção de alvo (25/09/2026, pedido do usuário).

Antes: retângulo sólido amarelo fixo em volta do alvo (ui/render_system.py).
Agora: tracejado que acompanha a SILHUETA real do sprite (via
`pygame.mask.Mask.outline()`, que já ignora a sombra "assada" no PNG —
sombra tem alpha ~89, objeto tem alpha 255, e o threshold padrão do
pygame, 127, separa os dois sozinho), com cor por disposição (mesmo
esquema da barra de HP: hostil=vermelho, amigável=verde, neutro=amarelo).
Sem sprite/frame disponível, cai no retângulo tracejado de sempre — só
que colorido por disposição também.

Estes testes cobrem os 3 caminhos (sprite de objeto, frame de
personagem, retângulo simples) verificando que a função certa é chamada
com a cor certa, sem travar.
"""
import unittest
import pygame

pygame.init()
pygame.display.set_mode((1, 1))


def _make_local_player(world, target_eid, x=100, y=100):
    from engine.components import Position, PlayerControlled, CombatState
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, PlayerControlled())
    cs = CombatState()
    cs.target_entity_id = target_eid
    world.add_component(eid, cs)
    return eid


class TestSelecaoUsaSilhuetaDeSprite(unittest.TestCase):
    """Alvo com sprite real de objeto (mesmo padrão da caixa de correio,
    vp_180) — tracejado deve seguir Mask.outline(), não um retângulo."""

    def test_sprite_usa_draw_dashed_polygon_com_cor_hostil(self):
        from engine.world import World
        from engine.components import Position, Renderable, Faction
        from ui.systems import RenderSystem
        import ui.render_system as render_system_mod

        world = World()
        target = world.create_entity()
        world.add_component(target, Position(x=200, y=200, prev_x=200, prev_y=200))
        world.add_component(target, Renderable(color=(100, 140, 200), width=32, height=32,
                                                sprite_id="vp_180"))
        world.add_component(target, Faction(faction_id="monstros_hostis"))
        _make_local_player(world, target, x=100, y=100)

        calls_poly = []
        calls_rect = []
        orig_poly = render_system_mod.draw_dashed_polygon
        orig_rect = render_system_mod.draw_dashed_rect
        render_system_mod.draw_dashed_polygon = lambda *a, **k: calls_poly.append((a, k))
        render_system_mod.draw_dashed_rect = lambda *a, **k: calls_rect.append((a, k))
        try:
            screen = pygame.display.get_surface()
            rs = RenderSystem(world, screen)
            rs.render(0, 0)
        finally:
            render_system_mod.draw_dashed_polygon = orig_poly
            render_system_mod.draw_dashed_rect = orig_rect

        self.assertEqual(len(calls_poly), 1, "sprite deveria gerar tracejado por silhueta")
        self.assertEqual(calls_rect, [], "nao deveria cair no fallback de retangulo")
        _, color, points = calls_poly[0][0]
        self.assertEqual(color, (200, 40, 40))   # DISPOSITION_HP_COLORS["hostil"]
        self.assertTrue(points, "contorno nao deveria vir vazio pro sprite vp_180")


class TestSelecaoUsaSilhuetaDePersonagem(unittest.TestCase):
    """Alvo com frame de personagem animado (player remoto) — mesmo
    mecanismo, cor amigável."""

    def test_frame_de_personagem_usa_draw_dashed_polygon_com_cor_amigavel(self):
        from engine.world import World
        from engine.components import Position, Renderable, RemoteControlled, TileMovement, Faction
        from ui.systems import RenderSystem
        import ui.render_system as render_system_mod

        world = World()
        target = world.create_entity()
        world.add_component(target, Position(x=200, y=200, prev_x=200, prev_y=200))
        world.add_component(target, Renderable(color=(40, 50, 60), width=24, height=24))
        world.add_component(target, RemoteControlled(class_id="guerreiro"))
        tm = TileMovement()
        tm.is_moving = False
        tm.facing = "down"
        world.add_component(target, tm)
        world.add_component(target, Faction(faction_id="guardas_vila"))
        _make_local_player(world, target, x=100, y=100)

        calls_poly = []
        calls_rect = []
        orig_poly = render_system_mod.draw_dashed_polygon
        orig_rect = render_system_mod.draw_dashed_rect
        render_system_mod.draw_dashed_polygon = lambda *a, **k: calls_poly.append((a, k))
        render_system_mod.draw_dashed_rect = lambda *a, **k: calls_rect.append((a, k))
        try:
            screen = pygame.display.get_surface()
            rs = RenderSystem(world, screen)
            rs.render(0, 0)
        finally:
            render_system_mod.draw_dashed_polygon = orig_poly
            render_system_mod.draw_dashed_rect = orig_rect

        self.assertEqual(len(calls_poly), 1, "frame de personagem deveria gerar tracejado por silhueta")
        self.assertEqual(calls_rect, [])
        _, color, points = calls_poly[0][0]
        self.assertEqual(color, (0, 200, 60))   # DISPOSITION_HP_COLORS["amigavel"] (HP_COLOR)
        self.assertTrue(points, "contorno nao deveria vir vazio pro frame do guerreiro")


class TestSelecaoSemSpriteCaiNoRetanguloTracejado(unittest.TestCase):
    """Alvo sem sprite_id e sem class_id resolvível (mob genérico de
    retângulo colorido) — fallback continua sendo retângulo, só que
    tracejado e colorido por disposição em vez do amarelo fixo sólido."""

    def test_mob_sem_sprite_usa_draw_dashed_rect_com_cor_neutra(self):
        from engine.world import World
        from engine.components import Position, Renderable, Faction
        from ui.systems import RenderSystem
        import ui.render_system as render_system_mod

        world = World()
        target = world.create_entity()
        world.add_component(target, Position(x=200, y=200, prev_x=200, prev_y=200))
        world.add_component(target, Renderable(color=(150, 60, 60), width=24, height=24))
        world.add_component(target, Faction(faction_id="vida_selvagem"))
        _make_local_player(world, target, x=100, y=100)

        calls_poly = []
        calls_rect = []
        orig_poly = render_system_mod.draw_dashed_polygon
        orig_rect = render_system_mod.draw_dashed_rect
        render_system_mod.draw_dashed_polygon = lambda *a, **k: calls_poly.append((a, k))
        render_system_mod.draw_dashed_rect = lambda *a, **k: calls_rect.append((a, k))
        try:
            screen = pygame.display.get_surface()
            rs = RenderSystem(world, screen)
            rs.render(0, 0)
        finally:
            render_system_mod.draw_dashed_polygon = orig_poly
            render_system_mod.draw_dashed_rect = orig_rect

        self.assertEqual(calls_poly, [])
        self.assertEqual(len(calls_rect), 1)
        _, color, rect = calls_rect[0][0]
        self.assertEqual(color, (235, 220, 110))   # DISPOSITION_HP_COLORS["neutro"]


if __name__ == "__main__":
    unittest.main()
