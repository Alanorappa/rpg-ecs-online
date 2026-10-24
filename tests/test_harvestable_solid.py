"""
tests/test_harvestable_solid.py

Colisão de coleta (Harvestable.solid) — 24/10/2026, bug real em produção:
as poças de Vômito da quest de guerreiro (`requires_quest`) ficavam
INVISÍVEIS pra quem não tinha a quest (ex: Xamã) mas continuavam sólidas
(sprite "pl_vomito" sumiu do catálogo → default sólido) — paredes
invisíveis que travavam o passo do player e o caminho de mob/NPC na zona.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.world import World
from engine.components import Harvestable
from engine.entity_factory import create_harvestable_entity


def _solid(**kw) -> bool:
    w = World()
    eid = create_harvestable_entity(w, 10, 10, corpse_id=1, **kw)
    return w.get_component(eid, Harvestable).solid


class TestHarvestableSolid(unittest.TestCase):
    def test_sprite_desconhecido_continua_solido(self):
        self.assertTrue(_solid(sprite_id="nao_existe_xyz"))

    def test_sprite_passavel_no_catalogo(self):
        self.assertFalse(_solid(sprite_id="vp_186"))

    def test_solid_explicito_vence_o_catalogo(self):
        self.assertFalse(_solid(sprite_id="nao_existe_xyz", solid=False))
        self.assertTrue(_solid(sprite_id="vp_186", solid=True))

    def test_coleta_de_quest_nunca_e_solida(self):
        self.assertFalse(_solid(sprite_id="nao_existe_xyz", requires_quest="q"))
        self.assertFalse(_solid(sprite_id="vp_186", requires_quest="q", solid=True))

    def test_vomito_do_mapa_real_e_passavel(self):
        """O Vômito de map_1 (zona com requires_quest + "solid": false)."""
        import json
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "maps", "map_1_entities.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        zones = [z for z in data.get("harvestable_zones", [])
                 if any(s.get("sprite") == "pl_vomito" for s in z.get("spawns", []))]
        self.assertTrue(zones)
        for z in zones:
            for s in z["spawns"]:
                self.assertFalse(_solid(sprite_id=s.get("sprite", ""),
                                        requires_quest=z.get("requires_quest", ""),
                                        solid=s.get("solid")))


if __name__ == "__main__":
    unittest.main()
