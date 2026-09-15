"""
tests/test_skill_effect_no_local_dup_sound.py — 14/09/2026, bug real
relatado pelo usuário: o som de impacto de Nova Congelante tocava DUAS
vezes — uma na previsão local do cliente (`ui/spell_system.py::
SpellCastSystem._apply_nova_congelante`, chamada quando a barra de cast
LOCAL termina) e outra quando a confirmação de rede chegava de volta
pro PRÓPRIO conjurador (`SKILL_EFFECT`, `client/network_handlers.py::
_handle_msg_skill_effect`) — esse broadcast existe pra QUALQUER outro
player perto ouvir a skill, mas o conjurador também está dentro do
próprio raio de AOI e acabava recebendo (e tocando) o aviso destinado
aos outros.

Fix: flag `skip_local_caster` em `effects.<evento>` no catálogo
(`content/skill_config.py`) — o handler de rede não tenta tocar de novo
pro conjurador quando essa flag está presente, mas continua tocando
normal pra qualquer OUTRO player (`is_local=False`). Ver
PROBLEMAS_ARQUITETURA.md §137.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from engine.world import World
from client.network_handlers import NetworkHandlers


class _FakeClient(NetworkHandlers):
    def __init__(self, my_eid: int):
        self.world = World()
        self._my_eid = my_eid
        self.player_entity = my_eid
        self._remote_players = {}
        self._remote_mobs = {}

    def _player_world_pos(self):
        return (0.0, 0.0)


class TestSkillEffectSkipLocalCaster(unittest.TestCase):

    def test_nova_congelante_nao_toca_som_de_novo_pro_proprio_conjurador(self):
        client = _FakeClient(my_eid=1)
        payload = {"sid": "nova_congelante", "event": "impact",
                   "caster_eid": 1, "tx": 5, "ty": 5, "target_eid": -1}
        with patch("client.network_handlers.SOUNDS") as mock_sounds:
            client._handle_msg_skill_effect(payload)
            mock_sounds.play_skill.assert_not_called()
            mock_sounds.play_random.assert_not_called()

    def test_nova_congelante_continua_tocando_pra_outro_player_por_perto(self):
        client = _FakeClient(my_eid=1)
        payload = {"sid": "nova_congelante", "event": "impact",
                   "caster_eid": 999, "tx": 5, "ty": 5, "target_eid": -1}
        with patch("client.network_handlers.SOUNDS") as mock_sounds:
            client._handle_msg_skill_effect(payload)
            mock_sounds.play_skill_at.assert_called_once()

    def test_calcinar_sem_effects_no_catalogo_nao_toca_nada_via_rede(self):
        # Guarda de regressão: calcinar nunca teve "effects" no catálogo,
        # então o handler de rede não tem o que tocar (som só vem da
        # previsão local) — precisa continuar assim.
        client = _FakeClient(my_eid=1)
        payload = {"sid": "calcinar", "event": "impact",
                   "caster_eid": 1, "tx": 5, "ty": 5, "target_eid": -1}
        with patch("client.network_handlers.SOUNDS") as mock_sounds:
            client._handle_msg_skill_effect(payload)
            mock_sounds.play_skill.assert_not_called()
            mock_sounds.play_random.assert_not_called()


if __name__ == "__main__":
    unittest.main()
