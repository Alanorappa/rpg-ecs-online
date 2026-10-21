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
    c0, r0 = ui_atlas._BTN_IDLE_COL0, ui_atlas._BTN_ROW_TALL
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
        self._mm_pressed    = ""
        self._save_config_calls = 0
        self._scale = 1.0
        self._window_mode_pref = "fullscreen"
        self.ui_scale_set = []

    def _set_ui_scale(self, v):
        self.ui_scale_set.append(v)

    def _save_config(self):
        self._save_config_calls += 1

    def to_screen(self, rx, ry, tw=None, th=None):
        tw = tw or self._SND_TW
        th = th or self._SND_TH
        px = (self.screen.get_width()  - tw * T * S) // 2
        py = (self.screen.get_height() - th * T * S) // 2
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


def _btn_center(fx, tx, ty, w, tall, tw=None, th=None):
    y0, y1 = ui_atlas.button_art_y(tall)
    return fx.to_screen((tx + w / 2) * T, ty * T + (y0 + y1) / 2, tw, th)


def _back_center(fx, tw=None, th=None, row=None):
    tw = tw or fx._SND_TW
    w = fx._mm_tiles_for(["Voltar"], 3)
    return _btn_center(fx, (tw - w) // 2, row if row is not None else fx._SND_BTN_ROW,
                       w, True, tw, th)


def _press_and_release(fx, draw, pos):
    """Clique completo: aperta (frame com botão segurado) e solta."""
    with _fake_mouse_and_clock():
        r1 = draw(_click_at(pos))
    r2 = draw(_release_at(pos))
    return r1, r2


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
        assert fx._mm_pressed == "snd:voltar"
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
    assert fx._mm_pressed == ""


def test_clique_fora_do_botao_nao_fecha():
    """Faixa transparente do bloco 3x2 do botão (acima da arte) não
    conta como clique no botão."""
    fx = _MenuFixture()
    w = fx._mm_tiles_for(["Voltar"], 3)
    bx = ((fx._SND_TW - w) // 2 + 1) * T + T / 2
    by = fx._SND_BTN_ROW * T + 2
    with _bigger_display():
        with _fake_mouse_and_clock():
            fx._draw_sound_submenu(_click_at(fx.to_screen(bx, by)))
        fx._draw_sound_submenu(_release_at(fx.to_screen(bx, by)))
    assert fx._pause_submenu == "sound"


# ── Menu principal / confirmação de saída / Resolução / Interface ─────────

def test_menu_principal_botao_age_no_soltar_e_abre_submenu():
    fx = _MenuFixture()
    fx._pause_submenu = ""
    tw, th, btn_w = fx._main_layout()
    labels = [lbl for lbl, _ in fx._MAIN_BTNS]
    i_som = labels.index("Som")
    pos = _btn_center(fx, 1, fx._MAIN_ROW0 + i_som, btn_w, False, tw, th)
    with _bigger_display():
        r1, r2 = _press_and_release(fx, fx._draw_pause_menu, pos)
    assert r1 is None
    assert fx._pause_submenu == "sound"


def test_menu_principal_continuar_devolve_resume():
    fx = _MenuFixture()
    fx._pause_submenu = ""
    tw, th, btn_w = fx._main_layout()
    pos = _btn_center(fx, 1, fx._MAIN_ROW0, btn_w, False, tw, th)
    with _bigger_display():
        _, r2 = _press_and_release(fx, fx._draw_pause_menu, pos)
    assert r2 == "resume"


def test_textos_cabem_nos_botoes_e_seletores_com_fonte_grande():
    """Regressão: a fonte acompanha a escala de UI do jogador (no jogo,
    1.25 ou mais), o atlas não — com largura fixa em tiles, "Atalhos do
    teclado" vazava do botão e "1.5x — 1920 × 1080" passava por cima das
    setas. Simula escala de UI 2.0 (fonte 44) e confere cada caso."""
    from ui.fonts import make as _font
    from ui.settings_screen import SCALE_OPTIONS
    fx = _MenuFixture()
    fx.font_md = _font(44)
    S_, T_ = ui_atlas.SCALE, ui_atlas.TILE

    def fits(text, tiles):
        return fx.font_md.size(text)[0] <= tiles * T_ * S_ - 2 * fx._MM_TEXT_PAD * S_

    tw, th, btn_w = fx._main_layout()
    for label, _ in fx._MAIN_BTNS:
        assert fits(label, btn_w), f"'{label}' não cabe no botão do menu ({btn_w} tiles)"
    assert tw == btn_w + 2

    tw, inner = fx._res_layout()
    for label in [l for l, _ in SCALE_OPTIONS] + ["Tela cheia", "Janela"]:
        assert fits(label, inner), f"'{label}' passa por cima das setas ({inner} tiles)"

    tw, inner = fx._iface_layout()
    assert fits(f"{2.0:.2f}×", inner)
    assert fx.font_md.size(fx._IFACE_LABEL)[0] <= (tw - 2) * T_ * S_

    tw, btn_w, col_sim, col_nao = fx._quit_layout()
    assert fx.font_md.size(fx._QUIT_MSG)[0] <= (tw - 2) * T_ * S_
    assert fits("Não", btn_w) and col_nao + btn_w <= tw - 1

    assert fits("Voltar", fx._mm_tiles_for(["Voltar"], 3))


def test_menu_principal_textos_em_portugues():
    fx = _MenuFixture()
    labels = [lbl for lbl, _ in fx._MAIN_BTNS]
    assert labels == ["Continuar", "Resolução", "Som", "Interface", "Atalhos do teclado",
                      "Voltar ao Spawn", "Deslogar", "Sair"]


def test_confirmacao_sair_sim_devolve_quit_e_nao_volta():
    fx = _MenuFixture()
    fx._pause_submenu = "quit_confirm"
    tw, btn_w, col_sim, col_nao = fx._quit_layout()
    th, row = fx._QUIT_TH, fx._QUIT_BTN_ROW
    with _bigger_display():
        _, r2 = _press_and_release(fx, fx._draw_pause_menu,
                                   _btn_center(fx, col_sim, row, btn_w, False, tw, th))
    assert r2 == "quit"
    fx = _MenuFixture()
    fx._pause_submenu = "quit_confirm"
    with _bigger_display():
        _, r2 = _press_and_release(fx, fx._draw_pause_menu,
                                   _btn_center(fx, col_nao, row, btn_w, False, tw, th))
    assert r2 is None and fx._pause_submenu == ""


def test_resolucao_setas_trocam_escala_e_modo():
    fx = _MenuFixture()
    fx._pause_submenu = "resolution"
    tw, inner = fx._res_layout()
    th = fx._RES_TH
    right_scale = fx.to_screen((inner + 2) * T + 8, fx._RES_ROW_SCALE * T + 8, tw, th)
    left_scale  = fx.to_screen(1 * T + 8, fx._RES_ROW_SCALE * T + 8, tw, th)
    mode_arrow  = fx.to_screen(1 * T + 8, fx._RES_ROW_MODE * T + 8, tw, th)
    with _bigger_display():
        _, r = _press_and_release(fx, fx._draw_pause_menu, right_scale)
        assert r == "resolution:1.25"
        _, r = _press_and_release(fx, fx._draw_pause_menu, left_scale)
        assert r is None, "já está na menor escala — seta esquerda não faz nada"
        _, r = _press_and_release(fx, fx._draw_pause_menu, mode_arrow)
        assert r == "window_mode:windowed_fullsize"


def test_interface_setas_mudam_1_passo():
    fx = _MenuFixture()
    fx._pause_submenu = "interface"
    fx._ui_scale = 1.25
    tw, inner = fx._iface_layout()
    th = fx._IFACE_TH
    col_l = (tw - (inner + 2)) // 2
    col_r = col_l + inner + 1
    with _bigger_display():
        _press_and_release(fx, fx._draw_pause_menu,
                           fx.to_screen(col_r * T + 8, fx._IFACE_ROW_SEL * T + 8, tw, th))
        _press_and_release(fx, fx._draw_pause_menu,
                           fx.to_screen(col_l * T + 8, fx._IFACE_ROW_SEL * T + 8, tw, th))
    assert fx.ui_scale_set == [1.5, 1.0]
