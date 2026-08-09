"""
tests/test_calamidade_channel_config.py — Fase 3 (piloto, 07/08/2026,
ver PROBLEMAS_ARQUITETURA.md §13): `engine/core_systems.py::
build_channeling_from_skill` vira o dono único da tradução
SKILL_CATALOG["calamidade_flamejante"]["params"] → componente `Channeling`
vivo — antes disso, servidor (`ui/skill_handlers.py`) e cliente
(`ui/spell_system.py::AoeTargetingSystem`) construíam `Channeling(...)`
cada um com os mesmos 6 números duplicados como literais Python.

Cobre 2 coisas:
  - O `Channeling` real gerado por um CAST_SKILL de verdade (fluxo
    completo via `WorldServer._process_skill_requests`) reflete o que
    está no catálogo, não um literal hardcoded — prova diferencial
    (muda o catálogo, confirma que o componente muda junto).
  - `ChannelingSystem` (cliente) não aplica mais dano local nenhum — o
    laço `Position/Enemy/CombatStats` → `_apply_magic_damage` que
    existia antes desta fase nunca executava de verdade online (mob
    online não tem `Enemy`+`CombatStats` local, só `RemoteEntityMeta`/
    `RemoteControlled`) e este branch não tem mais modo offline
    (`game.py::_connect_online()` é incondicional) — era código morto,
    removido. Este teste prova que o dano realmente não acontece mais
    mesmo no cenário sintético onde a query ANTES teria casado (entidade
    local real com Enemy+CombatStats, fora do fluxo online normal).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, authorize_skill, run_ticks
from content.skill_config import SKILL_CATALOG
from engine.components import Channeling, CharacterStats


class TestChannelingConstruidoDoCatalogo(unittest.TestCase):
    """CAST_SKILL de verdade (fila de skill do servidor, mesmo entrypoint
    que o cliente usa) gera um `Channeling` com os valores do catálogo,
    não literais hardcoded copiados em 2 lugares."""

    def setUp(self):
        self.ws = make_world_server()
        self.mage = spawn_player(self.ws, "chan_mage", 130, 374, class_id="mago")
        authorize_skill(self.ws, self.mage, "calamidade_flamejante")
        char = self.ws.world.get_component(self.mage, CharacterStats)
        char.mana = 999  # garante que o custo de mana nunca bloqueia o teste

    def _cast_and_get_channeling(self, aoe_x: float, aoe_y: float) -> "Channeling | None":
        sid = self.ws._player_eid_to_sid[self.mage]
        self.ws.queue_skill(sid, "calamidade_flamejante", -1, aoe_x, aoe_y, 0)
        run_ticks(self.ws, 1)
        return self.ws.world.get_component(self.mage, Channeling)

    def test_valores_batem_com_params_do_catalogo(self):
        params = SKILL_CATALOG["calamidade_flamejante"]["params"]
        ch = self._cast_and_get_channeling(4000.0, 5000.0)
        self.assertIsNotNone(ch, "CAST_SKILL não gerou Channeling — setup do teste está errado")
        self.assertEqual(ch.tick_interval,  params["tick_interval"])
        self.assertEqual(ch.mana_per_tick,  params["mana_per_tick"])
        self.assertEqual(ch.radius_tiles,   params["radius_tiles"])
        self.assertEqual(ch.slow_pct,       params["slow_pct"])
        self.assertEqual(ch.dmg_weapon_pct, params["dmg_weapon_pct"])
        self.assertEqual(ch.dmg_sp_coeff,   params["dmg_sp_coeff"])
        self.assertEqual(ch.duration, SKILL_CATALOG["calamidade_flamejante"]["channel_duration"])
        self.assertEqual(ch.target_x, 4000.0)
        self.assertEqual(ch.target_y, 5000.0)

    def test_prova_diferencial_muda_catalogo_muda_componente(self):
        """Se o componente ainda viesse de um literal hardcoded (bug que
        esta fase corrigiu), mudar o catálogo não teria efeito nenhum —
        prova de que a fonte é mesmo o catálogo, não um número copiado."""
        original = SKILL_CATALOG["calamidade_flamejante"]["params"]["radius_tiles"]
        SKILL_CATALOG["calamidade_flamejante"]["params"]["radius_tiles"] = 7.5
        try:
            ch = self._cast_and_get_channeling(4000.0, 5000.0)
            self.assertEqual(ch.radius_tiles, 7.5)
        finally:
            SKILL_CATALOG["calamidade_flamejante"]["params"]["radius_tiles"] = original


class TestChannelingSystemClienteNaoAplicaDanoLocal(unittest.TestCase):
    """`ChannelingSystem` (ui/spell_system.py, cliente) não deve conter
    nenhum caminho que aplique dano real — isso é responsabilidade
    exclusiva do servidor. Prova sintética: monta uma entidade local com
    Enemy+CombatStats (a query que o código morto usava) dentro do raio
    do Channeling e roda vários ticks — HP não pode mudar."""

    def test_alvo_local_enemy_combatstats_nao_perde_hp(self):
        import pygame as _pg
        from engine.world import World
        from ui.spell_system import ChannelingSystem
        from engine.components import (
            Position, Enemy, CombatStats, CombatState, PlayerControlled, TileMovement,
        )

        world = World()
        screen = _pg.Surface((100, 100))
        sysobj = ChannelingSystem(world, screen)

        caster = world.create_entity()
        world.add_component(caster, CombatState())
        world.add_component(caster, CharacterStats(class_id="mago"))
        world.add_component(caster, PlayerControlled())
        world.add_component(caster, TileMovement())
        ch = Channeling(spell_id="calamidade_flamejante", duration=10.0,
                        tick_interval=0.1, mana_per_tick=0,
                        target_x=100.0, target_y=100.0, radius_tiles=5.0)
        world.add_component(caster, ch)

        target = world.create_entity()
        world.add_component(target, Position(x=100.0, y=100.0))
        world.add_component(target, Enemy())
        tcs = CombatStats()
        tcs.current_hp = 100
        tcs.max_hp     = 100
        world.add_component(target, tcs)

        for _ in range(20):  # 2s a dt=0.1 -> várias janelas de tick_interval
            sysobj.update(events=[], dt=0.1)

        self.assertEqual(tcs.current_hp, 100,
                         "ChannelingSystem aplicou dano local — código morto voltou")


if __name__ == "__main__":
    unittest.main(verbosity=2)
