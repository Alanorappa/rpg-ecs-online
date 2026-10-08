"""Contorno de seleção de alvo (25-26/09/2026, pedido do usuário).

Histórico: retângulo sólido amarelo fixo (original) → `Mask.outline()`
traçando a borda exata da silhueta, `pygame.draw.polygon` (26/09/2026)
→ **anel externo dilatado** (07/10/2026, pedido do usuário: o contorno
por `outline()` ficava "na frente" do sprite E bem em cima da própria
borda — metade do traço caía DENTRO da área opaca, só a metade fina de
fora era visível, quase imperceptível). `_get_selection_ring_mask`
(`ui/render_system.py`) dilata a silhueta por alguns pixels e REMOVE a
original (`Mask.erase`) — o que resta nunca sobrepõe o sprite, então
não importa mais se é desenhado antes ou depois dele. Cor por
disposição (mesmo esquema da barra de HP: hostil=vermelho,
amigável=verde, neutro=amarelo). Sem sprite/frame disponível, cai no
retângulo de sempre — só que colorido por disposição também, em vez do
amarelo fixo.

Estes testes cobrem os 3 caminhos (sprite de objeto, frame de
personagem, retângulo simples): pra sprite/frame, confirma que nem
`pygame.draw.polygon` (mecanismo antigo) nem o fallback de retângulo
disparam — só o `blit` do anel (verificado separadamente, na unidade,
pelo teste de `_get_selection_ring_mask` abaixo). Pra retângulo simples
(sem sprite), o fallback continua sendo `pygame.draw.rect`, inalterado.
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


class TestSelectionRingMask(unittest.TestCase):
    """Unidade: `_get_selection_ring_mask` (ui/render_system.py) — a
    dilatação em si, isolada da renderização completa."""

    def _make_square_surface(self):
        surf = pygame.Surface((20, 20), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        pygame.draw.rect(surf, (255, 0, 0, 255), (5, 5, 10, 10))
        return surf

    def test_anel_nao_fica_vazio_pra_sprite_real(self):
        from ui.systems import RenderSystem
        from engine.world import World
        rs = RenderSystem(World(), pygame.display.get_surface())
        ring = rs._get_selection_ring_mask(self._make_square_surface())
        self.assertGreater(ring.count(), 0, "anel não deveria ficar vazio pra um sprite real")

    def test_anel_nunca_sobrepoe_a_silhueta_original(self):
        """O MESMO teste que validei manualmente antes de implementar —
        formalizado aqui: AND bit a bit entre o anel e a máscara original
        tem que ser SEMPRE zero (nenhum pixel em comum)."""
        from ui.systems import RenderSystem
        from engine.world import World
        surf = self._make_square_surface()
        rs = RenderSystem(World(), pygame.display.get_surface())
        ring = rs._get_selection_ring_mask(surf)
        base = rs._get_mask(surf)
        overlap = base.overlap_area(ring, (0, 0))
        self.assertEqual(overlap, 0, "anel de seleção nunca pode sobrepor o sprite real")

    def test_anel_fica_fora_do_raio_de_dilatacao(self):
        """Ponto bem no canto da superfície (fora do raio de 2px a partir
        da borda do quadrado) não deveria fazer parte do anel."""
        from ui.systems import RenderSystem
        from engine.world import World
        surf = self._make_square_surface()
        rs = RenderSystem(World(), pygame.display.get_surface())
        ring = rs._get_selection_ring_mask(surf)
        self.assertFalse(ring.get_at((0, 0)), "canto da superfície está fora do raio de dilatação")

    def test_cache_devolve_a_mesma_instancia(self):
        from ui.systems import RenderSystem
        from engine.world import World
        surf = self._make_square_surface()
        rs = RenderSystem(World(), pygame.display.get_surface())
        self.assertIs(rs._get_selection_ring_mask(surf), rs._get_selection_ring_mask(surf))


class TestSelecaoUsaSilhuetaDeSprite(unittest.TestCase):
    """Alvo com sprite real de objeto (mesmo padrão da caixa de correio,
    vp_180) — contorno novo (anel dilatado, blit) em vez do mecanismo
    antigo (`Mask.outline()` + `draw.polygon`) ou do fallback de
    retângulo. A correção da FORMA do anel já está coberta, na unidade,
    por `TestSelectionRingMask` acima — aqui só confirma que nenhum dos
    2 caminhos ANTIGOS dispara pra um alvo com sprite real."""

    def test_sprite_nao_usa_mais_polygon_nem_cai_no_fallback_de_retangulo(self):
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

        self.assertEqual(calls_poly, [], "mecanismo antigo (Mask.outline + draw.polygon) não deveria mais disparar")
        _HOSTIL = (200, 40, 40)   # DISPOSITION_HP_COLORS["hostil"]
        self.assertFalse(any(a[1] == _HOSTIL for a, _ in calls_rect),
                         "não deveria ter caído no fallback de retângulo (sprite real tem anel não-vazio)")


class TestSelecaoUsaSilhuetaDePersonagem(unittest.TestCase):
    """Alvo com frame de personagem animado (player remoto) — mesma
    verificação de ausência dos 2 caminhos antigos."""

    def test_frame_nao_usa_mais_polygon_nem_cai_no_fallback_de_retangulo(self):
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

        self.assertEqual(calls_poly, [], "mecanismo antigo (Mask.outline + draw.polygon) não deveria mais disparar")
        _AMIGAVEL = (0, 200, 60)   # DISPOSITION_HP_COLORS["amigavel"]
        self.assertFalse(any(a[1] == _AMIGAVEL for a, _ in calls_rect),
                         "não deveria ter caído no fallback de retângulo (frame real tem anel não-vazio)")


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
