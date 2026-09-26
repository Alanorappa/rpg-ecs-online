"""Contorno de seleção de alvo (25-26/09/2026, pedido do usuário).

Antes: retângulo sólido amarelo fixo em volta do alvo (ui/render_system.py).
Agora: `pygame.mask.Mask.outline()` traça o contorno real do sprite (ou
do frame de personagem) — contínuo, não tracejado (correção do usuário
26/09/2026) — já ignorando a sombra "assada" no PNG (sombra tem alpha
~89, objeto tem alpha 255, e o threshold padrão do pygame, 127, separa
os dois sozinho). `pygame.draw.polygon(..., width)` desenha esse
contorno fechado direto. Cor por disposição (mesmo esquema da barra de
HP: hostil=vermelho, amigável=verde, neutro=amarelo). Sem sprite/frame
disponível, cai no retângulo de sempre — só que colorido por disposição
também, em vez do amarelo fixo.

Estes testes cobrem os 3 caminhos (sprite de objeto, frame de
personagem, retângulo simples) verificando que o desenho certo
(pygame.draw.polygon OU pygame.draw.rect) acontece com a cor certa, sem
travar.
"""
import unittest
import pygame

pygame.init()
# 320x240 (não 1x1) — pygame.display.set_mode() é global pro processo:
# um tamanho minúsculo aqui sobrescreveria a tela de QUALQUER outro
# arquivo de teste que rode depois deste na mesma sessão do pytest
# (bug real achado 26/09/2026 rodando a suíte cheia — testes de nameplate/
# tab-target de outro arquivo quebravam só quando este arquivo entrava
# na mesma execução). Mesmo tamanho já usado por tests/test_client_ui.py.
pygame.display.set_mode((320, 240))


def _make_local_player(world, target_eid, x=100, y=100):
    from engine.components import Position, PlayerControlled, CombatState
    eid = world.create_entity()
    world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    world.add_component(eid, PlayerControlled())
    cs = CombatState()
    cs.target_entity_id = target_eid
    world.add_component(eid, cs)
    return eid


def _spy_pygame_draw():
    """Substitui pygame.draw.polygon/rect por gravadores de chamada,
    devolvendo (calls_poly, calls_rect, restore)."""
    calls_poly = []
    calls_rect = []
    orig_poly = pygame.draw.polygon
    orig_rect = pygame.draw.rect

    def _poly(*a, **k):
        calls_poly.append((a, k))
        return orig_poly(*a, **k)

    def _rect(*a, **k):
        calls_rect.append((a, k))
        return orig_rect(*a, **k)

    pygame.draw.polygon = _poly
    pygame.draw.rect = _rect

    def _restore():
        pygame.draw.polygon = orig_poly
        pygame.draw.rect = orig_rect

    return calls_poly, calls_rect, _restore


class TestSelecaoUsaSilhuetaDeSprite(unittest.TestCase):
    """Alvo com sprite real de objeto (mesmo padrão da caixa de correio,
    vp_180) — contorno deve seguir Mask.outline(), não um retângulo."""

    def test_sprite_usa_draw_polygon_com_cor_hostil(self):
        from engine.world import World
        from engine.components import Position, Renderable, Faction
        from ui.systems import RenderSystem

        world = World()
        target = world.create_entity()
        world.add_component(target, Position(x=200, y=200, prev_x=200, prev_y=200))
        world.add_component(target, Renderable(color=(100, 140, 200), width=32, height=32,
                                                sprite_id="vp_180"))
        world.add_component(target, Faction(faction_id="monstros_hostis"))
        _make_local_player(world, target, x=100, y=100)

        calls_poly, calls_rect, restore = _spy_pygame_draw()
        try:
            screen = pygame.display.get_surface()
            rs = RenderSystem(world, screen)
            rs.render(0, 0)
        finally:
            restore()

        self.assertEqual(len(calls_poly), 1, "sprite deveria gerar contorno por silhueta")
        _, color, points, width = calls_poly[0][0]
        self.assertEqual(color, (200, 40, 40))   # DISPOSITION_HP_COLORS["hostil"]
        self.assertTrue(points, "contorno nao deveria vir vazio pro sprite vp_180")
        self.assertGreater(width, 0, "contorno tem que ser so a borda, nunca preenchido")
        # Nenhum draw.rect deveria ter essa cor (nao caiu no fallback de retangulo)
        self.assertFalse(any(a[1] == (200, 40, 40) for a, _ in calls_rect))


class TestSelecaoUsaSilhuetaDePersonagem(unittest.TestCase):
    """Alvo com frame de personagem animado (player remoto) — mesmo
    mecanismo, cor amigável."""

    def test_frame_de_personagem_usa_draw_polygon_com_cor_amigavel(self):
        from engine.world import World
        from engine.components import Position, Renderable, RemoteControlled, TileMovement, Faction
        from ui.systems import RenderSystem

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

        calls_poly, calls_rect, restore = _spy_pygame_draw()
        try:
            screen = pygame.display.get_surface()
            rs = RenderSystem(world, screen)
            rs.render(0, 0)
        finally:
            restore()

        self.assertEqual(len(calls_poly), 1, "frame de personagem deveria gerar contorno por silhueta")
        _, color, points, width = calls_poly[0][0]
        self.assertEqual(color, (0, 200, 60))   # DISPOSITION_HP_COLORS["amigavel"] (HP_COLOR)
        self.assertTrue(points, "contorno nao deveria vir vazio pro frame do guerreiro")
        self.assertGreater(width, 0)


class TestSelecaoSemSpriteCaiNoRetangulo(unittest.TestCase):
    """Alvo sem sprite_id e sem class_id resolvível (mob genérico de
    retângulo colorido) — fallback continua sendo retângulo, só que
    colorido por disposição em vez do amarelo fixo."""

    def test_mob_sem_sprite_usa_draw_rect_com_cor_neutra(self):
        from engine.world import World
        from engine.components import Position, Renderable, Faction
        from ui.systems import RenderSystem

        world = World()
        target = world.create_entity()
        world.add_component(target, Position(x=200, y=200, prev_x=200, prev_y=200))
        world.add_component(target, Renderable(color=(150, 60, 60), width=24, height=24))
        world.add_component(target, Faction(faction_id="vida_selvagem"))
        _make_local_player(world, target, x=100, y=100)

        calls_poly, calls_rect, restore = _spy_pygame_draw()
        try:
            screen = pygame.display.get_surface()
            rs = RenderSystem(world, screen)
            rs.render(0, 0)
        finally:
            restore()

        self.assertEqual(calls_poly, [])
        # draw.rect também é chamado pra pintar o corpo do mob (preenchido,
        # width implícito 0) — o contorno de seleção é a chamada com width>0.
        outline_calls = [(a, k) for a, k in calls_rect if len(a) >= 4 and a[3] > 0]
        self.assertEqual(len(outline_calls), 1, "deveria ter exatamente 1 contorno desenhado")
        _, color, rect, width = outline_calls[0][0]
        self.assertEqual(color, (235, 220, 110))   # DISPOSITION_HP_COLORS["neutro"]


if __name__ == "__main__":
    unittest.main()
