"""
tests/test_nameplate_buff_filter.py — 31/08/2026, buff bar unificada.
Buff ganhou barra própria (ui/buff_bar.py, só jogador local) — o
nameplate (ui/systems.py::RenderSystem.render) não deve mais mostrar
ícone de buff junto do de debuff, senão duplica em 2 lugares. Testa
ui.systems._filter_nameplate_effects isoladamente (extraído de propósito
pra não precisar montar a pipeline de render inteira/tela real).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from ui.systems import _filter_nameplate_effects
from engine.components import StatusEffects, ActiveEffect


class TestFilterNameplateEffects(unittest.TestCase):

    def test_exclui_buff_mantem_debuff(self):
        sfx = StatusEffects()
        sfx.effects["stun"] = ActiveEffect(effect_type="stun", duration=2.0)
        sfx.effects["proc_amulet_warrior"] = ActiveEffect(effect_type="proc_amulet_warrior", duration=16.0)

        result = _filter_nameplate_effects(sfx)

        _types = {e.effect_type for e in result}
        self.assertIn("stun", _types)
        self.assertNotIn("proc_amulet_warrior", _types)

    def test_sfx_none_retorna_lista_vazia(self):
        self.assertEqual(_filter_nameplate_effects(None), [])

    def test_tipo_desconhecido_fica_visivel_por_padrao(self):
        sfx = StatusEffects()
        sfx.effects["algo_inventado"] = ActiveEffect(effect_type="algo_inventado", duration=1.0)
        result = _filter_nameplate_effects(sfx)
        self.assertEqual(len(result), 1)

    def test_enraged_e_taunted_aparecem_juntos_no_nameplate(self):
        """Bug real relatado pelo usuário (03/10/2026): "enraged" (bônus
        de dano do Brado Provocativo, is_buff=True até esta correção)
        nunca aparecia sobre mob/oponente de PvP -- buff bar só mostra
        pro jogador LOCAL, nameplate só mostra is_buff=False. Com
        is_buff=True, o único ícone visível no alvo provocado era
        "taunted" (3s) -- o bônus de dano real (10s) nunca tinha
        representação visual em quem estava sendo observado."""
        sfx = StatusEffects()
        sfx.effects["taunted"] = ActiveEffect(effect_type="taunted", duration=3.0)
        sfx.effects["enraged"] = ActiveEffect(effect_type="enraged", duration=10.0)
        result = _filter_nameplate_effects(sfx)
        _types = {e.effect_type for e in result}
        self.assertEqual(_types, {"taunted", "enraged"})


if __name__ == "__main__":
    unittest.main()
