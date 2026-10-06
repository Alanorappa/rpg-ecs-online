"""
tests/test_attacking_mob_respects_leash.py — bug real relatado pelo
usuário (06/10/2026): NPC "Custodio Benevide" (e mobs comuns) ficavam
"travados" lutando longe de casa pra sempre, sem nunca voltar ao spawn.

Causa raiz: a checagem de leash de verdade (`EnemyAISystem.update()`,
bloco "Perseguição do Jogador", ~linha 3870) só roda se o mob alcançar
esse ponto do código no tick — mas o bloco de ataque, mais ACIMA, dá
`continue` todo tick em que o mob já está em alcance (ATTACKING), antes
de chegar lá. Um mob ainda CHASING passa pela checagem normalmente e
desiste certo ao estourar o leash; mas se o PASSO final da perseguição
pousa o mob adjacente ao alvo no MESMO tick em que ele já passou dos 20
tiles de casa, vira ATTACKING ali mesmo e NUNCA MAIS reavalia o leash
enquanto continuar em alcance — preso lutando pra sempre se o combate
empacar (muitos "evadiu" dos dois lados, nenhum dos dois morre).

Fix: checagem de leash antecipada, só para o estado ATTACKING, logo
após `in_attack_range` ser calculado e ANTES de qualquer tentativa de
golpe — reusa `force_mob_give_up_target` (mesma função que o leash
normal já usa). O leash normal (cobrindo CHASING) continua intocado.

Ver PROBLEMAS_ARQUITETURA.md §241.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, first_mob
from engine.components import AIControlled, InitialPosition, TileMovement
from engine.utils import snap_to_tile
from engine.world_systems import EnemyAISystem
from engine.tileset import TILE_SIZE

MAP_A = "maps/map_1.csv"


def _find_enemy_ai_system(ws, map_file: str) -> EnemyAISystem:
    bundle = ws._map_bundles[map_file]
    for system in bundle.systems:
        if isinstance(system, EnemyAISystem):
            return system
    raise AssertionError("EnemyAISystem não encontrado no bundle")


class TestAttackingMobRespeitaLeash(unittest.TestCase):
    def setUp(self):
        from tests.helpers import run_ticks
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        run_ticks(self.ws, 60)
        self.mob_eid = first_mob(self.ws)
        self.assertIsNotNone(self.mob_eid, "precisa de um mob hostil real pro teste")

        mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)
        snap_to_tile(self.ws.world, self.player_eid,
                     mob_tm.current_tile_x + 1, mob_tm.current_tile_y)

        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        ai.state = "ATTACKING"
        ai.target_eid = self.player_eid
        ai.aggroed_by_damage = False

        # Simula o mob já ter perseguido o alvo pra bem longe de casa —
        # InitialPosition (âncora do leash) fica a 25 tiles de distância
        # da posição atual (MAX_LEASH_RADIUS = 20).
        ip = self.ws.world.get_component(self.mob_eid, InitialPosition)
        ip.x = (mob_tm.current_tile_x - 25) * TILE_SIZE + TILE_SIZE // 2
        ip.y = mob_tm.current_tile_y * TILE_SIZE + TILE_SIZE // 2

        self.enemy_ai_system = _find_enemy_ai_system(self.ws, MAP_A)

    def test_mob_atacando_alem_do_leash_desiste_no_proximo_tick(self):
        self.enemy_ai_system.update(0.05)

        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        self.assertEqual(ai.state, "RETURNING",
            "mob em ATTACKING além do leash deveria desistir e voltar pro spawn, "
            "não ficar preso lutando pra sempre")
        self.assertEqual(ai.target_eid, -1)

    def test_mob_atacando_dentro_do_leash_continua_lutando(self):
        """Guarda de regressão: só mob ALÉM do leash desiste — um mob
        legitimamente perto de casa, só que ATTACKING, não pode ser
        afetado por este fix."""
        ip = self.ws.world.get_component(self.mob_eid, InitialPosition)
        mob_tm = self.ws.world.get_component(self.mob_eid, TileMovement)
        ip.x = mob_tm.current_tile_x * TILE_SIZE + TILE_SIZE // 2
        ip.y = mob_tm.current_tile_y * TILE_SIZE + TILE_SIZE // 2

        self.enemy_ai_system.update(0.05)

        ai = self.ws.world.get_component(self.mob_eid, AIControlled)
        self.assertEqual(ai.state, "ATTACKING")
        self.assertEqual(ai.target_eid, self.player_eid)


if __name__ == "__main__":
    unittest.main()
