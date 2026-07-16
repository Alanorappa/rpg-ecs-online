"""
tests/test_faction.py — Sistema de Facções (ARQUITETURA_ONLINE.md).

TestGetRelationship/TestFactionSystemHelpers: Fase 1, infraestrutura pura
(content/faction_data.py + engine/faction_system.py isolados, sem rodar
nenhum sistema de jogo).

TestProximityAggroByFaction: Fase 2, comportamento real de
EnemyAISystem — mob hostil continua agroando por proximidade
(regressão), mob neutro ignora proximidade mas agroa ao ser atacado, e
reverte via o mesmo mecanismo de leash/evasão já existente (RETURNING).

TestCombatNpcArchetype: Fase 4 — create_combat_npc() (novo arquétipo,
compartilha _build_combat_entity com create_enemy), componente
Combatant genérico como gate de sync (server/world_server.py, no lugar
de Enemy sozinho), e o gate de dano "amigavel" trazido da Fase 5
(apply_damage_core) pra já valer aqui.

TestMultiTargetCombat: Fase 5 — _select_target() deixa de assumir só
PlayerControlled: mob hostil pode escolher um NPC de combate (ou outro
mob) como alvo, via can_engage(); NPC de combate (guarda) pode alvejar
mob hostil de volta.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.world import World
from engine.components import Faction, PlayerControlled
from content.faction_data import get_relationship, PLAYER_FACTION, DEFAULT_RELATIONSHIP
from engine.faction_system import (get_entity_faction, get_relationship_between,
                                   can_engage, is_hostile)


class TestGetRelationship(unittest.TestCase):

    def test_mesma_faccao_e_sempre_amigavel(self):
        self.assertEqual(get_relationship("bandidos", "bandidos"), "amigavel")

    def test_par_conhecido_hostil(self):
        self.assertEqual(get_relationship("monstros_hostis", PLAYER_FACTION), "hostil")

    def test_par_conhecido_neutro(self):
        self.assertEqual(get_relationship("vida_selvagem", PLAYER_FACTION), "neutro")

    def test_par_conhecido_amigavel(self):
        self.assertEqual(get_relationship("guardas_vila", PLAYER_FACTION), "amigavel")

    def test_busca_e_simetrica(self):
        # ("bandidos", "guardas_vila") está na tabela nessa ordem — a busca
        # invertida também deve resolver ao mesmo tier.
        direto    = get_relationship("bandidos", "guardas_vila")
        invertido = get_relationship("guardas_vila", "bandidos")
        self.assertEqual(direto, invertido)
        self.assertEqual(direto, "hostil")

    def test_par_desconhecido_cai_no_default(self):
        self.assertEqual(get_relationship("faccao_inventada_no_teste", PLAYER_FACTION),
                         DEFAULT_RELATIONSHIP)


class TestFactionSystemHelpers(unittest.TestCase):

    def setUp(self):
        self.world = World()

    def _make_faction_entity(self, faction_id: str) -> int:
        eid = self.world.create_entity()
        self.world.add_component(eid, Faction(faction_id=faction_id))
        return eid

    def _make_player_entity(self) -> int:
        eid = self.world.create_entity()
        self.world.add_component(eid, PlayerControlled())
        return eid

    def test_get_entity_faction_com_componente_faction(self):
        eid = self._make_faction_entity("bandidos")
        self.assertEqual(get_entity_faction(self.world, eid), "bandidos")

    def test_get_entity_faction_de_player(self):
        eid = self._make_player_entity()
        self.assertEqual(get_entity_faction(self.world, eid), PLAYER_FACTION)

    def test_get_entity_faction_sem_componente_nenhum(self):
        eid = self.world.create_entity()   # NPC estático/blocker/dummy
        # Não deve lançar exceção, e não deve resolver pra nenhuma facção
        # de verdade (sentinela interno).
        faction = get_entity_faction(self.world, eid)
        self.assertNotEqual(faction, PLAYER_FACTION)
        self.assertNotEqual(faction, "bandidos")

    def test_relationship_between_mob_hostil_e_player(self):
        mob    = self._make_faction_entity("monstros_hostis")
        player = self._make_player_entity()
        self.assertEqual(get_relationship_between(self.world, mob, player), "hostil")

    def test_relationship_between_entidade_sem_faccao_nunca_e_hostil(self):
        blocker = self.world.create_entity()   # sem Faction, sem PlayerControlled
        player  = self._make_player_entity()
        rel = get_relationship_between(self.world, blocker, player)
        self.assertNotEqual(rel, "hostil")

    def test_can_engage_tabela_verdade(self):
        hostil_eid   = self._make_faction_entity("monstros_hostis")
        neutro_eid   = self._make_faction_entity("vida_selvagem")
        amigavel_eid = self._make_faction_entity("guardas_vila")
        player       = self._make_player_entity()

        self.assertTrue(can_engage(self.world, hostil_eid, player))
        self.assertTrue(can_engage(self.world, neutro_eid, player))
        self.assertFalse(can_engage(self.world, amigavel_eid, player))

    def test_is_hostile_so_true_pro_tier_hostil(self):
        hostil_eid   = self._make_faction_entity("monstros_hostis")
        neutro_eid   = self._make_faction_entity("vida_selvagem")
        amigavel_eid = self._make_faction_entity("guardas_vila")
        player       = self._make_player_entity()

        self.assertTrue(is_hostile(self.world, hostil_eid, player))
        self.assertFalse(is_hostile(self.world, neutro_eid, player))
        self.assertFalse(is_hostile(self.world, amigavel_eid, player))

    def test_bandidos_vs_guardas_vila_e_hostil_sem_envolver_player(self):
        bandido = self._make_faction_entity("bandidos")
        guarda  = self._make_faction_entity("guardas_vila")
        self.assertTrue(is_hostile(self.world, bandido, guarda))
        # guardas_vila-vs-bandidos é "hostil" (não amigavel) -> can_engage True
        self.assertTrue(can_engage(self.world, guarda, bandido))


class TestProximityAggroByFaction(unittest.TestCase):
    """Fase 2: EnemyAISystem passa a consultar is_hostile() no gate de
    aggro por proximidade (engine/world_systems.py, bloco "Detecção
    inicial"). Usa um WorldServer real — precisa do tick completo
    (EnemyAISystem.update) rodando de verdade, não só os helpers puros
    de faction_system."""

    def setUp(self):
        from tests.helpers import make_world_server, spawn_player
        self.ws  = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        self.peid = self.ws._player_eids["s1"]

    def _spawn_mob(self, tx: int, ty: int, faction: str) -> int:
        from engine.entity_factory import create_enemy
        from engine.components import CombatState, Visible, MapLocation
        from tests.helpers import set_entity_tile
        eid = create_enemy(self.ws.world, tx, ty, faction=faction)
        self.ws._mob_eids.add(eid)
        self.ws.world.add_component(eid, CombatState())
        self.ws.world.add_component(eid, Visible())
        # EnemyAISystem tem 1 instância por mapa, filtrada por MapLocation
        # (world_systems.py:1515-1517) — mob sem o componente é IGNORADO
        # por toda instância filtrada, nunca entrando em nenhum tick de IA.
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        set_entity_tile(self.ws, eid, tx, ty)
        return eid

    def test_regressao_mob_hostil_ainda_agroa_por_proximidade(self):
        from tests.helpers import run_ticks
        from engine.components import AIControlled
        mob = self._spawn_mob(131, 374, faction="monstros_hostis")
        run_ticks(self.ws, 5)   # bem dentro do raio de aggro (adjacente)
        ai = self.ws.world.get_component(mob, AIControlled)
        self.assertNotEqual(ai.state, "IDLE",
                            "mob hostil deveria ter entrado em AGGRO_DELAY/CHASING")

    def test_mob_neutro_ignora_proximidade(self):
        from tests.helpers import run_ticks
        from engine.components import AIControlled
        mob = self._spawn_mob(131, 374, faction="vida_selvagem")
        run_ticks(self.ws, 30)   # tempo de sobra pra provar que NÃO agroa
        ai = self.ws.world.get_component(mob, AIControlled)
        self.assertEqual(ai.state, "IDLE",
                         "mob neutro não deveria agroar só por proximidade")

    def test_mob_neutro_agroa_ao_ser_atacado(self):
        from engine.components import AIControlled, CombatState, CombatStats
        mob = self._spawn_mob(131, 374, faction="vida_selvagem")
        cst = self.ws.world.get_component(self.peid, CombatState)
        cst.target_entity_id = mob
        cst.is_pursuing       = True
        player_cs = self.ws.world.get_component(self.peid, CombatStats)
        player_cs.acerto = 100.0   # hit garantido — aggro por dano só dispara se acertar
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp    = mob_cs.max_hp
        mob_cs.dodge_rating  = 0.0
        mob_cs.parry_rating  = 0.0
        self.ws._attack_timers["s1"] = 0.0
        snap = {mob: mob_cs.current_hp}
        self.ws._process_player_attacks(0.05, snap)

        ai = self.ws.world.get_component(mob, AIControlled)
        self.assertNotEqual(ai.state, "IDLE",
                            "mob neutro deveria agroar após ser atacado")
        self.assertTrue(ai.aggroed_by_damage)

    def test_mob_neutro_reverte_apos_leash_mesmo_mecanismo_de_hostil(self):
        """Não é lógica NOVA — prova que o gate de facção não interfere no
        mecanismo de leash/RETURNING já existente (evasão), reaproveitado
        sem alteração pra mobs neutros agroados por dano.

        Mob precisa ficar FORA do attack_range_tiles do player (senão
        EnemyAISystem entra no branch "in_attack_range" — world_systems.py
        ~1984-1992 — que dá `continue` ANTES do bloco de leash rodar nesse
        tick, um comportamento pré-existente e independente de facção)."""
        from engine.components import AIControlled, InitialPosition
        from tests.helpers import run_ticks, set_entity_tile
        mob = self._spawn_mob(140, 374, faction="vida_selvagem")
        set_entity_tile(self.ws, mob, 140, 374)   # 10 tiles do player — fora do attack_range
        ai = self.ws.world.get_component(mob, AIControlled)
        ai.state             = "CHASING"
        ai.target_eid         = self.peid
        ai.aggroed_by_damage  = True
        # Afasta o spawn original (InitialPosition) pra além do leash de
        # dano (MAX_LEASH_RADIUS_DMG=25 tiles) sem mover o mob de verdade.
        ip = self.ws.world.get_component(mob, InitialPosition)
        from engine.tileset import TILE_SIZE
        ip.x = (140 - 30) * TILE_SIZE
        ip.y = 374 * TILE_SIZE

        run_ticks(self.ws, 3)
        self.assertEqual(ai.state, "RETURNING",
                         "mob neutro agroado por dano deveria respeitar leash igual hostil")


class TestCombatNpcArchetype(unittest.TestCase):
    """Fase 4: create_combat_npc() (novo arquétipo), componente Combatant
    como gate de sync genérico, e o gate de dano "amigavel" (trazido da
    Fase 5 pra já proteger o NPC de combate amigável nesta fase)."""

    def setUp(self):
        from tests.helpers import make_world_server, spawn_player
        self.ws  = make_world_server()
        spawn_player(self.ws, "s1", 130, 374)
        self.peid = self.ws._player_eids["s1"]

    def test_create_combat_npc_tem_combatant_faction_npc_e_combatstats(self):
        from engine.entity_factory import create_combat_npc
        from engine.components import Combatant, Faction, NPC, CombatStats, Enemy
        eid = create_combat_npc(self.ws.world, 131, 374, faction="guardas_vila",
                                name="Guarda Real", profession="Guarda")
        self.assertIsNotNone(self.ws.world.get_component(eid, Combatant))
        fac = self.ws.world.get_component(eid, Faction)
        self.assertEqual(fac.faction_id, "guardas_vila")
        npc = self.ws.world.get_component(eid, NPC)
        self.assertEqual(npc.name, "Guarda Real")
        self.assertIsNotNone(self.ws.world.get_component(eid, CombatStats))
        # NÃO é Enemy — combatente amigável não é "inimigo" do player.
        self.assertIsNone(self.ws.world.get_component(eid, Enemy))

    def test_combat_npc_e_registrado_em_mob_eids_via_combatant(self):
        from engine.entity_factory import create_combat_npc
        from engine.components import MapLocation
        from tests.helpers import run_ticks
        eid = create_combat_npc(self.ws.world, 135, 374, faction="guardas_vila")
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        run_ticks(self.ws, 2)
        self.assertIn(eid, self.ws._mob_eids,
                     "NPC de combate deveria ser registrado em _mob_eids via Combatant")

    def test_spawn_payload_do_npc_de_combate_inclui_faction(self):
        from engine.entity_factory import create_combat_npc
        from engine.components import TileMovement
        eid = create_combat_npc(self.ws.world, 140, 374, faction="guardas_vila",
                                name="Guarda Real")
        tm = self.ws.world.get_component(eid, TileMovement)
        payload = self.ws._build_mob_spawn_payload(eid, tm)
        self.assertEqual(payload["faction"], "guardas_vila")
        self.assertEqual(payload["name"], "Guarda Real")

    def test_npc_de_combate_amigavel_nao_pode_ser_atacado(self):
        """Peça da Fase 5 (gate can_engage em apply_damage_core) trazida
        pra já valer na Fase 4 — sem ela, um NPC "amigável" seria
        livremente matável, contradizendo a própria palavra."""
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatState, CombatStats
        from engine.world_systems import deal_damage
        guard = create_combat_npc(self.ws.world, 131, 374, faction="guardas_vila")
        self.ws.world.add_component(guard, CombatState())
        guard_cs = self.ws.world.get_component(guard, CombatStats)
        hp_before = guard_cs.current_hp

        dead, outcome = deal_damage(self.peid, guard, "physical", pre_outcome="hit")

        self.assertEqual(guard_cs.current_hp, hp_before,
                         "dano entre facções amigaveis deveria ser bloqueado (HP intacto)")
        self.assertFalse(dead)

    def test_npc_de_combate_hostil_pode_ser_atacado_normalmente(self):
        """Regressão: o gate novo não bloqueia dano fora do caso amigavel —
        um NPC de combate de facção hostil (ex: bandido) continua
        recebendo dano normalmente."""
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatState, CombatStats
        from engine.world_systems import deal_damage
        bandit = create_combat_npc(self.ws.world, 131, 374, faction="bandidos")
        self.ws.world.add_component(bandit, CombatState())
        bandit_cs = self.ws.world.get_component(bandit, CombatStats)
        hp_before = bandit_cs.current_hp

        deal_damage(self.peid, bandit, "physical", pre_outcome="hit")

        self.assertLess(bandit_cs.current_hp, hp_before,
                        "dano entre facções hostis não deveria ser bloqueado")

    def test_set_player_target_recusa_alvo_amigavel(self):
        """Regressão real relatada pelo usuário 15/07/2026: atacar o
        "Guarda Real" de teste entrava em combate (golpes "erravam" em
        loop) e o guarda passava a perseguir o player. Causa raiz: só o
        dano final era bloqueado (apply_damage_core) — nada impedia o
        combate de sequer começar. Fix: set_player_target() recusa o
        alvo na origem."""
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatState
        guard = create_combat_npc(self.ws.world, 131, 374, faction="guardas_vila")
        self.ws.world.add_component(guard, CombatState())

        self.ws.set_player_target("s1", guard)

        cst = self.ws.world.get_component(self.peid, CombatState)
        self.assertEqual(cst.target_entity_id, -1,
                         "set_player_target não deveria aceitar alvo amigavel")

    def test_auto_attack_completo_nao_faz_guarda_amigavel_perseguir(self):
        """Reproduz o bug relatado ponta a ponta: seta o alvo (mesmo
        caminho de AUTO_ATTACK) e roda o loop real de
        _process_player_attacks várias vezes — confirma que o guarda
        NUNCA sai de IDLE (não persegue) e nunca perde HP."""
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatState, CombatStats, AIControlled
        guard = create_combat_npc(self.ws.world, 131, 374, faction="guardas_vila")
        self.ws.world.add_component(guard, CombatState())
        guard_cs = self.ws.world.get_component(guard, CombatStats)
        guard_ai = self.ws.world.get_component(guard, AIControlled)
        hp_before = guard_cs.current_hp

        self.ws.set_player_target("s1", guard)
        for _ in range(10):
            self.ws._attack_timers["s1"] = 0.0
            snap = {guard: guard_cs.current_hp}
            self.ws._process_player_attacks(0.05, snap)

        self.assertEqual(guard_ai.state, "IDLE",
                         "guarda amigavel não deveria sair de IDLE (perseguir o player)")
        self.assertEqual(guard_cs.current_hp, hp_before)
        cst = self.ws.world.get_component(self.peid, CombatState)
        self.assertEqual(cst.target_entity_id, -1,
                         "alvo amigavel deveria ser limpo, não ficar preso em loop de ataque")


class TestMultiTargetCombat(unittest.TestCase):
    """Fase 5: _select_target() deixa de assumir só PlayerControlled —
    mob hostil pode alvejar NPC de combate (e vice-versa), sem player
    envolvido. Player nesta suíte fica LONGE de propósito (10,10), fora
    de qualquer disputa de "mais perto", pra isolar o comportamento
    mob↔NPC sem interferência."""

    def setUp(self):
        from tests.helpers import make_world_server, spawn_player
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 10, 10)
        self.peid = self.ws._player_eids["s1"]

    def _spawn_mob(self, tx, ty, faction):
        from engine.entity_factory import create_enemy
        from engine.components import CombatState, Visible, MapLocation
        eid = create_enemy(self.ws.world, tx, ty, faction=faction)
        self.ws._mob_eids.add(eid)
        self.ws.world.add_component(eid, CombatState())
        self.ws.world.add_component(eid, Visible())
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        return eid

    def _spawn_guard(self, tx, ty, faction="guardas_vila"):
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatState, Visible, MapLocation
        eid = create_combat_npc(self.ws.world, tx, ty, faction=faction)
        self.ws._mob_eids.add(eid)
        self.ws.world.add_component(eid, CombatState())
        self.ws.world.add_component(eid, Visible())
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        return eid

    def test_mob_hostil_escolhe_npc_de_combate_como_alvo(self):
        from engine.components import AIControlled
        from tests.helpers import run_ticks
        guard = self._spawn_guard(131, 374)
        mob   = self._spawn_mob(132, 374, faction="monstros_hostis")

        run_ticks(self.ws, 3)

        ai = self.ws.world.get_component(mob, AIControlled)
        self.assertEqual(ai.target_eid, guard,
                         "mob hostil deveria escolher o guarda (só candidato perto) como alvo")

    def test_npc_de_combate_escolhe_mob_hostil_como_alvo(self):
        """Prova o outro lado do gate assimétrico (busca por _all_combatants_cache
        quando quem procura é um NPC) — sem isso o guarda nunca "vê" o mob."""
        from engine.components import AIControlled
        from tests.helpers import run_ticks
        guard = self._spawn_guard(131, 374)
        mob   = self._spawn_mob(132, 374, faction="monstros_hostis")

        run_ticks(self.ws, 3)

        guard_ai = self.ws.world.get_component(guard, AIControlled)
        self.assertEqual(guard_ai.target_eid, mob,
                         "NPC de combate deveria escolher o mob hostil como alvo")

    def test_combate_completo_mob_vs_npc_e_mutuo(self):
        """Integração ponta a ponta: mob hostil e guarda adjacentes, sem
        player por perto — os dois devem se ferir mutuamente com o tempo,
        exatamente o "NPCs se atacarão entre si" pedido pelo usuário."""
        from engine.components import CombatStats
        from tests.helpers import run_ticks
        guard = self._spawn_guard(131, 374)
        mob   = self._spawn_mob(132, 374, faction="monstros_hostis")
        guard_cs = self.ws.world.get_component(guard, CombatStats)
        mob_cs   = self.ws.world.get_component(mob, CombatStats)
        guard_hp_before = guard_cs.current_hp
        mob_hp_before   = mob_cs.current_hp

        run_ticks(self.ws, 200)   # tempo de sobra: aggro_delay + adjacência + cooldown de ataque

        self.assertLess(guard_cs.current_hp, guard_hp_before,
                        "guarda deveria ter recebido dano do mob hostil")
        self.assertLess(mob_cs.current_hp, mob_hp_before,
                        "mob deveria ter recebido dano de volta do guarda")

    def test_mob_neutro_nao_ataca_npc_amigavel_por_proximidade(self):
        """Regressão: relação neutra (ex: vida_selvagem vs guardas_vila)
        não inicia combate por proximidade — mesma regra da Fase 2, agora
        também vale entre não-jogadores."""
        from engine.components import AIControlled, CombatStats
        from tests.helpers import run_ticks
        guard = self._spawn_guard(131, 374)
        mob   = self._spawn_mob(132, 374, faction="vida_selvagem")
        guard_cs = self.ws.world.get_component(guard, CombatStats)
        hp_before = guard_cs.current_hp

        run_ticks(self.ws, 30)

        ai = self.ws.world.get_component(mob, AIControlled)
        self.assertEqual(ai.state, "IDLE",
                         "mob neutro não deveria agroar o guarda só por proximidade")
        self.assertEqual(guard_cs.current_hp, hp_before)


if __name__ == "__main__":
    unittest.main()
