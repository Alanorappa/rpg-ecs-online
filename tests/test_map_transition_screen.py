"""
tests/test_map_transition_screen.py — tela de transição de mapa/
instância (28/09/2026, pedido do usuário, ver PROBLEMAS_ARQUITETURA.md
§215): antes de `_do_transition()` rodar (troca de mapa DE VERDADE —
não o atalho de "morte no mesmo mapa"/portal pro mesmo arquivo), o
cliente mostra 1 frame de transição e silencia SFX de gameplay — sem
isso, o jogador via a tela CONGELADA por até ~2s (carregamento
síncrono de mapa/tilemap) sem nenhum aviso.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))

from client.network_handlers import NetworkHandlers
from client.habilidades_handlers import HabilidadesHandlers
from client.portal_handlers import PortalHandlers
from engine.world import World
from engine.components import Faction, Camera, TileMovement


class _ZoneChangeFixture(NetworkHandlers, HabilidadesHandlers, PortalHandlers):
    def __init__(self, current_map: str):
        self.world = World()
        self.player_entity = self.world.create_entity()
        self.world.add_component(self.player_entity, Faction(faction_id="time_a"))
        # PortalHandlers._cancel_portal_cast_local() (chamado por
        # _handle_msg_zone_change desde 01/10/2026) lê TileMovement do
        # player — sem o componente, o fixture quebraria pra QUALQUER
        # teste deste arquivo, não só os de portal.
        self.world.add_component(self.player_entity, TileMovement())
        self._current_map_file = current_map
        self._bg_result_val = "algo"
        self._bg_in_match_val = True
        self._bg_opponents_server_val = {1, 2}
        self.screen   = pygame.Surface((640, 480))
        self._display = pygame.Surface((640, 480))
        self.font_lg  = pygame.font.SysFont(None, 32)
        self.font_sm  = pygame.font.SysFont(None, 18)
        self.font_xs  = pygame.font.SysFont(None, 14)
        self.transitions_called: list[dict] = []

    def _do_transition(self, trans: dict) -> None:
        self.transitions_called.append(trans)


class TestHandleMsgZoneChange(unittest.TestCase):
    def setUp(self):
        import ui.sound_manager as sm_mod
        self._orig_set_game_ready = sm_mod.SOUNDS.set_game_ready
        self.mute_calls: list[bool] = []
        sm_mod.SOUNDS.set_game_ready = lambda ready: self.mute_calls.append(ready)

    def tearDown(self):
        import ui.sound_manager as sm_mod
        sm_mod.SOUNDS.set_game_ready = self._orig_set_game_ready

    def test_troca_de_mapa_real_mostra_tela_e_silencia_som(self):
        fx = _ZoneChangeFixture(current_map="maps/map_1.csv")

        fx._handle_msg_zone_change({
            "map_file": "maps/battleground_1.csv", "target_x": 5, "target_y": 5,
        })

        self.assertEqual(len(fx.transitions_called), 1)
        self.assertEqual(fx.transitions_called[0]["target_map"], "maps/battleground_1.csv")
        self.assertEqual(self.mute_calls, [False, True],
            "deveria silenciar antes e liberar depois da troca de mapa real")

    def test_teleporte_pro_mesmo_mapa_nao_mostra_tela_nem_silencia(self):
        """Portal de mundo aberto que leva a outro canto do MESMO
        arquivo — `_do_transition` já trata isso como caminho rápido
        (só reposiciona), não precisa da tela de transição."""
        fx = _ZoneChangeFixture(current_map="maps/map_1.csv")

        fx._handle_msg_zone_change({
            "map_file": "maps/map_1.csv", "target_x": 210, "target_y": 480,
        })

        self.assertEqual(len(fx.transitions_called), 1)
        self.assertEqual(self.mute_calls, [],
            "teleporte pro mesmo mapa nao deveria silenciar som nenhum")

    def test_troca_de_mapa_real_remove_faction_e_limpa_estado_de_bg(self):
        """Efeitos colaterais já existentes antes desta mudança
        (contexto de time/BG) continuam intactos — a tela de transição
        não deve interferir neles."""
        fx = _ZoneChangeFixture(current_map="maps/map_1.csv")

        fx._handle_msg_zone_change({
            "map_file": "maps/battleground_1.csv", "target_x": 5, "target_y": 5,
        })

        self.assertIsNone(fx.world.get_component(fx.player_entity, Faction))
        self.assertIsNone(fx._bg_result_val)
        self.assertFalse(fx._bg_in_match_val)
        self.assertEqual(fx._bg_opponents_server_val, set())

    def test_zone_change_desliga_camera_livre_ao_sair_de_instancia(self):
        """29/09/2026, bug real relatado pelo usuário (PROBLEMAS_ARQUITETURA.md
        §219): câmera livre estilo MOBA (tecla Y, só liga dentro da BG/
        Arena) ficava travada em `free_mode=True` pra sempre ao sair da
        instância sem apertar Y de novo antes — o toggle da tecla só
        funciona com a instância ainda ativa, então não tinha como o
        próprio jogador corrigir sozinho no mundo aberto. Câmera ficava
        congelada na última posição do pan (coordenadas de um mapa bem
        menor), fora de lugar/preta no mapa novo, só resolvia relogando.
        ZONE_CHANGE agora desliga free_mode incondicionalmente."""
        fx = _ZoneChangeFixture(current_map="maps/map_1.csv")
        cam_eid = fx.world.create_entity()
        fx.world.add_component(cam_eid, Camera(free_mode=True))

        fx._handle_msg_zone_change({
            "map_file": "maps/battleground_1.csv", "target_x": 5, "target_y": 5,
        })

        self.assertFalse(fx.world.get_component(cam_eid, Camera).free_mode)

    def test_zone_change_pro_mesmo_mapa_tambem_desliga_camera_livre(self):
        """Mesmo reset vale pro atalho de teleporte no MESMO mapa —
        free_mode nunca deveria sobreviver a NENHUM ZONE_CHANGE."""
        fx = _ZoneChangeFixture(current_map="maps/map_1.csv")
        cam_eid = fx.world.create_entity()
        fx.world.add_component(cam_eid, Camera(free_mode=True))

        fx._handle_msg_zone_change({
            "map_file": "maps/map_1.csv", "target_x": 210, "target_y": 480,
        })

        self.assertFalse(fx.world.get_component(cam_eid, Camera).free_mode)


class TestRemoveAllRemoteEntities(unittest.TestCase):
    """`GameEngine._remove_all_remote_entities()` (06/10/2026, bug real
    relatado pelo usuário: NPC/mob "travado" numa posição velha após
    reconectar no meio do jogo). Separado de `_clear_remote_entity_caches`
    de propósito (ver docstring de ambos em game.py) -- este teste cobre
    SÓ a remoção de entidade em si, com um World real."""

    def _make_fixture(self):
        import game

        class _FakeEntityHolder:
            pass

        fake = _FakeEntityHolder()
        fake.world = World()
        fake.player_entity = fake.world.create_entity()
        fake.camera_entity = fake.world.create_entity()
        fake.tilemap_entity = fake.world.create_entity()
        return fake, game

    def test_remove_mantem_player_camera_e_tilemap_quando_pedido(self):
        fake, game = self._make_fixture()
        mob_eid = fake.world.create_entity()

        game.GameEngine._remove_all_remote_entities(fake, keep_tilemap=True)

        self.assertIn(fake.player_entity, fake.world._components)
        self.assertIn(fake.camera_entity, fake.world._components)
        self.assertIn(fake.tilemap_entity, fake.world._components,
                      "keep_tilemap=True (reconexão pro MESMO mapa) não pode remover o tilemap")
        self.assertNotIn(mob_eid, fake.world._components)

    def test_remove_tilemap_tambem_por_padrao(self):
        """default keep_tilemap=False -- troca de mapa DE VERDADE
        (_do_transition) precisa derrubar o tilemap velho também, pra
        criar um novo do mapa de destino."""
        fake, game = self._make_fixture()

        game.GameEngine._remove_all_remote_entities(fake)

        self.assertIn(fake.player_entity, fake.world._components)
        self.assertIn(fake.camera_entity, fake.world._components)
        self.assertNotIn(fake.tilemap_entity, fake.world._components)


class TestClearRemoteEntityCaches(unittest.TestCase):
    """`GameEngine._clear_remote_entity_caches()` (02/10/2026, bug real
    relatado pelo usuário: corpo de mob morto numa instância de BG
    continuava aparecendo ao entrar numa instância NOVA depois) —
    `_do_transition()` só chama isto no branch de troca de mapa DE
    VERDADE. `_remote_corpses`/`_available_loot` foram esquecidos desta
    lista por muito tempo (só `_remote_mobs`/`_remote_players`/
    `_pending_mob_despawn`/`_mob_ghost_pos` eram limpos) — regressão
    futura (cache novo esquecido de novo) é pega por este teste batendo
    numa lista EXPLÍCITA dos atributos, não um subconjunto.
    `_dying_mobs`/`_pending_death_race_by_tile` (11/10/2026, animação
    de morte de mob) entraram na lista no mesmo commit em que nasceram."""

    def test_limpa_todos_os_caches_de_entidade_remota(self):
        import game

        class _FakeCacheHolder:
            def __init__(self):
                self._remote_mobs               = {1: "mob"}
                self._remote_players            = {2: "player"}
                self._pending_mob_despawn       = {3: 0.5}
                self._mob_ghost_pos             = {4: (10, 20)}
                self._remote_corpses            = {5: (30, 40)}
                self._available_loot            = {6: {"items": []}}
                self._dying_mobs                = {7: {"server_eid": 7, "deadline_ms": 123}}
                self._pending_death_race_by_tile = {(8, 8): {"mob_key": "goblin_guerreiro"}}

        fake = _FakeCacheHolder()
        game.GameEngine._clear_remote_entity_caches(fake)

        self.assertEqual(fake._remote_mobs, {})
        self.assertEqual(fake._remote_players, {})
        self.assertEqual(fake._pending_mob_despawn, {})
        self.assertEqual(fake._mob_ghost_pos, {})
        self.assertEqual(fake._remote_corpses, {},
                         "corpse de mapa/instância anterior não pode sobreviver à troca")
        self.assertEqual(fake._available_loot, {},
                         "loot de mapa/instância anterior não pode sobreviver à troca")
        self.assertEqual(fake._dying_mobs, {},
                         "mob tocando animação de morte de mapa/instância anterior não pode sobreviver à troca")
        self.assertEqual(fake._pending_death_race_by_tile, {})


if __name__ == "__main__":
    unittest.main()
