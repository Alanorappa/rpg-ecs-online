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

    def test_combat_targets_exclui_npc_amigavel_de_aoe(self):
        """Regressão real relatada pelo usuário 16/07/2026: Nova Congelante
        (AoE) enraizava o "Guarda Real" também. Causa raiz:
        _combat_targets() incluía TODO _mob_eids sem filtro de hostilidade
        — só players passavam por can_engage ali. NPC de combate amigável
        (gate é Combatant, não Enemy — guarda entra em _mob_eids igual
        bandido) virava alvo válido de qualquer AoE que iterasse
        _combat_targets (Nova Congelante, e qualquer outra atual/futura).
        Fix: mob/NPC agora passa pelo mesmo crivo can_engage que já
        protegia o player em duelo."""
        from engine.entity_factory import create_combat_npc
        guard = create_combat_npc(self.ws.world, 131, 374, faction="guardas_vila",
                                  name="Guarda Real")
        bandit = create_combat_npc(self.ws.world, 132, 374, faction="bandidos")
        # Registra direto em _mob_eids (o que o _tick faz via Combatant) —
        # sem run_ticks: evita rodar SpawnZoneSystem/AI reais (consomem do
        # random global não-seedado, e esse teste só quer exercitar
        # _combat_targets isoladamente).
        self.ws._mob_eids.update({guard, bandit})

        targets = self.ws._combat_targets(exclude_eid=self.peid)

        self.assertNotIn(guard, targets,
                         "NPC de combate amigavel nao deveria ser alvo de AoE")
        self.assertIn(bandit, targets,
                     "NPC de combate hostil continua sendo alvo valido de AoE")

    def test_nova_congelante_nao_enraiza_npc_amigavel(self):
        """Mesmo cenário, ponta a ponta pelo handler real da skill —
        Guarda Real dentro do raio da Nova Congelante não recebe dano
        nem o efeito 'root'."""
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatStats, StatusEffects
        # Guarda já nasce adjacente ao player (130,374) — dentro do raio
        # da Nova Congelante (cast_range=3). Registra em _mob_eids direto
        # (sem run_ticks — ver comentário do teste acima).
        guard = create_combat_npc(self.ws.world, 131, 374, faction="guardas_vila",
                                  name="Guarda Real")
        self.ws._mob_eids.add(guard)
        guard_cs = self.ws.world.get_component(guard, CombatStats)
        hp_before = guard_cs.current_hp

        self.ws._server_nova_congelante(self.peid, -1, {})

        self.assertEqual(guard_cs.current_hp, hp_before,
                         "Nova Congelante nao deveria causar dano no guarda amigavel")
        guard_sfx = self.ws.world.get_component(guard, StatusEffects)
        has_root = bool(guard_sfx and guard_sfx.has("root"))
        self.assertFalse(has_root,
                         "Nova Congelante nao deveria enraizar o guarda amigavel")

    def test_cast_skill_recusa_alvo_amigavel_sem_gastar_recurso(self):
        """Regressão real relatada pelo usuário 16/07/2026: Picada de
        Escorpião/Flecha Reiterada (arqueiro) e Bola de Fogo (mago)
        miradas no "Guarda Real" faziam a flecha/bola viajar até o alvo
        (cast time completo, animação de impacto) e só então o dano era
        bloqueado (apply_damage_core) — mana/cooldown gastos à toa.

        Causa raiz: server/skill_processor.py só validava can_engage()
        pro branch de PLAYER (PvP) na resolução de `tid` do CAST_SKILL;
        o branch de mob/NPC aceitava QUALQUER eid em _mob_eids sem
        checar hostilidade — e mesmo o branch de player, ao falhar, só
        deixava de ATUALIZAR combat_state.target_entity_id (sem recusar
        o cast), então o handler rodava do mesmo jeito com um alvo
        antigo. Fix: os dois branches agora recusam o CAST_SKILL inteiro
        (mesma proteção que set_player_target já dava pro auto-attack),
        antes de qualquer mana/cooldown/cast time ser gasto."""
        from tests.helpers import spawn_player, authorize_skill
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatStats, CharacterStats
        archer = spawn_player(self.ws, "s_arch", 130, 374, class_id="arqueiro")
        authorize_skill(self.ws, archer, "picada_escorpiao")
        guard = create_combat_npc(self.ws.world, 131, 374, faction="guardas_vila",
                                  name="Guarda Real")
        self.ws._mob_eids.add(guard)
        guard_cs = self.ws.world.get_component(guard, CombatStats)
        hp_before = guard_cs.current_hp
        char = self.ws.world.get_component(archer, CharacterStats)
        mana_before = char.mana

        self.ws._pending_skill_requests.append({
            "player_eid": archer, "sid": "picada_escorpiao",
            "tid": guard, "dir_x": 0.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        self.assertEqual(guard_cs.current_hp, hp_before,
                         "guarda amigavel nao deveria receber dano")
        self.assertEqual(char.mana, mana_before,
                         "mana/concentracao nao deveria ser gasta contra alvo amigavel")
        self.assertNotIn((archer, "picada_escorpiao"), self.ws._skill_last_used,
                         "cooldown nao deveria ser registrado contra alvo amigavel")
        results = self.ws._skill_results_this_tick
        self.assertTrue(any(r.get("failed") and r.get("sid") == "picada_escorpiao"
                            for r in results),
                       "deveria reportar failed=True pro cliente (feedback de alvo invalido)")


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

    def test_bystander_neutro_mais_perto_nao_rouba_a_vaga_do_alvo_hostil_mais_longe(self):
        """Regressão real: conteúdo de teste (Bandido perto do Guarda Real,
        maps/map_1_entities.json) nunca engajava — o guarda sempre
        escolhia o boneco de treino mais próximo (sem facção → resolve
        "neutro", passa em can_engage mas nunca deveria "roubar a vaga"
        de um alvo hostil de verdade só por estar mais perto). Fix:
        _select_target() filtra o loop de "outros combatentes" por
        is_hostile(), não can_engage() — só amigavel é excluído por
        can_engage, mas bystander neutro tinha que ser excluído também
        (a menos que já seja o alvo fixo por dano, tratado à parte)."""
        from tests.helpers import run_ticks
        from engine.world_systems import EnemyAISystem

        # Bystander sem Faction (ex: boneco de treino) bem mais perto do
        # guarda do que o bandido — sem o fix, ele "vencia" a seleção de
        # alvo por distância e o combate nunca começava.
        bystander = self._spawn_guard(131, 374, faction="__nunca_usada__")
        from engine.components import Faction as _FacRm
        self.ws.world.remove_component(bystander, _FacRm)  # simula "sem facção" (ex: boneco)

        guard  = self._spawn_guard(132, 374)                       # 1 tile do bystander
        bandit = self._spawn_mob(136, 374, faction="bandidos")     # 4 tiles do guarda — mais longe
                                                                    # que o bystander, mas DENTRO do
                                                                    # raio de aquisição (5 tiles,
                                                                    # AGGRO_RADIUS_TILES — §34.10:
                                                                    # aquisição nunca passa disso)

        # Chama _select_target() diretamente em vez de checar
        # AIControlled.target_eid após N ticks: um alvo fora do raio de
        # aggro nunca faz o mob sair de IDLE, e o target_eid é limpo no
        # fim do MESMO tick pra qualquer mob que continua IDLE (ver
        # world_systems.py, bloco "Retorno à Posição Inicial") — checar o
        # estado persistido testaria só se ALGUM mob de conteúdo real do
        # mapa (zonas de spawn) por acaso vagou perto o bastante,
        # resultando num teste flaky. _select_target() isolado testa a
        # ESCOLHA em si, independente de aggro persistir ou não.
        run_ticks(self.ws, 1)  # popula os caches de EnemyAISystem.update()
        bundle = self.ws._map_bundles[self.ws._map_file]
        ai_sys = next(s for s in bundle.systems if isinstance(s, EnemyAISystem))
        result_eid = ai_sys._select_target(guard)[0]

        self.assertNotEqual(result_eid, bystander,
                            "bystander neutro nunca deveria ser escolhido como alvo")
        self.assertEqual(result_eid, bandit,
                         "guarda deveria escolher o bandido hostil (único candidato válido), mesmo mais longe")


class TestAcquisitionVsRetention(unittest.TestCase):
    """Modelo WoW de aquisição vs retenção de alvo (bugs reais relatados
    pelo usuário 15/07/2026 testando Guarda Real vs Bandido ao vivo):

    1. Guarda matava o bandido e passava a PERSEGUIR o player — o loop de
       players em _select_target não tinha filtro de facção (legado de
       "todo mob é hostil ao player"), então na morte do alvo o guarda em
       estado de combate recebia o player como "próximo alvo válido".
       Aquisição agora exige is_hostile (WoW: aggro radius só em unidade
       "red"; LoL: minion só adquire alvo do time inimigo).
    2. A revidada do mob NEUTRO dependia por acidente desse mesmo loop sem
       filtro (após aggroed_by_damage ser limpo na aproximação) — virou
       RETENÇÃO explícita: alvo engajado persiste enquanto vivo/visível/
       atacável, independente de hostilidade (WoW: quem está na threat
       table continua alvo até a tabela esvaziar).
    3. Combate mob-vs-mob acontecia inteiro server-side de forma invisível
       (nenhum COMBAT_RESULT emitido) — cliente via os dois parados "sem
       desferir dano" com HP cheio.
    """

    def setUp(self):
        from tests.helpers import make_world_server, spawn_player
        self.ws = make_world_server()
        spawn_player(self.ws, "s1", 50, 300)   # terreno aberto, longe de zonas reais
        self.peid = self.ws._player_eids["s1"]

    def _spawn(self, factory_kind, tx, ty, faction):
        from engine.entity_factory import create_enemy, create_combat_npc
        from engine.components import CombatState, Visible, MapLocation
        fn = create_combat_npc if factory_kind == "npc" else create_enemy
        eid = fn(self.ws.world, tx, ty, faction=faction)
        self.ws._mob_eids.add(eid)
        self.ws.world.add_component(eid, CombatState())
        self.ws.world.add_component(eid, Visible())
        self.ws.world.add_component(eid, MapLocation(self.ws._map_file))
        return eid

    def test_guarda_nao_persegue_player_apos_matar_o_bandido(self):
        """Reproduz o bug relatado: player ADJACENTE ao guarda, guarda em
        combate com o bandido; bandido morre → guarda NUNCA pode receber
        o player como alvo (relação amigável), deve resetar e voltar pra
        casa (threat table vazia → evade, modelo WoW)."""
        from engine.components import AIControlled, CombatStats
        from tests.helpers import run_ticks, set_entity_tile
        set_entity_tile(self.ws, self.peid, 50, 300)
        guard  = self._spawn("npc", 51, 300, "guardas_vila")   # adjacente ao player
        bandit = self._spawn("mob", 52, 300, "bandidos")

        run_ticks(self.ws, 60)   # engajam (aggro_delay 1s + aproximação)
        guard_ai = self.ws.world.get_component(guard, AIControlled)
        self.assertEqual(guard_ai.target_eid, bandit, "pré-condição: guarda em combate com o bandido")

        # Bandido morre
        bandit_cs = self.ws.world.get_component(bandit, CombatStats)
        bandit_cs.current_hp = 0
        run_ticks(self.ws, 60)   # grace de 600ms + reset

        self.assertNotEqual(guard_ai.target_eid, self.peid,
                            "guarda amigável nunca deve receber o player como alvo")
        self.assertIn(guard_ai.state, ("IDLE", "RETURNING"),
                      "guarda deveria resetar (voltar pra casa) após o alvo morrer")

    def test_mob_neutro_mantem_retaliacao_sem_aggroed_by_damage(self):
        """Guarda de regressão da retenção: lobo neutro engajado no player
        continua revidando mesmo com aggroed_by_damage=False (o bloco
        "chegou perto → aggroed_by_damage=False" limpa a flag em combate
        normal) — antes, isso dependia do loop de players SEM filtro em
        _select_target, que o fix da aquisição removeu."""
        from engine.components import AIControlled
        from tests.helpers import run_ticks, set_entity_tile
        set_entity_tile(self.ws, self.peid, 50, 300)
        wolf = self._spawn("mob", 51, 300, "vida_selvagem")
        ai = self.ws.world.get_component(wolf, AIControlled)
        ai.state             = "ATTACKING"
        ai.target_eid         = self.peid
        ai.aggroed_by_damage  = False    # já foi limpo pela aproximação

        run_ticks(self.ws, 30)

        self.assertEqual(ai.target_eid, self.peid,
                         "retenção: alvo neutro engajado deve persistir sem aggroed_by_damage")
        self.assertIn(ai.state, ("ATTACKING", "CHASING"),
                      "lobo deveria continuar em combate com o player")

    def test_apos_matar_o_alvo_nao_adquire_hostil_fora_do_raio_de_aggro(self):
        """Bug real relatado pelo usuário 16/07/2026: "após matar o
        bandido, o guarda saiu atacando outros alvos" — o handoff
        pós-morte entregava o próximo hostil a QUALQUER distância (até o
        leash de 20 tiles), porque o cap de 5 tiles só existia no gate
        IDLE→AGGRO_DELAY, não na aquisição em si. Modelo WoW: só quem
        está dentro do aggro radius (ou quem o atacou — threat table)
        pode virar alvo; senão o NPC reseta e volta pra casa."""
        from engine.components import AIControlled, CombatStats
        from tests.helpers import run_ticks
        guard   = self._spawn("npc", 51, 300, "guardas_vila")
        bandit  = self._spawn("mob", 52, 300, "bandidos")
        far_foe = self._spawn("mob", 61, 300, "bandidos")   # 10 tiles do guarda — hostil, mas longe

        run_ticks(self.ws, 60)   # guarda engaja o bandido adjacente
        guard_ai = self.ws.world.get_component(guard, AIControlled)
        self.assertEqual(guard_ai.target_eid, bandit, "pré-condição: guarda vs bandido")

        bandit_cs = self.ws.world.get_component(bandit, CombatStats)
        bandit_cs.current_hp = 0
        run_ticks(self.ws, 60)

        self.assertNotEqual(guard_ai.target_eid, far_foe,
                            "hostil a 10 tiles não pode ser adquirido no handoff pós-morte")
        self.assertIn(guard_ai.state, ("IDLE", "RETURNING"),
                      "sem alvo dentro do raio, o guarda deve resetar e voltar pra casa")

    def test_mob_neutro_revida_ataque_melee_ponta_a_ponta(self):
        """Gap real achado implementando o raio de aquisição: o bloco de
        aggro por dano setava estado/aggroed_by_damage mas NUNCA o
        target_eid — a revidada do neutro dependia por acidente do loop
        de aquisição sem filtro (removido em §34.9). Sem o fix (dano põe
        o atacante na threat table: target_eid = attacker), um lobo
        neutro atacado em melee entrava em AGGRO_DELAY por 600ms e
        desistia sem nunca golpear de volta."""
        from engine.components import AIControlled, CombatState, CombatStats
        from tests.helpers import run_ticks, set_entity_tile
        set_entity_tile(self.ws, self.peid, 50, 300)
        wolf = self._spawn("mob", 51, 300, "vida_selvagem")

        # Ataque real do player pelo mesmo caminho do servidor
        cst = self.ws.world.get_component(self.peid, CombatState)
        cst.target_entity_id = wolf
        cst.is_pursuing       = True
        pcs = self.ws.world.get_component(self.peid, CombatStats)
        pcs.acerto = 100.0
        wolf_cs = self.ws.world.get_component(wolf, CombatStats)
        wolf_cs.dodge_rating = 0.0
        wolf_cs.parry_rating = 0.0
        self.ws._attack_timers["s1"] = 0.0
        self.ws._process_player_attacks(0.05, {wolf: wolf_cs.current_hp})

        ai = self.ws.world.get_component(wolf, AIControlled)
        self.assertEqual(ai.target_eid, self.peid,
                         "dano deve pôr o atacante como alvo (threat table)")

        # Bem além dos 600ms de grace: o lobo tem que CONTINUAR engajado
        # no player (retenção), não desistir e voltar pra casa.
        run_ticks(self.ws, 60)
        self.assertEqual(ai.target_eid, self.peid,
                         "lobo neutro deve continuar revidando após a grace de 600ms")
        self.assertIn(ai.state, ("AGGRO_DELAY", "CHASING", "ATTACKING"),
                      "lobo neutro deveria estar em combate com quem o atacou")

    def test_dano_mob_vs_mob_gera_combat_result_broadcast(self):
        """Sintoma "frente a frente sem desferir dano": o dano acontecia
        server-side mas nunca era transmitido — agora todo hit mob-vs-mob
        entra no canal de COMBAT_RESULT (via _log_mob_damage_hit)."""
        from tests.helpers import run_ticks
        guard  = self._spawn("npc", 51, 300, "guardas_vila")
        bandit = self._spawn("mob", 52, 300, "bandidos")

        deltas = run_ticks(self.ws, 200)

        pair = {guard, bandit}
        mvm_hits = [c for c in deltas["combat"]
                    if c.get("attacker") in pair and c.get("target") in pair
                    and c.get("damage", 0) > 0]
        self.assertTrue(mvm_hits,
                        "hits mob-vs-mob deveriam gerar COMBAT_RESULT no delta")
        self.assertGreaterEqual(mvm_hits[0].get("hp_after", -1), 0,
                                "COMBAT_RESULT mob-vs-mob deve carregar hp_after pro sync de HP")


class TestPvpContext(unittest.TestCase):
    """Contexto PvP plugável (decisão do usuário 16/07/2026: players são
    TODOS amigáveis por default — PvP só existe em contexto explícito:
    duelo aceito, zona PvP, arena/campo de batalha). O "PvP de mundo
    aberto" via flag global foi descontinuado de propósito; pvp_enabled
    virou só kill-switch de emergência (desliga TODO contexto).

    Também é a fundação MOBA: players com Faction de time sobrescrevem o
    default "jogadores" (resolução componente-primeiro) — inter-times
    briga por regra de facção pura, mesmo time fica protegido de fogo
    amigo sempre."""

    def setUp(self):
        from tests.helpers import make_world_server, spawn_player
        self.ws = make_world_server()
        self.a = spawn_player(self.ws, "s1", 130, 374)
        self.b = spawn_player(self.ws, "s2", 131, 374)

    def _hit(self, attacker, target) -> int:
        from engine.components import CombatStats
        from engine.world_systems import deal_damage
        cs = self.ws.world.get_component(target, CombatStats)
        hp_before = cs.current_hp
        deal_damage(attacker, target, "physical", pre_outcome="hit")
        return hp_before - cs.current_hp

    def test_players_amigaveis_por_default_nao_se_atacam(self):
        """A inversão de semântica (16/07/2026): sem contexto (nenhum
        duelo ativo), players NÃO podem se ferir — mesmo com o
        kill-switch pvp_enabled em True."""
        self.assertTrue(self.ws.pvp_enabled)
        self.assertEqual(len(self.ws._duel_pairs), 0, "pré-condição: nenhum duelo ativo")
        self.assertEqual(self._hit(self.a, self.b), 0,
                         "players sem contexto PvP deveriam ser amigáveis (dano 0)")

    def test_par_em_duelo_pode_se_atacar(self):
        """O primeiro contexto real: par presente em _duel_pairs libera o
        dano mútuo — e SÓ pra esse par."""
        self.ws._duel_pairs[frozenset((self.a, self.b))] = {}
        try:
            self.assertGreater(self._hit(self.a, self.b), 0,
                               "par em duelo deveria poder se ferir")
            self.assertGreater(self._hit(self.b, self.a), 0,
                               "duelo é mútuo — os dois lados atacam")
        finally:
            self.ws._duel_pairs.clear()

    def test_kill_switch_desliga_todo_contexto(self):
        self.ws._duel_pairs[frozenset((self.a, self.b))] = {}
        self.ws.pvp_enabled = False
        try:
            self.assertEqual(self._hit(self.a, self.b), 0,
                             "pvp_enabled=False é kill-switch: nem duelo libera dano")
        finally:
            self.ws.pvp_enabled = True
            self.ws._duel_pairs.clear()

    def test_times_diferentes_brigam_por_faccao_sem_depender_do_contexto(self):
        """Fundação MOBA: Faction de time no player sobrescreve
        "jogadores"; times distintos brigam por regra de facção pura —
        nem passa pelo contexto (relação não é amigavel)."""
        from engine.components import Faction
        self.ws.world.add_component(self.a, Faction(faction_id="time_a"))
        self.ws.world.add_component(self.b, Faction(faction_id="time_b"))
        self.assertGreater(self._hit(self.a, self.b), 0,
                           "times distintos deveriam brigar por facção, sem contexto PvP")

    def test_mesmo_time_protegido_de_fogo_amigo(self):
        """Fogo amigo de time: companheiros de time_a nunca se ferem —
        o contexto de duelo é o único que liberaria, e não há duelo."""
        from engine.components import Faction
        self.ws.world.add_component(self.a, Faction(faction_id="time_a"))
        self.ws.world.add_component(self.b, Faction(faction_id="time_a"))
        self.assertEqual(self._hit(self.a, self.b), 0,
                         "fogo amigo entre companheiros de time deveria ser bloqueado")

    def test_contexto_nunca_libera_mob_contra_amigavel(self):
        """O contexto PvP só se aplica entre DOIS PLAYERS — um mob nunca
        ganha permissão contra alvo amigável por contexto."""
        from engine.entity_factory import create_combat_npc
        from engine.components import CombatState, CombatStats
        from engine.world_systems import deal_damage
        guard = create_combat_npc(self.ws.world, 132, 374, faction="guardas_vila")
        self.ws.world.add_component(guard, CombatState())
        guard_cs = self.ws.world.get_component(guard, CombatStats)
        hp_before = guard_cs.current_hp

        deal_damage(self.a, guard, "physical", pre_outcome="hit")

        self.assertEqual(guard_cs.current_hp, hp_before,
                         "player vs NPC amigável continua bloqueado — contexto é só player-vs-player")

    def test_remote_controlled_resolve_como_jogadores(self):
        """Client-side: proxy de player remoto (RemoteControlled, sem
        PlayerControlled/Faction) resolve pra facção "jogadores" —
        amigável ao player local por default. Sem isso, o proxy caía no
        sentinela sem-facção (neutro → atacável) e clique direito/SPACE
        iniciavam ataque contra qualquer player."""
        from engine.world import World
        from engine.components import RemoteControlled, PlayerControlled
        from engine.faction_system import get_entity_faction, can_engage
        from content.faction_data import PLAYER_FACTION

        w = World()
        me = w.create_entity(); w.add_component(me, PlayerControlled())
        remote = w.create_entity(); w.add_component(remote, RemoteControlled())

        self.assertEqual(get_entity_faction(w, remote), PLAYER_FACTION)
        # Mundo de CLIENTE: pode haver resolver de contexto registrado por
        # um WorldServer de outro teste (global) — mas ele consulta
        # _duel_pairs, vazio ⇒ bloqueado. O que importa: não atacável.
        self.assertFalse(can_engage(w, me, remote),
                         "player remoto deveria ser amigável (inatacável) por default no cliente")


if __name__ == "__main__":
    unittest.main()
