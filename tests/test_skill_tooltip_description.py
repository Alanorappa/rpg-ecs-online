"""
tests/test_skill_tooltip_description.py
Tooltip de skill sempre usa a descrição do SKILL_CATALOG (09/10/2026,
pedido explícito do usuário) — client/tooltip_handlers.py::
_skill_tooltip_lines() tinha um if/elif por skill calculando dano/efeito
na mão (texto PARALELO ao catálogo, nunca lido dali) pra ~14 skills;
qualquer skill fora dessa lista já caía no fallback `skill.description`.
Unificado: SEMPRE `skill.description` (populado a partir de
SKILL_CATALOG["desc"] por PlayerSkills._make_skill), nunca mais um
bloco de texto calculado por skill_id.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import PlayerSkills, CombatStats, CharacterStats
from content.skill_config import SKILL_CATALOG
from client.tooltip_handlers import TooltipHandlers


class _Fixture(TooltipHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity


class TestTooltipUsaDescricaoDoCatalogo(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, CombatStats())
        self.world.add_component(self.player, CharacterStats(class_id="guerreiro"))
        self.fx = _Fixture(self.world, self.player)

    def _lines_for(self, sid: str) -> list:
        skill = PlayerSkills._make_skill(sid, SKILL_CATALOG)
        return self.fx._skill_tooltip_lines(
            skill, is_procced=False, visual_ready=True,
            target_hp_ratio=1.0, player_rage=0)

    def test_primeira_linha_e_sempre_a_descricao_do_catalogo(self):
        """Antes, skills como golpe_poderoso mostravam dano calculado na
        mão ("Dano: X–Y (3x...)") em vez da descrição do catálogo —
        verifica pra TODAS as skills que já tinham esse if/elif próprio,
        não só uma."""
        skills_antes_hardcoded = [
            "golpe_poderoso", "impacto", "executar", "vitoria_iminente",
            "interceptar", "golpe_debilitante", "punho_no_queixo",
            "fatiador_de_corpos", "brado_provocativo", "bola_de_fogo",
            "nova_congelante", "bloco_de_gelo", "polimorfia",
            "calamidade_flamejante",
        ]
        for sid in skills_antes_hardcoded:
            with self.subTest(sid=sid):
                entry = SKILL_CATALOG[sid]
                expected_desc = entry["desc"] if isinstance(entry, dict) else entry[1]
                lines = self._lines_for(sid)
                first_text = lines[0][0]
                self.assertEqual(first_text, expected_desc,
                                 f"{sid}: 1ª linha do tooltip deveria ser a descrição do catálogo")

    def test_descricao_nao_fica_vazia_pra_nenhuma_dessas_skills(self):
        skills_antes_hardcoded = [
            "golpe_poderoso", "impacto", "executar", "vitoria_iminente",
            "interceptar", "golpe_debilitante", "punho_no_queixo",
            "fatiador_de_corpos", "brado_provocativo", "bola_de_fogo",
            "nova_congelante", "bloco_de_gelo", "polimorfia",
            "calamidade_flamejante",
        ]
        for sid in skills_antes_hardcoded:
            with self.subTest(sid=sid):
                lines = self._lines_for(sid)
                self.assertTrue(lines[0][0].strip(), f"{sid}: descrição não pode ficar vazia")


if __name__ == "__main__":
    unittest.main()
