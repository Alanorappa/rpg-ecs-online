"""Animação de morte de mob (11/10/2026) — congela no último frame
(engine/mob_animation.py), e o ciclo de vida do "cadáver" em 2 casos:
sem loot (mob some do ECS local assim que a animação toca 1x) e com
loot (o Corpse existente passa a desenhar o frame congelado no lugar
da elipse, pelo mesmo tempo que já durava).

Testes de estado puro usam frames SINTÉTICOS (seed direto em
`_mob_frame_cache`) — não depende de nenhum sprite real estar
desenhado ainda, roda mesmo antes de qualquer mob ganhar arte de
morte."""
import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))

_TEST_MOB_KEY = "test_mob_death"  # já é o próprio slug (sem _ nas pontas) — mesmo valor com ou sem passar por engine.utils.slugify()


def _seed_death_frames(n=3, size=8):
    """Popula engine.mob_animation._mob_frame_cache com N frames
    sintéticos pro estado "death"/"down" de _TEST_MOB_KEY — evita
    depender de arquivo PNG real em assets/sprites/."""
    import engine.mob_animation as ma
    frames = [pygame.Surface((size, size)) for _ in range(n)]
    for i, f in enumerate(frames):
        f.fill((i * 30, 0, 0))
    ma._mob_frame_cache[(_TEST_MOB_KEY, "death", "down")] = frames
    return frames


def _clear_test_cache():
    import engine.mob_animation as ma
    for key in list(ma._mob_frame_cache.keys()):
        if key[0] == _TEST_MOB_KEY:
            del ma._mob_frame_cache[key]
    ma._mob_top_opaque_cache.pop(_TEST_MOB_KEY, None)


class TestMobDeathAnimState(unittest.TestCase):
    def tearDown(self):
        _clear_test_cache()

    def test_sem_sprite_de_morte_duracao_e_zero(self):
        from engine.mob_animation import get_mob_death_anim_duration_ms
        self.assertEqual(get_mob_death_anim_duration_ms("__mob_sem_death_nenhum__", "down"), 0)

    def test_sem_sprite_de_morte_last_frame_e_none(self):
        from engine.mob_animation import get_mob_death_last_frame
        self.assertIsNone(get_mob_death_last_frame("__mob_sem_death_nenhum__", "down"))

    def test_com_sprite_duracao_bate_com_frame_count(self):
        from engine.mob_animation import get_mob_death_anim_duration_ms, MOB_DEFAULT_FRAME_MS
        frames = _seed_death_frames(n=3)
        self.assertEqual(get_mob_death_anim_duration_ms(_TEST_MOB_KEY, "down"),
                         len(frames) * MOB_DEFAULT_FRAME_MS)

    def test_last_frame_e_o_ultimo_da_lista(self):
        from engine.mob_animation import get_mob_death_last_frame
        frames = _seed_death_frames(n=3)
        self.assertIs(get_mob_death_last_frame(_TEST_MOB_KEY, "down"), frames[-1])

    def test_trigger_liga_hold_last_frame(self):
        from engine.components import TileMovement
        from engine.mob_animation import trigger_mob_death_anim, MOB_DEATH_STATE
        _seed_death_frames(n=3)
        tm = TileMovement(facing="down")
        trigger_mob_death_anim(tm, _TEST_MOB_KEY, 1000)
        self.assertEqual(tm.anim_attack_state, MOB_DEATH_STATE)
        self.assertTrue(tm.anim_attack_hold_last_frame)

    def test_display_state_trava_no_ultimo_frame_apos_duracao(self):
        """Reprodução do pedido do usuário: animação toca, e depois de
        terminar o ciclo continua mostrando o ÚLTIMO frame pra sempre —
        nunca volta pra idle/run, diferente do golpe normal."""
        from engine.components import TileMovement
        from engine.mob_animation import (trigger_mob_death_anim, resolve_mob_display_state,
                                          MOB_DEATH_STATE, MOB_DEFAULT_FRAME_MS)
        frames = _seed_death_frames(n=3)
        tm = TileMovement(facing="down")
        trigger_mob_death_anim(tm, _TEST_MOB_KEY, 1000)

        # No meio da animação: toca normalmente.
        state, elapsed, _ = resolve_mob_display_state(tm, _TEST_MOB_KEY, 1000 + MOB_DEFAULT_FRAME_MS)
        self.assertEqual(state, MOB_DEATH_STATE)

        # MUITO depois do fim do ciclo (1 ciclo = 3*frame_ms) — ainda
        # "death", travado no ÚLTIMO frame (não pisca pra idle).
        duration = len(frames) * MOB_DEFAULT_FRAME_MS
        state2, elapsed2, frame_ms2 = resolve_mob_display_state(
            tm, _TEST_MOB_KEY, 1000 + duration + 5000)
        self.assertEqual(state2, MOB_DEATH_STATE)
        idx = int(elapsed2 // frame_ms2) % len(frames)
        self.assertEqual(idx, len(frames) - 1, "tem que resolver pro ÚLTIMO frame, não reiniciar o ciclo")

    def test_golpe_normal_continua_sem_hold(self):
        """trigger_mob_attack_anim (golpe normal, não-morte) continua
        expirando de volta pra idle/run — a mudança de hold_last_frame
        não pode vazar pro golpe comum."""
        from engine.components import TileMovement
        from engine.mob_animation import trigger_mob_attack_anim, resolve_mob_display_state
        from engine.mob_animation import MOB_DEFAULT_FRAME_MS
        _seed_attack = _seed_death_frames  # reusa helper só pra criar frames genéricos
        import engine.mob_animation as ma
        frames = [pygame.Surface((8, 8)) for _ in range(2)]
        ma._mob_frame_cache[(_TEST_MOB_KEY, "attack", "down")] = frames
        try:
            tm = TileMovement(facing="down", is_moving=False)
            trigger_mob_attack_anim(tm, _TEST_MOB_KEY, 1000)
            self.assertFalse(tm.anim_attack_hold_last_frame)
            duration = len(frames) * MOB_DEFAULT_FRAME_MS
            state, _, _ = resolve_mob_display_state(tm, _TEST_MOB_KEY, 1000 + duration + 500)
            self.assertIn(state, ("idle", "run"))
        finally:
            ma._mob_frame_cache.pop((_TEST_MOB_KEY, "attack", "down"), None)


class TestCorpseMobKeyField(unittest.TestCase):
    """engine.components.Corpse ganhou mob_key/facing — default vazio,
    sem regressão pra quem já cria Corpse sem esses campos (harvestable,
    testes antigos)."""

    def test_default_vazio(self):
        from engine.components import Corpse
        c = Corpse(loot=[], coins=10)
        self.assertEqual(c.mob_key, "")
        self.assertEqual(c.facing, "down")

    def test_create_corpse_passa_mob_key_adiante(self):
        from engine.world import World
        from engine.entity_factory import create_corpse
        from engine.components import Corpse
        world = World()
        eid = create_corpse(world, 100, 100, [], 0, mob_key="goblin_guerreiro", facing="left")
        c = world.get_component(eid, Corpse)
        self.assertEqual(c.mob_key, "goblin_guerreiro")
        self.assertEqual(c.facing, "left")


class TestLootSystemDrawsFrozenFrame(unittest.TestCase):
    """ui/loot_system.py::render_world desenha o frame congelado no
    lugar da elipse quando o Corpse tem mob_key resolvível — reprodução
    direta do pedido do usuário (congelar a animação no lugar do
    corpo-elipse atual)."""

    def tearDown(self):
        _clear_test_cache()

    def test_com_mob_key_resolvivel_desenha_sprite_nao_elipse(self):
        from engine.world import World
        from engine.components import Position, Corpse
        from ui.loot_system import LootSystem

        frames = _seed_death_frames(n=2, size=16)

        world = World()
        eid = world.create_entity()
        world.add_component(eid, Position(x=200, y=200))
        world.add_component(eid, Corpse(loot=[], coins=5, mob_key=_TEST_MOB_KEY, facing="down"))

        screen = pygame.display.get_surface()
        ls = LootSystem.__new__(LootSystem)
        ls.world = world
        ls.world_surf = screen

        before = screen.copy()
        ls.render_world(0, 0)
        # Não é uma prova pixel-a-pixel de QUAL desenho aconteceu, só
        # que ALGO foi desenhado (a superfície mudou) — suficiente pra
        # detectar regressão de "nada é desenhado".
        self.assertNotEqual(pygame.image.tobytes(before, "RGB"),
                            pygame.image.tobytes(screen, "RGB"))

    def test_sem_mob_key_com_loot_desenha_animacao_de_loot_png(self):
        """mob_key="" (sem sprite de morte conhecido) + loot presente →
        cai no marcador genérico animado (assets/sprites/loot.png,
        11/10/2026, pedido do usuário) — ainda desenha algo, só que a
        animação em vez da elipse antiga."""
        from engine.world import World
        from engine.components import Position, Corpse
        from ui.loot_system import LootSystem

        world = World()
        eid = world.create_entity()
        world.add_component(eid, Position(x=200, y=200))
        world.add_component(eid, Corpse(loot=[], coins=5))  # mob_key="" default

        screen = pygame.display.get_surface()
        ls = LootSystem.__new__(LootSystem)
        ls.world = world
        ls.world_surf = screen

        before = screen.copy()
        ls.render_world(0, 0)
        self.assertNotEqual(pygame.image.tobytes(before, "RGB"),
                            pygame.image.tobytes(screen, "RGB"),
                            "marcador de loot ainda deveria desenhar algo")

    def test_corpse_vazio_nao_desenha_nada(self):
        """Reprodução direta do pedido do usuário: cadáver SEM gold e
        SEM itens (já saqueado por completo, ainda dentro do timer de
        decay curto) não desenha mais nada — nem elipse escura nem
        animação."""
        from engine.world import World
        from engine.components import Position, Corpse
        from ui.loot_system import LootSystem

        world = World()
        eid = world.create_entity()
        world.add_component(eid, Position(x=200, y=200))
        world.add_component(eid, Corpse(loot=[], coins=0))  # vazio de verdade

        screen = pygame.display.get_surface()
        ls = LootSystem.__new__(LootSystem)
        ls.world = world
        ls.world_surf = screen

        before = screen.copy()
        ls.render_world(0, 0)
        self.assertEqual(pygame.image.tobytes(before, "RGB"),
                         pygame.image.tobytes(screen, "RGB"),
                         "cadáver vazio não pode desenhar nada")


class TestDrawRemoteCorpsesNaoDuplicaMarcaDoDono(unittest.TestCase):
    """Reprodução direta do bug relatado pelo usuário (§272): a elipse
    "continuava aparecendo" mesmo depois de ui/loot_system.py::
    render_world() já ter sido corrigido — causa real era
    `_draw_remote_corpses` (client/remote_entity_handlers.py), um
    SEGUNDO caminho de desenho que desenhava a elipse antiga por cima,
    sem saber do outro. Dono do loot (tem `_available_loot[corpse_id]`)
    nunca mais desenha aqui; só quem NÃO tem entitlement (outro player)
    usa esta função, agora com a animação de loot.png em vez da
    elipse."""

    def _make_fixture(self):
        from engine.world import World
        from client.network_handlers import NetworkHandlers as _NH
        from client.remote_entity_handlers import RemoteEntityHandlers as _REH

        class _Fixture(_NH, _REH):
            def __init__(self, world, player_entity):
                self.world = world
                self.player_entity = player_entity
                self._remote_corpses = {}
                self._available_loot = {}

        world = World()
        player = world.create_entity()
        fx = _Fixture(world, player)
        fx._zoom_surf = pygame.Surface((200, 200))
        return world, fx

    def test_dono_do_loot_nao_desenha_duplicado(self):
        from engine.components import Position, Corpse
        world, fx = self._make_fixture()
        eid = world.create_entity()
        world.add_component(eid, Position(x=100, y=100))
        world.add_component(eid, Corpse(loot=[], coins=5))
        fx._remote_corpses[1] = (3, 3)
        fx._available_loot[1] = {"local_eid": eid, "tx": 3, "ty": 3}

        before = fx._zoom_surf.copy()
        fx._draw_remote_corpses(0, 0)
        self.assertEqual(pygame.image.tobytes(before, "RGB"),
                         pygame.image.tobytes(fx._zoom_surf, "RGB"),
                         "dono do loot ja foi desenhado por ui/loot_system.py, nao pode desenhar de novo aqui")

    def test_outro_player_sem_entitlement_desenha_animacao(self):
        world, fx = self._make_fixture()
        fx._remote_corpses[2] = (5, 5)
        # sem entrada em _available_loot -- outro player, sem dado local

        before = fx._zoom_surf.copy()
        fx._draw_remote_corpses(0, 0)
        self.assertNotEqual(pygame.image.tobytes(before, "RGB"),
                            pygame.image.tobytes(fx._zoom_surf, "RGB"),
                            "marcador generico ainda deveria aparecer pra quem nao tem entitlement")


def _make_despawn_fixture():
    from engine.world import World
    from client.network_handlers import NetworkHandlers as _NH
    from client.remote_entity_handlers import RemoteEntityHandlers as _REH

    class _Fixture(_NH, _REH):
        def __init__(self, world, player_entity):
            self.world = world
            self.player_entity = player_entity
            self._remote_mobs = {}
            self._mob_move_queues = {}
            self._dying_mobs = {}
            self._pending_death_race_by_tile = {}

    world = World()
    player = world.create_entity()
    return world, _Fixture(world, player)


class TestDespawnMobOrPlayDeath(unittest.TestCase):
    """Reprodução fim-a-fim do caso "sem loot": mob morre, animação
    toca 1x, SÓ DEPOIS a entidade some do ECS local — não mais
    instantâneo. `_despawn_mob_or_play_death` é o ponto único chamado
    tanto pelo despawn direto (AOI_UPDATE) quanto pelo drenar de
    `_pending_mob_despawn` (knockback/movimento em cadeia)."""

    def tearDown(self):
        _clear_test_cache()

    def test_kill_com_sprite_de_morte_adia_remocao_e_toca_animacao(self):
        from engine.components import EntityIdentity, TileMovement, Position
        _seed_death_frames(n=2)
        world, fx = _make_despawn_fixture()
        eid = world.create_entity()
        world.add_component(eid, Position(x=100, y=100))
        world.add_component(eid, EntityIdentity(name="X", race=_TEST_MOB_KEY,
                                                entity_class="", level=1))
        world.add_component(eid, TileMovement(facing="down", current_tile_x=3, current_tile_y=4))

        fx._despawn_mob_or_play_death(eid, server_eid=99, is_kill=True)

        # Entidade NÃO foi removida ainda — animação de morte está tocando.
        tm = world.get_component(eid, TileMovement)
        self.assertIsNotNone(tm, "entidade não pode sumir antes da animação de morte terminar")
        self.assertEqual(tm.anim_attack_state, "death")
        self.assertTrue(tm.anim_attack_hold_last_frame)
        self.assertIn(eid, fx._dying_mobs)

        # Ponte de identidade gravada pro Corpse (se vier loot) usar depois.
        bridge = fx._pending_death_race_by_tile.get((3, 4))
        self.assertIsNotNone(bridge)
        self.assertEqual(bridge["mob_key"], _TEST_MOB_KEY)
        self.assertEqual(bridge["facing"], "down")

        # Animação termina -> _flush_dying_mobs remove de vez.
        fx._dying_mobs[eid]["deadline_ms"] = -1  # já vencido
        fx._flush_dying_mobs()
        self.assertIsNone(world.get_component(eid, TileMovement),
                          "entidade tem que sumir depois que a animação de morte terminou")
        self.assertNotIn(eid, fx._dying_mobs)

    def test_kill_sem_sprite_de_morte_remove_na_hora_como_sempre(self):
        """Mob sem animação de morte (comportamento de hoje, nenhuma
        regressão) — remove IMEDIATAMENTE, sem entrar em _dying_mobs."""
        from engine.components import EntityIdentity, TileMovement, Position
        world, fx = _make_despawn_fixture()
        eid = world.create_entity()
        world.add_component(eid, Position(x=100, y=100))
        world.add_component(eid, EntityIdentity(name="X", race="__mob_sem_death_nenhum__",
                                                entity_class="", level=1))
        world.add_component(eid, TileMovement(facing="down", current_tile_x=3, current_tile_y=4))

        fx._despawn_mob_or_play_death(eid, server_eid=99, is_kill=True)

        self.assertIsNone(world.get_component(eid, TileMovement))
        self.assertNotIn(eid, fx._dying_mobs)

    def test_saida_de_aoi_nunca_anima_mesmo_com_sprite_disponivel(self):
        """is_kill=False (mob saiu da área de visão, não morreu) nunca
        deve tocar animação de morte — remove na hora, igual sempre."""
        from engine.components import EntityIdentity, TileMovement, Position
        _seed_death_frames(n=2)
        world, fx = _make_despawn_fixture()
        eid = world.create_entity()
        world.add_component(eid, Position(x=100, y=100))
        world.add_component(eid, EntityIdentity(name="X", race=_TEST_MOB_KEY,
                                                entity_class="", level=1))
        world.add_component(eid, TileMovement(facing="down"))

        fx._despawn_mob_or_play_death(eid, server_eid=99, is_kill=False)

        self.assertIsNone(world.get_component(eid, TileMovement))
        self.assertNotIn(eid, fx._dying_mobs)
        self.assertEqual(fx._pending_death_race_by_tile, {})


if __name__ == "__main__":
    unittest.main()
