"""
tests/test_hp_pct_preserve_on_stamina_change.py — 30/08/2026.

Bug real relatado pelo usuário: dentro da BG, o auto-regen (HP5) ficava
"tickando" mesmo com o personagem aparentemente em HP cheio. Causa raiz:
qualquer coisa que aumentasse `stamina` (max_hp) — buff do chefe de
jungle, troca de equipamento, futuro talento — preservava o HP
ABSOLUTO (`engine/stat_fns.py::recalculate_combat_stats`, comportamento
correto só quando max_hp DIMINUI), deixando um buraco real: um player
100% cheio virava, por exemplo, 300/330 depois de um +10% de stamina,
sem cura nenhuma — e o HP5 (corretamente) ficava tickando até fechar
essa diferença de verdade. `recalculate_preserving_hp_pct` (novo, ponto
único de verdade — antes esse padrão existia 2x duplicado só pra
proc de item/expiração de buff) corrige isso preservando a PORCENTAGEM
de HP em vez do valor absoluto.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import CombatStats, Inventory, Equipment, Item, Modifier
from engine.stat_fns import recalculate_preserving_hp_pct, add_modifier, remove_modifier


class TestRecalculatePreservingHpPct(unittest.TestCase):
    """Unitário, direto no helper — sem WorldServer."""

    def _make_cs(self, current_hp, max_hp):
        # max_hp = int(stamina) direto (engine/components.py::_calculate_max_hp) —
        # base_stamina=max_hp já dá o max_hp certo sem modifier nenhum.
        cs = CombatStats(base_stamina=max_hp)
        cs.current_hp = current_hp
        return cs

    def test_full_hp_continua_full_quando_max_hp_sobe(self):
        cs = self._make_cs(300, 300)
        recalculate_preserving_hp_pct(
            cs, lambda: add_modifier(cs, Modifier("stamina", 3, "flat", source="buff")))
        self.assertEqual(cs.max_hp, 330)
        self.assertEqual(cs.current_hp, 330, "100% antes deveria continuar 100% depois")

    def test_hp_parcial_preserva_a_porcentagem_nao_o_absoluto(self):
        # Exemplo do próprio usuário: 50% de HP, max_hp sobe 100 (aprox.
        # via +10 de stamina flat = +100 hp) — current_hp deve continuar ~50%.
        cs = self._make_cs(150, 300)   # 50%
        recalculate_preserving_hp_pct(
            cs, lambda: add_modifier(cs, Modifier("stamina", 10, "flat", source="buff")))
        self.assertEqual(cs.max_hp, 400)
        self.assertEqual(cs.current_hp, 200, "50% de 400 = 200, não 150 (absoluto preservado errado)")

    def test_max_hp_diminuindo_continua_clampando_como_antes(self):
        cs = self._make_cs(300, 300)
        mod = Modifier("stamina", 3, "flat", source="buff")
        recalculate_preserving_hp_pct(cs, lambda: add_modifier(cs, mod))  # 330, ainda 100%
        recalculate_preserving_hp_pct(cs, lambda: remove_modifier(cs, mod))  # volta a 300
        self.assertEqual(cs.max_hp, 300)
        self.assertEqual(cs.current_hp, 300)

    def test_nao_mexe_em_current_hp_quando_max_hp_nao_muda(self):
        cs = self._make_cs(150, 300)
        recalculate_preserving_hp_pct(
            cs, lambda: add_modifier(cs, Modifier("armor", 10, "flat", source="buff")))
        self.assertEqual(cs.max_hp, 300)
        self.assertEqual(cs.current_hp, 150, "atributo que não afeta max_hp não deveria mexer em current_hp")


class TestEquipmentGrowsMaxHpPreservesPct(unittest.TestCase):
    """Cenário exato relatado pelo usuário: equipar item com +stamina."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.inv = self.ws.world.get_component(self.eid, Inventory)
        self.cs  = self.ws.world.get_component(self.eid, CombatStats)

    def test_full_hp_continua_full_apos_equipar_item_com_stamina(self):
        self.cs.current_hp = self.cs.max_hp   # fixture de spawn não garante full HP
        max_before = self.cs.max_hp

        item = Item("Peitoral Encantado", "armor", "chest",
                   modifiers=[Modifier("stamina", 10, "flat", source="equipment")],
                   armor_class="placa")
        self.inv.items.append(item)
        rejected = self.ws.equip_item_from_inventory("s1", 0)

        self.assertIsNone(rejected)
        self.assertGreater(self.cs.max_hp, max_before, "item deveria ter aumentado max_hp")
        self.assertEqual(self.cs.current_hp, self.cs.max_hp,
                         "estava 100% antes de equipar — deveria continuar 100%, não ganhar um buraco")

    def test_hp_parcial_preserva_porcentagem_apos_equipar(self):
        self.cs.current_hp = self.cs.max_hp // 2   # 50%
        max_before = self.cs.max_hp

        item = Item("Peitoral Encantado", "armor", "chest",
                   modifiers=[Modifier("stamina", 10, "flat", source="equipment")],
                   armor_class="placa")
        self.inv.items.append(item)
        self.ws.equip_item_from_inventory("s1", 0)

        self.assertGreater(self.cs.max_hp, max_before)
        _pct = self.cs.current_hp / self.cs.max_hp
        self.assertAlmostEqual(_pct, 0.5, delta=0.02,
                               msg="~50% antes deveria continuar ~50% depois de equipar")


class TestJungleBossBuffPreservesHpPct(unittest.TestCase):
    """Buff do chefe de jungle (BG) com +% de stamina."""

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        self.cs  = self.ws.world.get_component(self.eid, CombatStats)

    def test_buff_de_stamina_preserva_hp_cheio(self):
        # buff_cycle: índice 0 = só armor, índice 1 = stamina (10%) — 2
        # mortes do mesmo camp pra alcançar o buff que muda max_hp.
        from engine.components import Faction, InitialPosition
        self.ws.world.add_component(self.eid, Faction(faction_id="jogadores"))
        boss = self.ws.world.create_entity()
        self.ws.world.add_component(boss, InitialPosition(x=100, y=100))
        map_file = self.ws.get_player_map("s1")

        self.cs.current_hp = self.cs.max_hp   # fixture de spawn não garante full HP
        self.ws._grant_jungle_boss_buff(boss, "jogadores", map_file,
                                        "jungle_boss_guardiao_de_pedra")  # idx 0: só armor
        max_before = self.cs.max_hp
        self.assertEqual(self.cs.current_hp, max_before)

        self.ws._grant_jungle_boss_buff(boss, "jogadores", map_file,
                                        "jungle_boss_guardiao_de_pedra")  # idx 1: stamina +10%

        self.assertGreater(self.cs.max_hp, max_before, "2ª morte do camp dá +10% de stamina")
        self.assertEqual(self.cs.current_hp, self.cs.max_hp,
                         "100% antes do buff deveria continuar 100% depois — sem o hp5 ter que 'tickar'")


if __name__ == "__main__":
    unittest.main()
