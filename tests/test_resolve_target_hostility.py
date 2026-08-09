"""tests/test_resolve_target_hostility.py — SkillSystem._resolve_target
(ui/systems.py). Bug real relatado pelo usuário (01/08/2026): "as teclas de
atalho das habilidades ainda selecionam um player aliado" — as 3 buscas de
candidato dentro de _resolve_target não filtravam hostilidade nenhuma antes
deste fix (mesmo já corrigido pra TAB/SPACE numa rodada anterior). Ver
ui/systems.py::_resolve_target, comentário "01/08/2026, bug real...".

Fase 8 (06/08/2026) — segundo bug real, mesmo arquivo: minion (MOBA lane
creep) nunca tem Enemy/AIControlled de propósito (MinionSystem próprio) —
o loop de candidatos de _resolve_target exigia Enemy, então quando o alvo
explícito de uma skill morria (comum, TTK baixo de minion) e o auto-
fallback tentava escolher o próximo hostil mais perto, minion nunca era
achado — mesmo vivo e adjacente. Isso rodava tanto no cliente quanto no
SERVIDOR (SkillSystem é compartilhado — ver server/world_server.py:479),
que é onde o bug realmente importava. Ver ARQUITETURA_ONLINE.md §34.74.48.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.world import World
from engine.components import (
    Position, TileMovement, CombatState, CombatStats, Enemy, AIControlled,
    Visible, RemoteControlled, PlayerControlled, Faction, Minion,
)
from ui.systems import SkillSystem


class TestResolveTargetHostility(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, PlayerControlled())
        self.skill_sys = SkillSystem(self.world, self.player, screen=None)
        self.combat_state = CombatState()
        self.player_tm = TileMovement(current_tile_x=10, current_tile_y=10)

    def _spawn_hostile_mob(self, tx, ty, faction="monstros_hostis", hp=50):
        eid = self.world.create_entity()
        self.world.add_component(eid, Position(x=tx * 32, y=ty * 32))
        self.world.add_component(eid, Enemy())
        self.world.add_component(eid, AIControlled())
        self.world.add_component(eid, TileMovement(current_tile_x=tx, current_tile_y=ty))
        self.world.add_component(eid, CombatStats(base_stamina=hp))
        cs = self.world.get_component(eid, CombatStats)
        cs.current_hp = hp
        self.world.add_component(eid, Visible())
        self.world.add_component(eid, Faction(faction_id=faction))
        return eid

    def _spawn_remote_ally(self, tx, ty, hp=100):
        eid = self.world.create_entity()
        self.world.add_component(eid, Position(x=tx * 32, y=ty * 32))
        self.world.add_component(eid, RemoteControlled(hp=hp))
        self.world.add_component(eid, TileMovement(current_tile_x=tx, current_tile_y=ty))
        self.world.add_component(eid, Visible())
        return eid

    def test_nao_seleciona_player_aliado_remoto_sem_mob_hostil_por_perto(self):
        """Reproduz o bug relatado ponta a ponta: só um aliado remoto (sem
        Faction — resolve "jogadores", amigável por default) visível — a
        tecla de atalho NÃO deve auto-selecioná-lo como alvo."""
        ally = self._spawn_remote_ally(11, 10)
        result = self.skill_sys._resolve_target(self.combat_state, self.player_tm)
        self.assertNotEqual(result, ally,
                            "aliado remoto nunca deveria ser auto-selecionado pela hotkey")
        self.assertEqual(result, -1, "sem candidato hostil válido, deveria retornar -1")

    def test_seleciona_mob_hostil_ignorando_aliado_mais_perto(self):
        ally = self._spawn_remote_ally(11, 11)
        hostile = self._spawn_hostile_mob(11, 10)
        result = self.skill_sys._resolve_target(self.combat_state, self.player_tm)
        self.assertEqual(result, hostile)
        self.assertNotEqual(result, ally)

    def test_nao_seleciona_mob_neutro_apenas_hostil(self):
        """is_hostile (tier estrito) — mob "vida_selvagem" (neutro) não
        deveria ser auto-selecionado pela hotkey, mesmo can_engage permitindo
        dano caso ele já esteja engajado por outro caminho."""
        neutral = self._spawn_hostile_mob(11, 10, faction="vida_selvagem")
        result = self.skill_sys._resolve_target(self.combat_state, self.player_tm)
        self.assertNotEqual(result, neutral)
        self.assertEqual(result, -1)


class TestResolveTargetMinion(unittest.TestCase):
    """Fase 8 (06/08/2026) — ver docstring do módulo."""

    def setUp(self):
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, PlayerControlled())
        self.skill_sys = SkillSystem(self.world, self.player, screen=None)
        self.combat_state = CombatState()
        self.player_tm = TileMovement(current_tile_x=10, current_tile_y=10)

    def _spawn_hostile_minion(self, tx, ty, faction="monstros_hostis", hp=50):
        eid = self.world.create_entity()
        self.world.add_component(eid, Position(x=tx * 32, y=ty * 32))
        self.world.add_component(eid, Minion(
            minion_key="minion_melee", route=[], aggro_range_tiles=4,
            attack_range_tiles=1, xp_reward=0))
        self.world.add_component(eid, TileMovement(current_tile_x=tx, current_tile_y=ty))
        self.world.add_component(eid, CombatStats(base_stamina=hp))
        cs = self.world.get_component(eid, CombatStats)
        cs.current_hp = hp
        self.world.add_component(eid, Visible())
        self.world.add_component(eid, Faction(faction_id=faction))
        return eid

    def _spawn_hostile_mob(self, tx, ty, faction="monstros_hostis", hp=50):
        eid = self.world.create_entity()
        self.world.add_component(eid, Position(x=tx * 32, y=ty * 32))
        self.world.add_component(eid, Enemy())
        self.world.add_component(eid, AIControlled())
        self.world.add_component(eid, TileMovement(current_tile_x=tx, current_tile_y=ty))
        self.world.add_component(eid, CombatStats(base_stamina=hp))
        cs = self.world.get_component(eid, CombatStats)
        cs.current_hp = hp
        self.world.add_component(eid, Visible())
        self.world.add_component(eid, Faction(faction_id=faction))
        return eid

    def test_minion_hostil_sozinho_e_auto_selecionado(self):
        """Reproduz o bug real: sem mob comum por perto, só um minion
        hostil adjacente — hoje (sem o fix) retornaria -1 mesmo com o
        minion vivo e ao alcance."""
        minion = self._spawn_hostile_minion(11, 10)
        result = self.skill_sys._resolve_target(self.combat_state, self.player_tm)
        self.assertEqual(result, minion,
            "minion hostil adjacente deveria ser auto-selecionado, mesmo sem Enemy/AIControlled")

    def test_minion_neutro_nao_e_selecionado(self):
        """RELATIONSHIP não tem entrada específica pra time_a/time_b fora
        de contexto de BG registrado — usa uma facção que resolve neutra
        por padrão (mesma regra de hostilidade estrita do mob comum)."""
        neutral_minion = self._spawn_hostile_minion(11, 10, faction="vida_selvagem")
        result = self.skill_sys._resolve_target(self.combat_state, self.player_tm)
        self.assertNotEqual(result, neutral_minion)
        self.assertEqual(result, -1)

    def test_minion_e_mob_comum_nao_se_atrapalham(self):
        """Regressão: mob comum (Enemy) e minion hostis nos MESMOS tile/hp
        (empate) — não é crítico qual dos dois vence, só que o loop novo
        não quebra o antigo (um dos dois precisa ser escolhido, nunca -1)."""
        mob = self._spawn_hostile_mob(11, 10, faction="monstros_hostis")
        minion = self._spawn_hostile_minion(11, 10, faction="monstros_hostis")
        result = self.skill_sys._resolve_target(self.combat_state, self.player_tm)
        self.assertIn(result, (mob, minion))


if __name__ == "__main__":
    unittest.main()
