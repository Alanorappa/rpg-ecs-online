# settings_screen.py
"""Tela de configurações exibida antes do jogo iniciar."""
import pygame
import config
from fonts import make as _font

SCALE_OPTIONS = [
    ("1x  —  1280 × 720",  1.0),
    ("1.25x  —  1600 × 900",  1.25),
    ("1.5x  —  1920 × 1080", 1.5),
]

_BG        = (12, 10, 8)
_PANEL_BG  = (22, 18, 12)
_BORDER    = (90, 72, 44)
_TITLE_COL = (220, 190, 110)
_TEXT_COL  = (200, 185, 155)
_SEL_BG    = (55, 44, 22)
_SEL_BDR   = (200, 160, 60)
_HOV_BG    = (38, 30, 16)
_BTN_BG    = (50, 38, 18)
_BTN_HOV   = (80, 62, 28)
_BTN_TXT   = (230, 210, 160)
_W, _H     = 480, 320


def run(screen: pygame.Surface) -> float:
    """
    Exibe a tela de configurações e retorna o scale escolhido.
    Bloqueante — sai apenas quando o usuário confirma.
    """
    cfg   = config.load()
    scale = cfg.get("scale", 1.0)

    font_lg = _font(36)
    font_md = _font(26)
    font_sm = _font(22)
    clock   = pygame.time.Clock()

    sw, sh = screen.get_size()

    while True:
        clock.tick(60)
        mx, my = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                raise SystemExit
            if event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
                config.save({"scale": scale})
                return scale
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # Botão confirmar
                btn_r = _btn_rect(sw, sh)
                if btn_r.collidepoint(mx, my):
                    config.save({"scale": scale})
                    return scale
                # Opções de escala
                for idx, (label, val) in enumerate(SCALE_OPTIONS):
                    opt_r = _option_rect(sw, sh, idx)
                    if opt_r.collidepoint(mx, my):
                        scale = val

        # Render
        screen.fill(_BG)

        px = sw // 2 - _W // 2
        py = sh // 2 - _H // 2
        panel = pygame.Rect(px, py, _W, _H)
        pygame.draw.rect(screen, _PANEL_BG, panel, border_radius=6)
        pygame.draw.rect(screen, _BORDER,   panel, 2, border_radius=6)

        title = font_lg.render("Configuracoes", False, _TITLE_COL)
        screen.blit(title, (px + _W // 2 - title.get_width() // 2, py + 18))

        sub = font_sm.render("Resolucao / Escala de renderizacao", False, _TEXT_COL)
        screen.blit(sub, (px + _W // 2 - sub.get_width() // 2, py + 52))

        for idx, (label, val) in enumerate(SCALE_OPTIONS):
            opt_r  = _option_rect(sw, sh, idx)
            is_sel = (val == scale)
            is_hov = opt_r.collidepoint(mx, my)
            bg     = _SEL_BG if is_sel else (_HOV_BG if is_hov else _PANEL_BG)
            bdr    = _SEL_BDR if is_sel else (_BORDER if is_hov else _BORDER)
            bdr_w  = 2 if is_sel else 1
            pygame.draw.rect(screen, bg,  opt_r, border_radius=4)
            pygame.draw.rect(screen, bdr, opt_r, bdr_w, border_radius=4)
            lbl_s = font_md.render(label, False, _SEL_BDR if is_sel else _TEXT_COL)
            screen.blit(lbl_s, lbl_s.get_rect(center=opt_r.center))

        btn_r = _btn_rect(sw, sh)
        is_hov = btn_r.collidepoint(mx, my)
        pygame.draw.rect(screen, _BTN_HOV if is_hov else _BTN_BG, btn_r, border_radius=4)
        pygame.draw.rect(screen, _SEL_BDR, btn_r, 2, border_radius=4)
        btn_s = font_md.render("Confirmar  (Enter)", False, _BTN_TXT)
        screen.blit(btn_s, btn_s.get_rect(center=btn_r.center))

        pygame.display.flip()


def _option_rect(sw: int, sh: int, idx: int) -> pygame.Rect:
    px = sw // 2 - _W // 2
    py = sh // 2 - _H // 2
    opt_w, opt_h = 340, 38
    ox = px + _W // 2 - opt_w // 2
    oy = py + 88 + idx * (opt_h + 10)
    return pygame.Rect(ox, oy, opt_w, opt_h)


def _btn_rect(sw: int, sh: int) -> pygame.Rect:
    px = sw // 2 - _W // 2
    py = sh // 2 - _H // 2
    bw, bh = 200, 38
    return pygame.Rect(px + _W // 2 - bw // 2, py + _H - bh - 20, bw, bh)
