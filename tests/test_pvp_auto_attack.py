"""
tests/test_pvp_auto_attack.py — Unificação de auto-attack PvE/PvP
(16/08/2026, ver server/combat_processor.py::_resolve_auto_attack e
arquitetura/PROBLEMAS_ARQUITETURA.md).

Bug real relatado pelo usuário e confirmado empiricamente (2 contas,
duelo real): arqueiro com ARCO equipado mas SEM aljava ainda atirava
flecha contra outro JOGADOR — sem munição, sem checagem de linha de
visão, com a fórmula de dano genérica em vez da fórmula própria de
ataque à distância. Causa raiz: `_process_pvp_attack` (alvo=player) era
uma implementação PARALELA e mais pobre que `_process_player_attacks`
(alvo=mob) — cada checagem nova (LOS, munição, fórmula de arco) só
tinha sido adicionada no lado PvE, nunca replicada. Corrigido unificando
os 2 em `_resolve_auto_attack`, chamado pelos 2 braços.

Estes testes reproduzem o cenário EXATO que o usuário testou manualmente
antes de reportar, chamando `_process_pvp_attack` direto (mesma função
que o servidor real chama quando 2 players se atacam)."""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, engage_target
from engine.components import (CombatState, CombatStats, CharacterStats, Equipment,
                               Item, PlayerSkills, Tilemap, Faction)
from engine.tileset import WALL_TILE, FLOOR_TILE

# 2 players sem Faction são "amigáveis" por padrão (engine/faction_system.py
# ::can_engage) — dano PvP exige uma relação hostil de verdade, mesmo
# padrão que testes de arena/BG já usam (times registrados em
# content/faction_data.py, nunca facção inventada).
_FAC_A = "arena_time_a"
_FAC_B = "arena_time_b"

# Corredor com LOS livre no map_1 real (mesmo par de coordenadas já usado
# em tests/test_towers.py — a área padrão (10,10) tem obstáculo que
# bloqueia LOS a distância >1, só importa pra ranged).
_A = (50, 50)
_B = (53, 50)


def _get_tilemap(ws):
    bundle = ws._map_bundles[ws._map_file]
    return ws.world.get_component(bundle.tilemap_entity, Tilemap)


def _equip_bow(ws, eid, quiver: bool, arrow_count: int = 50, cast_range: int = 12):
    equip = ws.world.get_component(eid, Equipment)
    equip.slots["mainhand"] = Item("Arco Teste", "weapon", "mainhand",
                                   subtype="Bow", cast_range=cast_range)
    if quiver:
        equip.slots["offhand"] = Item("Aljava Teste", "quiver", "offhand",
                                      arrow_count=arrow_count, max_arrows=50)


class TestPvpRangedAmmoAndLos(unittest.TestCase):
    """Reproduz o cenário exato reportado pelo usuário."""

    def setUp(self):
        self.ws = make_world_server()
        self.attacker = spawn_player(self.ws, "s_atk", *_A, class_id="arqueiro")
        self.victim   = spawn_player(self.ws, "s_vic", *_B, class_id="guerreiro")
        self.ws.world.add_component(self.attacker, Faction(faction_id=_FAC_A))
        self.ws.world.add_component(self.victim,   Faction(faction_id=_FAC_B))
        engage_target(self.ws, "s_atk", self.victim)
        self.ws._attack_timers["s_atk"] = 0.0
        # Garante hit (sem miss/dodge/parry) — os testes que esperam dano
        # de verdade não podem depender de sorte de rolagem.
        self.ws.world.get_component(self.attacker, CombatStats).acerto = 100.0

    def test_arqueiro_sem_aljava_nao_ataca_player_em_pvp(self):
        """O bug relatado: arco SEM aljava ainda atirava em PvP."""
        _equip_bow(self.ws, self.attacker, quiver=False)
        victim_cs = self.ws.world.get_component(self.victim, CombatStats)
        hp_before = victim_cs.current_hp

        self.ws._process_pvp_attack(self.attacker, self.victim, "s_atk", 0.05)

        self.assertEqual(victim_cs.current_hp, hp_before,
            "arqueiro sem aljava não deveria conseguir atacar em PvP")

    def test_arqueiro_com_aljava_ataca_e_consome_flecha_em_pvp(self):
        _equip_bow(self.ws, self.attacker, quiver=True, arrow_count=10)
        victim_cs = self.ws.world.get_component(self.victim, CombatStats)
        hp_before = victim_cs.current_hp
        quiver = self.ws.world.get_component(self.attacker, Equipment).slots["offhand"]
        arrows_before = quiver.arrow_count

        self.ws._process_pvp_attack(self.attacker, self.victim, "s_atk", 0.05)

        self.assertLess(victim_cs.current_hp, hp_before,
            "arqueiro com aljava deveria conseguir atacar em PvP")
        self.assertEqual(quiver.arrow_count, arrows_before - 1,
            "flecha deveria ser consumida do carcás em PvP, igual já acontece em PvE")

    def test_parede_bloqueia_ataque_ranged_em_pvp(self):
        """LOS nunca era checada em PvP — parede de verdade no meio.

        `_server_apply_ranged_physical` (chamado só se a LOS "passar"
        aqui) TEM seu próprio guard de LOS interno, de propósito só como
        rede de segurança pras SKILLS que reusam essa função (ver
        docstring de _resolve_auto_attack) — então HP/flecha inalterados
        sozinhos NÃO provam que o check deste ponto é o que bloqueou (o
        guard interno também bloquearia dano/flecha, mascarando um
        revert daqui). O sinal que só ESTE check produz: retorno
        ANTES de consumir o cooldown — o guard interno só roda DEPOIS
        do cooldown já ter sido gasto neste arquivo."""
        _equip_bow(self.ws, self.attacker, quiver=True, arrow_count=10)
        tm = _get_tilemap(self.ws)
        wall_x = (_A[0] + _B[0]) // 2
        tm.tile_matrix[_A[1]][wall_x] = WALL_TILE

        victim_cs = self.ws.world.get_component(self.victim, CombatStats)
        hp_before = victim_cs.current_hp
        quiver = self.ws.world.get_component(self.attacker, Equipment).slots["offhand"]
        arrows_before = quiver.arrow_count

        self.ws._process_pvp_attack(self.attacker, self.victim, "s_atk", 0.05)

        self.assertEqual(victim_cs.current_hp, hp_before,
            "parede real deveria bloquear o ataque ranged em PvP")
        self.assertEqual(quiver.arrow_count, arrows_before,
            "ataque bloqueado por LOS não deveria consumir flecha")
        self.assertEqual(self.ws._attack_timers.get("s_atk", 0.0), 0.0,
            "bloqueio de LOS é ANTES do cooldown — não deveria gastar o timer de ataque")

    def test_sem_arco_cai_pra_melee_corretamente(self):
        """Sem arco nenhum — já funcionava certo antes, confirma que
        continua funcionando após a unificação."""
        victim_cs = self.ws.world.get_component(self.victim, CombatStats)
        hp_before = victim_cs.current_hp
        # Sem arco, alcance melee=1 — aproxima o alvo pra dentro do alcance.
        from engine.components import TileMovement
        vtm = self.ws.world.get_component(self.victim, TileMovement)
        vtm.current_tile_x, vtm.current_tile_y = _A[0] + 1, _A[1]

        self.ws._process_pvp_attack(self.attacker, self.victim, "s_atk", 0.05)

        self.assertLess(victim_cs.current_hp, hp_before,
            "sem arco, arqueiro deveria brigar em melee normalmente")


class TestPvpAttackerResources(unittest.TestCase):
    """Raiva do Guerreiro e combo de Punho no Queixo do Cavaleiro — antes
    só disparavam contra MOB, nunca em PvP (mesma causa raiz: lógica
    presa dentro do braço PvE)."""

    def setUp(self):
        self.ws = make_world_server()

    def test_guerreiro_ganha_raiva_atacando_player_em_pvp(self):
        atk = spawn_player(self.ws, "s_g1", *_A, class_id="guerreiro")
        vic = spawn_player(self.ws, "s_g2", _A[0] + 1, _A[1], class_id="guerreiro")
        self.ws.world.add_component(atk, Faction(faction_id=_FAC_A))
        self.ws.world.add_component(vic, Faction(faction_id=_FAC_B))
        engage_target(self.ws, "s_g1", vic)
        self.ws._attack_timers["s_g1"] = 0.0
        self.ws.world.get_component(atk, CombatStats).acerto = 100.0

        char = self.ws.world.get_component(atk, CharacterStats)
        rage_before = char.rage

        self.ws._process_pvp_attack(atk, vic, "s_g1", 0.05)

        self.assertGreater(char.rage, rage_before,
            "Raiva do Guerreiro deveria subir batendo em outro player, igual já acontece contra mob")

    def test_cavaleiro_ganha_carga_pnq_atacando_player_3x_em_pvp(self):
        from content.skill_config import SKILL_CATALOG
        from tests.helpers import authorize_skill

        atk = spawn_player(self.ws, "s_c1", *_A, class_id="guerreiro")
        vic = spawn_player(self.ws, "s_c2", _A[0] + 1, _A[1], class_id="guerreiro")
        self.ws.world.add_component(atk, Faction(faction_id=_FAC_A))
        self.ws.world.add_component(vic, Faction(faction_id=_FAC_B))
        authorize_skill(self.ws, atk, "punho_no_queixo")
        cs = self.ws.world.get_component(atk, CombatStats)
        cs.pnq_enabled = True
        cs.acerto      = 100.0  # garante hit, sem miss/dodge/parry

        ps = self.ws.world.get_component(atk, PlayerSkills)
        pnq_sk = next((sk for sk in ps.skills if sk and sk.skill_id == "punho_no_queixo"), None)
        if pnq_sk is None:
            pnq_sk = PlayerSkills._make_skill("punho_no_queixo", SKILL_CATALOG)
            idx = ps.skills.index(None)
            ps.skills[idx] = pnq_sk

        vic_cs = self.ws.world.get_component(vic, CombatStats)
        engage_target(self.ws, "s_c1", vic)

        self.assertEqual(pnq_sk.charges, 0)
        for _ in range(3):
            vic_cs.current_hp = vic_cs.max_hp  # mantém vivo entre os 3 golpes
            self.ws._attack_timers["s_c1"] = 0.0
            self.ws._process_pvp_attack(atk, vic, "s_c1", 0.05)

        self.assertEqual(pnq_sk.charges, 1,
            "3 acertos em PvP deveriam gerar 1 carga de Punho no Queixo, igual já acontece contra mob")


if __name__ == "__main__":
    unittest.main()
