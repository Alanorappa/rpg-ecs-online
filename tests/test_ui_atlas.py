"""
tests/test_ui_atlas.py — ui/ui_atlas.py (montagem de UI por tiles 16x16
do atlas assets/ui/ui_elements.png) e a primeira tela a usá-lo,
client/menu_handlers.py::_draw_sound_submenu.

Fixture leve (mesmo padrão de _ArenaFixture em test_arena_client_ui.py)
— não precisa de um GameEngine inteiro, só dos atributos que
MenuHandlers de fato usa.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
# NUNCA pygame.display.set_mode() fixo aqui no nível do módulo — outros
# arquivos de teste (test_client_ui.py etc.) fazem o mesmo na importação
# deles, o pytest importa TODOS os módulos antes de rodar qualquer
# teste, e a última chamada vaza pro resto da sessão (quebrava
# test_clique_na_sidebar_troca_categoria, que assume 320x240). O tamanho
# maior que os cliques daqui precisam é setado e desfeito por teste —
# ver `_bigger_display`.
if pygame.display.get_surface() is None:
    pygame.display.set_mode((320, 240))

from ui import ui_atlas
from client.menu_handlers import MenuHandlers
from ui.sound_manager import SOUNDS

T, S = ui_atlas.TILE, ui_atlas.SCALE


def _atlas_tile_bytes(tile):
    c, r = tile
    return ui_atlas._load().subsurface((c * T, r * T, T, T)).copy()


def _same(a, b):
    return pygame.image.tobytes(a, "RGBA") == pygame.image.tobytes(b, "RGBA")


# ── ui_atlas.py — montagem por tiles ─────────────────────────────────────

def test_painel_de_madeira_repete_tiles_sem_esticar():
    """Cada tile 16x16 do painel montado é uma cópia EXATA de um tile do
    atlas (nunca esticado): cantos nos cantos, miolo liso no meio."""
    tw, th = 9, 8
    panel = ui_atlas.wood_panel_raw(tw, th)
    assert panel.get_size() == (tw * T, th * T)

    def tile_at(i, j):
        return panel.subsurface((i * T, j * T, T, T)).copy()

    assert _same(tile_at(0, 0), _atlas_tile_bytes(ui_atlas._WOOD["tl"]))
    assert _same(tile_at(tw - 1, 0), _atlas_tile_bytes(ui_atlas._WOOD["tr"]))
    assert _same(tile_at(0, th - 1), _atlas_tile_bytes(ui_atlas._WOOD["bl"]))
    assert _same(tile_at(tw - 1, th - 1), _atlas_tile_bytes(ui_atlas._WOOD["br"]))
    center = _atlas_tile_bytes(ui_atlas._WOOD["center"])
    for j in range(1, th - 1):
        for i in range(1, tw - 1):
            assert _same(tile_at(i, j), center), f"miolo ({i},{j}) não é o tile liso"
    top_opts = [_atlas_tile_bytes(t) for t in ui_atlas._WOOD["top"]]
    for i in range(1, tw - 1):
        assert any(_same(tile_at(i, 0), o) for o in top_opts)


def test_botao_largo_repete_o_tile_do_meio():
    raw = pygame.Surface((5 * T, 2 * T), pygame.SRCALPHA)
    ui_atlas.blit_button(raw, 0, 0, 5)
    c0, r0 = ui_atlas._BTN_IDLE_COL0, ui_atlas._BTN_ROW0
    for k in range(2):
        left = raw.subsurface((0, k * T, T, T)).copy()
        assert _same(left, _atlas_tile_bytes((c0, r0 + k)))
        for i in (1, 2, 3):
            mid = raw.subsurface((i * T, k * T, T, T)).copy()
            assert _same(mid, _atlas_tile_bytes((c0 + 1, r0 + k)))
        right = raw.subsurface((4 * T, k * T, T, T)).copy()
        assert _same(right, _atlas_tile_bytes((c0 + 2, r0 + k)))


# ── client/menu_handlers.py::_draw_sound_submenu ─────────────────────────

class _MenuFixture(MenuHandlers):
    def __init__(self):
        self.screen            = pygame.Surface((1280, 720))
        self._ui_scale         = 1.0
        self._u_scale_override = None
        from ui.fonts import make as _font
        self.font_xs = _font(14)
        self.font_sm = _font(18)
        self.font_md = _font(22)
        self._pause_submenu = "sound"
        self._sound_drag    = ""
        self._sound_arrow_hold = None
        self._sound_back_pressed = False
        self._save_config_calls = 0

    def _u(self, px: int) -> int:
        return px

    def _set_panel_scale(self, *a, **k):
        pass

    def _save_config(self):
        self._save_config_calls += 1

    def origin(self):
        pw, ph = self._SND_TW * T * S, self._SND_TH * T * S
        return (self.screen.get_width() - pw) // 2, (self.screen.get_height() - ph) // 2

    def to_screen(self, rx, ry):
        px, py = self.origin()
        return int(px + rx * S), int(py + ry * S)


class _bigger_display:
    """pygame.mouse.set_pos() clampa pra dentro da janela de DISPLAY real
    (não da Surface off-screen do fixture). Aumenta só durante o teste e
    restaura ao sair — ver comentário no topo do arquivo."""

    def __enter__(self):
        self._prev = pygame.display.get_surface().get_size()
        pygame.display.set_mode((1280, 720))
        return self

    def __exit__(self, *exc):
        pygame.display.set_mode(self._prev)


def _click_at(pos):
    """O menu de pausa lê a posição REAL do cursor (pygame.mouse.get_pos),
    o evento só sinaliza 'houve clique' — por isso move o cursor de
    verdade (chamar dentro de `with _bigger_display():`)."""
    pygame.mouse.set_pos(pos)
    return [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)]


def test_clicar_no_icone_alterna_mute_sem_tocar_no_volume():
    fx = _MenuFixture()
    orig = (SOUNDS.music_enabled, SOUNDS.music_volume, SOUNDS.apply_music_settings)
    try:
        SOUNDS.music_enabled = True
        SOUNDS.music_volume  = 0.7
        SOUNDS.apply_music_settings = lambda: None
        icon = fx.to_screen(fx._SND_COL_ICON * T + T / 2, fx._SND_ROW_MUSIC * T + T / 2)
        with _bigger_display():
            fx._draw_sound_submenu(_click_at(icon))
        assert SOUNDS.music_enabled is False
        assert SOUNDS.music_volume == 0.7
        assert fx._save_config_calls == 1
    finally:
        SOUNDS.music_enabled, SOUNDS.music_volume, SOUNDS.apply_music_settings = orig


def test_arrastar_no_trilho_ajusta_volume_proporcional():
    fx = _MenuFixture()
    orig = (SOUNDS.sfx_volume, SOUNDS.apply_music_settings)
    try:
        SOUNDS.sfx_volume = 0.0
        SOUNDS.apply_music_settings = lambda: None
        # meio da área das barrinhas (entre as setas) -> ~50%
        bar_x0 = (fx._SND_COL_ARROW_L + 1) * T
        n_max  = (fx._SND_COL_ARROW_R - fx._SND_COL_ARROW_L - 1) * T // ui_atlas.SLIDER_FILL_BAR_W
        mid_rx = bar_x0 + n_max * ui_atlas.SLIDER_FILL_BAR_W / 2
        pos = fx.to_screen(mid_rx, fx._SND_ROW_SFX * T + T / 2)
        with _bigger_display():
            fx._draw_sound_submenu(_click_at(pos))
        assert fx._sound_drag == "sfx"
        assert abs(SOUNDS.sfx_volume - 0.5) < 0.05
    finally:
        SOUNDS.sfx_volume, SOUNDS.apply_music_settings = orig


class _fake_mouse_and_clock:
    """Simula botão do mouse segurado + relógio controlado (dummy driver
    nunca reporta botão pressionado de verdade)."""

    def __init__(self):
        self.now = 1000

    def __enter__(self):
        self._orig = (pygame.mouse.get_pressed, pygame.time.get_ticks)
        pygame.mouse.get_pressed = lambda *a, **k: (True, False, False)
        pygame.time.get_ticks = lambda: self.now
        return self

    def __exit__(self, *exc):
        pygame.mouse.get_pressed, pygame.time.get_ticks = self._orig


def _arrow_pos(fx, col, row):
    return fx.to_screen(col * T + T / 2, row * T + T / 2)


def test_clique_na_seta_muda_1_por_cento_e_nao_inicia_arraste():
    fx = _MenuFixture()
    orig = (SOUNDS.music_volume, SOUNDS.apply_music_settings)
    try:
        SOUNDS.music_volume = 0.50
        SOUNDS.apply_music_settings = lambda: None
        with _bigger_display():
            fx._draw_sound_submenu(_click_at(_arrow_pos(fx, fx._SND_COL_ARROW_R, fx._SND_ROW_MUSIC)))
            assert abs(SOUNDS.music_volume - 0.51) < 1e-9
            assert fx._sound_drag == "", "clicar na seta não pode virar arraste (pulava pra 0/100%)"
            fx._sound_arrow_hold = None
            fx._draw_sound_submenu(_click_at(_arrow_pos(fx, fx._SND_COL_ARROW_L, fx._SND_ROW_MUSIC)))
            fx._draw_sound_submenu(_click_at(_arrow_pos(fx, fx._SND_COL_ARROW_L, fx._SND_ROW_MUSIC)))
            assert abs(SOUNDS.music_volume - 0.49) < 1e-9
    finally:
        SOUNDS.music_volume, SOUNDS.apply_music_settings = orig


def test_segurar_a_seta_sobe_1_barrinha_a_cada_250ms():
    fx = _MenuFixture()
    orig = (SOUNDS.sfx_volume, SOUNDS.apply_music_settings)
    n_max = (fx._SND_COL_ARROW_R - fx._SND_COL_ARROW_L - 1) * T // ui_atlas.SLIDER_FILL_BAR_W
    try:
        SOUNDS.sfx_volume = 0.0
        SOUNDS.apply_music_settings = lambda: None
        pos = _arrow_pos(fx, fx._SND_COL_ARROW_R, fx._SND_ROW_SFX)
        with _bigger_display(), _fake_mouse_and_clock() as clk:
            fx._draw_sound_submenu(_click_at(pos))           # clique: +1%
            assert abs(SOUNDS.sfx_volume - 0.01) < 1e-9
            clk.now += 249
            fx._draw_sound_submenu([])                       # ainda não repetiu
            assert abs(SOUNDS.sfx_volume - 0.01) < 1e-9
            clk.now += 1
            fx._draw_sound_submenu([])                       # 250ms: 1 barrinha
            assert abs(SOUNDS.sfx_volume - 1 / n_max) < 1e-9
            clk.now += 250
            fx._draw_sound_submenu([])                       # +250ms: 2 barrinhas
            assert abs(SOUNDS.sfx_volume - 2 / n_max) < 1e-9
    finally:
        SOUNDS.sfx_volume, SOUNDS.apply_music_settings = orig


def _icon_drawn(fx, row):
    """Qual tile de ícone foi carimbado na coluna do ícone da linha `row`."""
    calls = []
    orig = ui_atlas.blit_tile
    ui_atlas.blit_tile = lambda dst, tile, tx, ty: (calls.append((tile, tx, ty)), orig(dst, tile, tx, ty))
    try:
        fx._draw_sound_submenu([])
    finally:
        ui_atlas.blit_tile = orig
    return [t for t, tx, ty in calls if tx == fx._SND_COL_ICON and ty == row][-1]


def test_volume_zero_mostra_icone_de_som_desligado():
    fx = _MenuFixture()
    orig = (SOUNDS.music_enabled, SOUNDS.music_volume)
    try:
        SOUNDS.music_enabled = True
        SOUNDS.music_volume = 0.0
        assert _icon_drawn(fx, fx._SND_ROW_MUSIC) == ui_atlas.ICON_MUSIC_OFF
        SOUNDS.music_volume = 0.01
        assert _icon_drawn(fx, fx._SND_ROW_MUSIC) == ui_atlas.ICON_MUSIC_ON
    finally:
        SOUNDS.music_enabled, SOUNDS.music_volume = orig


def _back_center(fx):
    bx0 = fx._SND_BTN_COL * T
    bx1 = (fx._SND_BTN_COL + fx._SND_BTN_W) * T
    by  = fx._SND_BTN_ROW * T + (ui_atlas.BTN_ART_Y0 + ui_atlas.BTN_ART_Y1) / 2
    return fx.to_screen((bx0 + bx1) / 2, by)


def _release_at(pos):
    pygame.mouse.set_pos(pos)
    return [pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos)]


def test_botao_voltar_afunda_ao_apertar_e_fecha_ao_soltar():
    """Age no SOLTAR — se agisse no clique, a janela fechava no mesmo
    frame e o estado pressionado nunca aparecia (bug relatado)."""
    fx = _MenuFixture()
    pos = _back_center(fx)
    with _bigger_display():
        with _fake_mouse_and_clock():
            fx._draw_sound_submenu(_click_at(pos))
        assert fx._sound_back_pressed is True
        assert fx._pause_submenu == "sound", "não pode fechar no clique, só no soltar"
        fx._draw_sound_submenu(_release_at(pos))
    assert fx._pause_submenu == ""


def test_soltar_fora_do_botao_cancela():
    fx = _MenuFixture()
    pos = _back_center(fx)
    with _bigger_display():
        with _fake_mouse_and_clock():
            fx._draw_sound_submenu(_click_at(pos))
        fx._draw_sound_submenu(_release_at((pos[0], pos[1] - 200)))
    assert fx._pause_submenu == "sound"
    assert fx._sound_back_pressed is False


def test_clique_fora_do_botao_nao_fecha():
    """Faixa transparente do bloco 3x2 do botão (acima da arte) não
    conta como clique no botão."""
    fx = _MenuFixture()
    bx = (fx._SND_BTN_COL + 1) * T + T / 2
    by = fx._SND_BTN_ROW * T + 2
    with _bigger_display():
        with _fake_mouse_and_clock():
            fx._draw_sound_submenu(_click_at(fx.to_screen(bx, by)))
        fx._draw_sound_submenu(_release_at(fx.to_screen(bx, by)))
    assert fx._pause_submenu == "sound"
