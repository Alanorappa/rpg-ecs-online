"""
tests/test_bg_kill_credit.py — Gold/XP de minion + kill credit/assist de
player, estilo LoL (16/08/2026, ver arquitetura/PROBLEMAS_ARQUITETURA.md).

3 problemas reais relatados pelo usuário (jogou uma partida de BG):
1. Gold de minion dado a quem não deu o golpe final.
2. Minion do PRÓPRIO time do player dando XP pra ele.
3. Kill credit de player nunca funcionava de verdade em partida real
   (achado investigando — não era só "não confio", era código morto:
   os 2 caminhos de morte por combate sempre apagavam o PendingDeath
   ANTES do único código que credita kill rodar).

Estes testes usam o caminho de combate REAL (`_process_pvp_attack`,
`_process_player_attacks`), não atalhos diretos em `apply_damage_core`
— é exatamente isso que fazia o teste antigo de `players_killed` dar
falso positivo (nunca exercitava a corrida de verdade)."""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, engage_target
from engine.components import (CombatStats, CharacterStats, CharStatsTracker, Faction,
                               PendingDeath, MapLocation, TileMovement)
from engine.entity_factory import create_minion, create_tower
from shared.constants import PLAYER_KILL_ASSIST_WINDOW_S

_FAC_A = "arena_time_a"
_FAC_B = "arena_time_b"


class TestMinionGoldStrictLastHit(unittest.TestCase):
    """Problema #1: gold de minion não é last-hit de verdade."""

    def setUp(self):
        self.ws = make_world_server()

    def test_minion_morto_por_outro_minion_nao_da_gold_a_player_que_bateu_antes(self):
        from server.instance_progression import enter_normalized_progression
        player = spawn_player(self.ws, "s1", 10, 10, class_id="guerreiro")
        enter_normalized_progression(self.ws, player)
        from engine.components import Wallet
        wallet = self.ws.world.get_component(player, Wallet)
        gold_antes = wallet.gold

        dying_minion = create_minion(self.ws.world, 11, 10, "minion_melee",
                                     faction_id=_FAC_B, route=[(11, 10)])
        self.ws.world.add_component(dying_minion, MapLocation(self.ws._map_file))
        ally_minion = create_minion(self.ws.world, 12, 10, "minion_melee",
                                    faction_id=_FAC_A, route=[(12, 10)])
        self.ws.world.add_component(ally_minion, MapLocation(self.ws._map_file))

        # Player bateu antes (registra no damage_log), mas quem dá o
        # golpe FINAL é o minion aliado, não o player.
        self.ws._mob_damage_log[dying_minion] = {player: 5}
        self.ws.world.add_component(dying_minion, PendingDeath(killer_entity_id=ally_minion))
        self.ws._mob_eids.add(dying_minion)

        self.ws._death_handler.update()

        wallet_depois = self.ws.world.get_component(player, Wallet)
        self.assertEqual(wallet_depois.gold, gold_antes,
            "player que só bateu (sem dar o golpe final) não deveria ganhar gold de minion")

    def test_minion_morto_pelo_proprio_player_da_gold_normalmente(self):
        """Regressão — last-hit de verdade continua funcionando."""
        from server.instance_progression import enter_normalized_progression
        player = spawn_player(self.ws, "s1", 10, 10, class_id="guerreiro")
        enter_normalized_progression(self.ws, player)
        from engine.components import Wallet
        wallet = self.ws.world.get_component(player, Wallet)
        gold_antes = wallet.gold

        dying_minion = create_minion(self.ws.world, 11, 10, "minion_melee",
                                     faction_id=_FAC_B, route=[(11, 10)])
        self.ws.world.add_component(dying_minion, MapLocation(self.ws._map_file))
        self.ws._mob_damage_log[dying_minion] = {player: 175}
        self.ws.world.add_component(dying_minion, PendingDeath(killer_entity_id=player))
        self.ws._mob_eids.add(dying_minion)

        self.ws._death_handler.update()

        wallet_depois = self.ws.world.get_component(player, Wallet)
        self.assertGreater(wallet_depois.gold, gold_antes,
            "player que deu o golpe final deveria ganhar o gold do minion")


class TestMinionXpFactionFilter(unittest.TestCase):
    """Problema #2: minion do próprio time dá XP pro player."""

    def setUp(self):
        self.ws = make_world_server()

    def _make_nearby_player(self, faction_id: str):
        from server.instance_progression import enter_normalized_progression
        p = spawn_player(self.ws, f"s_{faction_id}", 10, 10, class_id="guerreiro")
        self.ws.world.add_component(p, Faction(faction_id=faction_id))
        enter_normalized_progression(self.ws, p)
        return p

    def test_minion_do_proprio_time_nao_da_xp(self):
        ally_player = self._make_nearby_player(_FAC_A)
        dying_minion = create_minion(self.ws.world, 10, 11, "minion_melee",
                                     faction_id=_FAC_A, route=[(10, 11)])
        self.ws.world.add_component(dying_minion, MapLocation(self.ws._map_file))
        self.ws.world.add_component(dying_minion, PendingDeath(killer_entity_id=-1))
        self.ws._mob_eids.add(dying_minion)

        self.ws._death_handler.update()

        xp_entries = [e for e in self.ws._death_handler.pending_xp if e["player_eid"] == ally_player]
        self.assertEqual(xp_entries, [],
            "minion do PRÓPRIO time do player não deveria dar XP nenhum")

    def test_minion_inimigo_da_xp_normalmente(self):
        enemy_player = self._make_nearby_player(_FAC_B)
        dying_minion = create_minion(self.ws.world, 10, 11, "minion_melee",
                                     faction_id=_FAC_A, route=[(10, 11)])
        self.ws.world.add_component(dying_minion, MapLocation(self.ws._map_file))
        self.ws.world.add_component(dying_minion, PendingDeath(killer_entity_id=-1))
        self.ws._mob_eids.add(dying_minion)

        self.ws._death_handler.update()

        xp_entries = [e for e in self.ws._death_handler.pending_xp if e["player_eid"] == enemy_player]
        self.assertTrue(xp_entries, "minion INIMIGO deveria continuar dando XP por proximidade")


class TestPlayerKillCreditAndAssist(unittest.TestCase):
    """Problema #3 (+ achado investigando): kill credit/assist de player,
    passando pelo caminho de combate REAL, não atalho direto."""

    def setUp(self):
        self.ws = make_world_server()
        from server.instance_progression import enter_normalized_progression
        self.a = spawn_player(self.ws, "s_a", 10, 10, class_id="guerreiro")
        self.b = spawn_player(self.ws, "s_b", 10, 11, class_id="guerreiro")
        self.victim = spawn_player(self.ws, "s_v", 11, 10, class_id="guerreiro")
        self.ws.world.add_component(self.a, Faction(faction_id=_FAC_A))
        self.ws.world.add_component(self.b, Faction(faction_id=_FAC_A))
        self.ws.world.add_component(self.victim, Faction(faction_id=_FAC_B))
        for eid in (self.a, self.b, self.victim):
            enter_normalized_progression(self.ws, eid)
            self.ws.world.get_component(eid, CombatStats).acerto = 100.0

    def _hit_victim_with(self, attacker_eid: int, damage: int) -> None:
        """Golpe SUBLETAL de `attacker_eid` na vítima, via caminho real de
        combate (popula _player_damage_log de verdade, mesmo hook que
        produção usa — não um atalho de teste)."""
        vic_cs = self.ws.world.get_component(self.victim, CombatStats)
        vic_cs.current_hp = min(vic_cs.max_hp, vic_cs.current_hp + 10000)  # garante vivo, folga
        vic_cs.current_hp = damage + 1  # próximo golpe NÃO mata
        # Zera esquiva/aparo da VÍTIMA — acerto=100 no atacante sozinho
        # NÃO garante hit (dodge/parry do defensor entram na mesma
        # rolagem, mesmo padrão já usado em tests/test_server.py pros
        # testes de Punho no Queixo). Sem isso, o golpe ocasionalmente
        # erra, nunca populando _player_damage_log — flakiness real
        # achada rodando a suíte várias vezes.
        vic_cs.dodge_rating = 0.0
        vic_cs.parry_rating = 0.0
        sid = {self.a: "s_a", self.b: "s_b"}[attacker_eid]
        engage_target(self.ws, sid, self.victim)
        self.ws._attack_timers[sid] = 0.0
        self.ws._process_pvp_attack(attacker_eid, self.victim, sid, 0.05)

    def _finish_victim_with_minion(self) -> int:
        """Mata a vítima com o golpe FINAL de um minion inimigo (não
        player) — via o mesmo caminho catch-all de HP-diff que
        `_process_player_attacks` já roda em produção pra QUALQUER
        morte de player (exercita a race de verdade, não um atalho)."""
        from engine.core_systems import apply_damage_core
        minion = create_minion(self.ws.world, 11, 11, "minion_melee",
                               faction_id=_FAC_A, route=[(11, 11)])
        vic_cs = self.ws.world.get_component(self.victim, CombatStats)
        hp_before = vic_cs.current_hp
        snapshot = {self.victim: hp_before}
        apply_damage_core(self.ws.world, self.victim, hp_before + 999, killer_eid=minion)
        self.ws._process_player_attacks(0.05, snapshot)
        return minion

    def test_player_finalizado_por_minion_da_kill_pro_ultimo_player_que_bateu(self):
        """O caso central relatado: golpe final NÃO é de player, mas a
        kill deveria ir pro player que bateu por último, não pra
        ninguém."""
        self._hit_victim_with(self.a, damage=20)
        self._finish_victim_with_minion()

        cst_a = self.ws.world.get_component(self.a, CharStatsTracker)
        self.assertEqual(cst_a.players_killed, 1,
            "player que bateu por último deveria ganhar a kill, mesmo o golpe final sendo de minion")

    def test_segundo_player_que_bateu_ganha_assist_no_lugar_de_kill(self):
        self._hit_victim_with(self.b, damage=30)   # b bate primeiro
        self._hit_victim_with(self.a, damage=20)   # a bate por ÚLTIMO
        self._finish_victim_with_minion()

        cst_a = self.ws.world.get_component(self.a, CharStatsTracker)
        cst_b = self.ws.world.get_component(self.b, CharStatsTracker)
        self.assertEqual(cst_a.players_killed, 1, "a bateu por último -> kill")
        self.assertEqual(cst_b.players_killed, 0, "b não bateu por último -> não é kill")
        self.assertEqual(cst_b.assists, 1, "b bateu dentro da janela -> assist")

    def test_ninguem_bateu_ninguem_ganha_kill_nem_assist(self):
        """Equivalente à "regra de execução" do LoL — só minion/torre
        bateram, nenhum player participou."""
        self._finish_victim_with_minion()

        cst_a = self.ws.world.get_component(self.a, CharStatsTracker)
        cst_b = self.ws.world.get_component(self.b, CharStatsTracker)
        self.assertEqual(cst_a.players_killed, 0)
        self.assertEqual(cst_b.players_killed, 0)
        self.assertEqual(cst_a.assists, 0)
        self.assertEqual(cst_b.assists, 0)

    def test_kill_e_assist_concedem_gold_de_instancia(self):
        from shared.constants import INSTANCE_PLAYER_ASSIST_GOLD
        from server.instance_progression import INSTANCE_PLAYER_KILL_GOLD
        from engine.components import Wallet
        gold_a_antes = self.ws.world.get_component(self.a, Wallet).gold
        gold_b_antes = self.ws.world.get_component(self.b, Wallet).gold

        self._hit_victim_with(self.b, damage=30)
        self._hit_victim_with(self.a, damage=20)
        self._finish_victim_with_minion()

        gold_a_depois = self.ws.world.get_component(self.a, Wallet).gold
        gold_b_depois = self.ws.world.get_component(self.b, Wallet).gold
        self.assertEqual(gold_a_depois - gold_a_antes, INSTANCE_PLAYER_KILL_GOLD)
        self.assertEqual(gold_b_depois - gold_b_antes, INSTANCE_PLAYER_ASSIST_GOLD)

    def test_hit_fora_da_janela_de_assist_nao_conta(self):
        self._hit_victim_with(self.b, damage=30)
        # Simula a janela de assist expirando — recua o timestamp
        # registrado pra ANTES da janela (mantém o seq como está).
        _ts, _seq = self.ws._player_damage_log[self.victim][self.b]
        self.ws._player_damage_log[self.victim][self.b] = (_ts - (PLAYER_KILL_ASSIST_WINDOW_S + 1), _seq)
        self._hit_victim_with(self.a, damage=20)
        self._finish_victim_with_minion()

        cst_b = self.ws.world.get_component(self.b, CharStatsTracker)
        self.assertEqual(cst_b.assists, 0,
            "hit fora da janela de 10s não deveria contar como assist")


class TestPlayerDeathNotDoubleProcessed(unittest.TestCase):
    """§130 (09/09/2026) — kill por auto-attack PvP disparava
    `_handle_player_death` 2x no mesmo tick: uma vez no braço de PvP
    (`_process_pvp_attack`) e de novo no loop genérico de
    `_process_player_attacks` ("qualquer fonte que zerou o HP neste
    tick", pensado pra DoT/skill/mob — não sabe que o PvP já tratou a
    MESMA morte). Não dobrava `players_killed` (o log de contribuições
    já tinha sido consumido), mas dobrava incondicionalmente
    `CharStatsTracker.deaths` da vítima. Usa o caminho de combate REAL
    (`_process_player_attacks` com snapshot capturado ANTES do tick,
    mesmo formato que `WorldServer._tick` monta) — não um atalho direto
    em `_handle_player_death`, pra exercitar a race de verdade."""

    def setUp(self):
        self.ws = make_world_server()
        from server.instance_progression import enter_normalized_progression
        self.a = spawn_player(self.ws, "s_a", 10, 10, class_id="guerreiro")
        self.v = spawn_player(self.ws, "s_v", 11, 10, class_id="guerreiro")
        self.ws.world.add_component(self.a, Faction(faction_id=_FAC_A))
        self.ws.world.add_component(self.v, Faction(faction_id=_FAC_B))
        for eid in (self.a, self.v):
            enter_normalized_progression(self.ws, eid)
            self.ws.world.get_component(eid, CombatStats).acerto = 100.0

    def test_kill_por_auto_attack_nao_dobra_deaths_da_vitima(self):
        vic_cs = self.ws.world.get_component(self.v, CombatStats)
        vic_cs.dodge_rating = 0.0
        vic_cs.parry_rating = 0.0
        vic_cs.current_hp = 1   # um golpe mata

        # Snapshot capturado ANTES do dano do tick — mesmo formato que
        # WorldServer._tick monta em player_hp_snap (hp cheio/pré-morte).
        snapshot = {self.v: vic_cs.current_hp}

        engage_target(self.ws, "s_a", self.v)
        self.ws._attack_timers["s_a"] = 0.0
        self.ws._process_player_attacks(0.05, snapshot)

        cst_a = self.ws.world.get_component(self.a, CharStatsTracker)
        cst_v = self.ws.world.get_component(self.v, CharStatsTracker)
        self.assertEqual(cst_a.players_killed, 1,
            "matador deveria ganhar exatamente 1 kill")
        self.assertEqual(cst_v.deaths, 1,
            "vítima deveria ganhar exatamente 1 death, não 2 (processamento duplicado no mesmo tick)")

    def test_handle_player_death_chamado_2x_direto_e_no_op_na_segunda_vez(self):
        """Regressão de baixo nível: mesmo sem passar pelo combate real,
        chamar `_handle_player_death` de novo pro MESMO player já morto
        (GhostState.is_dead=True) não deve fazer nada — nem re-somar
        death, nem re-processar limpeza."""
        self.ws._handle_player_death(self.v, killer_eid=self.a)
        cst_a = self.ws.world.get_component(self.a, CharStatsTracker)
        cst_v = self.ws.world.get_component(self.v, CharStatsTracker)
        kills_after_first  = cst_a.players_killed
        deaths_after_first = cst_v.deaths

        self.ws._handle_player_death(self.v, killer_eid=self.a)
        self.assertEqual(cst_a.players_killed, kills_after_first,
            "2ª chamada pro mesmo player já morto não deveria dar kill de novo")
        self.assertEqual(cst_v.deaths, deaths_after_first,
            "2ª chamada pro mesmo player já morto não deveria somar death de novo")


if __name__ == "__main__":
    unittest.main()
