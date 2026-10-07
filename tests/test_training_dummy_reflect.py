"""
tests/test_training_dummy_reflect.py — boneco de treino com reflexão de
dano (07/10/2026, pedido do usuário: "treino de defesa/escudo" — quando
o personagem bate no boneco, o boneco reflete um valor fixo de volta,
passando pela MESMA rolagem de combate normal (acerto/esquiva/aparo/
bloqueio/crítico/armadura) — não um valor cru aplicado direto no HP.

Decisões fechadas com o usuário (AskUserQuestion + mensagem avulsa):
- Valor FIXO por acerto, configurável por boneco (`TrainingDummy.
  reflect_damage`, 0 = sem reflexão, comportamento de sempre).
- Mitigado normalmente (armadura/resistência/escudo) — reusa
  `CombatSystem.deal_damage` recursivamente (boneco "bate de volta" via
  hit real), nunca uma fórmula de dano paralela.
- Pode matar o personagem normalmente (sem clamp em 1 HP).
- Passa pela rolagem de acerto/esquiva/aparo (miss/dodge/parry
  possíveis), não só armadura.

Ver `engine/components.py::TrainingDummy`, `engine/world_systems.py::
CombatSystem.deal_damage` (bloco de reflexão), `server/world_server.py`
(`_on_dummy_reflect`/`emit_dummy_reflect` — broadcast de rede, mesma
indireção que `emit_retaliation` já usa pro Escudo de Fogo).
"""
import os, sys, unittest
from unittest.mock import patch
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player
from engine.components import TrainingDummy, CombatStats, PlayerControlled
from engine.world_systems import CombatSystem


def _make_dummy(ws, tx: int, ty: int, reflect_damage: int = 0) -> int:
    """Boneco mínimo (sem passar pela fábrica completa) — só o que
    CombatSystem.deal_damage precisa: Position/CombatStats/TrainingDummy."""
    from engine.components import Position, TileMovement
    from engine.tileset import TILE_SIZE
    eid = ws.world.create_entity()
    x = tx * TILE_SIZE + TILE_SIZE / 2
    y = ty * TILE_SIZE + TILE_SIZE / 2
    ws.world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
    ws.world.add_component(eid, TileMovement(
        current_tile_x=tx, current_tile_y=ty, target_tile_x=tx, target_tile_y=ty,
        start_pixel_x=x, start_pixel_y=y, target_pixel_x=x, target_pixel_y=y))
    stats = CombatStats(base_stamina=999_999, base_armor=100, base_attack_power=0)
    stats._recalculate_effective_stats()
    stats.current_hp = stats.max_hp
    ws.world.add_component(eid, stats)
    ws.world.add_component(eid, TrainingDummy(reflect_damage=reflect_damage))
    return eid


class TestTrainingDummyComponentDefault(unittest.TestCase):

    def test_reflect_damage_default_e_zero(self):
        """Regressão -- bonecos existentes (criados sem o parâmetro novo)
        continuam com reflect_damage=0, comportamento de sempre."""
        self.assertEqual(TrainingDummy().reflect_damage, 0)


class TestCreateTrainingDummyReflectParam(unittest.TestCase):

    def test_create_training_dummy_default_sem_reflexao(self):
        from engine.entity_factory import create_training_dummy
        from engine.world import World
        world = World()
        eid = create_training_dummy(world, 10, 10)
        td = world.get_component(eid, TrainingDummy)
        self.assertEqual(td.reflect_damage, 0)

    def test_create_training_dummy_com_reflect_damage(self):
        from engine.entity_factory import create_training_dummy
        from engine.world import World
        world = World()
        eid = create_training_dummy(world, 10, 10, reflect_damage=15)
        td = world.get_component(eid, TrainingDummy)
        self.assertEqual(td.reflect_damage, 15)


class TestMapLoaderParsesReflectDamage(unittest.TestCase):

    def test_training_dummies_le_reflect_damage_do_json(self):
        import json, tempfile, os as _os
        from engine.map_loader import _merge_entities_json
        data = {
            "training_dummies": [
                {"x": 10, "y": 20, "reflect_damage": 7},
                {"x": 11, "y": 21},
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            # Mesmo shape que map_loader.py monta antes de chamar
            # _merge_entities_json de verdade (ver load_map) -- "merchants"
            # precisa existir pro branch de fallback (sem "merchants" no
            # JSON de teste) não quebrar.
            spawn_points = {"training_dummies": [], "merchants": []}
            _merge_entities_json(path, spawn_points)
            self.assertEqual(spawn_points["training_dummies"],
                             [(10, 20, 7), (11, 21, 0)])
        finally:
            _os.unlink(path)


class TestDummyReflectDamageGate(unittest.TestCase):
    """CombatSystem.deal_damage: boneco com reflect_damage=0 (padrão,
    todos os bonecos já existentes) não reflete nada -- regressão."""

    def setUp(self):
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "s1", 130, 374)
        self.player_cs = self.ws.world.get_component(self.player, CombatStats)
        self.player_cs.current_hp = self.player_cs.max_hp
        self.dummy = _make_dummy(self.ws, 131, 374, reflect_damage=0)
        self.cs_sys = CombatSystem(self.ws.world, is_server=True)

    def test_boneco_sem_reflect_damage_nao_reflete(self):
        hp_before = self.player_cs.current_hp
        self.cs_sys.deal_damage(self.player, self.dummy, "physical",
                                base_ability_damage=50, apply_armor_reduction=False)
        self.assertEqual(self.player_cs.current_hp, hp_before,
            "boneco sem reflect_damage configurado não deveria revidar")


class TestDummyReflectDamageApplies(unittest.TestCase):
    """Boneco COM reflect_damage>0 reflete de volta via hit real."""

    def setUp(self):
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "s1", 130, 374)
        self.player_cs = self.ws.world.get_component(self.player, CombatStats)
        self.player_cs.current_hp = self.player_cs.max_hp
        # Zera avoidance/block do player pra reduzir a chance de miss/dodge/
        # parry/block mascarar o teste (ainda é possível por crit roll
        # residual, mas dodge/block ficam zerados -- determinismo razoável).
        self.player_cs.dodge_rating = 0.0
        self.player_cs.block_rating = 0.0
        self.dummy = _make_dummy(self.ws, 131, 374, reflect_damage=20)
        self.cs_sys = CombatSystem(self.ws.world, is_server=True)

    def test_golpe_no_boneco_reflete_dano_no_player(self):
        hp_before = self.player_cs.current_hp
        # RNG travado em 0.99 -- fora de QUALQUER faixa de miss/dodge/
        # parry/block/crit (todas são "roll < chance", chance sempre <1),
        # garante outcome='hit' determinístico nos DOIS golpes (original
        # E o reflexo recursivo, que não é controlável via pre_outcome
        # por vir de dentro do próprio deal_damage) -- sem isso, o teste
        # era flaky (boneco tem ~5% de miss próprio por padrão, suficiente
        # pra falhar de vez em quando numa suíte rodada muitas vezes).
        with patch("random.random", return_value=0.99):
            self.cs_sys.deal_damage(self.player, self.dummy, "physical",
                                    base_ability_damage=9999, apply_armor_reduction=False)
        self.assertLess(self.player_cs.current_hp, hp_before,
            "boneco com reflect_damage>0 deveria ter revidado algum dano")

    def test_broadcast_de_rede_registra_boneco_como_atacante(self):
        """Confirma que o hit refletido vira uma entrada real em
        _combat_this_tick (sem isso, o cliente nunca veria o texto
        flutuante de dano no player -- mesma indireção de
        emit_retaliation, _svc não conhece WorldServer diretamente)."""
        before = len(self.ws._combat_this_tick)
        with patch("random.random", return_value=0.99):
            self.cs_sys.deal_damage(self.player, self.dummy, "physical",
                                    base_ability_damage=9999, apply_armor_reduction=False)
        new_entries = self.ws._combat_this_tick[before:]
        reflect_entries = [e for e in new_entries if e["attacker"] == self.dummy
                          and e["target"] == self.player]
        self.assertTrue(reflect_entries,
            "deveria existir uma entrada de combat delta com o boneco como atacante")

    def test_armadura_do_player_mitiga_o_reflexo(self):
        """Confirma que o reflexo passa por mitigação de verdade (armor),
        não um valor cru -- compara dano recebido com armadura baixa vs
        alta, usando outcome='hit' forçado (pre_outcome) pros dois lados
        pra isolar só o efeito da armadura, sem ruído de miss/dodge/crit."""
        # Reconstroi 2 players com armaduras bem diferentes, mesmo reflect.
        ws2 = make_world_server()
        p_low  = spawn_player(ws2, "slow", 130, 374)
        p_high = spawn_player(ws2, "shigh", 132, 374)
        cs_low  = ws2.world.get_component(p_low,  CombatStats)
        cs_high = ws2.world.get_component(p_high, CombatStats)
        cs_low.current_hp  = cs_low.max_hp
        cs_low.dodge_rating  = 0.0
        cs_low.block_rating  = 0.0
        cs_high.base_armor = 5000
        cs_high._recalculate_effective_stats()
        cs_high.current_hp = cs_high.max_hp
        cs_high.dodge_rating = 0.0
        cs_high.block_rating = 0.0
        dummy_low  = _make_dummy(ws2, 131, 374, reflect_damage=100)
        dummy_high = _make_dummy(ws2, 133, 374, reflect_damage=100)
        cs_sys2 = CombatSystem(ws2.world, is_server=True)

        hp_low_before  = cs_low.current_hp
        hp_high_before = cs_high.current_hp
        # pre_outcome="hit" isola o golpe ORIGINAL (nunca sai como
        # miss) -- o reflexo recursivo não aceita pre_outcome (vem de
        # dentro do próprio deal_damage), por isso o RNG travado
        # também, mesmo racional do teste acima.
        with patch("random.random", return_value=0.99):
            cs_sys2.deal_damage(p_low,  dummy_low,  "physical",
                                base_ability_damage=9999, apply_armor_reduction=False,
                                pre_outcome="hit")
            cs_sys2.deal_damage(p_high, dummy_high, "physical",
                                base_ability_damage=9999, apply_armor_reduction=False,
                                pre_outcome="hit")
        dmg_low  = hp_low_before  - cs_low.current_hp
        dmg_high = hp_high_before - cs_high.current_hp
        self.assertLess(dmg_high, dmg_low,
            "player com mais armadura deveria tomar menos dano do reflexo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
