"""Âncora vertical do topo do nameplate (26/09/2026, pedido do usuário).

Antes:
  - sprite de objeto (torre/correio/boneco de treino): topo = topo do
    RECORTE cru do sprite (podia ter padding transparente/sombra acima
    do desenho de verdade, deixando um vão vazio).
  - personagem animado (jogador/player remoto): topo = topo do FRAME
    exatamente (`position.y - anchor_dy`), fazendo o nameplate flutuar
    acima do frame inteiro.

Agora:
  - sprite de objeto: sobe NAMEPLATE_SPRITE_TOP_GAP_PX a partir do
    PIXEL OPACO mais alto de verdade (via TileSpriteManager.
    get_top_opaque_y, que já ignora sombra "assada"/padding — mesmo
    threshold usado pelo contorno de seleção).
  - personagem animado: desce NAMEPLATE_CHAR_FRAME_TOP_INSET_PX a
    partir do topo do FRAME (NÃO do pixel real — decisão explícita do
    usuário, pra não "balançar" seguindo pose/direção).

Fonte única dos 2 valores: ui/hud_bars.py. Consumida em 3 lugares:
ui/render_system.py (entidade local), client/remote_entity_handlers.py
`_draw_mob_hp_bars` (mob/NPC remoto com sprite) e `_draw_remote_players`
(player remoto, sempre personagem animado).
"""
import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))


class TestTileSpriteTopOpaqueY(unittest.TestCase):
    """TileSpriteManager.get_top_opaque_y — unidade, sem depender de
    RenderSystem nenhum."""

    def test_sprite_com_padding_real_devolve_linha_maior_que_zero(self):
        from ui.tile_sprite_manager import TILE_SPRITES
        # vp_184 (boneco de treino) tem padding transparente real no
        # topo do recorte — valor exato confirmado por script antes de
        # escrever este teste (15px, ver histórico da conversa).
        top = TILE_SPRITES.get_top_opaque_y("vp_184")
        self.assertGreater(top, 0)
        self.assertEqual(top, 15)

    def test_repetido_devolve_o_mesmo_valor(self):
        from ui.tile_sprite_manager import TILE_SPRITES
        a = TILE_SPRITES.get_top_opaque_y("vp_184")
        b = TILE_SPRITES.get_top_opaque_y("vp_184")
        self.assertEqual(a, b)

    def test_sprite_id_inexistente_devolve_zero(self):
        from ui.tile_sprite_manager import TILE_SPRITES
        self.assertEqual(TILE_SPRITES.get_top_opaque_y("nao_existe_xyz"), 0)


class TestRenderSystemAncoraSpriteDeObjeto(unittest.TestCase):
    """Entidade LOCAL com sprite de objeto (ui/render_system.py) — topo
    do nameplate usa o pixel opaco real, não o recorte cru."""

    def test_hud_top_usa_pixel_opaco_real_do_sprite(self):
        from engine.world import World
        from engine.components import (Position, Renderable, Enemy, Combatant,
                                       EnemyTier, StatusEffects, CombatStats, EntityIdentity)
        from engine.tileset import TILE_SIZE
        from ui.systems import RenderSystem
        from ui.world_labels import WORLD_LABELS
        from ui.tile_sprite_manager import TILE_SPRITES
        from ui.hud_bars import NAMEPLATE_SPRITE_TOP_GAP_PX

        world = World()
        eid = world.create_entity()
        x, y = 200, 200
        world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
        world.add_component(eid, Renderable(color=(255, 215, 0), width=28, height=28,
                                            sprite_id="vp_184"))
        world.add_component(eid, Enemy())
        world.add_component(eid, Combatant())
        world.add_component(eid, EnemyTier(tier="boss"))
        world.add_component(eid, StatusEffects())
        world.add_component(eid, EntityIdentity(name="Boneco de treino", race="Mecânico",
                                                entity_class="Guerreiro", level=99, tier="Boss"))
        cs = CombatStats()
        cs.current_hp = cs.max_hp = 100
        world.add_component(eid, cs)

        screen = pygame.display.get_surface()
        rs = RenderSystem(world, screen)
        WORLD_LABELS._pending.clear()
        WORLD_LABELS._stack_offset.clear()
        rs.render(0, 0, nameplate_mode=0)

        entries = WORLD_LABELS._pending
        self.assertTrue(entries, "deveria ter enfileirado pelo menos a barra de HP")
        hud_top_world_y = entries[0][1]

        spr = TILE_SPRITES.get_raw_sprite("vp_184")
        sprite_top = y + TILE_SIZE / 2 - spr.get_height()
        top_opaque = TILE_SPRITES.get_top_opaque_y("vp_184")
        expected = sprite_top + top_opaque - NAMEPLATE_SPRITE_TOP_GAP_PX
        self.assertAlmostEqual(hud_top_world_y, expected)


class TestDrawMobHpBarsAncoraSpriteDeObjeto(unittest.TestCase):
    """Mesmo mecanismo, só que pelo caminho de mob/NPC REMOTO
    (client/remote_entity_handlers.py::_draw_mob_hp_bars) — mailbox/
    torre/boneco de treino remoto do ponto de vista do player local."""

    def test_world_y_top_usa_pixel_opaco_real_do_sprite(self):
        from engine.world import World
        from engine.components import (Position, Renderable, RemoteEntityMeta, EntityIdentity)
        from engine.tileset import TILE_SIZE
        from ui.tile_sprite_manager import TILE_SPRITES
        from ui.hud_bars import NAMEPLATE_SPRITE_TOP_GAP_PX
        import ui.world_labels as world_labels_mod
        from client.network_handlers import NetworkHandlers as _NH
        from client.remote_entity_handlers import RemoteEntityHandlers as _REH

        class _Fixture(_NH, _REH):
            def __init__(self, world, player_entity):
                self.world = world
                self.player_entity = player_entity
                self._remote_mobs = {}

        world = World()
        player = world.create_entity()
        fx = _Fixture(world, player)
        fx._zoom_surf = pygame.Surface((200, 200))
        fx._nameplate_mode = 0
        fx.font_sm = pygame.font.Font(None, 16)

        eid = world.create_entity()
        x, y = 300, 300
        world.add_component(eid, Position(x=x, y=y))
        world.add_component(eid, Renderable(color=(255, 215, 0), width=28, height=28,
                                            sprite_id="vp_184"))
        world.add_component(eid, RemoteEntityMeta(server_eid=eid, hp=100, hp_max=100))
        world.add_component(eid, EntityIdentity(name="Boneco de treino", race="humano",
                                                entity_class="npc", level=99))
        fx._remote_mobs[eid] = eid

        text_calls = []
        orig_text = world_labels_mod.WORLD_LABELS.add_text
        world_labels_mod.WORLD_LABELS.add_text = lambda *a, **k: text_calls.append((a, k))
        try:
            fx._draw_mob_hp_bars(0, 0)
        finally:
            world_labels_mod.WORLD_LABELS.add_text = orig_text

        self.assertEqual(len(text_calls), 1)
        world_y_top = text_calls[0][0][1]

        spr = TILE_SPRITES.get_raw_sprite("vp_184")
        sprite_top = y + TILE_SIZE / 2 - spr.get_height()
        top_opaque = TILE_SPRITES.get_top_opaque_y("vp_184")
        expected = sprite_top + top_opaque - NAMEPLATE_SPRITE_TOP_GAP_PX
        self.assertAlmostEqual(world_y_top, expected)


class TestDrawRemotePlayersAncoraPersonagemAnimado(unittest.TestCase):
    """client/remote_entity_handlers.py::_draw_remote_players — sempre
    personagem animado (RemoteControlled.class_id nunca é vazio). Usa
    remoto MORTO pra não precisar stubar _client_pvp_context (método
    de game.py, fora do escopo deste mixin) — _world_y_top é calculado
    igual antes do check de morto, então serve pra verificar a âncora
    sem precisar da dependência externa."""

    def test_world_y_top_e_topo_do_frame_mais_inset_fixo(self):
        from engine.world import World
        from engine.components import Position, RemoteControlled
        from engine.character_animation import character_anchor_offset
        from ui.hud_bars import NAMEPLATE_CHAR_FRAME_TOP_INSET_PX
        import ui.world_labels as world_labels_mod
        from client.network_handlers import NetworkHandlers as _NH
        from client.remote_entity_handlers import RemoteEntityHandlers as _REH

        class _Fixture(_NH, _REH):
            def __init__(self, world, player_entity):
                self.world = world
                self.player_entity = player_entity
                self._remote_players = {}

        world = World()
        player = world.create_entity()
        fx = _Fixture(world, player)
        fx._nameplate_mode = 0
        fx.font_sm = pygame.font.Font(None, 16)

        eid = world.create_entity()
        x, y = 400, 400
        world.add_component(eid, Position(x=x, y=y))
        # hp<=0 => morto, pula o branch que precisa de _client_pvp_context
        world.add_component(eid, RemoteControlled(class_id="guerreiro", hp=0, hp_max=100))
        fx._remote_players[eid] = eid

        text_calls = []
        orig_text = world_labels_mod.WORLD_LABELS.add_text
        world_labels_mod.WORLD_LABELS.add_text = lambda *a, **k: text_calls.append((a, k))
        try:
            fx._draw_remote_players(0, 0)
        finally:
            world_labels_mod.WORLD_LABELS.add_text = orig_text

        self.assertEqual(len(text_calls), 1)
        world_y_top = text_calls[0][0][1]

        _, anchor_dy = character_anchor_offset()
        expected = y - anchor_dy + NAMEPLATE_CHAR_FRAME_TOP_INSET_PX
        self.assertAlmostEqual(world_y_top, expected)


if __name__ == "__main__":
    unittest.main()
