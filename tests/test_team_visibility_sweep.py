"""
tests/test_team_visibility_sweep.py — §58 (13/08/2026), investigação
pedida pelo usuário depois de reportar torre/minion sumindo ao entrar
na bush. 2 fixes reais + 1 achado que se mostrou FALSO POSITIVO ao
escrever o teste:

- Achado B (fix real): torre/minion do PRÓPRIO time sempre visíveis
  pro time, independente de LOS (padrão comum de MOBA). Torre/minion
  ADVERSÁRIO continua sujeito à regra normal de LOS (§54-57).
- Achado D (fix real): a varredura genérica de "mob/torre/minion/
  harvestable em AOI ainda não conhecido" (`server/session.py`,
  `_build_update_for_session`) nunca chamava `_can_see` — só
  distância. Corrigido; adversário sem LOS agora fica de fora de
  verdade.
- Achado C (FALSO POSITIVO — registrado aqui pra não reinvestigar):
  cheguei a achar que "um aliado (minion) revela inimigo na bush, mas
  outro player do time nunca descobre" era uma lacuna nova — cheguei a
  IMPLEMENTAR um fix (incluir players na varredura de mob) antes de
  perceber, escrevendo o teste, que já existe um bloco SEPARADO em
  `_build_update_for_session` (`for other_session in self._sessions...`)
  que já re-varre TODOS os players conectados, todo tick, já com
  `_can_see` — sem gatilho de "moved" nenhum. O teste que "provava" o
  bug tinha um erro de setup (`Session.authenticated` nunca setado pra
  `True`) — corrigido o teste, o mecanismo JÁ existente passou de
  primeira. O fix redundante foi revertido antes de qualquer commit.

16/08/2026 — a MECÂNICA por trás de `TestAllyTriggeredPlayerDiscovery`
mudou (§58, 2ª rodada): o LOS de "aliado dentro da bush revela pro
time" deixou de ser um scan de Faction embutido em `_has_tile_los`
(sem raio, achava QUALQUER aliado em QUALQUER bush do mapa) e virou
união de fontes via `ally_centers` (posição+raio por tipo, mesma
lista de `_compute_ally_vision_centers`) — precisa estar POPULADO
antes da chamada. Testes que chamam `_build_update_for_session`
direto (fora de `_dispatch_tick_deltas`) agora precisam simular esse
passo (`self.mgr._ally_vision_centers = self.mgr.
_compute_ally_vision_centers()`) antes de cada chamada.

Ver `arquitetura/PROBLEMAS_ARQUITETURA.md` §58.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from server.session import _can_see, SessionManager, Session
from engine.components import Faction, MapLocation, Tilemap, TileMovement
from engine.tileset import WALL_TILE, FLOOR_TILE, OBJECT_MAPPING
from engine.entity_factory import create_tower, create_minion
from engine.utils import SpatialHash

_REAL_BUSH_TILE = OBJECT_MAPPING["pl_b1"]


def _get_tilemap(ws):
    bundle = ws._map_bundles[ws._map_file]
    return ws.world.get_component(bundle.tilemap_entity, Tilemap)


def _clear_area(ws, x0: int, y0: int, x1: int, y1: int) -> None:
    tm = _get_tilemap(ws)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            tm.tile_matrix[y][x] = FLOOR_TILE


def _put_wall(ws, x: int, y: int) -> None:
    _get_tilemap(ws).tile_matrix[y][x] = WALL_TILE


def _build_entity_hash(ws):
    """Mesmo índice construído por _dispatch_tick_deltas — só mob/
    torre/minion/harvestable. Players NÃO entram aqui (descoberta de
    player usa um sweep separado, já existente, dentro de
    `_build_update_for_session` — ver Achado C na docstring do
    módulo)."""
    positions = {}
    h = SpatialHash(cell_size=16)
    for eid in ws._mob_eids | ws._harvestable_eids:
        tm = ws.world.get_component(eid, TileMovement)
        if tm:
            positions[eid] = (tm.current_tile_x, tm.current_tile_y)
            h.insert(eid, tm.current_tile_x, tm.current_tile_y)
    return positions, h


class TestOwnTeamStructureAlwaysVisible(unittest.TestCase):
    """Achado B — torre/minion do próprio time sempre visível, mesmo
    atrás de bush/parede real."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        self.viewer = spawn_player(self.ws, "s_viewer", 10, 10)
        self.ws.world.add_component(self.viewer, Faction(faction_id="arena_time_a"))
        _put_wall(self.ws, 13, 10)

    def test_torre_do_proprio_time_atras_de_parede_continua_visivel(self):
        tower = create_tower(self.ws.world, 16, 10, "torre_de_flechas", faction_id="arena_time_a")
        self.ws.world.add_component(tower, MapLocation(self.ws._map_file))
        self.assertTrue(_can_see(self.ws, self.viewer, tower))

    def test_torre_adversaria_atras_de_parede_continua_escondida(self):
        tower = create_tower(self.ws.world, 16, 10, "torre_de_flechas", faction_id="arena_time_b")
        self.ws.world.add_component(tower, MapLocation(self.ws._map_file))
        self.assertFalse(_can_see(self.ws, self.viewer, tower))

    def test_minion_do_proprio_time_atras_de_parede_continua_visivel(self):
        minion = create_minion(self.ws.world, 16, 10, "minion_melee",
                               faction_id="arena_time_a", route=[(16, 10)])
        self.ws.world.add_component(minion, MapLocation(self.ws._map_file))
        self.assertTrue(_can_see(self.ws, self.viewer, minion))

    def test_minion_adversario_atras_de_parede_continua_escondido(self):
        minion = create_minion(self.ws.world, 16, 10, "minion_melee",
                               faction_id="arena_time_b", route=[(16, 10)])
        self.ws.world.add_component(minion, MapLocation(self.ws._map_file))
        self.assertFalse(_can_see(self.ws, self.viewer, minion))


class TestSweepRespectsLos(unittest.TestCase):
    """Achado D — a varredura genérica de "entidade em AOI ainda não
    conhecida" agora respeita `_can_see` (antes só checava distância)."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        self.mgr = SessionManager(self.ws)
        self.viewer = spawn_player(self.ws, "s_viewer", 10, 10)
        self.ws.world.add_component(self.viewer, Faction(faction_id="arena_time_a"))
        _put_wall(self.ws, 13, 10)

        self.sess = Session(ws=None, session_id="s_viewer")
        self.sess.entity_id = self.viewer
        self.sess.known_eids = set()

    def test_torre_adversaria_sem_los_nao_e_descoberta_pela_varredura(self):
        tower = create_tower(self.ws.world, 16, 10, "torre_de_flechas", faction_id="arena_time_b")
        self.ws.world.add_component(tower, MapLocation(self.ws._map_file))
        self.ws._mob_eids.add(tower)

        self.assertFalse(_can_see(self.ws, self.viewer, tower))
        pos, h = _build_entity_hash(self.ws)
        result = self.mgr._build_update_for_session(self.sess, {}, 10, 10, pos, h)
        self.assertNotIn(tower, self.sess.known_eids,
            "torre adversária sem LOS não deveria ser 'descoberta' só por estar perto")

    def test_torre_adversaria_com_los_e_descoberta_normalmente(self):
        tower = create_tower(self.ws.world, 12, 10, "torre_de_flechas", faction_id="arena_time_b")
        self.ws.world.add_component(tower, MapLocation(self.ws._map_file))
        self.ws._mob_eids.add(tower)

        self.assertTrue(_can_see(self.ws, self.viewer, tower))
        pos, h = _build_entity_hash(self.ws)
        result = self.mgr._build_update_for_session(self.sess, {}, 10, 10, pos, h)
        self.assertIn(tower, self.sess.known_eids)


class TestAllyTriggeredPlayerDiscovery(unittest.TestCase):
    """Achado C, regressão (não fix novo — ver docstring do módulo): um
    aliado (minion) entrando na bush onde um inimigo está já revela
    ele pro TIME INTEIRO, mesmo pra quem nunca se moveu — mecanismo
    JÁ existente (`_build_update_for_session`'s varredura de players),
    não algo que precisou ser adicionado agora."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        tm = _get_tilemap(self.ws)
        for x, y in ((20, 20), (21, 20)):
            tm.tile_matrix[y][x] = _REAL_BUSH_TILE

        self.mgr = SessionManager(self.ws)

    def test_aliado_minion_entra_na_bush_revela_inimigo_pro_time_sem_o_viewer_se_mover(self):
        # Outro player do time, longe da bush, nunca se move.
        teammate = spawn_player(self.ws, "s_teammate", 10, 10)
        self.ws.world.add_component(teammate, Faction(faction_id="arena_time_a"))
        enemy = spawn_player(self.ws, "s_enemy", 20, 20)  # escondido na bush
        self.ws.world.add_component(enemy, Faction(faction_id="arena_time_b"))

        map_file = self.ws.get_entity_map(teammate)
        minion = create_minion(self.ws.world, 5, 5, "minion_melee",
                               faction_id="arena_time_a", route=[(5, 5)])
        self.ws.world.add_component(minion, MapLocation(map_file))
        # _compute_ally_vision_centers só considera torre/minion que
        # estão em _mob_eids (fonte real de "mob ativo no mundo") — sem
        # isso o minion nunca vira fonte de visão de time, mesmo com
        # Faction certa (mesmo padrão já usado nos testes de torre acima).
        self.ws._mob_eids.add(minion)

        teammate_sess = Session(ws=None, session_id="s_teammate")
        teammate_sess.entity_id = teammate
        teammate_sess.known_eids = set()
        teammate_sess.authenticated = True
        self.mgr._sessions["s_teammate"] = teammate_sess

        # enemy também precisa de uma Session real registrada em
        # mgr._sessions (authenticated=True) — é de lá que o sweep de
        # players de _build_update_for_session descobre quem está online.
        enemy_sess = Session(ws=None, session_id="s_enemy")
        enemy_sess.entity_id = enemy
        enemy_sess.known_eids = set()
        enemy_sess.authenticated = True
        self.mgr._sessions["s_enemy"] = enemy_sess

        # Tick 1: minion longe, inimigo ainda escondido de todo mundo.
        # _ally_vision_centers normalmente é recalculado 1x por tick no
        # TOPO de _dispatch_tick_deltas (§58) — o teste chama
        # _build_update_for_session direto, então precisa simular esse
        # passo à mão pra ally_centers chegar populado em _can_see.
        self.mgr._ally_vision_centers = self.mgr._compute_ally_vision_centers()
        pos, h = _build_entity_hash(self.ws)
        self.mgr._build_update_for_session(teammate_sess, {}, 10, 10, pos, h,
            ally_centers=self.mgr._ally_vision_centers.get(teammate, []))
        self.assertNotIn(enemy, teammate_sess.known_eids)

        # Minion entra na MESMA bush do inimigo — teammate nunca se move.
        set_entity_tile(self.ws, minion, 21, 20)
        moved = {"moved": [{"eid": minion, "tx": 21, "ty": 20, "from_tx": 5, "from_ty": 5}]}
        self.mgr._ally_vision_centers = self.mgr._compute_ally_vision_centers()
        pos, h = _build_entity_hash(self.ws)
        self.mgr._build_update_for_session(teammate_sess, moved, 10, 10, pos, h,
            ally_centers=self.mgr._ally_vision_centers.get(teammate, []))

        self.assertIn(enemy, teammate_sess.known_eids,
            "aliado (minion) revelando a bush deveria descobrir o inimigo pro time, mesmo sem o viewer se mover")


if __name__ == "__main__":
    unittest.main()
