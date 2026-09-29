"""
tests/test_talent_skill_grant_skipped_in_instance.py — 28-29/09/2026,
bug real relatado pelo usuário (ver PROBLEMAS_ARQUITETURA.md §216):
dentro da BG, "Fatiador de Corpos" (e qualquer skill de talento)
"piscava" em slots diferentes da hotbar a cada level-up, só
estabilizando no slot certo (preferência salva) bem mais tarde.

Causa raiz: `ui/talent_system.py::TalentSystem.apply_talent_effects()`
roda de novo em TODO `talent_allocated` (todo ganho de XP dentro da
BG, não só entrada/saída — `server/instance_progression.py::
_push_stats_update` sempre inclui esse campo) e concedia a skill
desbloqueada por talento direto no 1º slot vazio, sem saber da ordem
curada da instância (`INSTANCE_SKILL_UNLOCK_ORDER`) nem da preferência
salva do jogador — o bloco `skills_hotbar` (client/network_handlers.py,
que SIM respeita a ordem curada e a preferência) removia de novo por
achar a skill "não deveria estar aqui ainda", criando o ciclo de
concede→remove a cada level até a ordem curada alcançar o nível real
da skill.

Fix: dentro da instância, `apply_talent_effects()` para de conceder
skill nova na hotbar (isso já é 100% responsabilidade do bloco
skills_hotbar) — mas continua aplicando modifiers/cs_flags
normalmente (não pode regredir o fix do §136, Precisão Elemental/Nova
Congelante).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import (
    PlayerControlled, TalentTree, CombatStats, PlayerSkills, CharacterStats,
)
from ui.talent_system import TalentSystem
from ui.ui_components import InstanceInventoryUIState


class TestTalentSkillGrantSkippedInInstance(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        self.world.add_component(self.eid, CharacterStats(class_id="guerreiro"))
        self.world.add_component(self.eid, CombatStats())
        self.world.add_component(self.eid, PlayerSkills())
        tt = TalentTree()
        tt.chosen_build = "cavaleiro"
        tt.allocated = {}
        self.world.add_component(self.eid, tt)
        self.ts = TalentSystem(self.world, self.eid, pygame.display.get_surface())

    def _allocate(self, talent_id: str, points: int):
        tt = self.world.get_component(self.eid, TalentTree)
        tt.allocated = {talent_id: points}
        self.ts.apply_talent_effects()

    def _find_talent_that_unlocks_a_skill(self) -> tuple[str, str, int]:
        """Acha um talento real do build 'cavaleiro' que desbloqueia
        skill — não hardcoda um talent_id (o catálogo pode mudar)."""
        from content.talent_data import TALENTS
        for tid, t in TALENTS.items():
            if t.get("build") == "cavaleiro" and t.get("unlocks_skill"):
                unlock_at = t.get("unlock_at", t["max_points"])
                return tid, t["unlocks_skill"], unlock_at
        raise AssertionError("nenhum talento do build cavaleiro desbloqueia skill — catálogo mudou?")

    def test_nao_concede_skill_na_hotbar_dentro_da_instancia(self):
        talent_id, skill_id, unlock_at = self._find_talent_that_unlocks_a_skill()
        iius = InstanceInventoryUIState()
        iius.active = True
        self.world.add_component(self.eid, iius)

        self._allocate(talent_id, unlock_at)

        ps = self.world.get_component(self.eid, PlayerSkills)
        self.assertIsNone(ps.skill_by_id(skill_id),
            "dentro da instância, apply_talent_effects() não deveria conceder "
            "a skill na hotbar sozinho — isso é responsabilidade do bloco skills_hotbar")

    def test_continua_concedendo_normalmente_fora_da_instancia(self):
        """Regressão: fora da BG, o comportamento de sempre continua intacto."""
        talent_id, skill_id, unlock_at = self._find_talent_that_unlocks_a_skill()
        # Sem InstanceInventoryUIState nenhum componente = fora de instância.

        self._allocate(talent_id, unlock_at)

        ps = self.world.get_component(self.eid, PlayerSkills)
        self.assertIsNotNone(ps.skill_by_id(skill_id),
            "fora da instância, a skill de talento deveria continuar sendo concedida normalmente")

    def test_continua_aplicando_modifiers_dentro_da_instancia(self):
        """O gate é só pra concessão de skill — os efeitos de STAT (modifiers)
        continuam aplicando normalmente dentro da BG (não pode regredir §136)."""
        from content.talent_data import TALENTS
        # Acha um talento do build cavaleiro com efeito de atributo real.
        talent_id = None
        for tid, t in TALENTS.items():
            if t.get("build") == "cavaleiro" and t.get("effects"):
                talent_id = tid
                break
        if talent_id is None:
            self.skipTest("nenhum talento do build cavaleiro com efeito de atributo — catálogo mudou?")

        iius = InstanceInventoryUIState()
        iius.active = True
        self.world.add_component(self.eid, iius)

        cs = self.world.get_component(self.eid, CombatStats)
        tt = self.world.get_component(self.eid, TalentTree)
        self.assertEqual(len(tt._applied_modifiers), 0)

        self._allocate(talent_id, 1)

        self.assertGreater(len(tt._applied_modifiers), 0,
            "modifiers de talento deveriam continuar sendo aplicados dentro da instância")

    def test_skill_nao_pisca_entre_niveis_ate_ordem_curada_alcancar(self):
        """Ponta a ponta, reproduzindo o cenário real relatado: dentro da BG,
        talent_allocated chega em TODO level-up (árvore já maxada desde a
        entrada) — antes do fix, isso concedia a skill prematuramente a
        cada chamada; com o fix, ela só aparece quando o bloco skills_hotbar
        (fonte única de verdade dentro da instância) de fato a revela."""
        talent_id, skill_id, unlock_at = self._find_talent_that_unlocks_a_skill()
        iius = InstanceInventoryUIState()
        iius.active = True
        self.world.add_component(self.eid, iius)
        ps = self.world.get_component(self.eid, PlayerSkills)

        # Simula 5 "level-ups" seguidos dentro da BG: talent_allocated chega
        # de novo em TODOS eles (árvore já maxada), mas a skill NUNCA deveria
        # aparecer sozinha na hotbar por causa disso.
        for _ in range(5):
            self._allocate(talent_id, unlock_at)
            self.assertIsNone(ps.skill_by_id(skill_id),
                "skill nao deveria aparecer prematuramente em NENHUMA das chamadas repetidas")


if __name__ == "__main__":
    unittest.main()
