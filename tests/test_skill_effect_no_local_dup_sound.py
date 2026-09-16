"""
tests/test_skill_effect_no_local_dup_sound.py — histórico de 2 bugs
reais no mesmo mecanismo (som de impacto de Nova Congelante):

14/09/2026 (§137): tocava DUAS vezes — uma na previsão local do
cliente (`ui/spell_system.py::SpellCastSystem._apply_nova_congelante`,
chamada quando a barra de cast LOCAL termina) e outra quando a
confirmação de rede chegava de volta pro PRÓPRIO conjurador
(`SKILL_EFFECT`, `client/network_handlers.py::_handle_msg_skill_effect`)
— esse broadcast existe pra QUALQUER outro player perto ouvir a skill,
mas o conjurador também está dentro do próprio raio de AOI. Fix na
época: flag `skip_local_caster` em `effects.<evento>` no catálogo —
suprimia o eco de rede pro conjurador, que já tinha ouvido local.

16/09/2026 (§148): com "Precisão Elemental" maxada, o cast de Nova
Congelante vira instantâneo — o cliente nem cria a barra de cast local
nesse caso, então a previsão local NUNCA rodava, e o `skip_local_caster`
suprimia o único som que restava (o eco de rede), deixando o conjurador
em silêncio total. Fix definitivo: removida a previsão local (o
`SOUNDS.play_spell` que existia em `_apply_nova_congelante`) E o
`skip_local_caster` — o som passa a vir SÓ do `SKILL_EFFECT` do
servidor, pro conjurador igual pra qualquer outro player, funcionando
idêntico com cast normal ou instantâneo (mesmo modelo de Bola de Fogo).
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

    def test_nova_congelante_toca_pro_proprio_conjurador_via_servidor(self):
        # Sem previsão local nem skip_local_caster (§148) — o som do
        # PRÓPRIO conjurador vem só daqui, igual qualquer outro player.
        client = _FakeClient(my_eid=1)
        payload = {"sid": "nova_congelante", "event": "impact",
                   "caster_eid": 1, "tx": 5, "ty": 5, "target_eid": -1}
        with patch("client.network_handlers.SOUNDS") as mock_sounds:
            client._handle_msg_skill_effect(payload)
            mock_sounds.play_skill.assert_called_once_with("skill_nova_congelante_impact")

    def test_nova_congelante_nao_tem_mais_skip_local_caster_no_catalogo(self):
        # Guarda de regressão pro bug de §148 (cast instantâneo via
        # Precisão Elemental maxada ficava mudo pro conjurador) — se
        # skip_local_caster voltar sem uma previsão local de verdade pra
        # cobrir o caso instantâneo, o silêncio total volta.
        from content.skill_config import SKILL_CATALOG
        fx = SKILL_CATALOG["nova_congelante"].get("effects", {}).get("impact", {})
        self.assertFalse(fx.get("skip_local_caster", False))

    def test_nova_congelante_continua_tocando_pra_outro_player_por_perto(self):
        client = _FakeClient(my_eid=1)
        payload = {"sid": "nova_congelante", "event": "impact",
                   "caster_eid": 999, "tx": 5, "ty": 5, "target_eid": -1}
        with patch("client.network_handlers.SOUNDS") as mock_sounds:
            client._handle_msg_skill_effect(payload)
            mock_sounds.play_skill_at.assert_called_once()

    def test_apply_nova_congelante_local_nao_toca_som_diretamente(self):
        # Guarda de regressão pro bug de §148 — se _apply_nova_congelante
        # voltar a chamar SOUNDS.play_spell diretamente, o cast NORMAL
        # (cast_time > 0, SpellCast local existe) volta a duplicar o som
        # (previsão local + eco de rede, já que não tem mais
        # skip_local_caster pra suprimir o eco).
        import pygame
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        pygame.display.set_mode((10, 10))
        from engine.components import TileMovement, CombatStats
        from ui.spell_system import SpellCastSystem

        world = World()
        eid = world.create_entity()
        world.add_component(eid, TileMovement(current_tile_x=5, current_tile_y=5))
        world.add_component(eid, CombatStats())
        sys_ = SpellCastSystem(world, pygame.Surface((10, 10)))

        with patch("ui.spell_system.SOUNDS") as mock_sounds:
            sys_._apply_nova_congelante(eid, -1)
            mock_sounds.play_spell.assert_not_called()

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
