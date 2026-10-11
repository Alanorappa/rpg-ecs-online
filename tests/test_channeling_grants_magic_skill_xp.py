"""
tests/test_channeling_grants_magic_skill_xp.py

Bug real relatado pelo usuário (10/10/2026): os ticks de Calamidade
Flamejante não concediam XP da skill Magic — só o cast único de Bola de
Fogo/Calcinar/Nova Congelante concedia (via
`_process_spell_cast_completions`, que deduz mana 1x e chama
`grant_skill_xp(..., "magic", 1)` no mesmo golpe). Calamidade Flamejante
deduz mana por TICK dentro de `_process_player_channeling` — caminho
totalmente separado que nunca replicava essa concessão de XP, mesma
classe de bug já catalogada pra Choque Térmico (PROBLEMAS_ARQUITETURA.md
§263) — um mecanismo compartilhado existia só no caminho de cast único,
nunca no de canalização.

Fix: `_process_player_channeling` agora chama `grant_skill_xp(..., "magic",
1)` toda vez que deduz `mana_per_tick` com sucesso, mesma regra do cast
único (custo efetivo de mana > 0, independente de acerto/dano).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import Channeling, SkillLevels


class TestChannelingConcedeMagicSkillXP(unittest.TestCase):
    """Reproduz o sintoma relatado: canalizar Calamidade Flamejante por
    tempo suficiente pra pelo menos 1 tick de mana/dano deveria subir XP
    de Magic — antes do fix, ficava parado em 0 pra sempre."""

    def setUp(self):
        self.ws = make_world_server()
        self.mage = spawn_player(self.ws, "magicxp_mage", 130, 374, class_id="mago")

        skl = self.ws.world.get_component(self.mage, SkillLevels)
        self.assertIsNotNone(skl, "setup do teste está errado — mage sem SkillLevels")
        self.xp_antes = skl.xp.get("magic", 0)

        aoe_x, aoe_y = 130 * 32 + 16, 374 * 32 + 16
        ch = Channeling(spell_id="calamidade_flamejante", duration=10.0, tick_interval=0.1,
                        mana_per_tick=10, target_x=aoe_x, target_y=aoe_y, radius_tiles=3)
        self.ws.world.add_component(self.mage, ch)

    def test_tick_de_canalizacao_concede_xp_de_magic(self):
        run_ticks(self.ws, 10, dt=0.05)  # 0.5s -> cobre 1+ tick de mana (tick_interval=0.1)
        skl = self.ws.world.get_component(self.mage, SkillLevels)
        xp_depois = skl.xp.get("magic", 0)
        self.assertGreater(xp_depois, self.xp_antes,
                           "tick de canalização não concedeu XP de Magic — "
                           "mesmo bug relatado: Calamidade Flamejante não sobe skill level")


if __name__ == "__main__":
    unittest.main(verbosity=2)
