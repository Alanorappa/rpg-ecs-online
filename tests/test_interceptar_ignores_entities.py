"""
tests/test_interceptar_ignores_entities.py — 09/09/2026, pedido do
usuário: Interceptar (e o mesmo `ignore_entities=True` que qualquer
skill futura de dash possa reusar) não deve ser bloqueado por
player/mob/minion/NPC no caminho — só por obstáculo SÓLIDO de verdade
(árvore/pedra/parede/piso). Mesmo espírito que o knockback do Tiro
Repulsivo já tinha (`server/spell_completion_processor.py::_is_solid`,
uma checagem local própria que nunca olha ocupação por entidade) —
Interceptar é o único que usa a função COMPARTILHADA
(`is_tile_walkable`), que antes checava as duas coisas juntas.

Fix: `engine.world_systems.is_tile_walkable`/
`TileValidationSystem.is_tile_walkable` ganham `ignore_entities=True`
— pula só a checagem de ocupação (`self._occupied`), nunca a de
`tile_type.is_solid` nem as regras de piso/elevação. Usado nos 2
pontos do Interceptar: `_dash_path_clear` (caminho do dash) e a
seleção do tile de destino adjacente ao alvo.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob, set_entity_tile, authorize_skill
from engine.components import CombatState, CharacterStats, PlayerSkills, TileMovement
from engine.world_systems import is_tile_walkable
from engine.components import Tilemap


def _ready_skill(ws, eid, sid):
    authorize_skill(ws, eid, sid)
    ps = ws.world.get_component(eid, PlayerSkills)
    from content.skill_config import SKILL_CATALOG
    sk = next((s for s in ps.skills if s and s.skill_id == sid), None)
    if sk is None:
        sk = PlayerSkills._make_skill(sid, SKILL_CATALOG)
        idx = ps.skills.index(None)
        ps.skills[idx] = sk
    sk.current_cooldown = 0.0
    return sk


class TestIsTileWalkableIgnoreEntities(unittest.TestCase):
    """Unidade: `ignore_entities=True` pula só ocupação, nunca solidez real."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")

    def test_ignore_entities_permite_tile_ocupado_por_outra_entidade(self):
        blocker = spawn_player(self.ws, "s2", 129, 374, class_id="guerreiro")
        run_ticks(self.ws, 1)   # TileValidationSystem só reconstrói `_occupied` em update()

        # Sem ignore_entities: bloqueado (comportamento de sempre, MOVE normal).
        self.assertFalse(is_tile_walkable(self.eid, 129, 374, 130, 374))
        # Com ignore_entities: passa — entidade nunca bloqueia.
        self.assertTrue(is_tile_walkable(self.eid, 129, 374, 130, 374, ignore_entities=True))

    def test_ignore_entities_nao_libera_tile_solido_de_verdade(self):
        tilemap = next(tm for _, tm in self.ws.world.get_entities_with(Tilemap))
        # Acha um tile sólido real no mapa (borda de montanha, sempre existe
        # — ver CLAUDE.md: "toda linha começa/termina com m/montanha").
        solid_xy = None
        for ty, row in enumerate(tilemap.tile_matrix):
            for tx, tile in enumerate(row):
                if tile.is_solid:
                    solid_xy = (tx, ty)
                    break
            if solid_xy:
                break
        self.assertIsNotNone(solid_xy, "mapa de teste deveria ter ao menos 1 tile sólido")
        sx, sy = solid_xy
        self.assertFalse(is_tile_walkable(self.eid, sx, sy, ignore_entities=True),
                        "ignore_entities nunca deveria liberar obstáculo sólido de verdade")


class TestInterceptarNaoBloqueiaPorEntidade(unittest.TestCase):
    """Integração: dash real do Interceptar passando por/pousando em tile
    ocupado por outra entidade (mob/player) — deve funcionar normalmente."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        run_ticks(self.ws, 50)
        self.mob = first_mob(self.ws)
        set_entity_tile(self.ws, self.mob, 127, 374)
        cst = self.ws.world.get_component(self.eid, CombatState)
        cst.target_entity_id = self.mob
        cst.is_pursuing = True
        char = self.ws.world.get_component(self.eid, CharacterStats)
        char.rage = 100
        _ready_skill(self.ws, self.eid, "interceptar")

    def _cast_interceptar(self):
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "interceptar",
            "tid": self.mob, "dir_x": 0.0, "dir_y": 0.0, "ts": 0, "dbg_seq": 0,
        })
        self.ws._process_skill_requests()

    def test_dash_ignora_entidade_no_tile_de_destino_adjacente_ao_alvo(self):
        """O candidato de destino mais próximo do caster (128,374, entre
        caster e mob) fica ocupado por um 3º player — sem ignore_entities
        o Interceptar recusaria ("Sem espaço ao redor do alvo") e cairia
        pro 2º candidato mais distante; com ignore_entities, continua
        escolhendo o candidato MAIS PRÓXIMO normalmente."""
        spawn_player(self.ws, "s3", 128, 374, class_id="mago")

        self._cast_interceptar()

        tm = self.ws.world.get_component(self.eid, TileMovement)
        self.assertTrue(tm.is_dash, "Interceptar não deveria ser bloqueado por entidade no destino")
        self.assertEqual((tm.target_tile_x, tm.target_tile_y), (128, 374))

    def test_dash_ignora_entidade_no_caminho_do_bresenham(self):
        """3º player exatamente no meio do caminho reto entre caster
        (130,374) e o destino real (128,374, adjacente ao mob em
        127,374 — mesmo valor confirmado no teste acima) — sem
        ignore_entities, `_dash_path_clear` recusaria ("Caminho
        bloqueado") ao passar por (129,374)."""
        spawn_player(self.ws, "s3", 129, 374, class_id="mago")   # no meio do caminho

        self._cast_interceptar()

        tm = self.ws.world.get_component(self.eid, TileMovement)
        self.assertTrue(tm.is_dash,
                        "Interceptar não deveria ser bloqueado por entidade NO CAMINHO do dash")


if __name__ == "__main__":
    unittest.main()
