"""
tests/test_bush_stealth.py — Stealth de bush estilo MOBA (13/08/2026,
pedido do usuário). Ver server/bush_zone_processor.py e
PROBLEMAS_ARQUITETURA.md.

Modelado em cima de tests/test_pvp_zone.py — mesmo padrão de zona
sintética (`_bush_zones_by_map[map_file] = [...]`) em vez de depender de
bushes reais pintadas num mapa (nenhuma foi pintada ainda).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from server.session import _can_see
from engine.components import Faction, MapLocation, CombatState, Tilemap
from engine.entity_factory import create_minion
from engine.tileset import FLOOR_TILE

_BUSH_A = (100, 100, 105, 105)   # x1,y1,x2,y2 em tiles
_BUSH_B = (200, 200, 205, 205)   # outro bush, longe do A
_IN_A    = (102, 102)
_IN_B    = (202, 202)
_OUTSIDE = (5, 5)


def _clear_terrain(ws, x0: int, y0: int, x1: int, y1: int) -> None:
    """Sobrescreve um retângulo com FLOOR_TILE — necessário desde §57
    (linha de visão de terreno universal): `_can_see` agora também
    checa `_has_tile_los` pra QUALQUER par, então testes de zona de
    bush (§47, `_OUTSIDE`/`_IN_A` bem distantes um do outro) precisam
    de um caminho livre de verdade entre eles, ou terreno real de
    `map_1.csv` interfere sem relação nenhuma com o que o teste quer
    validar (a zona de bush em si)."""
    bundle = ws._map_bundles[ws._map_file]
    tm = ws.world.get_component(bundle.tilemap_entity, Tilemap)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            tm.tile_matrix[y][x] = FLOOR_TILE


class TestGetBushZone(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", *_OUTSIDE)
        map_file = self.ws.get_entity_map(self.a)
        self.ws._bush_zones_by_map[map_file] = [
            {"name": "Bush A", "rect": _BUSH_A},
            {"name": "Bush B", "rect": _BUSH_B},
        ]

    def test_fora_de_qualquer_bush_retorna_none(self):
        self.assertIsNone(self.ws._get_bush_zone(self.a))

    def test_dentro_do_bush_a_retorna_indice_0(self):
        set_entity_tile(self.ws, self.a, *_IN_A)
        self.assertEqual(self.ws._get_bush_zone(self.a), 0)

    def test_dentro_do_bush_b_retorna_indice_1(self):
        set_entity_tile(self.ws, self.a, *_IN_B)
        self.assertEqual(self.ws._get_bush_zone(self.a), 1)

    def test_mapa_sem_bush_zones_nao_quebra(self):
        map_file = self.ws.get_entity_map(self.a)
        self.ws._bush_zones_by_map[map_file] = []
        set_entity_tile(self.ws, self.a, *_IN_A)
        self.assertIsNone(self.ws._get_bush_zone(self.a))


class TestCanSeeBush(unittest.TestCase):
    """`_can_see` — visibilidade dentro de bush, com/sem visão de time
    compartilhada."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_terrain(self.ws, 0, 0, 210, 210)
        self.a = spawn_player(self.ws, "s1", *_IN_A)   # vai ficar dentro do bush
        self.b = spawn_player(self.ws, "s2", *_OUTSIDE)  # viewer, fora
        map_file = self.ws.get_entity_map(self.a)
        self.ws._bush_zones_by_map[map_file] = [{"name": "Bush A", "rect": _BUSH_A}]

    def test_alvo_no_bush_viewer_fora_sem_time_e_invisivel(self):
        self.assertFalse(_can_see(self.ws, self.b, self.a))

    def test_alvo_e_viewer_no_mesmo_bush_e_visivel(self):
        set_entity_tile(self.ws, self.b, *_IN_A)
        self.assertTrue(_can_see(self.ws, self.b, self.a))

    def test_fora_de_bush_visibilidade_normal_nao_muda(self):
        """Sanity check — regressão: sem ninguém em bush, _can_see continua
        se comportando como antes (só is_visible/ghost importam)."""
        set_entity_tile(self.ws, self.a, *_OUTSIDE)
        self.assertTrue(_can_see(self.ws, self.b, self.a))
        self.assertTrue(_can_see(self.ws, self.a, self.b))

    def test_aliado_no_mesmo_bush_revela_pro_time_inteiro(self):
        """Visão de bush compartilhada por time (pedido explícito do
        usuário, estilo LoL): viewer fora do bush, mas um ALIADO dele
        está dentro — o time inteiro enxerga quem está lá."""
        c = spawn_player(self.ws, "s3", *_IN_A)  # aliado de b, dentro do bush
        self.ws.world.add_component(self.b, Faction(faction_id="time_x"))
        self.ws.world.add_component(c,      Faction(faction_id="time_x"))
        self.assertTrue(_can_see(self.ws, self.b, self.a))

    def test_time_adversario_no_bush_nao_revela(self):
        """Visão de bush é só compartilhada DENTRO do próprio time —
        inimigo no bush não ajuda o time do viewer a enxergar."""
        c = spawn_player(self.ws, "s3", *_IN_A)  # time DIFERENTE de b
        self.ws.world.add_component(self.b, Faction(faction_id="time_x"))
        self.ws.world.add_component(c,      Faction(faction_id="time_y"))
        self.assertFalse(_can_see(self.ws, self.b, self.a))

    def test_minion_no_bush_tambem_fica_oculto(self):
        map_file = self.ws.get_entity_map(self.a)
        minion = create_minion(self.ws.world, _IN_A[0], _IN_A[1], "minion_melee",
                               faction_id="time_minion", route=[_IN_A])
        self.ws.world.add_component(minion, MapLocation(map_file))
        self.assertFalse(_can_see(self.ws, self.b, minion))

    def test_minion_aliado_no_bush_revela_pro_time(self):
        map_file = self.ws.get_entity_map(self.a)
        minion = create_minion(self.ws.world, _IN_A[0], _IN_A[1], "minion_melee",
                               faction_id="time_x", route=[_IN_A])
        self.ws.world.add_component(minion, MapLocation(map_file))
        self.ws.world.add_component(self.b, Faction(faction_id="time_x"))
        self.assertTrue(_can_see(self.ws, self.b, self.a))


class TestBushTargetClear(unittest.TestCase):
    """`WorldServer._tick_bush_target_clear` — alvo travado antes da
    entrada no bush precisa ser limpo explicitamente (mesmo motivo já
    documentado na Camuflagem: is_visible/bush sozinho só bloqueia mira
    NOVA, não limpa uma trava já existente)."""

    def setUp(self):
        self.ws = make_world_server()
        self.attacker = spawn_player(self.ws, "s1", *_OUTSIDE)
        self.target   = spawn_player(self.ws, "s2", *_OUTSIDE)
        map_file = self.ws.get_entity_map(self.attacker)
        self.ws._bush_zones_by_map[map_file] = [{"name": "Bush A", "rect": _BUSH_A}]

    def _lock_target(self):
        cst = self.ws.world.get_component(self.attacker, CombatState)
        cst.target_entity_id = self.target

    def test_alvo_trava_some_quando_ele_entra_sozinho_num_bush(self):
        self._lock_target()
        set_entity_tile(self.ws, self.target, *_IN_A)
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, -1)

    def test_alvo_trava_sobrevive_se_os_2_entram_juntos_no_bush(self):
        self._lock_target()
        set_entity_tile(self.ws, self.target,   *_IN_A)
        set_entity_tile(self.ws, self.attacker, *_IN_A)
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, self.target)

    def test_sem_transicao_de_bush_nao_mexe_em_nada(self):
        """Ninguém mudou de zona — o tick não deveria fazer nada, nem
        limpar um alvo que continua perfeitamente visível."""
        self._lock_target()
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, self.target)

    def test_minion_perde_current_target_eid_quando_alvo_entra_no_bush(self):
        map_file = self.ws.get_entity_map(self.attacker)
        minion = create_minion(self.ws.world, *_OUTSIDE, "minion_melee",
                               faction_id="time_minion", route=[_OUTSIDE])
        self.ws.world.add_component(minion, MapLocation(map_file))
        from engine.components import Minion as _MinTC
        m = self.ws.world.get_component(minion, _MinTC)
        m.current_target_eid = self.target
        m.state = "FIGHTING"

        set_entity_tile(self.ws, self.target, *_IN_A)
        self.ws._tick_bush_target_clear()

        m = self.ws.world.get_component(minion, _MinTC)
        self.assertEqual(m.current_target_eid, -1)
        self.assertEqual(m.state, "ADVANCING")

    def test_torre_nunca_participa(self):
        """Torre não tem TileMovement que muda de tile de verdade — o
        tick não deveria quebrar nem afetar nada por causa dela."""
        from engine.entity_factory import create_tower
        map_file = self.ws.get_entity_map(self.attacker)
        tower = create_tower(self.ws.world, _IN_A[0], _IN_A[1], "torre_de_fogo",
                             faction_id="time_torre")
        self.ws.world.add_component(tower, MapLocation(map_file))
        self._lock_target()
        # Não deveria lançar exceção nem afetar o alvo travado do attacker
        # (que não tem relação nenhuma com a torre).
        self.ws._tick_bush_target_clear()
        cst = self.ws.world.get_component(self.attacker, CombatState)
        self.assertEqual(cst.target_entity_id, self.target)


if __name__ == "__main__":
    unittest.main()
