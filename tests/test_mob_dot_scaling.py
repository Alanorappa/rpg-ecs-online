"""
tests/test_mob_dot_scaling.py — escala de dano por tick dos DoTs de
habilidade de mob (bleed/poison/burn), 28/08/2026, pedido do usuário.

`base_attack_power` do mob já escala LINEAR com level
(`entity_factory.py`). Multiplicar de novo por `AbilityDef.magnitude`
compunha 2 crescimentos lineares e explodia em level alto — achado
real: urso level 24, Laceração batendo 96/tick. Fix: `damage_calculator.
mob_dot_tick_damage()` divide por `√level`, ponto único de verdade
usado pelos 2 pontos de `engine/world_systems.py` que aplicam efeito
de habilidade de mob (corpo-a-corpo e projétil).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import unittest

from engine.damage_calculator import mob_dot_tick_damage


class TestMobDotTickDamage(unittest.TestCase):

    def test_level_1_identico_a_formula_antiga(self):
        # antiga: magnitude * base_attack_power (sem divisão nenhuma) —
        # em level 1, √1 == 1, então a fórmula nova não muda nada.
        magnitude, ap = 2.0, 2
        self.assertEqual(mob_dot_tick_damage(magnitude, ap, level=1),
                          round(magnitude * ap))

    def test_urso_level_24_nao_bate_mais_96(self):
        # Caso real reportado: Laceração (magnitude=2.0) do urso
        # (attack_power base 2) no level 24 — base_attack_power já
        # escalado linear pelo entity_factory = 2 * 24 = 48.
        dmg = mob_dot_tick_damage(2.0, 48, level=24)
        self.assertLess(dmg, 96)
        self.assertEqual(dmg, 20)

    def test_cresce_bem_mais_devagar_que_a_formula_linear_antiga(self):
        # base_attack_power cresce linear com level (já simulado aqui);
        # a fórmula nova precisa crescer beeem mais devagar que ela.
        magnitude = 2.0
        base_ap_per_level = 2
        dmg_10 = mob_dot_tick_damage(magnitude, base_ap_per_level * 10, level=10)
        dmg_40 = mob_dot_tick_damage(magnitude, base_ap_per_level * 40, level=40)
        old_formula_ratio = 4.0   # level 40 / level 10 == 4x na fórmula antiga
        self.assertLess(dmg_40 / dmg_10, old_formula_ratio)

    def test_nunca_decresce_com_o_level(self):
        magnitude, base_ap_per_level = 1.5, 3
        prev = mob_dot_tick_damage(magnitude, base_ap_per_level, level=1)
        for level in range(2, 61):
            dmg = mob_dot_tick_damage(magnitude, base_ap_per_level * level, level=level)
            self.assertGreaterEqual(dmg, prev)
            prev = dmg

    def test_minimo_1_garantido(self):
        self.assertEqual(mob_dot_tick_damage(0.01, 1, level=50), 1)

    def test_level_zero_ou_negativo_nao_quebra(self):
        # defensivo — level real nunca deveria vir <1, mas não pode
        # dividir por zero/dar raiz negativa se algo passar errado.
        self.assertEqual(mob_dot_tick_damage(2.0, 10, level=0),
                          mob_dot_tick_damage(2.0, 10, level=1))


if __name__ == "__main__":
    unittest.main()
