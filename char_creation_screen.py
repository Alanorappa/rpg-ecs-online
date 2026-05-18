# char_creation_screen.py
"""
Tela de seleção e criação de personagens (estilo WoW).

Funções públicas:
  run(screen) -> (slot: int, char_data: dict | None)
    - slot     : slot a ser carregado/criado
    - char_data: None = carregar save existente; dict = personagem novo
"""
from __future__ import annotations
import pygame
from fonts import make as _font
from save_system import list_saves, next_free_slot, MAX_SLOTS

# ── Paleta ───────────────────────────────────────────────────────────────────
_BG        = (8, 8, 14)
_PANEL_BG  = (18, 16, 26)
_BORDER    = (70, 55, 110)
_TITLE_COL = (210, 185, 255)
_TEXT_COL  = (185, 170, 215)
_SUB_COL   = (130, 115, 160)
_SEL_BG    = (45, 35, 70)
_SEL_BDR   = (170, 130, 255)
_HOV_BG    = (30, 24, 48)
_BTN_BG    = (45, 35, 70)
_BTN_HOV   = (75, 58, 120)
_BTN_TXT   = (220, 205, 255)
_DEL_BG    = (60, 18, 18)
_DEL_HOV   = (100, 28, 28)
_DEL_TXT   = (255, 140, 140)
_LOCK_COL  = (80, 70, 95)
_LOCK_TXT  = (100, 90, 115)
_INPUT_BG  = (14, 12, 22)
_INPUT_BDR = (90, 70, 140)
_INPUT_ACT = (170, 130, 255)
_NEW_BG    = (22, 32, 22)
_NEW_BDR   = (70, 110, 70)
_NEW_TXT   = (140, 210, 140)
_NEW_HOV   = (30, 50, 30)

_CLASS_COLORS = {
    "guerreiro": (200, 100,  60),
    "mago":      ( 80, 130, 220),
    "arqueiro":  ( 80, 180, 100),
}

_CLASSES = [
    {"id": "guerreiro", "label": "Guerreiro", "locked": False,
     "description": "Mestre do combate corpo a corpo.\nAlta resistencia e dano fisico."},
    {"id": "mago",      "label": "Mago",      "locked": False, "description": "Mestre das artes arcanas.\nAlto poder mágico e controle de área."},
    {"id": "arqueiro",  "label": "Arqueiro",  "locked": False, "description": "Mestre do arco e da flecha.\nAtaques ranged e controle de posicionamento."},
]

# ── Layout base (referência 720p) ─────────────────────────────────────────────
_BASE_CHAR_W  = 680
_BASE_CHAR_H  = 520
_BASE_SLOT_H  = 84
_BASE_SLOT_PAD = 10
_BASE_BTN_W, _BASE_BTN_H = 200, 42


def _sc(screen: pygame.Surface) -> float:
    """Fator de escala baseado na altura da janela."""
    return screen.get_height() / 720.0


def run(screen: pygame.Surface) -> tuple[int, "dict | None"]:
    """
    Exibe a seleção de personagens.
    Retorna (slot, None) para carregar save existente, ou (slot, dict) para novo.
    """
    clock = pygame.time.Clock()
    confirm_delete = -1

    while True:
        saves      = list_saves()
        occupied   = {s["slot"] for s in saves}
        can_create = len(occupied) < MAX_SLOTS

        sc = _sc(screen)
        sw, sh = screen.get_size()

        font_lg = _font(int(34 * sc))
        font_md = _font(int(24 * sc))
        font_sm = _font(int(19 * sc))
        font_xs = _font(int(15 * sc))

        char_w   = int(_BASE_CHAR_W  * sc)
        char_h   = int(_BASE_CHAR_H  * sc)
        slot_h   = int(_BASE_SLOT_H  * sc)
        slot_pad = int(_BASE_SLOT_PAD * sc)

        px = sw // 2 - char_w // 2
        py = sh // 2 - char_h // 2

        clock.tick(60)
        mx, my = pygame.mouse.get_pos()

        # ── Eventos ──────────────────────────────────────────────────────────
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                raise SystemExit
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if confirm_delete >= 0:
                        confirm_delete = -1
                    else:
                        pygame.quit()
                        raise SystemExit

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if confirm_delete >= 0:
                    yes_r, no_r = _confirm_btn_rects(sw, sh, sc)
                    if yes_r.collidepoint(event.pos):
                        from save_system import delete_save
                        delete_save(confirm_delete)
                        confirm_delete = -1
                    elif no_r.collidepoint(event.pos):
                        confirm_delete = -1
                    continue

                for i, s in enumerate(saves):
                    r = _slot_rect(px, py, i, char_w, slot_h, slot_pad)
                    if not r.collidepoint(mx, my):
                        continue
                    del_r = _delete_btn_rect(r, slot_h, sc)
                    if del_r.collidepoint(mx, my):
                        confirm_delete = s["slot"]
                        break
                    play_r = _play_btn_rect(r, slot_h, sc)
                    if play_r.collidepoint(mx, my):
                        return s["slot"], None

                if can_create:
                    create_r = _create_btn_rect(px, py, char_w, char_h, slot_h, slot_pad, sc)
                    if create_r.collidepoint(mx, my):
                        result = _run_creation(screen, clock, sc)
                        if result is not None:
                            slot = next_free_slot()
                            return slot, result

                quit_r = _quit_btn_rect(px, py, char_w, char_h, sc)
                if quit_r.collidepoint(mx, my):
                    pygame.quit()
                    raise SystemExit

        # ── Confirmação de exclusão ───────────────────────────────────────────
        if confirm_delete >= 0:
            _draw_selection(screen, saves, occupied, can_create,
                            px, py, mx, my, confirm_delete,
                            font_lg, font_md, font_sm, font_xs,
                            char_w, char_h, slot_h, slot_pad, sc)
            _draw_confirm_overlay(screen, confirm_delete, saves,
                                  px, py, mx, my, font_md, font_sm, sc)

            pygame.display.flip()
            continue

        # ── Render principal ─────────────────────────────────────────────────
        _draw_selection(screen, saves, occupied, can_create,
                        px, py, mx, my, -1,
                        font_lg, font_md, font_sm, font_xs,
                        char_w, char_h, slot_h, slot_pad, sc)
        pygame.display.flip()


# ── Tela de criação ──────────────────────────────────────────────────────────

def _run_creation(screen, clock, sc: float) -> "dict | None":
    """Tela de criação de personagem. Retorna dict ou None (voltar)."""
    name_text      = ""
    name_active    = False
    selected_class = "guerreiro"
    MAX_NAME       = 18

    font_lg = _font(int(34 * sc))
    font_md = _font(int(24 * sc))
    font_sm = _font(int(19 * sc))
    font_xs = _font(int(15 * sc))

    sw, sh = screen.get_size()
    PW = int(700 * sc)
    PH = int(460 * sc)
    px = sw // 2 - PW // 2
    py = sh // 2 - PH // 2

    CARD_W = int(180 * sc)
    CARD_H = int(160 * sc)
    cards_y = py + int(190 * sc)
    total_w = len(_CLASSES) * CARD_W + (len(_CLASSES) - 1) * int(16 * sc)
    cards_x = px + PW // 2 - total_w // 2

    btn_w = int(_BASE_BTN_W * sc)
    btn_h = int(_BASE_BTN_H * sc)

    def _card_rect(idx):
        return pygame.Rect(cards_x + idx * (CARD_W + int(16 * sc)), cards_y, CARD_W, CARD_H)

    btn_confirm = pygame.Rect(px + PW - btn_w - int(20 * sc), py + PH - btn_h - int(18 * sc), btn_w, btn_h)
    btn_back    = pygame.Rect(px + int(20 * sc),               py + PH - btn_h - int(18 * sc), btn_w, btn_h)
    name_rect   = pygame.Rect(px + PW // 2 - int(180 * sc),   py + int(108 * sc), int(360 * sc), int(38 * sc))

    while True:
        clock.tick(60)
        mx, my = pygame.mouse.get_pos()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return None
                if name_active:
                    if event.key == pygame.K_BACKSPACE:
                        name_text = name_text[:-1]
                    elif event.key == pygame.K_RETURN:
                        name_active = False
                    elif len(name_text) < MAX_NAME and event.unicode.isprintable():
                        name_text += event.unicode
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                name_active = name_rect.collidepoint(mx, my)
                for idx, cls in enumerate(_CLASSES):
                    if not cls["locked"] and _card_rect(idx).collidepoint(mx, my):
                        selected_class = cls["id"]
                if btn_confirm.collidepoint(mx, my):
                    return {"name": name_text.strip() or "Aventureiro",
                            "class_id": selected_class}
                if btn_back.collidepoint(mx, my):
                    return None

        screen.fill(_BG)
        panel = pygame.Rect(px, py, PW, PH)
        pygame.draw.rect(screen, _PANEL_BG, panel, border_radius=8)
        pygame.draw.rect(screen, _BORDER,   panel, 2, border_radius=8)

        title = font_lg.render("Criar Personagem", True, _TITLE_COL)
        screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + int(18 * sc)))

        lbl = font_sm.render("Nome do personagem:", True, _TEXT_COL)
        screen.blit(lbl, (name_rect.x, name_rect.y - lbl.get_height() - int(4 * sc)))

        bdr_col = _INPUT_ACT if name_active else _INPUT_BDR
        pygame.draw.rect(screen, _INPUT_BG, name_rect, border_radius=4)
        pygame.draw.rect(screen, bdr_col,   name_rect, 2, border_radius=4)
        cursor   = "|" if name_active and pygame.time.get_ticks() % 1000 < 500 else ""
        disp_txt = (name_text + cursor) if name_text else ("Aventureiro" if not name_active else cursor)
        txt_col  = _TEXT_COL if name_text else _LOCK_TXT
        txt_surf = font_md.render(disp_txt, True, txt_col)
        screen.blit(txt_surf, (name_rect.x + int(10 * sc),
                               name_rect.y + name_rect.h // 2 - txt_surf.get_height() // 2))

        lbl2 = font_sm.render("Escolha sua classe:", True, _TEXT_COL)
        screen.blit(lbl2, (px + PW // 2 - lbl2.get_width() // 2, cards_y - lbl2.get_height() - int(8 * sc)))

        icon_sz = int(36 * sc)
        for idx, cls in enumerate(_CLASSES):
            r      = _card_rect(idx)
            locked = cls["locked"]
            is_sel = cls["id"] == selected_class
            hov    = r.collidepoint(mx, my) and not locked
            if locked:
                bg, bdr, bdr_w = (16, 14, 22), _LOCK_COL, 1
            elif is_sel:
                bg, bdr, bdr_w = _SEL_BG, _SEL_BDR, 2
            elif hov:
                bg, bdr, bdr_w = _HOV_BG, _BORDER, 1
            else:
                bg, bdr, bdr_w = _PANEL_BG, _BORDER, 1
            pygame.draw.rect(screen, bg,  r, border_radius=6)
            pygame.draw.rect(screen, bdr, r, bdr_w, border_radius=6)

            icon_col = _LOCK_COL if locked else _CLASS_COLORS.get(cls["id"], (150, 150, 150))
            icon_r   = pygame.Rect(r.x + r.w // 2 - icon_sz // 2, r.y + int(18 * sc), icon_sz, icon_sz)
            pygame.draw.rect(screen, icon_col, icon_r, border_radius=4)

            n_col  = _LOCK_TXT if locked else (_SEL_BDR if is_sel else _TEXT_COL)
            n_surf = font_sm.render(cls["label"], True, n_col)
            screen.blit(n_surf, (r.x + r.w // 2 - n_surf.get_width() // 2, r.y + int(64 * sc)))

            for li, line in enumerate(cls["description"].split("\n")):
                d = font_xs.render(line, True, _LOCK_TXT if locked else _TEXT_COL)
                screen.blit(d, (r.x + r.w // 2 - d.get_width() // 2,
                                r.y + int(90 * sc) + li * (font_xs.get_height() + int(2 * sc))))
            if locked:
                lock_s = font_xs.render("[em breve]", True, _LOCK_COL)
                screen.blit(lock_s, (r.x + r.w // 2 - lock_s.get_width() // 2, r.y + int(138 * sc)))

        for r, label, bg, bdr in [
            (btn_back,    "Voltar",    _BTN_BG,  _BORDER),
            (btn_confirm, "Confirmar", _BTN_BG,  _SEL_BDR),
        ]:
            hov = r.collidepoint(mx, my)
            pygame.draw.rect(screen, _BTN_HOV if hov else bg, r, border_radius=5)
            pygame.draw.rect(screen, bdr, r, 2, border_radius=5)
            s = font_md.render(label, True, _BTN_TXT)
            screen.blit(s, s.get_rect(center=r.center))

        pygame.display.flip()


# ── Render da seleção ────────────────────────────────────────────────────────

def _draw_selection(screen, saves, occupied, can_create,
                    px, py, mx, my, confirm_delete,
                    font_lg, font_md, font_sm, font_xs,
                    char_w, char_h, slot_h, slot_pad, sc):
    screen.fill(_BG)
    panel = pygame.Rect(px, py, char_w, char_h)
    pygame.draw.rect(screen, _PANEL_BG, panel, border_radius=8)
    pygame.draw.rect(screen, _BORDER,   panel, 2, border_radius=8)

    title = font_lg.render("Selecionar Personagem", True, _TITLE_COL)
    screen.blit(title, (px + char_w // 2 - title.get_width() // 2, py + int(16 * sc)))

    for i, s in enumerate(saves):
        r   = _slot_rect(px, py, i, char_w, slot_h, slot_pad)
        hov = r.collidepoint(mx, my)
        bg  = _SEL_BG if hov else _PANEL_BG
        pygame.draw.rect(screen, bg,      r, border_radius=5)
        pygame.draw.rect(screen, _BORDER, r, 1, border_radius=5)

        icon_sz = int(36 * sc)
        col = _CLASS_COLORS.get(s["class_id"], (150, 150, 150))
        icon = pygame.Rect(r.x + int(14 * sc), r.y + slot_h // 2 - icon_sz // 2, icon_sz, icon_sz)
        pygame.draw.rect(screen, col, icon, border_radius=4)

        name_s = font_md.render(s["name"], True, _TITLE_COL)
        screen.blit(name_s, (r.x + int(64 * sc), r.y + int(12 * sc)))

        cls_label = s["class_id"].capitalize()
        info_s = font_sm.render(f"{cls_label}  —  Nivel {s['level']}", True, _TEXT_COL)
        screen.blit(info_s, (r.x + int(64 * sc), r.y + int(12 * sc) + name_s.get_height() + int(2 * sc)))

        if s["saved_at"]:
            date_str = s["saved_at"].replace("T", "  ")
            date_s = font_xs.render(date_str, True, _SUB_COL)
            screen.blit(date_s, (r.x + int(64 * sc), r.y + slot_h - date_s.get_height() - int(8 * sc)))

        play_r = _play_btn_rect(r, slot_h, sc)
        hov_p  = play_r.collidepoint(mx, my)
        pygame.draw.rect(screen, _BTN_HOV if hov_p else _BTN_BG, play_r, border_radius=4)
        pygame.draw.rect(screen, _SEL_BDR, play_r, 2, border_radius=4)
        play_s = font_sm.render("Jogar", True, _BTN_TXT)
        screen.blit(play_s, play_s.get_rect(center=play_r.center))

        del_r = _delete_btn_rect(r, slot_h, sc)
        hov_d = del_r.collidepoint(mx, my)
        pygame.draw.rect(screen, _DEL_HOV if hov_d else _DEL_BG, del_r, border_radius=4)
        pygame.draw.rect(screen, _DEL_TXT, del_r, 1, border_radius=4)
        del_s = font_xs.render("Excluir", True, _DEL_TXT)
        screen.blit(del_s, del_s.get_rect(center=del_r.center))

    create_r = _create_btn_rect(px, py, char_w, char_h, slot_h, slot_pad, sc)
    if can_create:
        hov_c = create_r.collidepoint(mx, my)
        pygame.draw.rect(screen, _NEW_HOV if hov_c else _NEW_BG, create_r, border_radius=5)
        pygame.draw.rect(screen, _NEW_BDR, create_r, 2, border_radius=5)
        c_s = font_md.render("+ Criar Personagem", True, _NEW_TXT)
        screen.blit(c_s, c_s.get_rect(center=create_r.center))
    else:
        pygame.draw.rect(screen, _PANEL_BG, create_r, border_radius=5)
        pygame.draw.rect(screen, _LOCK_COL, create_r, 1, border_radius=5)
        c_s = font_sm.render("Slots cheios (max 8)", True, _LOCK_TXT)
        screen.blit(c_s, c_s.get_rect(center=create_r.center))

    quit_r = _quit_btn_rect(px, py, char_w, char_h, sc)
    hov_q  = quit_r.collidepoint(mx, my)
    pygame.draw.rect(screen, _BTN_HOV if hov_q else _BTN_BG, quit_r, border_radius=5)
    pygame.draw.rect(screen, _BORDER, quit_r, 2, border_radius=5)
    q_s = font_md.render("Sair", True, _BTN_TXT)
    screen.blit(q_s, q_s.get_rect(center=quit_r.center))


def _draw_confirm_overlay(screen, slot, saves, px, py, mx, my, font_md, font_sm, sc):
    sw, sh = screen.get_size()
    overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    screen.blit(overlay, (0, 0))

    DW = int(400 * sc)
    DH = int(160 * sc)
    dx = sw // 2 - DW // 2
    dy = sh // 2 - DH // 2
    d_panel = pygame.Rect(dx, dy, DW, DH)
    pygame.draw.rect(screen, (30, 10, 10), d_panel, border_radius=8)
    pygame.draw.rect(screen, _DEL_TXT,    d_panel, 2, border_radius=8)

    save_info = next((s for s in saves if s["slot"] == slot), None)
    name_str  = save_info["name"] if save_info else "?"
    line1 = font_md.render("Excluir personagem?", True, (255, 200, 200))
    line2 = font_sm.render(f'"{name_str}" sera removido permanentemente.', True, _TEXT_COL)
    screen.blit(line1, (dx + DW // 2 - line1.get_width() // 2, dy + int(20 * sc)))
    screen.blit(line2, (dx + DW // 2 - line2.get_width() // 2, dy + int(20 * sc) + line1.get_height() + int(8 * sc)))

    yes_r, no_r = _confirm_btn_rects(sw, sh, sc)
    for r, label, bg, hov_bg, col in [
        (yes_r, "Excluir",  _DEL_BG,  _DEL_HOV,  _DEL_TXT),
        (no_r,  "Cancelar", _BTN_BG,  _BTN_HOV,  _BTN_TXT),
    ]:
        hov = r.collidepoint(mx, my)
        pygame.draw.rect(screen, hov_bg if hov else bg, r, border_radius=5)
        pygame.draw.rect(screen, col, r, 2, border_radius=5)
        s = font_md.render(label, True, col)
        screen.blit(s, s.get_rect(center=r.center))


# ── Helpers de geometria ─────────────────────────────────────────────────────

def _slot_rect(px, py, idx, char_w, slot_h, slot_pad) -> pygame.Rect:
    pad = int(20 * (slot_h / 84))   # escala o recuo lateral com slot_h
    x = px + pad
    y = py + slot_h + idx * (slot_h + slot_pad)
    return pygame.Rect(x, y, char_w - pad * 2, slot_h)


def _play_btn_rect(slot_r: pygame.Rect, slot_h: int, sc: float) -> pygame.Rect:
    bw = int(80 * sc)
    bh = int(30 * sc)
    return pygame.Rect(slot_r.right - bw - int(110 * sc), slot_r.y + slot_h // 2 - bh // 2, bw, bh)


def _delete_btn_rect(slot_r: pygame.Rect, slot_h: int, sc: float) -> pygame.Rect:
    bw = int(72 * sc)
    bh = int(26 * sc)
    return pygame.Rect(slot_r.right - bw - int(14 * sc), slot_r.y + slot_h // 2 - bh // 2, bw, bh)


def _create_btn_rect(px, py, char_w, char_h, slot_h, slot_pad, sc) -> pygame.Rect:
    bw = int(260 * sc)
    bh = int(42  * sc)
    y  = py + char_h - bh - int(60 * sc)
    return pygame.Rect(px + char_w // 2 - bw // 2, y, bw, bh)


def _quit_btn_rect(px, py, char_w, char_h, sc) -> pygame.Rect:
    bw = int(120 * sc)
    bh = int(36  * sc)
    return pygame.Rect(px + char_w // 2 - bw // 2, py + char_h - bh - int(14 * sc), bw, bh)


def _confirm_btn_rects(sw, sh, sc) -> tuple:
    DW = int(400 * sc)
    DH = int(160 * sc)
    dx = sw // 2 - DW // 2
    dy = sh // 2 - DH // 2
    bw = int(140 * sc)
    bh = int(36  * sc)
    y  = dy + DH - bh - int(16 * sc)
    yes_r = pygame.Rect(dx + DW // 2 - bw - int(12 * sc), y, bw, bh)
    no_r  = pygame.Rect(dx + DW // 2 + int(12 * sc),      y, bw, bh)
    return yes_r, no_r
