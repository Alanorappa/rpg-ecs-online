"""
tests/test_flt_dedup.py — Fase C (23/07/2026): dano PvP duplicado no
floating text.

Causa raiz (não é específica de nenhuma skill): `_process_player_attacks`
(server/combat_processor.py) detecta dano de MOB→player por diferença de
HP (`mob_delta` — "qualquer perda de HP não explicada por DoT/PvP já
rastreado é um golpe de mob não visto"). Qualquer caminho que reduz o HP
de um player via `apply_damage_core` com um `killer_eid` PLAYER, sem se
registrar em `_pvp_damage_this_tick`, "vaza" pro mob_delta — que então
gera um SEGUNDO `combat_this_tick` fantasma pro MESMO golpe (1 real do
call site + 1 fantasma do mob_delta, atacante=-1 ou mob errado). HP só
sofre a subtração REAL uma vez (apply_damage_core não é chamado 2x) — só
o EVENTO de rede duplica, daí o "FLT duplicado, dano recebido não" que o
usuário relatou.

Bug real relatado pelo usuário em 2 skills sem nenhuma relação entre si:
- Escudo de Fogo (retaliation): `_on_retaliation` (server/world_server.py)
  nunca registrava em `_pvp_damage_this_tick`.
- Calamidade Flamejante (canalização): `_server_apply_magic_damage(...,
  report=True)` (server/spell_completion_processor.py), usado por
  `_process_player_channeling`, também nunca registrava.

Fix: centralizar o registro em `WorldServer._damage_tracker_composite`
(engine/core_systems.py::register_damage_tracker — o ÚNICO hook que roda
pra QUALQUER dano com killer_eid válido, já usado pra arena/CharStatsTracker
desde a Fase E) — dispara pra TODO dano PLAYER→PLAYER automaticamente,
sem precisar que cada call site novo lembre de rastrear à mão. Os 4 call
sites que faziam esse rastreio manualmente (combat_processor.py,
skill_processor.py, spell_completion_processor.py ×2) tiveram a linha
removida, pra não contar 2x.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import asyncio
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks
from engine.components import FireShieldEffect, CombatState, Channeling


def run_ticks_per_tick(ws, n: int, dt: float = 0.05) -> list:
    """Mesmo padrão de tests.helpers.run_ticks, mas preserva a fronteira
    de cada tick (uma lista de listas) em vez de mesclar tudo — precisa
    disso aqui pra detectar duplicata "no MESMO tick" sem confundir com
    2 procs legítimos e separados em ticks diferentes."""
    per_tick: list = []
    orig = ws._collect_deltas
    def patched():
        d = orig()
        per_tick.append(list(d.get("combat", [])))
        return d
    ws._collect_deltas = patched

    async def _run():
        for _ in range(n):
            ws._tick(dt)

    asyncio.run(_run())
    ws._collect_deltas = orig
    return per_tick


class TestEscudoDeFogoNaoDuplicaFLT(unittest.TestCase):
    """Retaliation do Escudo de Fogo (dano no ATACANTE) não deve gerar 2
    combat_this_tick pro mesmo golpe."""

    def setUp(self):
        self.ws = make_world_server()
        self.attacker = spawn_player(self.ws, "flt_atk", 130, 374, class_id="guerreiro")
        self.wearer   = spawn_player(self.ws, "flt_wearer", 131, 374, class_id="mago")
        self.assertIsNone(self.ws.request_duel(self.attacker, self.wearer))
        self.ws.respond_duel_invite(self.wearer, accept=True)
        self.ws.world.add_component(self.wearer, FireShieldEffect(duration=15.0))
        cst = self.ws.world.get_component(self.attacker, CombatState)
        cst.target_entity_id = self.wearer
        cst.is_pursuing = True
        sid = self.ws._player_eid_to_sid[self.attacker]
        self.ws._attack_timers[sid] = 0.0

    def test_retaliation_gera_so_1_evento_por_tick_nunca_2_iguais(self):
        """Verifica DENTRO de cada tick individual (não agregado) — 2
        procs legítimos de retaliation em ticks DIFERENTES têm o mesmo
        valor (retaliation é fixo, sem variância), então só checar "nunca
        se repete" no agregado daria falso positivo. A duplicata real
        (bug) sempre nasce no MESMO tick que o golpe original.

        Agrupa por ASSINATURA (target, damage, hp_after), não por
        "attacker" — a entrada fantasma do bug (mob_delta) tinha
        attacker=-1/mob errado, então filtrar pelo attacker real perderia
        justamente a entrada que prova o bug. (target, damage, hp_after)
        idênticos DUAS vezes no mesmo tick só acontece reportando o MESMO
        evento 2x — um golpe de mob de fundo coincidente (make_world_server
        tem mobs reais rodando) teria damage/hp_after DIFERENTES (dano
        somado é cumulativo), nunca uma cópia exata."""
        per_tick = run_ticks_per_tick(self.ws, 240, dt=0.05)  # 12s
        any_hit_on_wearer = False
        for tick_entries in per_tick:
            hits_on_wearer = [c for c in tick_entries
                             if c["damage"] > 0 and c["target"] == self.wearer]
            if hits_on_wearer:
                any_hit_on_wearer = True
            for target_eid in (self.attacker, self.wearer):
                sigs = [(c["target"], c["damage"], c["hp_after"]) for c in tick_entries
                        if c["damage"] > 0 and c["target"] == target_eid]
                dupes = [s for s in set(sigs) if sigs.count(s) > 1]
                self.assertEqual(dupes, [],
                                 f"assinatura duplicada no mesmo tick (eid {target_eid}): "
                                 f"{[c for c in tick_entries if (c['target'], c['damage'], c['hp_after']) in dupes]}")
        self.assertTrue(any_hit_on_wearer, "nenhum golpe conectou em 12s — teste inconclusivo")


class TestCalamidadeFlamejanteNaoDuplicaFLT(unittest.TestCase):
    """Tick de canalização de Calamidade Flamejante contra um player não
    deve gerar 2 combat_this_tick pro mesmo tick de dano."""

    def setUp(self):
        self.ws = make_world_server()
        self.mage   = spawn_player(self.ws, "flt_mage", 130, 374, class_id="mago")
        self.victim = spawn_player(self.ws, "flt_victim", 131, 374, class_id="guerreiro")
        self.assertIsNone(self.ws.request_duel(self.mage, self.victim))
        self.ws.respond_duel_invite(self.victim, accept=True)
        vtm_x, vtm_y = 131 * 32 + 16, 374 * 32 + 16
        ch = Channeling(spell_id="calamidade_flamejante", duration=10.0, tick_interval=0.5,
                        mana_per_tick=0, target_x=vtm_x, target_y=vtm_y, radius_tiles=3)
        self.ws.world.add_component(self.mage, ch)

    def test_cada_tick_de_dano_gera_so_1_evento(self):
        per_tick = run_ticks_per_tick(self.ws, 40, dt=0.05)  # 2s -> ao menos 1 tick de 0.5s
        any_hit = False
        for tick_entries in per_tick:
            hits = [c for c in tick_entries if c["damage"] > 0 and c["target"] == self.victim]
            if hits:
                any_hit = True
            # 1 tick de canalização só pode gerar 1 evento de dano — 2 no
            # MESMO tick do server é a assinatura exata do bug relatado.
            self.assertLessEqual(len(hits), 1,
                                 f"tick de canalização duplicado no mesmo tick: {hits}")
        self.assertTrue(any_hit, "nenhum tick de dano ocorreu em 2s — teste inconclusivo")


class TestPvpDamageTrackingNaoQuebraMobDelta(unittest.TestCase):
    """Regressão: dano de MOB de verdade contra um player continua sendo
    detectado via mob_delta normalmente — a exclusão da Fase C só se
    aplica quando o KILLER também é player (PvP de verdade).

    Chama `_process_player_attacks` diretamente com um `player_hp_snapshot`
    controlado (mesma assinatura usada pelo `_tick()` de verdade) em vez de
    depender da IA/RNG do mob de fato acertar um golpe dentro de uma janela
    de N ticks — determinístico, sem flakiness, e testa exatamente a
    lógica de diffing que a Fase C tocou."""

    def test_hp_perdido_sem_fonte_conhecida_ainda_vira_combat_this_tick(self):
        ws = make_world_server()
        p = spawn_player(ws, "mobdmg_p", 130, 374)
        from engine.components import CombatStats
        cs = ws.world.get_component(p, CombatStats)
        hp_before = cs.current_hp
        # Simula "EnemyAISystem já rodou e tirou 15 HP do player este tick"
        # — nenhuma fonte conhecida (não é DoT, não é PvP) registrou isso
        # em _sfx_damage_players/_pvp_damage_this_tick, exatamente o
        # cenário que o mob_delta existe pra cobrir.
        cs.current_hp -= 15
        ws._combat_this_tick.clear()

        ws._process_player_attacks(0.05, {p: hp_before})

        mob_hits_on_player = [c for c in ws._pending_mob_attacks
                              if c["target"] == p and c["damage"] == 15]
        self.assertEqual(len(mob_hits_on_player), 1,
                         f"mob_delta deveria reportar a perda de HP não rastreada exatamente "
                         f"1 vez: {ws._pending_mob_attacks}")


if __name__ == "__main__":
    unittest.main()
