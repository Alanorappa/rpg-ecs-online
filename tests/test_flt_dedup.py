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
from engine.components import FireShieldEffect, CombatState, Channeling, CombatStats
from engine.world_systems import CombatSystem


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


class TestBonecoReflectNaoDuplicaFLT(unittest.TestCase):
    """Reflexo do Boneco de Treino (`TrainingDummy.reflect_damage`, §246,
    07/10/2026) é a 3ª categoria desta mesma classe de bug — não é PvP
    (killer é o boneco, não um player), mas JÁ registra seu próprio
    combat_this_tick manualmente (`_on_dummy_reflect`/`emit_dummy_reflect`,
    mesma indireção do Escudo de Fogo acima). Causa raiz real (não o
    guard `is_server` que foi o 1º fix tentado, insuficiente por si só):
    `_damage_tracker_composite` (server/world_server.py) só excluía do
    `_pvp_damage_this_tick` quando o KILLER também é player — boneco não
    é player, então `mob_delta` (combat_processor.py) "descobria" a MESMA
    perda de HP de novo e gerava um 2º evento fantasma."""

    def setUp(self):
        # Boneco via FÁBRICA real (create_training_dummy), não o helper
        # minimalista `_make_dummy` de test_training_dummy_reflect.py --
        # aquele helper não anexa Enemy/Combatant, então o boneco nunca
        # entra em `WorldServer._mob_eids` e `_process_player_attacks`
        # (server/combat_processor.py:289) nunca dispara o auto-attack
        # contra ele (cai no branch "não é mob nem alvo PvP válido" e
        # zera o target) -- sem o auto-attack de verdade disparando, o
        # reflexo (que vive DENTRO de CombatSystem.deal_damage, só
        # chamado por _resolve_auto_attack) nunca roda, e o teste "passa"
        # por motivo errado (ou falha por coincidência de mob de fundo
        # acertar o player, nada a ver com o reflexo -- achado real
        # verificando este teste com revert-to-confirm).
        from engine.entity_factory import create_training_dummy
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "flt_dummy_p", 130, 374)
        cs = self.ws.world.get_component(self.player, CombatStats)
        cs.current_hp = cs.max_hp
        cs.dodge_rating = 0.0
        cs.block_rating = 0.0
        self.dummy = create_training_dummy(self.ws.world, 131, 374, reflect_damage=20)
        # Registro em _mob_eids é feito por um sweep periódico do próprio
        # _tick() (checa Combatant) -- força 1 tick "vazio" primeiro pra
        # garantir que o boneco já está registrado antes do loop de
        # medição abaixo (sem isso, só o 1º tick da medição arriscaria
        # perder o auto-attack por 1 tick de atraso).
        self.ws._tick(0.05)
        self.assertIn(self.dummy, self.ws._mob_eids,
                      "boneco precisa estar em _mob_eids pro auto-attack disparar")
        cst = self.ws.world.get_component(self.player, CombatState)
        cst.target_entity_id = self.dummy
        cst.is_pursuing = True
        sid = self.ws._player_eid_to_sid[self.player]
        self.ws._attack_timers[sid] = 0.0

    def test_reflexo_gera_so_1_evento_por_tick_nunca_2_iguais(self):
        from unittest.mock import patch
        # RNG travado em 0.99 -- mesmo racional de
        # tests/test_training_dummy_reflect.py: sem isso, miss/dodge/parry
        # ocasional (golpe original OU o reflexo recursivo) deixa o teste
        # flaky, sem relação com o bug sendo verificado aqui.
        with patch("random.random", return_value=0.99):
            per_tick = run_ticks_per_tick(self.ws, 240, dt=0.05)  # 12s
        any_hit_on_player = False
        for tick_entries in per_tick:
            hits_on_player = [c for c in tick_entries
                              if c["damage"] > 0 and c["target"] == self.player]
            if hits_on_player:
                any_hit_on_player = True
            sigs = [(c["target"], c["damage"], c["hp_after"]) for c in tick_entries
                    if c["damage"] > 0 and c["target"] == self.player]
            dupes = [s for s in set(sigs) if sigs.count(s) > 1]
            self.assertEqual(dupes, [],
                             f"assinatura duplicada no mesmo tick (reflexo do boneco): "
                             f"{[c for c in tick_entries if (c['target'], c['damage'], c['hp_after']) in dupes]}")
        self.assertTrue(any_hit_on_player, "nenhum reflexo conectou em 12s — teste inconclusivo")


class TestBonecoReflectMissNaoDuplicaFLT(unittest.TestCase):
    """Quando o reflexo do boneco ERRA (miss/dodge/parry), o "Errou" não
    deve duplicar na tela do player -- achado real relatado pelo usuário
    07/10/2026, depois do fix do dano (classe acima) já estar em produção.

    Causa raiz: diferente do dano (detectado via `mob_delta`, um diff de
    HP — não existe HP-diff numa esquiva, já que o HP não muda), miss/
    dodge/parry de MOB→player tem seu PRÓPRIO canal de fallback genérico:
    `CombatSystem.mob_avoidance_events` (engine/world_systems.py), drenado
    por `_process_player_attacks` (server/combat_processor.py) pra dentro
    de `_pending_mob_attacks`. O reflexo do boneco JÁ se auto-registra via
    `emit_dummy_reflect` pra QUALQUER outcome (hit OU miss) — mas o
    `mob_avoidance_events.append(...)` dentro do PRÓPRIO `deal_damage`
    (não sabia disso) alimentava o MESMO evento de novo, só que por outro
    canal. Mesma classe de bug do dano, 4ª ocorrência desta família na
    sessão (Escudo de Fogo, Calamidade Flamejante, reflexo/dano, agora
    reflexo/miss) — ver PROBLEMAS_ARQUITETURA.md §246."""

    def setUp(self):
        from tests.test_training_dummy_reflect import _make_dummy
        self.ws = make_world_server()
        self.player = spawn_player(self.ws, "flt_dummy_miss_p", 130, 374)
        self.player_cs = self.ws.world.get_component(self.player, CombatStats)
        self.player_cs.current_hp = self.player_cs.max_hp
        self.dummy = _make_dummy(self.ws, 131, 374, reflect_damage=20)
        self.cs_sys = CombatSystem(self.ws.world, is_server=True)

    def test_reflexo_errando_gera_so_1_evento_de_miss(self):
        # Golpe ORIGINAL (player->boneco) forçado via pre_outcome="hit"
        # (determinístico, sem depender de RNG) pra garantir que o
        # reflexo dispara (só dispara com final_damage>0). O reflexo
        # recursivo (boneco->player) NÃO aceita pre_outcome -- força-se
        # o outcome dele direto em _calculate_damage, interceptado só
        # quando o ATACANTE é o boneco (golpe original continua usando a
        # rolagem real, intocada).
        orig_calc = self.cs_sys._calculate_damage
        def _forced_miss_for_dummy(attacker_id, *args, **kwargs):
            if attacker_id == self.dummy:
                return 0, "miss"
            return orig_calc(attacker_id, *args, **kwargs)
        self.cs_sys._calculate_damage = _forced_miss_for_dummy

        before = len(self.ws._combat_this_tick)
        self.cs_sys.deal_damage(self.player, self.dummy, "physical",
                                base_ability_damage=9999, apply_armor_reduction=False,
                                pre_outcome="hit")

        new_entries = self.ws._combat_this_tick[before:]
        miss_entries = [e for e in new_entries if e["attacker"] == self.dummy
                        and e["target"] == self.player and e["outcome"] == "miss"]
        self.assertEqual(len(miss_entries), 1,
            f"reflexo errando deveria gerar exatamente 1 evento de miss: {new_entries}")
        self.assertEqual(self.cs_sys.mob_avoidance_events, [],
            "reflexo do boneco não deve alimentar mob_avoidance_events (duplicaria o 'Errou')")


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
