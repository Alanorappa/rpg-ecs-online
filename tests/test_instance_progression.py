"""
tests/test_instance_progression.py
Progressão normalizada de instância (31/07/2026) — ver
server/instance_progression.py e arquitetura/ARQUITETURA_ONLINE.md. ATIVO
desde 04-05/08/2026 via server/bg_queue_processor.py (fila real de BG,
ver PROBLEMAS_ARQUITETURA.md §12/§45) — os testes aqui exercitam o módulo
diretamente (sem passar pela fila real) por isolamento, não porque o
sistema seja teórico.
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
from content.merchant_data import (
    SHOPS, INSTANCE_SHOP_STOCK_BY_CLASS, INSTANCE_SHOP_RARITY_PRICE,
    instance_shop_class_allows,
)
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

    def test_instance_shop_vende_o_catalogo_inteiro(self):
        stock = SHOPS["instance_shop"]["stock"]
        self.assertEqual(len(stock), len(ITEMS))

    def test_instance_shop_preco_bate_com_a_formula_de_raridade(self):
        for entry in SHOPS["instance_shop"]["stock"]:
            item = entry["factory"]()
            if item.item_type == "consumable":
                self.assertEqual(entry["price"], int(item.value), item.name)
            else:
                self.assertEqual(entry["price"], INSTANCE_SHOP_RARITY_PRICE[item.rarity], item.name)

    def test_instance_shop_filtro_de_classe_e_consistente(self):
        for cls, entries in INSTANCE_SHOP_STOCK_BY_CLASS.items():
            self.assertGreater(len(entries), 0, cls)
            for entry in entries:
                item = entry["factory"]()
                self.assertTrue(instance_shop_class_allows(item, cls),
                                 f"{item.name} não deveria estar na lista de {cls}")
        # nenhuma joia/consumível fica de fora de nenhuma classe (regra "geral")
        for entry in SHOPS["instance_shop"]["stock"]:
            item = entry["factory"]()
            if item.item_type in ("jewelry", "consumable"):
                for cls in ("guerreiro", "arqueiro", "mago"):
                    self.assertTrue(instance_shop_class_allows(item, cls), f"{item.name}/{cls}")


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

    def test_levelup_nao_cura_player_morto_esperando_respawn_na_bg(self):
        """Bug real relatado pelo usuário (12/08/2026): dentro da BG, o
        corpo do player morto recuperava HP cheio sozinho (e minions
        passavam a atacar o corpo de novo) — causa raiz era este mesmo
        `cs.current_hp = cs.max_hp` do level-up disparando pra um player
        que já está morto (GhostState.is_dead=True) esperando o timer de
        respawn, porque XP de proximidade (kill de minion perto) continua
        chegando normalmente pra quem está morto — mesmo comportamento de
        MOBA de verdade. current_hp tem que continuar <= 0 (nunca ficar
        um alvo válido de novo pra EnemyAISystem/MinionSystem/torres, que
        checam só current_hp<=0, nunca GhostState.is_dead)."""
        from engine.components import GhostState
        cs = self.ws.world.get_component(self.eid, CombatStats)
        cs.current_hp = 0
        gst = self.ws.world.get_component(self.eid, GhostState)
        gst.is_dead = True
        grant_instance_xp(self.ws, self.eid, 10_000_000)  # sobe até o cap
        self.assertGreater(self.char.level, 1, "level-up deveria ter acontecido mesmo assim")
        self.assertLessEqual(cs.current_hp, 0,
                              "level-up não deveria reviver/curar um player já morto")


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


class TestInstanceProgressionXpGoldClientSync(unittest.TestCase):
    """Bug real relatado pelo usuário (13/08/2026): "a barra de xp está
    mostrando a xp do personagem de fora da instância" — causa raiz: mesma
    classe de gap já corrigida pra level/atributos/talento (ver
    TestInstanceProgressionClientSync acima), só que pra current_xp/
    xp_to_next_level, que nunca entravam no payload de STATS_UPDATE.
    Pedido junto: texto flutuante de XP ganho, som de level-up e texto
    flutuante de gold ganho — todos DENTRO da instância."""

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")
        char = self.ws.world.get_component(self.eid, CharacterStats)
        char.level = 23
        char.current_xp = 999

    def test_enter_empurra_current_xp_zero_pro_dono(self):
        self.ws._pending_stats_updates.clear()
        enter_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates if u.get("player_eid") == self.eid)
        self.assertEqual(entry["current_xp"], 0)
        self.assertEqual(entry["xp_to_next_level"], CharacterStats.xp_for_level(1))

    def test_exit_empurra_current_xp_real_pro_dono(self):
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        exit_normalized_progression(self.ws, self.eid)
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates if u.get("player_eid") == self.eid)
        self.assertEqual(entry["current_xp"], 999)

    def test_grant_xp_sem_levelup_ainda_empurra_update_com_xp_ganho(self):
        """Antes, um ganho de XP que não completava o próximo nível não
        empurrava NADA pro cliente — a barra só "pulava" ao subir de
        nível. Agora todo ganho empurra, com o valor final de current_xp
        E o delta (instance_xp_gained) só pro texto flutuante."""
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        grant_instance_xp(self.ws, self.eid, 5)  # bem menos que o próximo nível
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates if u.get("player_eid") == self.eid)
        self.assertEqual(entry["current_xp"], 5)
        self.assertEqual(entry["instance_xp_gained"], 5)
        self.assertNotIn("instance_leveled_up", entry,
                         "sem level-up, não deveria marcar instance_leveled_up")

    def test_grant_xp_com_levelup_marca_instance_leveled_up(self):
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        grant_instance_xp(self.ws, self.eid, 10_000_000)  # sobe até o cap
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates if u.get("player_eid") == self.eid)
        self.assertTrue(entry.get("instance_leveled_up"))
        self.assertEqual(entry["level"], INSTANCE_LEVEL_CAP)

    def test_grant_gold_inclui_instance_gold_gained(self):
        from server.instance_progression import grant_instance_gold
        enter_normalized_progression(self.ws, self.eid)
        self.ws._pending_stats_updates.clear()
        grant_instance_gold(self.ws, self.eid, 20)
        updates = self.ws._pending_stats_updates
        entry = next(u for u in updates if u.get("player_eid") == self.eid)
        self.assertEqual(entry["instance_gold_gained"], 20)
        self.assertEqual(entry["gold"], INSTANCE_STARTING_GOLD + 20)


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


class TestInstanceReciclagemNoEffect(unittest.TestCase):
    """Reciclagem (talento que devolve flechas do alvo morto) é IGNORADA
    dentro da instância (16/09/2026, decisão explícita do usuário — ver
    PROBLEMAS_ARQUITETURA.md §159, reverte a decisão de 03/08/2026 abaixo).
    A versão anterior mandava a flecha recuperada DIRETO pra bag do killer
    sem lootear corpse — mas nunca notificava o cliente disso, causando
    desync silencioso entre o Inventory do cliente e o do servidor (achado
    investigando o bug real da aljava trocada em BG não equipando no
    servidor). Reciclagem é mecânica de progressão real (farm/roleplay no
    mundo aberto), sem propósito dentro de PvP instanciado — mais simples
    não ter efeito nenhum aqui do que consertar a notificação. Fora da
    instância, comportamento inalterado (cai no corpse, como sempre foi).

    Nome da classe corrigido em §160 — chamava "...GoesToBag" mesmo depois
    do teste ter sido invertido pra provar o oposto (achado da revisão
    cruzada de 3 agentes sobre o commit do §159)."""

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

    def test_enter_zera_reciclagem_mas_mantem_resto_do_build_maxado(self):
        """§160: `enter_normalized_progression` não deve mais depender de
        `arrow_recovery_enabled` já estar True antes de entrar (setUp desta
        classe seta manualmente) — o próprio ato de entrar na instância já
        precisa zerar bardo_reciclagem sozinho, via INSTANCE_DISABLED_TALENTS,
        SEM deixar de maxar o resto do build (prova que a exclusão é
        pontual, não um efeito colateral que zera o build inteiro)."""
        self.cs.arrow_recovery_enabled = False  # estado real antes de qualquer talento
        enter_normalized_progression(self.ws, self.eid)

        tt = self.ws.world.get_component(self.eid, TalentTree)
        self.assertEqual(tt.allocated.get("bardo_reciclagem"), 0,
                         "bardo_reciclagem deveria entrar com 0 pontos dentro da BG")
        self.assertFalse(self.cs.arrow_recovery_enabled,
                         "efeito do talento não deveria ligar com 0 pontos alocados")

        build = CLASS_BUILD_MAP["arqueiro"]
        outros_talentos_do_build = [tid for tid, t in TALENTS.items()
                                    if t.get("build") == build and tid != "bardo_reciclagem"]
        self.assertTrue(outros_talentos_do_build, "sanity check — build deveria ter outros talentos")
        for tid in outros_talentos_do_build:
            self.assertEqual(tt.allocated.get(tid), TALENTS[tid]["max_points"],
                             f"{tid} deveria continuar maxado normalmente dentro da BG")

    def test_dentro_da_instancia_reciclagem_nao_tem_efeito(self):
        enter_normalized_progression(self.ws, self.eid)
        inv = self.ws.world.get_component(self.eid, Inventory)
        self.assertEqual(inv.items, [])

        # "Lobo" não dropa moeda nenhuma por design (content/loot_tables.py)
        # e o item table é por CHANCE (1-4%, não garantido) — sem a flecha
        # reciclada (desligada aqui de propósito, é o que este teste prova),
        # o kill ficaria com items=[]/coins=0 e NENHUM corpse nasceria
        # (02/10/2026, regra nova: sem loot nenhum, sem corpse) — o que
        # tornaria a asserção abaixo trivial/sem sentido (não existe corpse
        # pra conferir que a flecha não está nele). Garante 1 moeda
        # determinística só pra ESTE teste existir um corpse de verdade
        # pra inspecionar.
        import unittest.mock as _mock
        with _mock.patch("content.loot_tables.roll_mob_coins", return_value=1):
            loot = self._kill_mob_with_arrows_received(self.eid)

        self.assertEqual(inv.items, [],
                         "Reciclagem não deveria ter efeito nenhum dentro da instância "
                         "— nem bag, nem corpse (bag do servidor não pode crescer sem o "
                         "cliente saber, causa raiz do bug da aljava trocada em BG)")
        self.assertFalse(any(it.get("item_type") == "ammo" for it in loot["items"]),
                         "corpse também não deveria ter a flecha recuperada dentro da "
                         "instância — o talento é ignorado por completo aqui, não "
                         "redirecionado pra outro destino")

    def test_fora_da_instancia_continua_indo_pro_corpse(self):
        inv = self.ws.world.get_component(self.eid, Inventory)
        inv_antes = len(inv.items)

        loot = self._kill_mob_with_arrows_received(self.eid)

        self.assertEqual(len(inv.items), inv_antes,
                         "fora da instância, bag não deveria mudar sozinha")
        self.assertTrue(any(it.get("item_type") == "ammo" for it in loot["items"]),
                        "corpse deveria ter a flecha recuperada (pode ter OUTRO loot "
                        "normal do mob junto, isso é esperado)")

    def test_flecha_reciclada_tem_item_id_real_e_e_sacavel(self):
        """Fase 4.7 (12/08/2026, ver PROBLEMAS_ARQUITETURA.md §39) — bug
        real relatado pelo usuário: flecha da Reciclagem às vezes não
        aparecia no loot, outras vezes aparecia mas saqueá-la dava "Já
        foi saqueado". Causa: `server_death_handler.py` montava o Item
        à mão, sem `item_id` (ficava ""). Corrigido: resolve pelo
        catálogo (`content.item_table.resolve_item_by_name`), então tem
        `item_id` real igual a qualquer outro loot. Prova ponta a ponta:
        item_id não-vazio E `request_loot` consegue sacá-la de verdade
        por esse id (não só "não está mais vazio")."""
        from engine.components import Inventory as _InvArr
        corpse_id = None
        keys_antes = set(self.ws._corpses.keys())
        loot = self._kill_mob_with_arrows_received(self.eid)
        novas = set(self.ws._corpses.keys()) - keys_antes
        # _kill_mob_with_arrows_received já validou que há exatamente 1
        # corpse novo do killer — reencontra o id (o dict devolvido não
        # inclui a própria chave).
        for cid in novas:
            if self.ws._corpses[cid] is loot:
                corpse_id = cid
                break
        self.assertIsNotNone(corpse_id, "não achou o corpse_id do loot devolvido")

        arrow = next((it for it in loot["items"] if it.get("item_type") == "ammo"), None)
        self.assertIsNotNone(arrow, "deveria ter uma flecha reciclada no corpse")
        self.assertTrue(arrow.get("item_id"),
                        "flecha reciclada deveria ter item_id real (não vazio) — "
                        "sem isso o cliente nunca reconstrói/saqueia certo")

        result = self.ws.request_loot("p1", corpse_id, take="item", item_id=arrow["item_id"])
        self.assertIsNotNone(result, "dono do corpse deveria conseguir sacar")
        self.assertTrue(result["items"], "deveria ter concedido a flecha de verdade, "
                                         "não devolver vazio (\"Já foi saqueado\")")
        self.assertEqual(result["items"][0]["item_id"], arrow["item_id"])


class TestInstanceShoppingClientServerAgreement(unittest.TestCase):
    """Reprodução ponta a ponta do bug relatado pelo usuário (16/09/2026,
    ver PROBLEMAS_ARQUITETURA.md §154/§156/§157/§158/§159): comprar arco,
    comprar aljava básica, comprar aljava de batalha por cima — cada compra
    auto-equipando e o item substituído voltando pro FIM da bag — usando o
    código REAL dos dois lados (WorldServer.process_shop_buy/
    equip_item_from_inventory no servidor; NetworkHandlers.
    _handle_msg_buy_result/InventoryHandlers._equip_item no cliente), cada
    lado com seu PRÓPRIO Inventory/Equipment independente, exatamente como
    a rede faz. Causa raiz real (§159) era o talento Reciclagem inserindo
    uma "Flecha" só na bag do servidor sem avisar o cliente — com o talento
    desligado dentro da instância, este teste prova que compra+equip
    encadeados continuam de acordo nos dois lados sem mais nenhuma fonte de
    desync conhecida."""

    def _make_client_fixture(self, player_entity_marker: int = 1):
        from engine.world import World
        from client.inventory_handlers import InventoryHandlers
        from client.network_handlers import NetworkHandlers
        from client.save_sync_handlers import SaveSyncHandlers

        class _Fixture(InventoryHandlers, NetworkHandlers, SaveSyncHandlers):
            def __init__(self, world, player_entity):
                self.world = world
                self.player_entity = player_entity
                self._my_eid = player_entity_marker
                self._remote_players = {}
                self._remote_mobs = {}
                self._net = None
                self.sent_equip_indices: list[int] = []

            def _send_equip_item(self, inv_index):
                self.sent_equip_indices.append(inv_index)

        cworld  = World()
        cplayer = cworld.create_entity()
        cworld.add_component(cplayer, CharacterStats(class_id="arqueiro"))
        cworld.add_component(cplayer, CombatStats())
        cworld.add_component(cplayer, Inventory(items=[]))
        cworld.add_component(cplayer, Equipment())
        from ui.ui_components import InstanceInventoryUIState
        iius = InstanceInventoryUIState()
        iius.active = True
        cworld.add_component(cplayer, iius)
        return _Fixture(cworld, cplayer), cworld, cplayer

    def setUp(self):
        self.ws  = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="arqueiro")
        self.ws.world.get_component(self.eid, Wallet).gold = 9999
        self.equip_ws = self.ws.world.get_component(self.eid, Equipment)
        self.inv_ws   = self.ws.world.get_component(self.eid, Inventory)
        self.fx, self.cworld, self.cplayer = self._make_client_fixture()
        self.equip_c = self.cworld.get_component(self.cplayer, Equipment)
        self.inv_c   = self.cworld.get_component(self.cplayer, Inventory)

    def _buy_and_equip(self, item_id: str):
        result = self.ws.process_shop_buy("s1", "instance_shop", "", 1, item_id=item_id)
        self.assertTrue(result["success"], result)
        self.fx._handle_msg_buy_result(result)
        self.assertTrue(self.fx.sent_equip_indices,
                        f"cliente não mandou EQUIP_ITEM pra {item_id}")
        inv_index = self.fx.sent_equip_indices.pop()
        return self.ws.equip_item_from_inventory("s1", inv_index)

    def test_arco_aljava_e_troca_de_aljava_ficam_de_acordo_nos_dois_lados(self):
        r_bow = self._buy_and_equip("elven_bow")
        self.assertIsNone(r_bow)
        self.assertEqual(self.equip_ws.slots["mainhand"].item_id, "elven_bow")
        self.assertEqual(self.equip_c.slots["mainhand"].item_id, "elven_bow")

        r_basic = self._buy_and_equip("basic_quiver")
        self.assertIsNone(r_basic)
        self.assertEqual(self.equip_ws.slots["offhand"].item_id, "basic_quiver")
        self.assertEqual(self.equip_c.slots["offhand"].item_id, "basic_quiver")

        r_battle = self._buy_and_equip("battleground_quiver")
        self.assertIsNone(r_battle)
        self.assertEqual(self.equip_ws.slots["offhand"].item_id, "battleground_quiver")
        self.assertEqual(self.equip_c.slots["offhand"].item_id, "battleground_quiver")

        # A aljava básica substituída volta pro FIM da bag dos DOIS lados.
        self.assertEqual([i.item_id for i in self.inv_ws.items], ["basic_quiver"])
        self.assertEqual([i.item_id for i in self.inv_c.items], ["basic_quiver"])


if __name__ == "__main__":
    unittest.main()
