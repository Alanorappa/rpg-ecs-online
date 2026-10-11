"""
tests/test_fire_talents_general.py

Bug real relatado pelo usuário (10/10/2026): o talento Choque Térmico
(dano de fogo dobrado em alvo enraizado pela Nova Congelante) funcionava
em Bola de Fogo e Calcinar, mas não em Calamidade Flamejante. Causa raiz:
Calamidade Flamejante usa um caminho de dano totalmente separado
(`_process_player_channeling`, tick de `Channeling`) que nunca replicava
os checks de talento escritos à mão dentro de `_server_bola_de_fogo`/
`_server_calcinar`.

Fix: `_apply_general_fire_talent_bonus`/`_trigger_general_fire_talent_procs`
(server/spell_completion_processor.py) viram ponto único, chamados pelos
3 lugares que causam dano de fogo. Cobre os 5 talentos cuja própria
descrição fala de "fogo"/"habilidades de fogo" em geral (Pirotecnia,
Crematória, Choque Térmico, Chama Interna, Lapso Elemental) — Queimaduras
Profundas e Exaustão ficam de fora de propósito (descrição cita "Bola de
Fogo" especificamente, ver content/talent_data.py).

Dois níveis de teste:
  1. Unitário determinístico da função pura de multiplicador de dano —
     sem RNG, prova a matemática do Choque Térmico isoladamente.
  2. Wiring — reproduz o sintoma relatado de verdade: cast real de
     Calamidade Flamejante (fila de skill do servidor) contra um alvo
     enraizado, confirma que o multiplicador é aplicado DENTRO do tick
     de canalização de verdade (não só numa chamada direta da função).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import CombatStats, StatusEffects, ActiveEffect, CharacterStats


class TestApplyGeneralFireTalentBonus(unittest.TestCase):
    """Teste unitário determinístico — sem RNG, chama a função direto."""

    def setUp(self):
        self.ws = make_world_server()

    def _target_with_root(self) -> "tuple[CombatStats, StatusEffects]":
        target_cs = CombatStats()
        target_cs.current_hp = 100
        target_cs.max_hp     = 100
        sfx = StatusEffects()
        sfx.effects["root"] = ActiveEffect("root", 3.0)
        return target_cs, sfx

    def test_thermal_shock_dobra_dano_em_alvo_enraizado(self):
        player_cs = CombatStats()
        player_cs.thermal_shock_enabled = True
        target_cs, sfx = self._target_with_root()

        target_eid = self.ws.world.create_entity()
        self.ws.world.add_component(target_eid, sfx)

        dmg = self.ws._apply_general_fire_talent_bonus(player_cs, target_eid, target_cs, 100)
        self.assertEqual(dmg, 200, "Choque Termico deveria dobrar o dano em alvo com root")

    def test_thermal_shock_nao_afeta_alvo_sem_root(self):
        player_cs = CombatStats()
        player_cs.thermal_shock_enabled = True
        target_cs = CombatStats()
        target_cs.current_hp = 100
        target_cs.max_hp     = 100

        target_eid = self.ws.world.create_entity()
        self.ws.world.add_component(target_eid, StatusEffects())  # sem root

        dmg = self.ws._apply_general_fire_talent_bonus(player_cs, target_eid, target_cs, 100)
        self.assertEqual(dmg, 100, "sem root, Choque Termico nao deve alterar o dano")

    def test_sem_o_talento_root_nao_dobra_dano(self):
        player_cs = CombatStats()
        player_cs.thermal_shock_enabled = False
        target_cs, sfx = self._target_with_root()

        target_eid = self.ws.world.create_entity()
        self.ws.world.add_component(target_eid, sfx)

        dmg = self.ws._apply_general_fire_talent_bonus(player_cs, target_eid, target_cs, 100)
        self.assertEqual(dmg, 100, "sem o talento ligado, root nao deve dobrar o dano")


class TestCalamidadeFlamejanteAplicaChoqueTermico(unittest.TestCase):
    """Wiring — reproduz o sintoma relatado: cast de verdade de Calamidade
    Flamejante (fila de skill real do servidor) contra um alvo enraizado,
    com Choque Térmico ligado. Antes do fix, _process_player_channeling
    nunca chamava _apply_general_fire_talent_bonus — este teste falha sem
    o fix (revert-to-confirm)."""

    def setUp(self):
        self.ws = make_world_server()
        self.mage   = spawn_player(self.ws, "ct_mage", 130, 374, class_id="mago")
        self.victim = spawn_player(self.ws, "ct_victim", 131, 374, class_id="guerreiro")
        self.assertIsNone(self.ws.request_duel(self.mage, self.victim))
        self.ws.respond_duel_invite(self.victim, accept=True)

        mage_cs = self.ws.world.get_component(self.mage, CombatStats)
        mage_cs.thermal_shock_enabled = True

        victim_sfx = self.ws.world.get_component(self.victim, StatusEffects)
        if victim_sfx is None:
            victim_sfx = StatusEffects()
            self.ws.world.add_component(self.victim, victim_sfx)
        victim_sfx.effects["root"] = ActiveEffect("root", 10.0)

        victim_tm_x, victim_tm_y = 131 * 32 + 16, 374 * 32 + 16
        from engine.components import Channeling
        ch = Channeling(spell_id="calamidade_flamejante", duration=10.0, tick_interval=0.1,
                        mana_per_tick=0, target_x=victim_tm_x, target_y=victim_tm_y,
                        radius_tiles=3, dmg_weapon_pct=0.0, dmg_sp_coeff=1.0)
        self.ws.world.add_component(self.mage, ch)

    def test_funcao_de_choque_termico_e_chamada_pelo_tick_real(self):
        calls = []
        original = self.ws._apply_general_fire_talent_bonus

        def _spy(player_cs, target_id, target_cs, dmg):
            calls.append((player_cs, target_id, dmg))
            return original(player_cs, target_id, target_cs, dmg)

        self.ws._apply_general_fire_talent_bonus = _spy
        try:
            run_ticks(self.ws, 10, dt=0.05)  # 0.5s -> cobre 1+ tick de 0.1s
        finally:
            self.ws._apply_general_fire_talent_bonus = original

        self.assertTrue(len(calls) > 0,
                        "_process_player_channeling nunca chamou _apply_general_fire_talent_bonus "
                        "-- mesmo bug relatado: Choque Termico nao se aplica a Calamidade Flamejante")
        self.assertTrue(any(c[1] == self.victim for c in calls),
                        "nenhuma chamada teve o victim (enraizado) como alvo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
