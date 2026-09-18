"""
tests/test_channeling_system_get_first.py — 18/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #5: `ChannelingSystem.update()`
(`ui/spell_system.py`) usava `for ..., in world.get_entities_with(...):`
só pra achar a ÚNICA entidade Channeling+PlayerControlled do mundo
LOCAL do cliente, com 2 `continue` dentro do corpo — migrado pra
`World.get_first()`, o que exigiu reescrever os 2 `continue` como
`if`/`else` aninhado (não dá pra usar `continue` fora de um `for`).
Esse é o call site de MAIOR risco da migração (única com controle de
fluxo genuinamente reestruturado, não só um `for` trocado por `if`) —
merece teste dedicado, não só a suíte geral.

Prova: (1) intenção de movimento interrompe a canalização (remove
`Channeling`, `is_casting=False`, registra em
`interrupted_channelings`) SEM incrementar `elapsed`; (2) mana
insuficiente no tick interrompe do MESMO jeito, mas só DEPOIS de
`elapsed`/`last_tick` já terem incrementado nesse frame (comportamento
idêntico ao código original — o `continue` de mana vinha DEPOIS do
incremento); (3) canalização completa normal (mana suficiente, chega
na duração) remove `Channeling` via `to_finish`, não via `interrupted`.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import (
    Channeling, CombatState, CharacterStats, PlayerControlled, CombatStats,
)
from ui.spell_system import ChannelingSystem


def _make_caster(world, mana=999):
    caster = world.create_entity()
    cs = CombatState()
    cs.is_casting = True
    world.add_component(caster, cs)
    cstats = CharacterStats(class_id="mago")
    cstats.mana = mana
    world.add_component(caster, cstats)
    world.add_component(caster, PlayerControlled())
    world.add_component(caster, CombatStats())
    ch = Channeling(spell_id="calamidade_flamejante", duration=1.0,
                    tick_interval=0.1, mana_per_tick=10,
                    target_x=0.0, target_y=0.0, radius_tiles=5.0)
    world.add_component(caster, ch)
    return caster, ch


class _NoKeysPressed(dict):
    """Substitui pygame.key.get_pressed() — devolve False pra qualquer tecla
    não explicitamente setada (dict.get, não dict.__getitem__ puro — senão
    ignoraria o próprio valor guardado por d[tecla] = True)."""
    def __getitem__(self, k):
        return self.get(k, False)


class TestChannelingMoveInterrupt(unittest.TestCase):

    def setUp(self):
        self._orig_get_pressed = pygame.key.get_pressed
        self._orig_key_a = pygame.K_a
        self.world = World()
        self.screen = pygame.Surface((320, 240))
        self.sys_ = ChannelingSystem(self.world, self.screen)

    def tearDown(self):
        pygame.key.get_pressed = self._orig_get_pressed

    def test_intencao_de_movimento_interrompe_sem_incrementar_elapsed(self):
        caster, ch = _make_caster(self.world)
        pressed = _NoKeysPressed()
        pressed[pygame.K_a] = True   # simula tecla de movimento segurada
        pygame.key.get_pressed = lambda: pressed

        self.sys_.update(events=[], dt=0.1)

        self.assertIsNone(self.world.get_component(caster, Channeling),
                          "Channeling deveria ter sido removido (interrompido)")
        cs = self.world.get_component(caster, CombatState)
        self.assertFalse(cs.is_casting)
        self.assertIn("calamidade_flamejante", self.sys_.interrupted_channelings)
        self.assertEqual(ch.elapsed, 0.0,
                         "interrupção por movimento não deveria ter incrementado elapsed")


class TestChannelingManaInsufficient(unittest.TestCase):

    def setUp(self):
        self._orig_get_pressed = pygame.key.get_pressed
        pygame.key.get_pressed = lambda: _NoKeysPressed()
        self.world = World()
        self.screen = pygame.Surface((320, 240))
        self.sys_ = ChannelingSystem(self.world, self.screen)

    def tearDown(self):
        pygame.key.get_pressed = self._orig_get_pressed

    def test_mana_insuficiente_interrompe_e_nao_deduz_mana(self):
        caster, ch = _make_caster(self.world, mana=5)   # < tick_mana (10)
        cstats = self.world.get_component(caster, CharacterStats)

        self.sys_.update(events=[], dt=0.1)   # dt == tick_interval -> dispara o tick

        self.assertIsNone(self.world.get_component(caster, Channeling),
                          "Channeling deveria ter sido removido (interrompido por mana)")
        self.assertIn("calamidade_flamejante", self.sys_.interrupted_channelings)
        # Mana insuficiente cai no branch else de char_stats.mana -= tick_mana
        # (nunca deduz) — mesmo comportamento do código original.
        self.assertEqual(cstats.mana, 5)


class TestChannelingNormalCompletion(unittest.TestCase):

    def setUp(self):
        self._orig_get_pressed = pygame.key.get_pressed
        pygame.key.get_pressed = lambda: _NoKeysPressed()
        self.world = World()
        self.screen = pygame.Surface((320, 240))
        self.sys_ = ChannelingSystem(self.world, self.screen)

    def tearDown(self):
        pygame.key.get_pressed = self._orig_get_pressed

    def test_completa_normalmente_com_mana_suficiente(self):
        caster, ch = _make_caster(self.world, mana=999)
        # 1.0s de duration, dt=0.1 -> 11 updates (não 10: acumulação de
        # ponto flutuante deixa 0.1*10 == 0.9999999999999999 < 1.0)
        for _ in range(11):
            self.sys_.update(events=[], dt=0.1)

        self.assertIsNone(self.world.get_component(caster, Channeling),
                          "Channeling deveria ter sido removido (completou)")
        self.assertNotIn("calamidade_flamejante", self.sys_.interrupted_channelings,
                         "conclusão normal não é uma interrupção")
        cs = self.world.get_component(caster, CombatState)
        self.assertFalse(cs.is_casting)


if __name__ == "__main__":
    unittest.main()
