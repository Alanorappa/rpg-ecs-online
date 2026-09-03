"""
tests/test_buff_bar_tooltip.py — 01/09/2026, tooltip ao passar o mouse
nos ícones da buff bar (pedido do usuário, igual já existe nas skills
da hotbar). Sistema centralizado, não hardcoded: descrição fica em
content/status_effects_data.py::EFFECT_DEFS (mesmo catálogo que já dá
ícone/cor), e a geometria de hit-test vem do MESMO cálculo que já
desenha cada ícone (_build_effects_row devolve `(surface, rects)`,
nunca duplica a matemática num hit-test separado — mesmo princípio já
usado pela hotbar, client/hotbar_handlers.py).
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from content.status_effects_data import EFFECT_DEFS
from ui.systems import _build_effects_row
from ui.fonts import make as _font
from engine.components import ActiveEffect
from ui.buff_bar import BuffBar, _FakeEffect


# Todo effect_type que a buff bar PODE mostrar hoje — StatusEffects
# (fases 1) + _gather_extra_buffs (fase 2). Lista espelha exatamente o
# que já existe no jogo (não inventa nada novo) — serve de guarda de
# regressão pra buff futuro esquecer a descrição.
_BUFF_BAR_EFFECT_TYPES = [
    "cc_immune",
    "proc_amulet_warrior", "proc_amulet_hunter", "proc_amulet_arcane",
    "proc_death_blade", "proc_doom_axe", "proc_phantom_bow",
    "proc_amulet_undying", "proc_ring_fury",
    "cancao_inspiracao", "so_um_gole",
    "jungle_boss_buff_jungle_boss_ancestral",
    "jungle_boss_buff_jungle_boss_guardiao_de_pedra",
    "jungle_boss_buff_jungle_boss_dragao_jovem",
    "jungle_boss_buff_jungle_boss_horror_das_profundezas",
    "lapso_elemental_crit",
    "escudo_fogo", "bloco_de_gelo", "camuflagem", "chama_interna", "na_mosca",
]


class TestEffectDefsHaveDescription(unittest.TestCase):

    def test_toda_entrada_de_buff_bar_tem_descricao(self):
        for etype in _BUFF_BAR_EFFECT_TYPES:
            self.assertIn(etype, EFFECT_DEFS, f"{etype} sumiu de EFFECT_DEFS")
            defn = EFFECT_DEFS[etype]
            self.assertTrue(defn.is_buff, f"{etype} deveria ser is_buff=True")
            self.assertTrue(defn.description.strip(),
                            f"{etype} não tem description — tooltip ficaria vazio")


class TestBuildEffectsRowRects(unittest.TestCase):

    def setUp(self):
        self.font = _font(18)

    def test_sem_efeitos_devolve_none_e_lista_vazia(self):
        row, rects = _build_effects_row([], self.font)
        self.assertIsNone(row)
        self.assertEqual(rects, [])

    def test_rects_batem_com_posicao_e_ordem(self):
        effs = [
            ActiveEffect(effect_type="proc_amulet_warrior", duration=16.0),
            ActiveEffect(effect_type="cc_immune", duration=5.0),
        ]
        row, rects = _build_effects_row(effs, self.font, icon_size=32)
        self.assertIsNotNone(row)
        self.assertEqual([et for et, _ in rects], ["proc_amulet_warrior", "cc_immune"])
        # 1º ícone em x=0, 2º em x=32+3=35 (ICON=32, GAP=3)
        self.assertEqual(rects[0][1], pygame.Rect(0, 0, 32, 32))
        self.assertEqual(rects[1][1], pygame.Rect(35, 0, 32, 32))
        self.assertEqual(row.get_size(), (67, 32))


class TestBuffBarCheckHover(unittest.TestCase):

    def setUp(self):
        # BuffBar.check_hover só lê self._last_icon_rects — não precisa
        # de WorldServer/entidade real pra este teste, só simula o que
        # render() já teria preenchido.
        self.bb = BuffBar(world=None, player_entity=-1)

    def test_mouse_sobre_icone_devolve_label_e_descricao(self):
        self.bb._last_icon_rects = [
            ("proc_amulet_warrior", pygame.Rect(100, 50, 32, 32), 16.0),
            ("cc_immune",           pygame.Rect(135, 50, 32, 32), 5.0),
        ]
        result = self.bb.check_hover(110, 60)   # dentro do 1º ícone
        self.assertIsNotNone(result)
        title, lines = result
        self.assertEqual(title, EFFECT_DEFS["proc_amulet_warrior"].label)
        self.assertEqual(lines[0][0], EFFECT_DEFS["proc_amulet_warrior"].description)
        self.assertIn("restantes", lines[1][0])

    def test_mouse_fora_de_qualquer_icone_devolve_none(self):
        self.bb._last_icon_rects = [
            ("proc_amulet_warrior", pygame.Rect(100, 50, 32, 32), 16.0),
        ]
        self.assertIsNone(self.bb.check_hover(0, 0))

    def test_proc_sem_duracao_nao_mostra_linha_de_tempo(self):
        self.bb._last_icon_rects = [
            ("chama_interna", pygame.Rect(0, 0, 32, 32), 0.0),
        ]
        title, lines = self.bb.check_hover(5, 5)
        self.assertEqual(title, "Chama Interna")
        self.assertFalse(any("restantes" in l[0] for l in lines))


if __name__ == "__main__":
    unittest.main()
