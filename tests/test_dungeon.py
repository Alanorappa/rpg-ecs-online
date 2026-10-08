"""
tests/test_dungeon.py
Dungeon PvE instanciada (07/10/2026, pedido do usuário — primeira dungeon:
Aracnópolis). Cobre as peças genéricas novas (reusáveis por qualquer
dungeon futura), não a Aracnópolis especificamente:

- SpawnZone.respawnable=False: zona esvaziada por morte NUNCA se
  reenche sozinha (bug seria ela cair no branch de "preenchimento
  inicial" de novo — ver engine/world_systems.py::SpawnZoneSystem).
- CustomLootTable tem prioridade total sobre MOB_TABLE
  (server/server_death_handler.py).
- DungeonProcessorMixin: grupo cai na MESMA instância, solo em
  instâncias diferentes; morte dentro da dungeon respawna no ponto de
  entrada (nunca RESPAWN_TILE global); saída remove membership e
  derruba a instância quando o último sai.
- GhostState.auto_respawn/respawn_s (desacoplado de
  InstanceInventoryUIState) controla a UI de respawn automático tanto
  pra dungeon quanto pra BG.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, set_entity_tile
from engine.world import World
from engine.components import (SpawnZone, SpawnZoneOwner, Enemy, MapLocation,
                              CustomLootTable, CombatStats, PendingDeath,
                              GhostState, TileMovement)
from engine.world_systems import SpawnZoneSystem


# ── SpawnZone.respawnable — zona de dungeon nunca se reenche sozinha ────────

class _FakePathfinding:
    """Mesmo stub mínimo já usado em tests/test_client_ui.py — SpawnZoneSystem
    só precisa de _get_tilemap_component() pra não cair no serviço global
    (_svc['pathfinding'], nunca registrado num World() isolado de teste)."""
    def _get_tilemap_component(self):
        return None


class TestSpawnZoneNaoRespawnavel(unittest.TestCase):
    """Isolado (World() puro, sem WorldServer) — só a lógica de
    SpawnZoneSystem em si."""

    def setUp(self):
        self.world = World()
        self.sys = SpawnZoneSystem(self.world, pathfinding=_FakePathfinding())

    def _make_zone(self, respawnable: bool) -> int:
        zone_eid = self.world.create_entity()
        self.world.add_component(zone_eid, SpawnZone(
            center_x=5, center_y=5, enemy_type="melee", enemy_tier="elite",
            radius=1, max_count=1, respawn_cooldown=1.0,
            level_min=5, level_max=7, race="Aranha",
            respawnable=respawnable))
        return zone_eid

    def _run_until_spawned(self, zone_eid: int, max_ticks: int = 20) -> int:
        zone = self.world.get_component(zone_eid, SpawnZone)
        for _ in range(max_ticks):
            self.sys.update(dt=0.2)
            if zone.active_entity_ids:
                return next(iter(zone.active_entity_ids))
        self.fail("zona nunca spawnou o mob inicial")

    def test_zona_respawnavel_normal_reenche_apos_morte(self):
        """Controle: comportamento de SEMPRE (respawnable=True, default)
        continua intacto — mob morto volta depois do cooldown."""
        zone_eid = self._make_zone(respawnable=True)
        mob_eid = self._run_until_spawned(zone_eid)
        self.world.remove_entity(mob_eid)

        zone = self.world.get_component(zone_eid, SpawnZone)
        for _ in range(30):  # cooldown=1.0s, dt=0.2 -> 5 ticks bastam, sobra margem
            self.sys.update(dt=0.2)
            if zone.active_entity_ids:
                break
        self.assertTrue(zone.active_entity_ids,
                        "zona respawnable=True deveria repor o mob morto")

    def test_zona_sem_respawn_fica_vazia_para_sempre_depois_de_morrer(self):
        zone_eid = self._make_zone(respawnable=False)
        mob_eid = self._run_until_spawned(zone_eid)
        self.world.remove_entity(mob_eid)

        zone = self.world.get_component(zone_eid, SpawnZone)
        for _ in range(60):   # bem além do que um cooldown normal levaria
            self.sys.update(dt=0.2)
        self.assertFalse(zone.active_entity_ids,
                         "zona respawnable=False NUNCA deveria repor o mob morto")

    def test_zona_sem_respawn_nao_cai_de_volta_no_preenchimento_inicial(self):
        """Regressão específica do bug encontrado durante a implementação:
        sem o campo `ever_filled`, a zona esvaziada reentrava no branch de
        'preenchimento inicial escalonado' (mesma condição que o 1º fill
        usa: timers vazios + alive < max_count) e se reenchia na hora,
        anulando respawnable=False por completo."""
        zone_eid = self._make_zone(respawnable=False)
        zone = self.world.get_component(zone_eid, SpawnZone)
        self.assertTrue(zone.ever_filled is False)
        mob_eid = self._run_until_spawned(zone_eid)
        self.assertTrue(zone.ever_filled)
        self.world.remove_entity(mob_eid)
        self.sys.update(dt=0.2)   # só 1 tick — exatamente onde o bug disparava
        self.assertFalse(zone.active_entity_ids)


# ── CustomLootTable — prioridade total sobre MOB_TABLE ──────────────────────

class TestCustomLootTablePrioridadeSobreMobTable(unittest.TestCase):
    """Aranha tem gold_chance=0.0 em MOB_TABLE (NUNCA dropa ouro pelo
    caminho normal) e seu loot normal não inclui "cracked_club" — usar os
    dois como sinal inequívoco de que o caminho CUSTOM rodou, não o
    genérico."""

    def setUp(self):
        self.ws = make_world_server()

    def _spawn_custom_boss(self, x: int, y: int) -> int:
        from engine.entity_factory import create_enemy
        eid = create_enemy(self.ws.world, x, y, tier="boss", race="Aranha", level=7)
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        self.ws.world.add_component(eid, CustomLootTable(
            items={"cracked_club": 1.0}, gold_min=77, gold_max=77))
        return eid

    def test_loot_customizado_tem_prioridade_sobre_mob_table(self):
        mob_eid = self._spawn_custom_boss(40, 40)
        killer = spawn_player(self.ws, "sk", 40, 40)

        cs = self.ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(mob_eid, PendingDeath(killer_entity_id=killer))
        antes = set(self.ws._corpses.keys())
        run_ticks(self.ws, 2)

        # O mundo real (map_1) pode criar outros corpses/nós de coleta
        # alheios durante esses ticks (irrelevante aqui) — filtra pelo
        # corpse NO TILE exato onde o nosso boss morreu.
        novos = [cid for cid in (set(self.ws._corpses.keys()) - antes)
                if (self.ws._corpses[cid]["tx"], self.ws._corpses[cid]["ty"]) == (40, 40)]
        self.assertEqual(len(novos), 1, "deveria ter criado exatamente 1 corpse no tile do boss")
        corpse = self.ws._corpses[novos[0]]

        item_ids = [i.get("item_id") for i in corpse["items"]]
        self.assertIn("cracked_club", item_ids,
                      "item do CustomLootTable deveria estar no corpse")
        self.assertEqual(corpse["coins"], 77,
                         "coins deveriam vir do CustomLootTable (Aranha normal "
                         "tem gold_chance=0.0, nunca dropa ouro)")

    def test_sem_custom_loot_table_aranha_continua_sem_ouro(self):
        """Controle: um boss SEM CustomLootTable continua caindo no
        caminho normal (MOB_TABLE) — Aranha nunca dropa ouro ali."""
        from engine.entity_factory import create_enemy
        mob_eid = create_enemy(self.ws.world, 41, 41, tier="boss", race="Aranha", level=7)
        self.ws.world.add_component(mob_eid, MapLocation(self.ws._map_file))
        killer = spawn_player(self.ws, "sk2", 41, 41)

        cs = self.ws.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 0
        self.ws.world.add_component(mob_eid, PendingDeath(killer_entity_id=killer))
        antes = set(self.ws._corpses.keys())
        run_ticks(self.ws, 2)

        novos = [cid for cid in (set(self.ws._corpses.keys()) - antes)
                if (self.ws._corpses[cid]["tx"], self.ws._corpses[cid]["ty"]) == (41, 41)]
        if novos:  # pode não criar corpse nenhum se não dropou nada (comportamento normal)
            corpse = self.ws._corpses[novos[0]]
            self.assertEqual(corpse["coins"], 0)


# ── DungeonProcessorMixin — instanciamento por grupo/solo ───────────────────

class TestDungeonInstanciaPorGrupoOuSolo(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_dois_membros_do_mesmo_grupo_caem_na_mesma_instancia(self):
        p1 = spawn_player(self.ws, "sp1", 10, 10)
        p2 = spawn_player(self.ws, "sp2", 10, 10)
        self.ws.request_party_invite(p1, p2)
        self.ws.respond_party_invite(p2, accept=True)

        ok1 = self.ws.request_dungeon_enter("sp1", p1, "aracnopolis", 87, 148)
        ok2 = self.ws.request_dungeon_enter("sp2", p2, "aracnopolis", 87, 148)
        self.assertTrue(ok1)
        self.assertTrue(ok2)

        key1 = self.ws._player_dungeon_instance[p1]
        key2 = self.ws._player_dungeon_instance[p2]
        self.assertEqual(key1, key2, "grupo deveria cair na MESMA instância")
        self.assertIn(p1, self.ws._dungeon_instances[key1]["members"])
        self.assertIn(p2, self.ws._dungeon_instances[key1]["members"])

    def test_dois_solos_caem_em_instancias_diferentes(self):
        p1 = spawn_player(self.ws, "ss1", 10, 10)
        p2 = spawn_player(self.ws, "ss2", 10, 10)

        self.ws.request_dungeon_enter("ss1", p1, "aracnopolis", 87, 148)
        self.ws.request_dungeon_enter("ss2", p2, "aracnopolis", 87, 148)

        key1 = self.ws._player_dungeon_instance[p1]
        key2 = self.ws._player_dungeon_instance[p2]
        self.assertNotEqual(key1, key2, "solos deveriam cair em instâncias DIFERENTES")

    def test_dungeon_key_desconhecido_nao_faz_nada(self):
        p1 = spawn_player(self.ws, "sx", 10, 10)
        ok = self.ws.request_dungeon_enter("sx", p1, "dungeon_que_nao_existe", 0, 0)
        self.assertFalse(ok)
        self.assertNotIn(p1, self.ws._player_dungeon_instance)


class TestDungeonSaidaEDesconexao(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_saida_remove_membership_e_ultimo_a_sair_derruba_instancia(self):
        p1 = spawn_player(self.ws, "so1", 10, 10)
        self.ws.request_dungeon_enter("so1", p1, "aracnopolis", 87, 148)
        key = self.ws._player_dungeon_instance[p1]
        self.assertIn(key, self.ws._map_bundles)

        # "saiu" = destino de alguma transição que NÃO é a própria instância
        self.ws.dungeon_handle_zone_change(p1, "maps/map_1.csv")

        self.assertNotIn(p1, self.ws._player_dungeon_instance)
        self.assertNotIn(key, self.ws._dungeon_instances)
        self.assertNotIn(key, self.ws._map_bundles,
                         "última saída deveria ter descarregado a instância")

    def test_transicao_para_a_propria_instancia_nao_conta_como_saida(self):
        """to_map == instance_key acontece no momento da PRÓPRIA entrada
        (ZONE_CHANGE ecoando o mapa que acabou de carregar) — nunca deveria
        disparar limpeza de membership."""
        p1 = spawn_player(self.ws, "so2", 10, 10)
        self.ws.request_dungeon_enter("so2", p1, "aracnopolis", 87, 148)
        key = self.ws._player_dungeon_instance[p1]

        self.ws.dungeon_handle_zone_change(p1, key)

        self.assertIn(p1, self.ws._player_dungeon_instance)
        self.assertIn(key, self.ws._dungeon_instances)

    def test_desconexao_restaura_mapa_de_origem_e_limpa_membership(self):
        p1 = spawn_player(self.ws, "sd1", 20, 20)
        set_entity_tile(self.ws, p1, 20, 20)
        self.ws.request_dungeon_enter("sd1", p1, "aracnopolis", 87, 148)
        key = self.ws._player_dungeon_instance[p1]

        self.ws.end_dungeon_of(p1)

        self.assertNotIn(p1, self.ws._player_dungeon_instance)
        self.assertNotIn(key, self.ws._dungeon_instances)
        tm = self.ws.world.get_component(p1, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (20, 20),
                         "desconexão deveria restaurar o tile de ORIGEM, não ficar na dungeon")

    def test_end_dungeon_of_sem_estar_em_dungeon_nao_faz_nada(self):
        p1 = spawn_player(self.ws, "sd2", 20, 20)
        self.ws.end_dungeon_of(p1)  # não deveria lançar exceção nem mudar nada
        self.assertNotIn(p1, self.ws._player_dungeon_instance)


# ── Respawn automático dentro da dungeon ─────────────────────────────────────

class TestDungeonRespawnAutomatico(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()

    def test_morte_na_dungeon_respawna_no_ponto_de_entrada_nao_no_global(self):
        from shared.constants import RESPAWN_TILE
        p1 = spawn_player(self.ws, "sr1", 10, 10)
        self.ws.request_dungeon_enter("sr1", p1, "aracnopolis", 87, 148)

        set_entity_tile(self.ws, p1, 90, 150)  # morre longe do ponto de entrada
        self.ws._handle_player_death(p1)  # calcula auto_respawn/respawn_s de verdade

        gst = self.ws.world.get_component(p1, GhostState)
        self.assertTrue(gst.auto_respawn,
                        "jogador dentro de uma dungeon deveria ter auto_respawn=True")
        self.assertTrue(gst.is_dead)

        key = self.ws._player_dungeon_instance[p1]
        self.ws._dungeon_instances[key]["respawn_timers"][p1] = 0.01

        self.ws._tick_dungeon_respawns()  # timer=0.01 - TICK_INTERVAL já fica <= 0

        tm = self.ws.world.get_component(p1, TileMovement)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), (87, 148),
                         "deveria reviver no ponto de entrada da dungeon (87,148)")
        self.assertNotEqual((tm.current_tile_x, tm.current_tile_y), RESPAWN_TILE)
        self.assertFalse(gst.is_dead, "deveria estar vivo depois do respawn automático")

    def test_morte_fora_de_dungeon_e_bg_nao_tem_auto_respawn(self):
        p1 = spawn_player(self.ws, "sr2", 10, 10)
        gst = self.ws.world.get_component(p1, GhostState)
        self.ws._handle_player_death(p1)
        self.assertFalse(gst.auto_respawn,
                         "morte no mundo aberto deveria continuar manual (Liberar espírito)")
