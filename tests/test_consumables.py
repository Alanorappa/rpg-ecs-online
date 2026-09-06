"""
tests/test_consumables.py — Fase 6 de bugfix de playtest (06/08/2026, ver
arquitetura/ARQUITETURA_ONLINE.md). Bug real relatado: player morto podia
usar poção (item consumido sem curar nada) e, se tinha um HoT de
consumível ativo no exato tick da morte, o "corpo" recuperava um pouco de
HP antes de `_handle_player_death` limpar o HoT — current_hp>0 fazia
qualquer mob aceitar o corpo como alvo válido de novo. `apply_damage_core`
já tem o choke-point "current_hp<=0 = morto" pro lado do DANO; não existia
o equivalente pro lado da CURA (`apply_consumable` + loop de
ActiveRegen/ActiveManaRegen).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import CombatStats, ActiveRegen, ActiveManaRegen, CharacterStats


class TestConsumableRejectedWhenDead(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.cs = self.ws.world.get_component(self.player_eid, CombatStats)
        self.cs.current_hp = 0  # "morto" pro invariante que apply_damage_core já usa

    def test_heal_instant_rejeitado_com_player_morto(self):
        self.ws.apply_consumable("p1", {"item_id": "pocao_vida", "heal_instant": 50})
        self.assertEqual(self.cs.current_hp, 0,
            "poção não deveria curar um player morto")
        rejected = [u for u in self.ws._pending_stats_updates
                   if u.get("consumable_rejected")]
        self.assertTrue(rejected, "servidor deveria ter respondido com rejeição (nunca silêncio)")
        self.assertEqual(rejected[-1]["reason"], "dead")

    def test_hot_nao_e_registrado_com_player_morto(self):
        self.ws.apply_consumable("p1", {
            "item_id": "pocao_regen",
            "hot": {"heal_per_tick": 10, "interval": 1.0, "ticks": 5},
        })
        self.assertIsNone(self.ws.world.get_component(self.player_eid, ActiveRegen),
            "HoT não deveria ser registrado num player morto")
        rejected = [u for u in self.ws._pending_stats_updates
                   if u.get("consumable_rejected")]
        self.assertTrue(rejected)
        self.assertEqual(rejected[-1]["reason"], "dead")

    def test_consumable_ok_nunca_e_enviado_pro_morto(self):
        # Contrato documentado em apply_consumable: cliente só descarta o
        # item local depois de "consumable_ok" — nunca pode receber os
        # dois (ok E rejected) pro mesmo uso.
        self.ws.apply_consumable("p1", {"item_id": "pocao_vida", "heal_instant": 50})
        ok = [u for u in self.ws._pending_stats_updates if u.get("consumable_ok")]
        self.assertFalse(ok, "não deveria confirmar uso de item num player morto")


class TestActiveRegenPausesOnDeath(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.cs = self.ws.world.get_component(self.player_eid, CombatStats)

    def _add_hp_hot(self, heal_per_tick=10, interval=1.0, ticks=5):
        regen = ActiveRegen(heal_per_tick=heal_per_tick, interval=interval, ticks_total=ticks)
        regen.tick_timer = 0.0  # dispara no primeiro tick processado
        self.ws.world.add_component(self.player_eid, regen)
        return regen

    def test_hot_nao_cura_player_que_morreu_no_mesmo_tick(self):
        self._add_hp_hot()
        self.cs.current_hp = 0  # morreu ANTES do loop de ActiveRegen rodar neste tick
        self.ws._tick(0.05)
        self.assertEqual(self.cs.current_hp, 0,
            "ActiveRegen não deveria curar um player já com current_hp<=0 — "
            "isso é exatamente o bug real: corpo recuperava HP e reagroava mob")

    def test_hot_pausa_em_vez_de_consumir_tick_quando_morto(self):
        regen = self._add_hp_hot(ticks=5)
        self.cs.current_hp = 0
        self.ws._tick(0.05)
        self.assertEqual(regen.ticks_remaining, 5,
            "tick não deveria ser consumido enquanto o player está morto — "
            "se ele reviver, os ticks restantes do HoT continuam válidos")

    def test_hot_continua_curando_normalmente_quando_vivo(self):
        self._add_hp_hot(heal_per_tick=10)
        self.cs.current_hp = 50
        self.ws._tick(0.05)
        self.assertEqual(self.cs.current_hp, 60,
            "regressão: HoT precisa continuar curando normalmente pra player vivo")


class TestActiveManaRegenPausesOnDeath(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="mago")
        self.cs = self.ws.world.get_component(self.player_eid, CombatStats)
        self.char = self.ws.world.get_component(self.player_eid, CharacterStats)

    def test_mana_hot_nao_regenera_player_morto(self):
        mregen = ActiveManaRegen(mana_per_tick=10, interval=1.0, ticks_total=5)
        mregen.tick_timer = 0.0
        self.ws.world.add_component(self.player_eid, mregen)
        self.char.mana = 0
        self.cs.current_hp = 0
        self.ws._tick(0.05)
        self.assertEqual(self.char.mana, 0,
            "mana regen não deveria aplicar num player morto")


class TestActiveRegenClearedOnDeath(unittest.TestCase):
    """_handle_player_death já removia ActiveRegen — Fase 6 espelha isso
    pra ActiveManaRegen (assimetria pequena, mesma limpeza)."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="mago")

    def test_active_mana_regen_e_removido_na_morte(self):
        mregen = ActiveManaRegen(mana_per_tick=10, interval=1.0, ticks_total=5)
        self.ws.world.add_component(self.player_eid, mregen)
        cs = self.ws.world.get_component(self.player_eid, CombatStats)
        cs.current_hp = 0
        self.ws._handle_player_death(self.player_eid)
        self.assertIsNone(self.ws.world.get_component(self.player_eid, ActiveManaRegen))


class TestActiveRegenPlayerCheckIsComponentBased(unittest.TestCase):
    """05/09/2026 (ver PROBLEMAS_ARQUITETURA.md §113) — `eid not in
    self._player_eids.values()` era O(n) por checagem (`dict.values()`
    não é indexado) e piorava sozinho com mais jogadores online.
    Trocado por `world.get_component(eid, PlayerControlled) is None` —
    O(1), mesmo padrão já usado em `index_build`. Este teste prova que a
    troca não mudou QUEM é elegível: uma entidade com `ActiveRegen` mas
    SEM `PlayerControlled` (cenário hipotético — hoje só player ganha
    esse componente, mas o gate certo é por COMPONENTE, não por
    pertencer ao dict de sessões) continua sendo ignorada."""

    def setUp(self):
        self.ws = make_world_server()
        self.player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")

    def test_entidade_sem_playercontrolled_nunca_regenera_mesmo_com_activeregen(self):
        from engine.entity_factory import create_combat_npc
        npc = create_combat_npc(self.ws.world, 12, 10, faction="bandidos")
        npc_cs = self.ws.world.get_component(npc, CombatStats)
        npc_cs.current_hp = npc_cs.max_hp - 50
        regen = ActiveRegen(heal_per_tick=999, interval=1.0, ticks_total=5)
        regen.tick_timer = 0.0
        self.ws.world.add_component(npc, regen)

        self.ws._tick(0.05)

        self.assertEqual(npc_cs.current_hp, npc_cs.max_hp - 50,
                         "entidade sem PlayerControlled nao deveria ser regenerada "
                         "pelo loop de ActiveRegen, mesmo tendo o componente")

    def test_player_de_verdade_continua_regenerando_normalmente(self):
        """Regressão simples: a troca de O(n) pra O(1) não pode ter
        quebrado o caso comum (player real com HoT ativo)."""
        cs = self.ws.world.get_component(self.player_eid, CombatStats)
        cs.current_hp = 50
        regen = ActiveRegen(heal_per_tick=10, interval=1.0, ticks_total=5)
        regen.tick_timer = 0.0
        self.ws.world.add_component(self.player_eid, regen)

        self.ws._tick(0.05)

        self.assertEqual(cs.current_hp, 60)


class TestPostTickBookkeepingNeverMisclassifiesPlayerAsMob(unittest.TestCase):
    """05/09/2026 (ver PROBLEMAS_ARQUITETURA.md §113) — o mesmo trecho
    de `post_tick_bookkeeping` que detecta "combatente novo" (criado
    pelo SpawnZoneSystem) usava a mesma checagem O(n) por
    `_player_eids.values()`. Corrigido pra checagem por componente —
    este teste prova que um player de verdade nunca é registrado em
    `_mob_eids` por essa varredura, nem no primeiro tick logo após
    spawnar."""

    def setUp(self):
        self.ws = make_world_server()

    def test_player_recem_spawnado_nunca_vira_mob_no_primeiro_tick(self):
        player_eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.ws._tick(0.05)
        self.assertNotIn(player_eid, self.ws._mob_eids,
                         "player nunca deveria ser registrado como mob por "
                         "post_tick_bookkeeping, nem logo apos spawnar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
