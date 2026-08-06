"""
tests/test_instance_progression.py
Progressão normalizada de instância (31/07/2026, base pro futuro modo
Battlefield) — ver server/instance_progression.py e
arquitetura/ARQUITETURA_ONLINE.md. Sistema INERTE nesta fase (nenhum
processador de jogo chama enter_/exit_normalized_progression ainda), então
os testes aqui exercitam o módulo diretamente, sem passar por Arena/match.
"""
import unittest
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.helpers import make_world_server, spawn_player, enter_instance_progression, run_ticks

from engine.components import (
    CharacterStats, CombatStats, TalentTree, PlayerSkills, Wallet, Inventory,
    Equipment, InstanceProgressionSnapshot, Item, PermanentStats,
)
from engine.world_systems import is_skill_authorized
from server.instance_progression import (
    enter_normalized_progression, exit_normalized_progression,
    is_in_normalized_progression, grant_instance_xp,
    INSTANCE_LEVEL_CAP,
    INSTANCE_INVENTORY_SLOTS, INSTANCE_STARTING_GOLD,
)
from content.skill_config import INSTANCE_SKILL_UNLOCK_ORDER, SKILL_CATALOG
from content.instance_shop import INSTANCE_SHOP_ITEM_IDS
from content.item_table import ITEMS
from content.talent_data import TALENTS, CLASS_BUILD_MAP
from engine.stats_system import CLASS_BASE_STATS


class TestInstanceProgressionData(unittest.TestCase):
    """Validação dos dados novos — ids referenciados existem nos catálogos
    reais e pertencem à classe certa."""

    def test_instance_skill_unlock_order_ids_existem_e_sao_da_classe_certa(self):
        for class_id, order in INSTANCE_SKILL_UNLOCK_ORDER.items():
            self.assertGreater(len(order), 0, f"{class_id} sem skills")
            for sid in order:
                self.assertIn(sid, SKILL_CATALOG, f"{sid} não existe em SKILL_CATALOG")
                self.assertEqual(SKILL_CATALOG[sid]["class_id"], class_id,
                                  f"{sid} não é da classe {class_id}")

    def test_instance_shop_item_ids_existem_no_catalogo_real(self):
        self.assertGreater(len(INSTANCE_SHOP_ITEM_IDS), 0)
        for iid in INSTANCE_SHOP_ITEM_IDS:
            self.assertIn(iid, ITEMS, f"{iid} não existe em content/item_table.py::ITEMS")


class TestInstanceProgressionRoundTrip(unittest.TestCase):
    """Entra/sai da progressão normalizada — estado real precisa ser
    restaurado EXATAMENTE (mesma identidade de objeto onde aplicável)."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        # Simula um personagem real com progresso significativo, pra provar
        # que a restauração não é um no-op trivial (level1 -> level1).
        self.char = self.ws.world.get_component(self.eid, CharacterStats)
        self.tt = self.ws.world.get_component(self.eid, TalentTree)
        self.ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.wallet = self.ws.world.get_component(self.eid, Wallet)
        self.inv = self.ws.world.get_component(self.eid, Inventory)
        self.equip = self.ws.world.get_component(self.eid, Equipment)

        self.char.level = 20
        self.char.current_xp = 321
        self.char.xp_to_next_level = CharacterStats.xp_for_level(20)
        self.char.strength = 40
        self.char.intelligence = 5
        self.char.agility = 12
        self.char.vitality = 55
        self.char.defense = 30
        self.tt.allocated = {"cav_reflexos": 2}
        self.tt.available_points = 7
        self.ps.learned_skill_ids = {"golpe_poderoso", "impacto"}
        self.wallet.gold = 500
        self.inv.items = [Item("Espada de treinamento", "weapon", "mainhand")]

    def test_enter_reseta_level_atributos_e_overlay(self):
        enter_normalized_progression(self.ws, self.eid)

        self.assertTrue(is_in_normalized_progression(self.ws, self.eid))
        self.assertEqual(self.char.level, 1)
        self.assertEqual(self.char.current_xp, 0)
        self.assertEqual(self.char.xp_to_next_level, CharacterStats.xp_for_level(1))
        base = CLASS_BASE_STATS["guerreiro"]
        self.assertEqual(self.char.strength, base["strength"])
        self.assertEqual(self.char.intelligence, base["intelligence"])
        self.assertEqual(self.char.agility, base["agility"])
        self.assertEqual(self.char.vitality, base["vitality"])
        self.assertEqual(self.char.defense, base["defense"])

        new_tt = self.ws.world.get_component(self.eid, TalentTree)
        self.assertIsNot(new_tt, self.tt)
        # Talentos vêm PRÉ-ALOCADOS no máximo (02/08/2026, redesign — ver
        # TestInstanceProgressionTalentAutoMax pro teste dedicado).
        expected_allocated = {tid: t["max_points"] for tid, t in TALENTS.items()
                              if t.get("build") == "cavaleiro"}
        self.assertEqual(new_tt.allocated, expected_allocated)
        self.assertEqual(new_tt.available_points, 0)
        self.assertEqual(new_tt.chosen_build, "cavaleiro")

        new_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertIsNot(new_ps, self.ps)
        self.assertEqual(new_ps.learned_skill_ids,
                          {INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"][0]})

        new_wallet = self.ws.world.get_component(self.eid, Wallet)
        self.assertEqual(new_wallet.gold, INSTANCE_STARTING_GOLD)

        new_inv = self.ws.world.get_component(self.eid, Inventory)
        self.assertEqual(new_inv.items, [])
        self.assertEqual(new_inv.max_slots, INSTANCE_INVENTORY_SLOTS)

        new_equip = self.ws.world.get_component(self.eid, Equipment)
        self.assertIsNot(new_equip, self.equip)
        self.assertTrue(all(v is None for v in new_equip.slots.values()))

    def test_exit_restaura_estado_real_exatamente(self):
        enter_normalized_progression(self.ws, self.eid)
        # Muta o overlay de instância, pra provar que é descartado (não mesclado).
        inst_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        inst_ps.learned_skill_ids.add("interceptar")
        inst_wallet = self.ws.world.get_component(self.eid, Wallet)
        inst_wallet.gold = 9999

        exit_normalized_progression(self.ws, self.eid)

        self.assertFalse(is_in_normalized_progression(self.ws, self.eid))
        self.assertIsNone(self.ws.world.get_component(self.eid, InstanceProgressionSnapshot))
        self.assertEqual(self.char.level, 20)
        self.assertEqual(self.char.current_xp, 321)
        self.assertEqual(self.char.strength, 40)
        self.assertEqual(self.char.intelligence, 5)
        self.assertEqual(self.char.agility, 12)
        self.assertEqual(self.char.vitality, 55)
        self.assertEqual(self.char.defense, 30)

        self.assertIs(self.ws.world.get_component(self.eid, TalentTree), self.tt)
        self.assertEqual(self.tt.allocated, {"cav_reflexos": 2})
        self.assertEqual(self.tt.available_points, 7)

        self.assertIs(self.ws.world.get_component(self.eid, PlayerSkills), self.ps)
        self.assertEqual(self.ps.learned_skill_ids, {"golpe_poderoso", "impacto"})

        self.assertIs(self.ws.world.get_component(self.eid, Wallet), self.wallet)
        self.assertEqual(self.wallet.gold, 500)

        self.assertIs(self.ws.world.get_component(self.eid, Inventory), self.inv)
        self.assertEqual(len(self.inv.items), 1)

        self.assertIs(self.ws.world.get_component(self.eid, Equipment), self.equip)

    def test_exit_e_idempotente_sem_entrar(self):
        eid2 = spawn_player(self.ws, "p2", 12, 12)
        try:
            exit_normalized_progression(self.ws, eid2)
        except Exception as e:
            self.fail(f"exit_normalized_progression não deveria lançar: {e}")
        self.assertFalse(is_in_normalized_progression(self.ws, eid2))


class TestInstanceProgressionPermanentStatsSwap(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): HP máximo dentro da
    instância batia com o valor REAL do personagem, não com o piso de
    level 1. Causa raiz: `PermanentStats` (bônus legado de uma mecânica
    roguelike desativada — nada incrementa mais, mas valores ANTIGOS
    continuam salvos em personagens estabelecidos) nunca era trocado por
    um zerado durante a instância, então `apply_char_stats_to_combat`
    (chamada por `_apply_talent_modifiers` dentro de enter/exit) somava
    esse bônus legado em cima dos atributos já resetados pro piso de
    level 1."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="arqueiro")
        self.char = self.ws.world.get_component(self.eid, CharacterStats)
        self.cs = self.ws.world.get_component(self.eid, CombatStats)
        self.perm = self.ws.world.get_component(self.eid, PermanentStats)
        # Simula personagem estabelecido com bônus legado de PermanentStats
        # (de antes da mecânica ser desativada) + level/vitality reais altos.
        self.char.level = 25
        self.char.vitality = 25
        self.perm.vitality = 25
        from engine.stats_system import apply_char_stats_to_combat
        apply_char_stats_to_combat(self.char, self.cs, self.perm)
        self.real_max_hp = self.cs.max_hp  # bem inflado pelo bônus legado

    def test_enter_nao_vaza_bonus_legado_de_permanent_stats_no_max_hp(self):
        from engine.stats_system import CLASS_BASE_HP, CLASS_BASE_STATS
        enter_normalized_progression(self.ws, self.eid)

        new_perm = self.ws.world.get_component(self.eid, PermanentStats)
        self.assertIsNot(new_perm, self.perm)
        self.assertEqual(new_perm.vitality, 0)

        # Valor EXATO esperado com PermanentStats zerado — assertLess sozinho
        # não pega a regressão (mesmo SEM zerar o perm, resetar char.vitality
        # pro piso de level 1 já reduz max_hp o bastante pra passar num
        # "assertLess" contra o valor real inflado; só o valor exato prova
        # que o bônus legado (perm.vitality=25) não entrou na conta).
        base_vit = CLASS_BASE_STATS["arqueiro"]["vitality"]
        expected_max_hp = CLASS_BASE_HP["arqueiro"] + base_vit * 10
        self.assertEqual(self.cs.max_hp, expected_max_hp)
        self.assertLess(self.cs.max_hp, self.real_max_hp)

    def test_exit_restaura_permanent_stats_real_e_max_hp_real(self):
        enter_normalized_progression(self.ws, self.eid)
        exit_normalized_progression(self.ws, self.eid)

        self.assertIs(self.ws.world.get_component(self.eid, PermanentStats), self.perm)
        self.assertEqual(self.perm.vitality, 25)
        self.assertEqual(self.cs.max_hp, self.real_max_hp)


class TestInstanceProgressionLevelUp(unittest.TestCase):
    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        enter_normalized_progression(self.ws, self.eid)
        self.char = self.ws.world.get_component(self.eid, CharacterStats)
        self.tt = self.ws.world.get_component(self.eid, TalentTree)
        self.ps = self.ws.world.get_component(self.eid, PlayerSkills)

    def test_level1_ja_concede_primeira_skill(self):
        self.assertEqual(self.char.level, 1)
        self.assertIn(INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"][0], self.ps.learned_skill_ids)

    def test_xp_grande_caps_no_level_15_sem_overflow(self):
        grant_instance_xp(self.ws, self.eid, 10_000_000)
        self.assertEqual(self.char.level, INSTANCE_LEVEL_CAP)
        self.assertEqual(self.char.current_xp, 0)
        # Talento não ganha pontos por level (02/08/2026, redesign — já vem
        # pré-alocado no máximo desde a entrada, nunca muda depois).
        self.assertEqual(self.tt.available_points, 0)

    def test_xp_alem_do_cap_e_descartado_sem_grant_extra(self):
        grant_instance_xp(self.ws, self.eid, 10_000_000)
        pts_no_cap = self.tt.available_points
        grant_instance_xp(self.ws, self.eid, 5000)  # não deve fazer nada além do cap
        self.assertEqual(self.char.level, INSTANCE_LEVEL_CAP)
        self.assertEqual(self.tt.available_points, pts_no_cap)

    def test_todas_as_skills_do_unlock_order_sao_concedidas_ate_o_cap(self):
        grant_instance_xp(self.ws, self.eid, 10_000_000)
        order = INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"]
        for sid in order:
            self.assertIn(sid, self.ps.learned_skill_ids, f"{sid} deveria ter sido concedida")

    def test_levelup_cura_current_hp_pro_novo_maximo(self):
        """Bug real relatado pelo usuário (02/08/2026): "a cada level que o
        player ganha, em vez de subir o current_hp junto com o max_hp, só
        sobe max_hp" — mesmo comportamento que engine/stats_system.py::
        process_levelups já tem no jogo real (`cs.current_hp = cs.max_hp`),
        que este módulo nunca replicava."""
        cs = self.ws.world.get_component(self.eid, CombatStats)
        cs.current_hp = 1
        hp_max_antes = cs.max_hp
        grant_instance_xp(self.ws, self.eid, 10_000_000)  # sobe até o cap
        self.assertGreater(cs.max_hp, hp_max_antes, "level-up deveria ter aumentado o max_hp")
        self.assertEqual(cs.current_hp, cs.max_hp)


class TestInstanceProgressionHpHeal(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): current_hp real
    (ex.: 300/340) só era CLAMPADO contra o novo max_hp baixo da
    instância (`recalculate_combat_stats`), nunca curado ao cheio — e ao
    SAIR, o HP da instância nunca era restaurado/curado no personagem
    real. Cura 100% em enter/exit (confirmado com o usuário)."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.cs = self.ws.world.get_component(self.eid, CombatStats)
        self.cs.current_hp = max(1, self.cs.max_hp - 40)  # personagem "ferido" antes de entrar

    def test_enter_cura_100_por_cento(self):
        enter_normalized_progression(self.ws, self.eid)
        self.assertEqual(self.cs.current_hp, self.cs.max_hp)

    def test_exit_cura_100_por_cento(self):
        enter_normalized_progression(self.ws, self.eid)
        self.cs.current_hp = 1  # simula dano sofrido durante a partida
        exit_normalized_progression(self.ws, self.eid)
        self.assertEqual(self.cs.current_hp, self.cs.max_hp)


class TestInstanceProgressionSkillGate(unittest.TestCase):
    """is_skill_authorized precisa funcionar contra os componentes trocados,
    sem NENHUMA mudança na função em si."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        enter_normalized_progression(self.ws, self.eid)

    def test_skill_concedida_no_level_1_autoriza(self):
        sid = INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"][0]
        ok, _ = is_skill_authorized(self.ws.world, self.eid, sid)
        self.assertTrue(ok)

    def test_skill_ainda_nao_desbloqueada_nao_autoriza(self):
        # "executar" é o último da ordem de guerreiro — não desbloqueado no level 1.
        ok, _ = is_skill_authorized(self.ws.world, self.eid, "executar")
        self.assertFalse(ok)

    def test_skill_desbloqueada_por_talento_autoriza_apos_grant(self):
        """punho_no_queixo é talento-gated no jogo real — is_skill_authorized
        pra ela ignora learned_skill_ids e só olha TalentTree.allocated.
        Continua autorizando até o cap (a árvore já vem pré-maxada desde a
        entrada, 02/08/2026 — ver test_skill_talento_gated_ja_autoriza_no_
        level_1 pro caso que muda de verdade com o redesign)."""
        grant_instance_xp(self.ws, self.eid, 10_000_000)  # sobe até o cap, concede tudo
        ok, reason = is_skill_authorized(self.ws.world, self.eid, "punho_no_queixo")
        self.assertTrue(ok, reason)

    def test_skill_talento_gated_ja_autoriza_no_level_1(self):
        """Redesign 02/08/2026 (pedido do usuário): talentos vêm PRÉ-
        ALOCADOS no máximo desde `enter_normalized_progression` — uma
        skill talento-gated (que `is_skill_authorized` autoriza olhando só
        `TalentTree.allocated`, nunca `learned_skill_ids`) já autoriza de
        verdade no level 1, sem precisar de nenhum level-up. A REVELAÇÃO
        na hotbar continua obedecendo INSTANCE_SKILL_UNLOCK_ORDER — só a
        AUTORIZAÇÃO (uma checagem server-side de "pode executar", não de
        "está na hotbar") muda."""
        ok, reason = is_skill_authorized(self.ws.world, self.eid, "punho_no_queixo")
        self.assertTrue(ok, reason)


class TestInstanceProgressionTalentAutoMax(unittest.TestCase):
    """Redesign 02/08/2026 (pedido do usuário): substitui a antiga
    TestInstanceProgressionTalentSymmetry — não existe mais alocação
    manual de ponto de talento dentro da instância (available_points
    sempre 0), a árvore inteira do build da classe já vem pré-alocada no
    máximo desde `enter_normalized_progression`. Prova que os efeitos de
    verdade aplicam (não é só cosmético/UI) e revertem exatamente ao
    sair, sem vazar modifier em nenhuma direção."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.cs = self.ws.world.get_component(self.eid, CombatStats)
        self.parry_antes = self.cs.parry_rating

    def test_enter_aloca_todos_os_talentos_do_build_no_maximo(self):
        enter_normalized_progression(self.ws, self.eid)
        tt = self.ws.world.get_component(self.eid, TalentTree)
        build = CLASS_BUILD_MAP["guerreiro"]
        expected = {tid: t["max_points"] for tid, t in TALENTS.items()
                   if t.get("build") == build}
        self.assertEqual(tt.allocated, expected)
        self.assertEqual(tt.available_points, 0)

    def test_stat_do_talento_maximo_ja_aplica_de_verdade_ao_entrar(self):
        """cav_reflexos (parry_rating +20/ponto, max_points=3) — prova que
        _apply_talent_modifiers já roda com a árvore MAXADA (não vazia)
        dentro do próprio enter_normalized_progression, sem precisar de
        nenhuma chamada manual extra."""
        enter_normalized_progression(self.ws, self.eid)
        max_pts = TALENTS["cav_reflexos"]["max_points"]
        self.assertEqual(self.cs.parry_rating, self.parry_antes + 20.0 * max_pts)

    def test_sair_reverte_parry_rating_exatamente(self):
        enter_normalized_progression(self.ws, self.eid)
        exit_normalized_progression(self.ws, self.eid)
        self.assertEqual(self.cs.parry_rating, self.parry_antes)


class TestInstanceProgressionClientSync(unittest.TestCase):
    """Bug real relatado pelo usuário (01/08/2026): "o level do Player não
    está aparecendo 1" no client — enter_/exit_normalized_progression
    mutava CharacterStats.level no servidor mas nunca avisava o dono via
    STATS_UPDATE (queue_stats_update não tinha campo "level" nenhum;
    client não tinha código pra aplicar "level" na própria CharacterStats
    de qualquer forma). Corrigido: `level` novo no schema de
    queue_stats_update + _push_stats_update chamado em enter/exit."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        char = self.ws.world.get_component(self.eid, CharacterStats)
        char.level = 23

    def test_enter_empurra_level_1_pro_dono(self):
        self.ws._pending_stats_updates.clear()
        enter_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        self.assertTrue(any(u.get("player_eid") == self.eid and u.get("level") == 1
                            for u in updates))

    def test_exit_empurra_level_real_pro_dono(self):
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        exit_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        self.assertTrue(any(u.get("player_eid") == self.eid and u.get("level") == 23
                            for u in updates))


class TestInstanceProgressionTalentSync(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): "a cada level que o
    personagem ganha os pontos de talento parecem acumular, mesmo eu
    usando" — causa raiz: `TalentTree.allocated` do cliente nunca era
    sincronizado ao entrar/sair da instância (gap documentado em
    _push_stats_update, mesma classe do bug de Inventory/Equipment já
    corrigido). Corrigido: `talent_allocated` novo no payload de
    STATS_UPDATE."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.tt = self.ws.world.get_component(self.eid, TalentTree)
        self.tt.allocated = {"cav_reflexos": 2}
        self.tt.available_points = 1

    def test_enter_empurra_allocated_maxado_pro_dono(self):
        """Redesign 02/08/2026: não é mais vazio — vem pré-alocado no
        máximo (ver TestInstanceProgressionTalentAutoMax)."""
        self.ws._pending_stats_updates.clear()
        enter_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates
                     if u.get("player_eid") == self.eid and "talent_allocated" in u)
        build = CLASS_BUILD_MAP["guerreiro"]
        expected = {tid: t["max_points"] for tid, t in TALENTS.items()
                   if t.get("build") == build}
        self.assertEqual(entry["talent_allocated"], expected)

    def test_exit_empurra_allocated_real_pro_dono(self):
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        exit_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates
                     if u.get("player_eid") == self.eid and "talent_allocated" in u)
        self.assertEqual(entry["talent_allocated"], {"cav_reflexos": 2})


class TestInstanceProgressionSkillsHotbarSync(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): "ainda aparece todas
    as skills na barra de ações... deveria aparecer as habilidades
    conforme ele sobe level" — causa raiz: `PlayerSkills` do cliente nunca
    era sincronizado ao entrar/sair da instância (mesmo gap já fechado
    pra Inventory/Equipment/TalentTree). Corrigido: `skills_hotbar`/
    `learned_skill_ids` novos no payload de STATS_UPDATE."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.ps = self.ws.world.get_component(self.eid, PlayerSkills)
        # "executar" e "vitoria_iminente" — de propósito NÃO são a 1ª
        # skill da ordem de desbloqueio de instância (golpe_poderoso),
        # pra provar que a real não vaza pra hotbar da instância.
        self.ps.learned_skill_ids = {"executar", "vitoria_iminente"}

    def test_enter_empurra_hotbar_so_com_a_1a_skill_de_instancia(self):
        self.ws._pending_stats_updates.clear()
        enter_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates
                     if u.get("player_eid") == self.eid and "skills_hotbar" in u)
        primeira = INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"][0]
        self.assertEqual(entry["learned_skill_ids"], [primeira])
        self.assertIn(primeira, entry["skills_hotbar"])
        self.assertNotIn("executar", entry["skills_hotbar"])
        self.assertNotIn("vitoria_iminente", entry["skills_hotbar"])

    def test_exit_empurra_learned_skill_ids_real_pro_dono(self):
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        exit_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates
                     if u.get("player_eid") == self.eid and "learned_skill_ids" in u)
        self.assertEqual(set(entry["learned_skill_ids"]), {"executar", "vitoria_iminente"})

    def test_levelup_de_instancia_empurra_a_skill_nova(self):
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        grant_instance_xp(self.ws, self.eid, 10_000_000)
        updates = self.ws._pending_stats_updates
        order = INSTANCE_SKILL_UNLOCK_ORDER["guerreiro"]
        entry = next(u for u in reversed(updates) if "skills_hotbar" in u)
        for sid in order:
            self.assertIn(sid, entry["learned_skill_ids"])


class TestInstanceProgressionInventorySync(unittest.TestCase):
    """Bug real relatado pelo usuário (01/08/2026): "comprei um anel mas
    não consegui equipar" — causa raiz mais profunda que level_requirement
    (já corrigido): o client nunca ficava sabendo que Inventory/Equipment
    foram trocados no servidor ao entrar/sair da instância (gap documentado
    em _push_stats_update). Corrigido: inv_snapshot/inv_max_slots/
    equip_snapshot/in_instance novos no payload de STATS_UPDATE, emitidos
    nos dois pontos de troca real (add_component)."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        self.inv = self.ws.world.get_component(self.eid, Inventory)
        self.inv.items = [Item("Espada de treinamento", "weapon", "mainhand")]
        self.equip = self.ws.world.get_component(self.eid, Equipment)
        self.equip.slots["mainhand"] = Item("Machado Real", "weapon", "mainhand")

    def test_enter_empurra_inventario_vazio_de_6_slots_e_in_instance_true(self):
        self.ws._pending_stats_updates.clear()
        enter_normalized_progression(self.ws, self.eid)
        updates = [u for u in self.ws._pending_stats_updates if u.get("player_eid") == self.eid]
        self.assertTrue(any(u.get("in_instance") is True for u in updates))
        entry = next(u for u in updates if "inv_snapshot" in u)
        self.assertEqual(entry["inv_snapshot"], [])
        self.assertEqual(entry["inv_max_slots"], INSTANCE_INVENTORY_SLOTS)
        self.assertEqual(entry["equip_snapshot"], {slot: None for slot in Equipment.SLOT_LABELS})

    def test_exit_empurra_inventario_real_restaurado_e_in_instance_false(self):
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        exit_normalized_progression(self.ws, self.eid)
        updates = [u for u in self.ws._pending_stats_updates if u.get("player_eid") == self.eid]
        self.assertTrue(any(u.get("in_instance") is False for u in updates))
        entry = next(u for u in updates if "inv_snapshot" in u)
        self.assertEqual(len(entry["inv_snapshot"]), 1)
        self.assertEqual(entry["inv_snapshot"][0]["name"], "Espada de treinamento")
        self.assertEqual(entry["equip_snapshot"]["mainhand"]["name"], "Machado Real")

    def test_enter_nunca_vaza_o_item_real_no_snapshot(self):
        """Prova diferencial do bug relatado: se enter_normalized_progression
        passasse o Inventory REAL (antigo) pra _push_stats_update em vez do
        novo (recém-anexado via add_component), o item real vazaria aqui —
        o usuário veria a bag "errada" na instância."""
        self.ws._pending_stats_updates.clear()
        enter_normalized_progression(self.ws, self.eid)
        entry = next(u for u in self.ws._pending_stats_updates
                     if u.get("player_eid") == self.eid and "inv_snapshot" in u)
        nomes = [it["name"] for it in entry["inv_snapshot"]]
        self.assertNotIn("Espada de treinamento", nomes)


class TestInstanceReciclagemGoesToBag(unittest.TestCase):
    """Reciclagem (talento que devolve flechas do alvo morto) dentro da
    instância vai DIRETO pra bag do killer, sem precisar lootear o corpse
    (pedido do usuário, 03/08/2026 — battleground de teste é rápido demais
    pra gerenciar loot manual de cada minion morto). Fora da instância,
    comportamento inalterado (cai no corpse, como antes)."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="arqueiro")
        self.cs = self.ws.world.get_component(self.eid, CombatStats)
        self.cs.arrow_recovery_enabled = True

    def _kill_mob_with_arrows_received(self, killer_eid: int) -> dict:
        """Mata o mob via o TICK completo (run_ticks — chama
        _process_loot_drops, que é quem de fato registra o corpse em
        self.ws._corpses a partir de _death_handler.pending_loot; chamar só
        _death_handler.update() nunca cria a entrada). Devolve o corpse
        NOVO desta morte (map_1 real já tem harvestables fixos registrados
        em _corpses desde o load do mapa — pegar por diff de chaves antes/
        depois, nunca por max(), que pode pegar um desses harvestables)."""
        from engine.entity_factory import create_enemy
        from engine.components import PendingDeath
        keys_antes = set(self.ws._corpses.keys())
        mob_eid = create_enemy(self.ws.world, 11, 10, race="Lobo")
        mob_cs = self.ws.world.get_component(mob_eid, CombatStats)
        mob_cs.arrows_received = 10
        mob_cs.current_hp = 0
        self.ws.world.add_component(mob_eid, PendingDeath(killer_entity_id=killer_eid))
        self.ws._mob_damage_log[mob_eid] = {killer_eid: 999}
        run_ticks(self.ws, 1)
        # Filtra por owner_eid==killer (não só chave nova): o primeiro tick
        # real também pode inicializar harvestables preguiçosos do mapa
        # (owner_eid=-1, público) — não é o corpse desta morte.
        novas = [cid for cid in (set(self.ws._corpses.keys()) - keys_antes)
                if self.ws._corpses[cid].get("owner_eid") == killer_eid]
        self.assertEqual(len(novas), 1, "deveria ter registrado exatamente 1 corpse novo do killer")
        return self.ws._corpses[novas[0]]

    def test_dentro_da_instancia_flecha_recuperada_vai_pra_bag(self):
        enter_normalized_progression(self.ws, self.eid)
        inv = self.ws.world.get_component(self.eid, Inventory)
        self.assertEqual(inv.items, [])

        loot = self._kill_mob_with_arrows_received(self.eid)

        self.assertEqual(len(inv.items), 1, "flecha recuperada deveria estar na bag")
        self.assertGreaterEqual(inv.items[0].stack, 5)
        self.assertFalse(any(it.get("item_type") == "ammo" for it in loot["items"]),
                         "corpse não deveria ter a flecha recuperada — já foi pra bag "
                         "(pode ter OUTRO loot normal do mob, isso é esperado)")

    def test_fora_da_instancia_continua_indo_pro_corpse(self):
        inv = self.ws.world.get_component(self.eid, Inventory)
        inv_antes = len(inv.items)

        loot = self._kill_mob_with_arrows_received(self.eid)

        self.assertEqual(len(inv.items), inv_antes,
                         "fora da instância, bag não deveria mudar sozinha")
        self.assertTrue(any(it.get("item_type") == "ammo" for it in loot["items"]),
                        "corpse deveria ter a flecha recuperada (pode ter OUTRO loot "
                        "normal do mob junto, isso é esperado)")


if __name__ == "__main__":
    unittest.main()
