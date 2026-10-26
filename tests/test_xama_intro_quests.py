"""
tests/test_xama_intro_quests.py — quests de introdução do Xamã (26/10/2026):
bem_vindo_xama (Caterina → Maelor, Cajado do Iniciante) → ecos_do_cajado (5
acertos de flecha espiritual no boneco, ganha Toque Ancestral) →
o_corredor_ancestral (Toque no boneco 3x).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from content.quests_data import QUESTS
from content.item_table import ITEMS
from content.skill_config import SKILL_CATALOG, INITIAL_SKILLS_BY_CLASS
from engine.quest_logic import match_objective

_CHAIN = ("bem_vindo_xama", "ecos_do_cajado", "o_corredor_ancestral")


class TestObjetivoComCajado(unittest.TestCase):
    def setUp(self):
        self.obj = QUESTS["ecos_do_cajado"].objectives[0]

    def _ev(self, kind):
        return {"name": "Boneco de treino", "race": "", "ranged_kind": kind}

    def test_so_conta_flecha_espiritual(self):
        self.assertTrue(match_objective("auto_attack_hit", self._ev("spirit"), self.obj))
        self.assertFalse(match_objective("auto_attack_hit", self._ev(""), self.obj))
        self.assertFalse(match_objective("auto_attack_hit", self._ev("arrow"), self.obj))

    def test_objetivo_sem_param_continua_aceitando_qualquer_ataque(self):
        obj = QUESTS["primeiros_tiros"].objectives[0]
        self.assertTrue(match_objective("auto_attack_hit", self._ev(""), obj))
        self.assertTrue(match_objective("auto_attack_hit", self._ev("arrow"), obj))


class TestCadeiaDoXama(unittest.TestCase):
    def test_cadeia_e_recompensas(self):
        q1, q2, q3 = (QUESTS[q] for q in _CHAIN)
        for q in (q1, q2, q3):
            self.assertEqual(q.class_req, "xama")
        self.assertIn("bem_vindo_xama", q2.requires)
        self.assertIn("ecos_do_cajado", q3.requires)
        self.assertIn("starter_staff", q1.reward.items)
        self.assertIn("starter_staff", ITEMS)
        self.assertEqual(q2.reward.skill, "toque_ancestral")
        self.assertIn("toque_ancestral", SKILL_CATALOG)

    def test_toque_nao_nasce_aprendido(self):
        self.assertNotIn("toque_ancestral", INITIAL_SKILLS_BY_CLASS.get("xama", []))

    def test_npcs_do_mapa_dao_e_recebem(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "maps", "map_1_entities.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        npcs = {n["name"]: n for lst in data.values() if isinstance(lst, list)
                for n in lst if isinstance(n, dict) and "name" in n}
        cat, mae = npcs["Caterina Alisarf"], npcs["Maelor Lobo Alfa"]
        self.assertIn("bem_vindo_xama", cat["quest_ids"])
        for q in _CHAIN:
            self.assertIn(q, mae["turn_in_ids"])
        self.assertIn("ecos_do_cajado", mae["quest_ids"])
        self.assertIn("o_corredor_ancestral", mae["quest_ids"])
        # o objetivo de falar com o Maelor usa o nome exato do NPC
        self.assertEqual(QUESTS["bem_vindo_xama"].objectives[0].target, "Maelor Lobo Alfa")


if __name__ == "__main__":
    unittest.main()
