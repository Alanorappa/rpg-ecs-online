"""
tests/test_char_select_ui.py — tela de seleção de personagem no atlas de
UI (ui/char_creation_screen.py::_draw_selection/_draw_confirm_delete).
Botões agem no SOLTAR do mouse; o driver dummy não move o cursor, então
os testes falsificam pygame.mouse.get_pos/get_pressed.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
import pytest
pygame.init()

import ui.char_creation_screen as cs
from ui import atlas_window as aw
from ui.fonts import make as _font
from shared.constants import MAX_CHARACTERS_PER_ACCOUNT

_W, _H = 1280, 720


@pytest.fixture(autouse=True)
def _display():
    """Atlas carrega com convert_alpha (precisa de janela); restaura o
    tamanho anterior no fim pra não vazar pros outros arquivos de teste."""
    prev = pygame.display.get_surface()
    prev_size = prev.get_size() if prev is not None else None
    pygame.display.set_mode((_W, _H))
    yield
    if prev_size is not None:
        pygame.display.set_mode(prev_size)


def _chars(n):
    classes = ["guerreiro", "mago", "arqueiro"]
    return [{"id": 100 + i, "name": f"Heroi{i}", "class_id": classes[i % 3], "level": i + 1}
            for i in range(n)]


class _Ui:
    def __init__(self, chars, can_create=True):
        self.screen = pygame.Surface((_W, _H))
        self.chars, self.can_create = chars, can_create
        self.press, self.scroll = aw.PressState(), aw.ScrollState()
        self.fmd, self.fsm, self.fxs = _font(24), _font(19), _font(15)

    def frame(self, events=(), confirm_name=None):
        hits = {}
        # Mesmo que run_online: com a confirmação aberta, a lista de fundo
        # não recebe eventos e usa um estado de botão descartável.
        action, rect = cs._draw_selection(self.screen, [] if confirm_name else list(events),
                                          self.chars, self.can_create,
                                          aw.PressState() if confirm_name else self.press, self.scroll,
                                          self.fmd, self.fsm, self.fxs, 1.0, hits=hits)
        answer = None
        if confirm_name:
            answer = cs._draw_confirm_delete(self.screen, list(events), confirm_name,
                                             self.press, self.fmd, self.fsm, hits=hits)
        return action, rect, hits, answer

    def click(self, hit_id, confirm_name=None):
        """Aperta e solta no centro do elemento; devolve (ação, resposta) do soltar."""
        _, _, hits, _ = self.frame(confirm_name=confirm_name)
        pos = hits[hit_id].center
        orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
        try:
            pygame.mouse.get_pos = lambda: pos
            pygame.mouse.get_pressed = lambda num_buttons=3: (True, False, False)
            self.frame([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)], confirm_name)
            pygame.mouse.get_pressed = lambda num_buttons=3: (False, False, False)
            action, _, _, answer = self.frame(
                [pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos)], confirm_name)
        finally:
            pygame.mouse.get_pos, pygame.mouse.get_pressed = orig
        return action, answer


def test_limite_de_personagens_e_8():
    assert MAX_CHARACTERS_PER_ACCOUNT == 8


def test_jogar_e_excluir_disparam_no_soltar_com_o_indice_certo():
    ui = _Ui(_chars(2))
    assert ui.click("sel:play:1")[0] == ("play", 1)
    assert ui.click("sel:del:0")[0] == ("delete", 0)


def test_apertar_sem_soltar_nao_dispara():
    ui = _Ui(_chars(1))
    _, _, hits, _ = ui.frame()
    pos = hits["sel:play:0"].center
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
    try:
        pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: (True, False, False)
        action, *_ = ui.frame([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)])
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed = orig
    assert action is None


def test_criar_e_sair():
    ui = _Ui(_chars(1))
    assert ui.click("sel:create")[0] == ("create",)
    assert ui.click("sel:quit")[0] == ("quit",)


def test_criar_desabilitado_no_limite_nao_dispara():
    ui = _Ui(_chars(MAX_CHARACTERS_PER_ACCOUNT), can_create=False)
    assert ui.click("sel:create")[0] is None


def test_sempre_3_molduras_e_rola_com_mais_de_3():
    ui = _Ui(_chars(1))
    lay = cs._sel_layout(ui.chars, ui.fmd, ui.fsm)
    assert lay["n_slots"] == 3 and lay["view"] == 3
    ui = _Ui(_chars(8))
    _, _, hits, _ = ui.frame()
    assert "sel:play:0" in hits and "sel:play:5" not in hits   # fora da área visível
    _, rect, _, _ = ui.frame()
    orig = pygame.mouse.get_pos
    try:
        pygame.mouse.get_pos = lambda: rect.center
        ui.frame([pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-20)])
    finally:
        pygame.mouse.get_pos = orig
    _, _, hits, _ = ui.frame()
    assert ui.scroll.offset > 0
    assert "sel:play:7" in hits and "sel:play:0" not in hits


def test_janela_dentro_da_area_segura_com_fonte_grande():
    ui = _Ui(_chars(8))
    ui.fmd, ui.fsm = _font(40), _font(32)
    _, rect, _, _ = ui.frame()
    assert aw.safe_area(_W, _H, _H).contains(rect)


def test_confirmacao_excluir_e_cancelar():
    ui = _Ui(_chars(2))
    assert ui.click("del:yes", confirm_name="Heroi0")[1] == "yes"
    assert ui.click("del:no", confirm_name="Heroi0")[1] == "no"


def test_confirmacao_aberta_bloqueia_botoes_da_lista():
    ui = _Ui(_chars(2))
    _, _, hits, _ = ui.frame()
    pos = hits["sel:quit"].center
    orig = pygame.mouse.get_pos, pygame.mouse.get_pressed
    try:
        pygame.mouse.get_pos = lambda: pos
        pygame.mouse.get_pressed = lambda num_buttons=3: (True, False, False)
        ui.frame([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)], "Heroi0")
        pygame.mouse.get_pressed = lambda num_buttons=3: (False, False, False)
        action, _, _, answer = ui.frame(
            [pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos)], "Heroi0")
    finally:
        pygame.mouse.get_pos, pygame.mouse.get_pressed = orig
    assert action is None and answer is None
