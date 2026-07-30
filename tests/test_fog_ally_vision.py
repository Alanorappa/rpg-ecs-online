"""
tests/test_fog_ally_vision.py
Visão compartilhada de time — parte de FOG-OF-WAR (30/07/2026, pedido do
usuário: "eu quero que as torres e minions do time explorem a fog, ou seja,
da mesma forma que eu vejo a minha. É idêntico ao LoL").

Cobre as DUAS pontas:
- Servidor (`server/session.py::_build_update_for_session`): inclui
  `ally_vision_centers` no AOI_UPDATE quando há aliado, omite quando não há
  (custo zero fora de contexto de time).
- Cliente (`ui/systems.py::FogSystem`): revela `fog.visible`/`fog.explored`
  com shadowcasting PRÓPRIO a partir de cada centro de aliado — não da
  posição do jogador — pra que uma torre do outro lado de uma parede revele
  área que o próprio jogador não enxergaria em linha reta.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.world import World
from engine.entity_factory import create_tilemap
from engine.components import FogOfWar, TileMovement
from ui.systems import FogSystem


class TestServerSendsAllyVisionCenters(unittest.TestCase):
    """`_build_update_for_session` — o lado SERVIDOR do payload novo."""

    def setUp(self):
        self.ws = make_world_server()
        from server.session import SessionManager
        self.mgr = SessionManager(self.ws)

    def test_inclui_ally_vision_centers_quando_ha_aliado(self):
        p1 = spawn_player(self.ws, "p1", 10, 10)
        from tests.helpers import clear_login_immunity
        clear_login_immunity(self.ws, p1)
        session = self.mgr._sessions.get("p1") if hasattr(self.mgr, "_sessions") else None
        # SessionManager de verdade não tem sessão sem handshake completo —
        # chama _build_update_for_session direto, como já é feito em
        # tests/test_session.py:688 (mesmo padrão estabelecido).
        from server.session import Session
        fake_session = Session(ws=None, session_id="p1")
        fake_session.entity_id = p1
        ally_centers = [(12, 10, 18), (9, 9, 8)]
        result = self.mgr._build_update_for_session(fake_session, {}, 10, 10,
                                                     ally_centers=ally_centers)
        self.assertIn("ally_vision_centers", result)
        self.assertEqual(sorted(tuple(c) for c in result["ally_vision_centers"]),
                         sorted(ally_centers))

    def test_omite_ally_vision_centers_sem_aliado(self):
        p1 = spawn_player(self.ws, "p1", 10, 10)
        from server.session import Session
        fake_session = Session(ws=None, session_id="p1")
        fake_session.entity_id = p1
        result = self.mgr._build_update_for_session(fake_session, {}, 10, 10,
                                                     ally_centers=None)
        self.assertNotIn("ally_vision_centers", result,
                         "sem aliado não deveria mandar o campo (custo zero fora de time)")


def _make_fog_world(width: int, height: int, wall_col: int):
    """World headless com um Tilemap width×height e uma parede VERTICAL
    sólida (vision_height=2, char '#') na coluna wall_col, separando o
    mapa em metade esquerda/direita — usado pra provar que o aliado
    enxerga através da própria posição, não da do jogador."""
    w = World()
    rows = []
    for _ in range(height):
        row = ["."] * width
        row[wall_col] = "#"
        rows.append("".join(row))
    objects = [["."] * width for _ in range(height)]
    create_tilemap(w, rows, objects, None)
    return w


class TestFogSystemAllyVisionSharing(unittest.TestCase):
    """FogSystem — o lado CLIENTE: shadowcasting a partir de cada centro
    de aliado, além da posição do próprio jogador."""

    def setUp(self):
        # Parede vertical na coluna 5, mapa 11 de largura x 5 de altura.
        self.world = _make_fog_world(width=11, height=5, wall_col=5)
        self.fog_sys = FogSystem(self.world)
        self.player_eid = self.world.create_entity()
        # explore_radius pequeno de propósito: o flood do PRÓPRIO player
        # (sem checar parede, comportamento pré-existente) não alcança o
        # tile de teste (7,2) — distância 5 do player em (2,2) — então
        # qualquer aparição de (7,2) em fog.explored só pode vir da visão
        # do ALIADO, tornando o teste realmente diferenciador.
        self.world.add_component(self.player_eid, FogOfWar(radius=15, explore_radius=2))
        self.world.add_component(self.player_eid,
                                 TileMovement(current_tile_x=2, current_tile_y=2))

    def _fog(self) -> FogOfWar:
        return self.world.get_component(self.player_eid, FogOfWar)

    def test_torre_aliada_do_outro_lado_da_parede_revela_area_que_player_nao_veria(self):
        fog = self._fog()
        fog.ally_centers = [(8, 2, 3)]   # aliado no lado DIREITO da parede
        self.fog_sys.update()

        # (7,2): vizinho do aliado, mesmo lado da parede — só alcançável via
        # shadowcast PRÓPRIO do aliado, nunca pelo shadowcast do player (2,2,
        # lado esquerdo, bloqueado pela parede na coluna 5).
        self.assertIn((7, 2), fog.visible,
                      "torre aliada deveria revelar tile do lado dela da parede")
        self.assertIn((7, 2), fog.explored,
                      "minimap (explored) também deveria ser revelado pela torre aliada")

    def test_sem_aliado_player_nao_ve_do_outro_lado_da_parede(self):
        """Regressão: sem ally_centers, o comportamento de `visible`
        (shadowcasting, respeita parede) é IDÊNTICO a antes desta feature.
        `explored` (círculo largo, SEM checagem de parede — comportamento
        PRÉ-EXISTENTE, não desta feature) não serve pra essa asserção aqui,
        só `visible`."""
        fog = self._fog()
        self.fog_sys.update()
        self.assertNotIn((7, 2), fog.visible)

    def test_recomputa_quando_aliado_se_move_mesmo_sem_player_se_mover(self):
        """FogSystem só recomputava ao trocar de tile do PLAYER — sem o
        centro de aliado entrar no gate de recompute, uma torre/minion se
        movendo (ou um teammate) nunca atualizaria a névoa revelada."""
        fog = self._fog()
        self.fog_sys.update()   # assenta _last_tile, sem aliado ainda
        self.assertNotIn((7, 2), fog.visible)

        fog.ally_centers = [(8, 2, 3)]   # aliado aparece, player NÃO se moveu
        self.fog_sys.update()
        self.assertIn((7, 2), fog.visible,
                      "deveria recomputar e revelar mesmo sem o player mudar de tile")

    def test_switch_map_descarta_centros_de_aliado_do_mapa_anterior(self):
        fog = self._fog()
        fog.ally_centers = [(8, 2, 3)]
        fog.switch_map("maps/outro_mapa.csv")
        self.assertEqual(fog.ally_centers, [],
                         "centros do mapa anterior não fazem sentido nas coordenadas do novo mapa")


if __name__ == "__main__":
    unittest.main()
