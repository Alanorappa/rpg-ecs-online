"""
tests/test_talent_skill_cooldown_preserved.py — 17/09/2026, recorrência
do bug já catalogado em §34.74.23 (03/08/2026, "cooldown de skill só
aparecia depois de apertar o atalho de novo"): usuário relatou o MESMO
sintoma de novo, desta vez com Tiro Múltiplo (arqueiro) dentro da BG —
a barra de cooldown "sumia" sozinha pouco depois de usar a skill, só
reaparecia (com o valor certo) quando apertava o atalho de novo já em
cooldown (a rejeição do servidor reaplica o valor por fora).

Causa raiz DESTA vez é diferente do bug original (aquele, em
`client/network_handlers.py::_handle_msg_stats_update` bloco
`skills_hotbar`, continua corrigido): `ui/talent_system.py::
TalentSystem.apply_talent_effects()` descartava e recriava do ZERO
toda skill desbloqueada por TALENTO (ex: Tiro Múltiplo, via
`bardo_tiro_multiplo`) em TODA chamada — mesmo quando a alocação não
mudou nada. Essa função roda de novo toda vez que `talent_allocated`
chega via STATS_UPDATE (`client/network_handlers.py`), e dentro da BG
isso é em TODO ganho de XP (`server/instance_progression.py::
_push_stats_update`, "empurra STATS_UPDATE em TODO ganho" desde
13/08/2026) — não só quando o jogador aloca um ponto de verdade. Uma
skill de cooldown longo (90s) tem uma janela bem maior pra uma dessas
chamadas acontecer no meio e resetar o objeto, perdendo
`current_cooldown` ao vivo — explica por que fica mais visível em
skills de cooldown alto.
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
from client.network_handlers import NetworkHandlers
from ui.talent_system import TalentSystem


class _FakeNet:
    def __init__(self):
        self.connected = True
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class _FakeClientWithTalentSystem(NetworkHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = player_entity
        self._net = _FakeNet()
        self._remote_players = {}
        self._remote_mobs = {}
        self._logged_char_name = None
        self._talent_system = TalentSystem(world, player_entity, pygame.display.get_surface())


class TestTalentSkillPreservesLiveCooldown(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        self.world.add_component(self.eid, CharacterStats(class_id="arqueiro"))
        self.world.add_component(self.eid, CombatStats())
        self.world.add_component(self.eid, PlayerSkills())
        tt = TalentTree()
        tt.chosen_build = "bardo"
        tt.allocated = {}
        self.world.add_component(self.eid, tt)
        self.client = _FakeClientWithTalentSystem(self.world, self.eid)

    def _push_talent_allocated(self):
        self.client._handle_msg_stats_update({
            "eid": self.eid, "talent_points": 0,
            "talent_allocated": {"bardo_tiro_multiplo": 1},
        })

    def test_reaplicar_mesma_alocacao_preserva_objeto_e_cooldown_da_skill(self):
        # 1ª chamada: desbloqueia Tiro Múltiplo pela primeira vez.
        self._push_talent_allocated()
        ps = self.world.get_component(self.eid, PlayerSkills)
        skill = next((s for s in ps.skills if s and s.skill_id == "tiro_multiplo"), None)
        self.assertIsNotNone(skill, "Tiro Múltiplo deveria ter sido concedido")

        # Simula SKILL_RESULT: acabou de usar, 90s de cooldown ao vivo.
        skill.current_cooldown = 90.0
        skill_obj_id_antes = id(skill)

        # 2ª chamada: MESMA alocação chegando de novo (simula outro ganho
        # de XP na BG, sem o jogador ter mexido em talento nenhum).
        self._push_talent_allocated()

        ps_depois = self.world.get_component(self.eid, PlayerSkills)
        skill_depois = next((s for s in ps_depois.skills if s and s.skill_id == "tiro_multiplo"), None)
        self.assertIsNotNone(skill_depois, "Tiro Múltiplo não deveria sumir da hotbar")
        self.assertEqual(id(skill_depois), skill_obj_id_antes,
                         "reaplicar a MESMA alocação de talento não deveria trocar o "
                         "objeto Skill — perde current_cooldown/charges ao vivo")
        self.assertEqual(skill_depois.current_cooldown, 90.0,
                         "cooldown ao vivo não deveria ser resetado só por "
                         "apply_talent_effects() rodar de novo com a mesma alocação")


if __name__ == "__main__":
    unittest.main()
