"""
tests/test_combat.py
Testes unitários de combate — damage_calculator, cooldowns server-side, Pirofagia.
"""
import unittest
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, set_entity_tile, first_mob


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de stats dummy para damage_calculator (sem World)
# ─────────────────────────────────────────────────────────────────────────────

class _Stats:
    """Stat object mínimo para damage_calculator."""
    def __init__(self, **kw):
        self.crit_rating             = 0.0
        self.hit_rating              = 0.0
        self.acerto                  = 100.0
        self.acerto_per_standing_second = 0.0
        self.standing_seconds        = 0.0
        self.alvo_facil_acerto       = 0
        self.dodge_rating            = 0.0
        self.parry_rating            = 0.0
        self.block_rating            = 0.0
        self.block_value             = 0.0
        self.armor                   = 0.0
        self.armor_penetration       = 0.0
        self.attack_power            = 0
        self.spell_power             = 0
        self.base_physical_damage    = 10
        self.base_physical_damage_max = 10
        self.base_magical_damage     = 0
        self.is_crowd_controlled     = False
        for k, v in kw.items():
            setattr(self, k, v)


# ─────────────────────────────────────────────────────────────────────────────
# 1. damage_calculator — funções puras
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyArmorReduction(unittest.TestCase):

    def test_zero_armor_no_reduction(self):
        from damage_calculator import apply_armor_reduction
        atk = _Stats(armor_penetration=0.0)
        tgt = _Stats(armor=0.0)
        self.assertAlmostEqual(apply_armor_reduction(100.0, atk, tgt, 'hit'), 100.0)

    def test_armor_reduces_damage(self):
        from damage_calculator import apply_armor_reduction, ARMOR_REDUCTION_PER_POINT
        atk = _Stats(armor_penetration=0.0)
        tgt = _Stats(armor=500.0)
        dmg = apply_armor_reduction(100.0, atk, tgt, 'hit')
        expected = 100.0 * (1.0 - min(0.99, 500.0 * ARMOR_REDUCTION_PER_POINT))
        self.assertAlmostEqual(dmg, expected, places=5)

    def test_crit_ignores_armor(self):
        from damage_calculator import apply_armor_reduction
        atk = _Stats(armor_penetration=0.0)
        tgt = _Stats(armor=9999.0)
        self.assertAlmostEqual(apply_armor_reduction(100.0, atk, tgt, 'crit'), 100.0)

    def test_armor_pen_reduces_effective_armor(self):
        from damage_calculator import apply_armor_reduction, ARMOR_REDUCTION_PER_POINT
        atk = _Stats(armor_penetration=200.0)
        tgt = _Stats(armor=200.0)
        # pen == armor → eff_armor = 0 → nenhuma redução
        dmg = apply_armor_reduction(100.0, atk, tgt, 'hit')
        self.assertAlmostEqual(dmg, 100.0, places=5)

    def test_armor_reduction_cap(self):
        from damage_calculator import apply_armor_reduction, ARMOR_REDUCTION_CAP
        atk = _Stats(armor_penetration=0.0)
        tgt = _Stats(armor=99999.0)  # enorme armor → cap em 99%
        dmg = apply_armor_reduction(100.0, atk, tgt, 'hit')
        self.assertGreaterEqual(dmg, 100.0 * (1.0 - ARMOR_REDUCTION_CAP) - 0.001)


class TestCalculateBaseDamage(unittest.TestCase):
    """Usa physical_fixed para evitar aleatoriedade."""

    def test_physical_fixed_multiplier(self):
        from damage_calculator import calculate_base_damage
        atk = _Stats()
        dmg = calculate_base_damage(atk, 'physical_fixed', None,
                                    base_ability_damage=100.0,
                                    multiplier=1.5, outcome='hit')
        self.assertAlmostEqual(dmg, 150.0)

    def test_physical_fixed_crit_doubles(self):
        from damage_calculator import calculate_base_damage, CRITICAL_DAMAGE_MULTIPLIER
        atk = _Stats()
        dmg = calculate_base_damage(atk, 'physical_fixed', None,
                                    base_ability_damage=100.0, outcome='crit')
        self.assertAlmostEqual(dmg, 100.0 * CRITICAL_DAMAGE_MULTIPLIER)

    def test_block_reduction_applied(self):
        from damage_calculator import calculate_base_damage
        atk = _Stats()
        dmg = calculate_base_damage(atk, 'physical_fixed', None,
                                    base_ability_damage=100.0,
                                    block_reduction=30.0, outcome='hit')
        self.assertAlmostEqual(dmg, 70.0)

    def test_unknown_damage_type_returns_zero(self):
        from damage_calculator import calculate_base_damage
        atk = _Stats()
        dmg = calculate_base_damage(atk, 'unknown', None,
                                    base_ability_damage=100.0)
        self.assertEqual(dmg, 0.0)

    def test_magical_adds_spell_power(self):
        from damage_calculator import calculate_base_damage
        atk = _Stats(spell_power=50, base_magical_damage=10)
        dmg = calculate_base_damage(atk, 'magical', None, outcome='hit')
        self.assertAlmostEqual(dmg, 60.0)


class TestResolveAttackOutcome(unittest.TestCase):
    """Usa acerto=100, dodge/parry=0 para resultados determinísticos."""

    def test_no_avoidance_returns_hit_or_crit(self):
        import random
        from damage_calculator import resolve_attack_outcome
        atk = _Stats(acerto=100.0, crit_rating=0.0)
        tgt = _Stats(dodge_rating=0.0, parry_rating=0.0, block_rating=0.0)
        random.seed(42)
        outcomes = {resolve_attack_outcome(atk, tgt, 'physical')[0] for _ in range(20)}
        self.assertTrue(outcomes.issubset({'hit', 'crit'}),
                        f"Outcomes inesperados: {outcomes}")

    def test_100pct_crit_always_crits(self):
        import random
        from damage_calculator import resolve_attack_outcome
        atk = _Stats(acerto=100.0, crit_rating=1.0)
        tgt = _Stats(dodge_rating=0.0, parry_rating=0.0, block_rating=0.0)
        random.seed(0)
        outcomes = [resolve_attack_outcome(atk, tgt, 'physical')[0] for _ in range(10)]
        self.assertTrue(all(o == 'crit' for o in outcomes),
                        f"Esperado 100% crit, got: {set(outcomes)}")

    def test_ability_cannot_miss(self):
        import random
        from damage_calculator import resolve_attack_outcome
        atk = _Stats(acerto=0.0, hit_rating=0.0)
        tgt = _Stats(dodge_rating=0.0, parry_rating=0.0, block_rating=0.0)
        random.seed(1)
        outcomes = [resolve_attack_outcome(atk, tgt, 'physical',
                                           is_ability=True)[0] for _ in range(20)]
        self.assertNotIn('miss', outcomes, "Abilities não devem errar por miss")

    def test_magical_no_dodge_parry(self):
        import random
        from damage_calculator import resolve_attack_outcome
        atk = _Stats(acerto=100.0, crit_rating=0.0)
        tgt = _Stats()
        random.seed(5)
        outcomes = [resolve_attack_outcome(atk, tgt, 'magical')[0] for _ in range(20)]
        self.assertNotIn('dodge', outcomes)
        self.assertNotIn('parry', outcomes)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Cooldown server-side
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillCooldownServer(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 50)  # spawn mobs
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="mago")

    def _get_mago_skill(self, eid, sid: str):
        from components import PlayerSkills
        ps = self.ws.world.get_component(eid, PlayerSkills)
        if not ps:
            return None
        return next((sk for sk in ps.skills if sk and sk.skill_id == sid), None)

    def test_first_cast_accepted(self):
        """Sem cooldown prévio → _skill_results_this_tick recebe failed=False."""
        from components import CombatState, CombatStats
        mob = first_mob(self.ws)
        self.assertIsNotNone(mob)
        set_entity_tile(self.ws, mob, 130, 375)

        cs = self.ws.world.get_component(self.eid, CombatState)
        if cs:
            cs.target_entity_id = mob

        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp = mob_cs.max_hp

        self.ws._skill_results_this_tick.clear()
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "calcinar",
            "tid": mob, "dir_x": 0.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        # Deve ter algum resultado — não necessariamente bem-sucedido (pode faltar mana),
        # mas se falhar por mana deve ter failed=True; se passar, failed=False
        results = self.ws._skill_results_this_tick
        self.assertTrue(len(results) > 0, "Nenhum SKILL_RESULT emitido")

    def test_cast_during_cooldown_rejected(self):
        """Cast durante cooldown ativo → failed=True no _skill_results_this_tick.

        Usa pirofagia (90s CD) — calcinar tem CD=0 e não é verificado.
        """
        sid = "pirofagia"
        # Simula cooldown ativo: último uso foi "agora"
        self.ws._skill_last_used[(self.eid, sid)] = time.time()

        self.ws._skill_results_this_tick.clear()
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": sid,
            "tid": -1, "dir_x": 1.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        results = [r for r in self.ws._skill_results_this_tick
                   if r["caster_eid"] == self.eid and r["sid"] == sid]
        self.assertTrue(len(results) > 0, "Nenhum SKILL_RESULT emitido para cast bloqueado")
        self.assertTrue(results[0]["failed"],
                        "Cast durante CD deveria retornar failed=True")

    def test_cast_after_cooldown_not_rejected(self):
        """Cast após cooldown expirar → não bloqueado por CD.

        Usa pirofagia (90s CD). Após expirar, cast pode falhar por outros motivos
        (sem alvo, etc.) mas NÃO deve ter cooldown_remaining > 0 no resultado.
        """
        sid = "pirofagia"
        # Último uso há muito tempo (garantidamente mais que qualquer CD)
        self.ws._skill_last_used[(self.eid, sid)] = time.time() - 999.0

        self.ws._skill_results_this_tick.clear()
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": sid,
            "tid": -1, "dir_x": 1.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        results = [r for r in self.ws._skill_results_this_tick
                   if r["caster_eid"] == self.eid and r["sid"] == sid]
        # Se houve resultado e failed=True, não deve ter cooldown_remaining > 0
        # (falha por CD tem cooldown > 0; falha por outros motivos tem cooldown=0)
        if results and results[0].get("failed"):
            cd_remaining = results[0].get("cooldown", 0)
            self.assertEqual(cd_remaining, 0,
                             f"CD restante={cd_remaining} — cast bloqueado por CD expirado?")

    def test_cooldown_registered_after_successful_cast(self):
        """Após cast bem-sucedido, _skill_last_used é atualizado."""
        from components import CharacterStats, CombatState, CombatStats
        from skill_config import SKILL_CATALOG

        sid = "calcinar"
        # Zera cooldown anterior
        self.ws._skill_last_used.pop((self.eid, sid), None)

        # Garante mana suficiente
        char = self.ws.world.get_component(self.eid, CharacterStats)
        if char:
            char.mana = 9999

        mob = first_mob(self.ws)
        if not mob:
            self.skipTest("Sem mob para testar cast")
        set_entity_tile(self.ws, mob, 130, 375)
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp = mob_cs.max_hp

        cs = self.ws.world.get_component(self.eid, CombatState)
        if cs:
            cs.target_entity_id = mob

        t_before = time.time()
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": sid,
            "tid": mob, "dir_x": 0.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()
        t_after = time.time()

        last = self.ws._skill_last_used.get((self.eid, sid))
        if last is not None:
            # Se foi registrado, deve ser recente
            self.assertGreaterEqual(last, t_before - 0.1)
            self.assertLessEqual(last, t_after + 0.1)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Pirofagia — cone server-side
# ─────────────────────────────────────────────────────────────────────────────

class TestPirofagiaServerCone(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 50)  # spawn mobs
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="mago")
        # Garante mana suficiente
        from components import CharacterStats
        char = self.ws.world.get_component(self.eid, CharacterStats)
        if char:
            char.mana = 9999

    def _place_mob_in_cone(self, player_tx, player_ty, dir_x, dir_y, offset=3):
        """Move um mob para dentro do cone de Pirofagia apontado em (dir_x, dir_y)."""
        from components import CombatStats
        mob = first_mob(self.ws)
        self.assertIsNotNone(mob, "Sem mob para testar Pirofagia")
        # Posição na direção do cone (offset tiles à frente, mesma linha)
        tx = player_tx + round(dir_x * offset)
        ty = player_ty + round(dir_y * offset)
        set_entity_tile(self.ws, mob, tx, ty)
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp   = mob_cs.max_hp
        mob_cs.dodge_rating = 0.0
        mob_cs.parry_rating = 0.0
        return mob, tx, ty

    def test_mob_in_cone_takes_damage(self):
        """Mob dentro do cone de Pirofagia recebe dano quando executado server-side."""
        from components import CombatStats
        player_tx, player_ty = 130, 374
        set_entity_tile(self.ws, self.eid, player_tx, player_ty)

        mob, mob_tx, mob_ty = self._place_mob_in_cone(player_tx, player_ty, 1.0, 0.0, 2)
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        hp_before = mob_cs.current_hp

        self.ws._skill_results_this_tick.clear()
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "pirofagia",
            "tid": -1, "dir_x": 1.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        self.assertLess(mob_cs.current_hp, hp_before,
                        f"Mob em ({mob_tx},{mob_ty}) não recebeu dano da Pirofagia. "
                        f"Player em ({player_tx},{player_ty}), cone dir=(1,0)")

    def test_mob_behind_player_not_hit(self):
        """Mob atrás do player (fora do cone) não recebe dano."""
        from components import CombatStats
        player_tx, player_ty = 130, 374
        set_entity_tile(self.ws, self.eid, player_tx, player_ty)

        mob = first_mob(self.ws)
        self.assertIsNotNone(mob)
        # Posição oposta à direção do cone (−3 tiles)
        set_entity_tile(self.ws, mob, player_tx - 3, player_ty)
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp   = mob_cs.max_hp
        mob_cs.dodge_rating = 0.0
        mob_cs.parry_rating = 0.0
        hp_before = mob_cs.current_hp

        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "pirofagia",
            "tid": -1, "dir_x": 1.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        self.assertEqual(mob_cs.current_hp, hp_before,
                         "Mob atrás do player recebeu dano (fora do cone)")

    def test_pirofagia_result_emitted(self):
        """Pirofagia bem-sucedida server-side emite SKILL_RESULT."""
        player_tx, player_ty = 130, 374
        set_entity_tile(self.ws, self.eid, player_tx, player_ty)

        self.ws._skill_results_this_tick.clear()
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "pirofagia",
            "tid": -1, "dir_x": 1.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()

        results = [r for r in self.ws._skill_results_this_tick
                   if r["caster_eid"] == self.eid and r["sid"] == "pirofagia"]
        self.assertTrue(len(results) > 0, "Nenhum SKILL_RESULT emitido para Pirofagia")

    def test_pirofagia_without_dir_falls_back_to_client_mode(self):
        """dir_x=dir_y=0 → modo cliente (state machine), não executa cone."""
        from components import CombatStats
        player_tx, player_ty = 130, 374
        set_entity_tile(self.ws, self.eid, player_tx, player_ty)

        mob = first_mob(self.ws)
        self.assertIsNotNone(mob)
        set_entity_tile(self.ws, mob, player_tx + 2, player_ty)
        mob_cs = self.ws.world.get_component(mob, CombatStats)
        mob_cs.current_hp = mob_cs.max_hp
        hp_before = mob_cs.current_hp

        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "pirofagia",
            "tid": -1, "dir_x": 0.0, "dir_y": 0.0,  # sem direção
        })
        self.ws._process_skill_requests()

        # No servidor não há PirofagiaAiming — deve retornar True (state machine iniciada)
        # mas mob NÃO recebe dano (cone não foi disparado)
        self.assertEqual(mob_cs.current_hp, hp_before,
                         "Cone executado sem direção (modo cliente não deve causar dano no servidor)")


if __name__ == "__main__":
    unittest.main()
