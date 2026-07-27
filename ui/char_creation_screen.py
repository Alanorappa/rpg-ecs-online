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
from ui.fonts import make as _font
from engine.save_system import list_saves, next_free_slot, MAX_SLOTS

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
                        from engine.save_system import delete_save
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


def run_creation(screen: pygame.Surface) -> "dict | None":
    """Tela de criação de personagem (entrada pública para uso externo).

    Retorna {"name": str, "class_id": str} ou None (cancelado).
    """
    clock = pygame.time.Clock()
    sc    = screen.get_height() / 720.0
    return _run_creation(screen, clock, sc)


def _drain_char_select_batch(msgs: list, pending_action: str, game_buffer: list,
                             chars: list, confirm_del_id: int) -> tuple:
    """Processa UMA leva de mensagens (`net.poll()`) recebida durante a tela
    de seleção de personagem (`run_online`). Extraído em função pura pra
    ser testável isoladamente.

    Bug real corrigido aqui (25/07/2026, relatado pelo usuário: harvestable
    perto do spawn nunca tinha loot disponível, mesmo aparecendo na tela):
    a versão antiga dava `return True` NO MEIO do `for` assim que achava
    WORLD_STATE, sem terminar de examinar o resto de `msgs` — mensagens que
    vinham DEPOIS dele na MESMA leva (aqui, LOOT_AVAILABLE de um harvestable
    perto o bastante do spawn pra já sair no snapshot de login — ver
    server/session.py::_spawn_and_start) ficavam presas na lista local e
    eram perdidas pra sempre quando a função retornava. Só acontecia quando
    o harvestable estava perto o bastante do spawn pra já sair na leva do
    login (por isso nunca apareceu com a caixa de teste antiga, longe do
    spawn — ela só era descoberta bem depois, via sweep de tick, quando
    esta tela já tinha fechado). Fix: processa a leva INTEIRA antes de
    decidir voltar — WORLD_STATE só marca a intenção (`got_world_state`),
    quem decide se entra no jogo é o CHAMADOR, depois do `for` terminar.

    Retorna (pending_action, status_or_None, game_buffer, chars,
             reset_confirm_del: bool, got_world_state: bool)."""
    from shared.messages import MsgType as _MT
    status = None
    reset_confirm_del = False
    got_world_state = False
    for mt, payload, _seq, _ts in msgs:
        if pending_action == "selecting":
            if mt == _MT.WORLD_STATE:
                game_buffer.append((mt, payload, _seq, _ts))
                got_world_state = True
            elif mt == _MT.CHARACTER_ERROR:
                status = f"Erro: {payload.get('reason', 'desconhecido')}"
                pending_action = ""
                game_buffer.clear()
                got_world_state = False
            else:
                game_buffer.append((mt, payload, _seq, _ts))
        elif mt == _MT.DELETE_CHARACTER_OK:
            chars = [c for c in chars if c.get("id") != confirm_del_id]
            reset_confirm_del = True
    return pending_action, status, game_buffer, chars, reset_confirm_del, got_world_state


def run_online(screen: pygame.Surface,
               char_list: "list[dict]",
               net) -> bool:
    """
    Versão online da tela de seleção/criação de personagens.

    Usa a mesma UI do run() offline, mas com dados do servidor.
    char_list : lista de dicts de personagem recebida no AUTH_OK.
    net       : NetworkClient conectado e autenticado.

    Retorna True  → LOGIN_OK + WORLD_STATE foram re-enfileirados em net.inbox,
                    GameEngine pode iniciar.
    Retorna False → usuário voltou ao login (sair).
    """
    import time
    from shared.messages import MsgType as _MT

    clock = pygame.time.Clock()
    sc    = screen.get_height() / 720.0
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

    MAX_ONLINE_CHARS = 3

    chars           = list(char_list)   # cópia local, muda ao criar/excluir
    confirm_del_id  = -1               # char_id aguardando confirmação de exclusão
    confirm_del_idx = -1
    status          = ""               # mensagem de erro/info
    # "selecting": aguardando LOGIN_OK + WORLD_STATE após SELECT_CHARACTER
    # (CREATE_CHARACTER é resolvido DENTRO de _run_creation agora — erro
    # tipo "nome já usado" mantém a tela de criação aberta, não passa por aqui)
    pending_action  = ""
    pending_start   = 0.0
    game_buffer: list = []             # mensagens LOGIN_OK + WORLD_STATE para GameEngine

    def _to_save(c: dict, idx: int) -> dict:
        """Adapta dict do servidor para o formato de _draw_selection."""
        return {
            "slot":     idx,
            "name":     c.get("name",     "Aventureiro"),
            "class_id": c.get("class_id", "guerreiro"),
            "level":    c.get("level",    1),
            "saved_at": None,
        }

    while True:
        clock.tick(60)
        mx, my = pygame.mouse.get_pos()

        # ── Poll de rede ──────────────────────────────────────────────────────
        # Ver docstring de _drain_char_select_batch (bug real relatado pelo
        # usuário 25/07/2026: harvestable perto do spawn nunca tinha loot
        # disponível, mesmo aparecendo na tela).
        msgs = net.poll()
        pending_action, _new_status, game_buffer, chars, _reset_del, _got_ws = \
            _drain_char_select_batch(msgs, pending_action, game_buffer,
                                     chars, confirm_del_id)
        if _new_status is not None:
            status = _new_status
        if _reset_del:
            confirm_del_id  = -1
            confirm_del_idx = -1
        if _got_ws:
            for m in game_buffer:
                net.inbox.put(m)
            return True

        if pending_action and time.time() - pending_start > 15.0:
            status         = "Servidor não respondeu. Tente novamente."
            pending_action = ""
            game_buffer.clear()

        # ── Eventos ───────────────────────────────────────────────────────────
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if pending_action == "selecting":
                    pass   # não cancela enquanto aguarda spawn
                elif confirm_del_id >= 0:
                    confirm_del_id  = -1
                    confirm_del_idx = -1
                else:
                    return False

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if pending_action:
                    continue

                # Confirmação de exclusão
                if confirm_del_id >= 0:
                    yes_r, no_r = _confirm_btn_rects(sw, sh, sc)
                    if yes_r.collidepoint(event.pos):
                        net.send(_MT.DELETE_CHARACTER, {"char_id": confirm_del_id})
                        # Remoção otimista: já remove da lista local
                        chars = [c for c in chars if c.get("id") != confirm_del_id]
                        confirm_del_id  = -1
                        confirm_del_idx = -1
                    elif no_r.collidepoint(event.pos):
                        confirm_del_id  = -1
                        confirm_del_idx = -1
                    continue

                saves = [_to_save(c, i) for i, c in enumerate(chars)]

                # Clique nos slots existentes
                for i, s in enumerate(saves):
                    r = _slot_rect(px, py, i, char_w, slot_h, slot_pad)
                    if not r.collidepoint(mx, my):
                        continue
                    del_r = _delete_btn_rect(r, slot_h, sc)
                    if del_r.collidepoint(mx, my):
                        confirm_del_id  = chars[i].get("id", -1)
                        confirm_del_idx = i
                        break
                    play_r = _play_btn_rect(r, slot_h, sc)
                    if play_r.collidepoint(mx, my):
                        net.send(_MT.SELECT_CHARACTER, {"char_id": chars[i].get("id", -1)})
                        pending_action = "selecting"
                        pending_start  = time.time()
                        game_buffer.clear()
                        break

                # Botão criar personagem
                if len(chars) < MAX_ONLINE_CHARS:
                    create_r = _create_btn_rect(px, py, char_w, char_h, slot_h, slot_pad, sc)
                    if create_r.collidepoint(mx, my):
                        result = _run_creation(screen, clock, sc, net=net)
                        if result is not None:
                            # _run_creation já mandou CREATE_CHARACTER e esperou
                            # CHARACTER_CREATED internamente (erro tipo "nome já
                            # usado" mantém a tela de criação aberta com aviso —
                            # não passa disso pra cá) — result é o char pronto.
                            chars.append(result)
                            status = ""

                # Botão sair
                quit_r = _quit_btn_rect(px, py, char_w, char_h, sc)
                if quit_r.collidepoint(mx, my):
                    return False

        # ── Render ────────────────────────────────────────────────────────────
        saves = [_to_save(c, i) for i, c in enumerate(chars)]
        can_create = len(chars) < MAX_ONLINE_CHARS
        occupied   = set(range(len(chars)))

        if pending_action == "selecting":
            screen.fill(_BG)
            lbl = font_md.render("Entrando no mundo...", False, _TITLE_COL)
            screen.blit(lbl, lbl.get_rect(center=(sw // 2, sh // 2)))
            if status:
                e = font_sm.render(status, False, _DEL_TXT)
                screen.blit(e, e.get_rect(centerx=sw // 2, y=sh // 2 + int(36 * sc)))
            pygame.display.flip()
            continue

        if confirm_del_id >= 0:
            _draw_selection(screen, saves, occupied, can_create,
                            px, py, mx, my, confirm_del_id,
                            font_lg, font_md, font_sm, font_xs,
                            char_w, char_h, slot_h, slot_pad, sc)
            del_name = chars[confirm_del_idx]["name"] if 0 <= confirm_del_idx < len(chars) else "?"
            _draw_confirm_overlay(
                screen, confirm_del_id,
                [{"slot": confirm_del_id, "name": del_name}],
                px, py, mx, my, font_md, font_sm, sc)
        else:
            _draw_selection(screen, saves, occupied, can_create,
                            px, py, mx, my, -1,
                            font_lg, font_md, font_sm, font_xs,
                            char_w, char_h, slot_h, slot_pad, sc)

        if status:
            e = font_sm.render(status, False, _DEL_TXT)
            screen.blit(e, e.get_rect(centerx=sw // 2, y=py - e.get_height() - int(6 * sc)))

        pygame.display.flip()


# ── Tela de criação ──────────────────────────────────────────────────────────

def _run_creation(screen, clock, sc: float, net=None) -> "dict | None":
    """Tela de criação de personagem. Retorna dict ou None (voltar).

    `net`: se fornecido (modo online), a caixa de nome já vem preenchida
    com uma sugestão verificada contra o banco (SUGGEST_NAME/
    NAME_SUGGESTION — só o servidor sabe quais nomes já existem) e o botão
    "Sortear" pede uma nova ao servidor. Sem `net` (modo offline, sem
    banco compartilhado), a sugestão é gerada localmente e o botão
    "Sortear" só gera outra local, sem round-trip de rede.

    No modo online, "Confirmar" manda CREATE_CHARACTER e ESPERA a
    resposta do servidor AQUI DENTRO — se vier CHARACTER_ERROR (ex:
    nome já usado), a tela de criação continua aberta com uma mensagem
    abaixo da caixa de nome (não volta pra seleção de personagem, que
    era o bug reportado: "name_taken" cru aparecendo na tela errada).
    Só retorna quando o servidor confirma (CHARACTER_CREATED, com o
    dict completo do personagem) ou o jogador clica Voltar/ESC."""
    import time
    from shared.messages import MsgType as _MT_names
    from shared.character_names import (
        is_valid_name, is_valid_name_char, generate_name_candidate,
        normalize_name, NAME_MIN_LEN, NAME_MAX_LEN,
    )

    _ERROR_MESSAGES = {
        "invalid_name_format": f"Nome inválido — {NAME_MIN_LEN}-{NAME_MAX_LEN} letras, sem espaço/número/símbolo.",
        "name_taken":          "Nome já escolhido, digite outro.",
        "limit_reached":       "Limite de personagens atingido.",
        "creation_failed":     "Erro ao criar personagem. Tente novamente.",
    }

    name_text        = ""
    name_active      = False
    name_user_edited = False   # trava a auto-sugestão assim que o jogador digita
    suggest_pending  = False   # aguardando NAME_SUGGESTION do servidor
    creating_pending = False   # aguardando CHARACTER_CREATED/CHARACTER_ERROR
    creating_start   = 0.0
    error_msg        = ""
    selected_class   = "guerreiro"
    MAX_NAME         = NAME_MAX_LEN

    def _request_suggestion():
        nonlocal suggest_pending, name_text
        if net is not None:
            net.send(_MT_names.SUGGEST_NAME, {})
            suggest_pending = True
        else:
            name_text = generate_name_candidate()

    _request_suggestion()

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
    name_rect   = pygame.Rect(px + PW // 2 - int(180 * sc) - int(50 * sc), py + int(108 * sc), int(360 * sc), int(38 * sc))
    reroll_rect = pygame.Rect(name_rect.right + int(8 * sc), name_rect.y, int(84 * sc), name_rect.h)

    while True:
        clock.tick(60)
        mx, my = pygame.mouse.get_pos()

        if net is not None:
            for mt, payload, _seq, _ts in net.poll():
                if mt == _MT_names.NAME_SUGGESTION:
                    suggest_pending = False
                    if not name_user_edited:
                        name_text = payload.get("name", "")
                elif mt == _MT_names.CHARACTER_CREATED:
                    return payload.get("char")
                elif mt == _MT_names.CHARACTER_ERROR:
                    creating_pending = False
                    error_msg = _ERROR_MESSAGES.get(
                        payload.get("reason"), "Erro ao criar personagem.")

            if creating_pending and time.time() - creating_start > 15.0:
                creating_pending = False
                error_msg = "Servidor não respondeu. Tente novamente."

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return None
                if name_active:
                    if event.key == pygame.K_BACKSPACE:
                        name_text = name_text[:-1]
                        name_user_edited = True
                        error_msg = ""
                    elif event.key == pygame.K_RETURN:
                        name_active = False
                    elif (len(name_text) < MAX_NAME and event.unicode
                          and is_valid_name_char(event.unicode)):
                        name_text += event.unicode
                        name_user_edited = True
                        error_msg = ""
                    # 1ª letra sempre maiúscula, digitada em qualquer caixa —
                    # jogador pode digitar tudo minúsculo sem se preocupar.
                    name_text = normalize_name(name_text)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                name_active = name_rect.collidepoint(mx, my)
                if reroll_rect.collidepoint(mx, my) and not suggest_pending:
                    name_user_edited = False
                    error_msg = ""
                    _request_suggestion()
                for idx, cls in enumerate(_CLASSES):
                    if not cls["locked"] and _card_rect(idx).collidepoint(mx, my):
                        selected_class = cls["id"]
                if btn_confirm.collidepoint(mx, my) and not creating_pending:
                    candidate = name_text.strip()
                    if not is_valid_name(candidate):
                        error_msg = (f"Nome precisa ter {NAME_MIN_LEN}-{NAME_MAX_LEN} "
                                     "letras, sem espaço/número/símbolo.")
                    elif net is not None:
                        net.send(_MT_names.CREATE_CHARACTER, {
                            "name": candidate, "class_id": selected_class})
                        creating_pending = True
                        creating_start   = time.time()
                        error_msg        = ""
                    else:
                        return {"name": candidate, "class_id": selected_class}
                if btn_back.collidepoint(mx, my):
                    return None

        screen.fill(_BG)
        panel = pygame.Rect(px, py, PW, PH)
        pygame.draw.rect(screen, _PANEL_BG, panel, border_radius=8)
        pygame.draw.rect(screen, _BORDER,   panel, 2, border_radius=8)

        title = font_lg.render("Criar Personagem", False, _TITLE_COL)
        screen.blit(title, (px + PW // 2 - title.get_width() // 2, py + int(18 * sc)))

        lbl = font_sm.render("Nome do personagem:", False, _TEXT_COL)
        screen.blit(lbl, (name_rect.x, name_rect.y - lbl.get_height() - int(4 * sc)))

        bdr_col = _INPUT_ACT if name_active else _INPUT_BDR
        pygame.draw.rect(screen, _INPUT_BG, name_rect, border_radius=4)
        pygame.draw.rect(screen, bdr_col,   name_rect, 2, border_radius=4)
        cursor   = "|" if name_active and pygame.time.get_ticks() % 1000 < 500 else ""
        if name_text:
            disp_txt, txt_col = name_text + cursor, _TEXT_COL
        elif suggest_pending:
            disp_txt, txt_col = "Gerando nome...", _LOCK_TXT
        else:
            disp_txt, txt_col = cursor, _TEXT_COL
        txt_surf = font_md.render(disp_txt, False, txt_col)
        screen.blit(txt_surf, (name_rect.x + int(10 * sc),
                               name_rect.y + name_rect.h // 2 - txt_surf.get_height() // 2))

        reroll_hov = reroll_rect.collidepoint(mx, my) and not suggest_pending
        pygame.draw.rect(screen, _BTN_HOV if reroll_hov else _BTN_BG, reroll_rect, border_radius=4)
        pygame.draw.rect(screen, _BORDER, reroll_rect, 2, border_radius=4)
        reroll_s = font_sm.render("Sortear", False, _BTN_TXT)
        screen.blit(reroll_s, reroll_s.get_rect(center=reroll_rect.center))

        if error_msg:
            err_s = font_xs.render(error_msg, False, _DEL_TXT)
            screen.blit(err_s, (name_rect.x, name_rect.bottom + int(4 * sc)))

        lbl2 = font_sm.render("Escolha sua classe:", False, _TEXT_COL)
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
            n_surf = font_sm.render(cls["label"], False, n_col)
            screen.blit(n_surf, (r.x + r.w // 2 - n_surf.get_width() // 2, r.y + int(64 * sc)))

            for li, line in enumerate(cls["description"].split("\n")):
                d = font_xs.render(line, False, _LOCK_TXT if locked else _TEXT_COL)
                screen.blit(d, (r.x + r.w // 2 - d.get_width() // 2,
                                r.y + int(90 * sc) + li * (font_xs.get_height() + int(2 * sc))))
            if locked:
                lock_s = font_xs.render("[em breve]", False, _LOCK_COL)
                screen.blit(lock_s, (r.x + r.w // 2 - lock_s.get_width() // 2, r.y + int(138 * sc)))

        confirm_label = "Criando..." if creating_pending else "Confirmar"
        for r, label, bg, bdr, disabled in [
            (btn_back,    "Voltar",      _BTN_BG,  _BORDER,  False),
            (btn_confirm, confirm_label, _BTN_BG,  _SEL_BDR, creating_pending),
        ]:
            hov = r.collidepoint(mx, my) and not disabled
            pygame.draw.rect(screen, _BTN_HOV if hov else bg, r, border_radius=5)
            pygame.draw.rect(screen, bdr, r, 2, border_radius=5)
            txt_c = _LOCK_TXT if disabled else _BTN_TXT
            s = font_md.render(label, False, txt_c)
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

    title = font_lg.render("Selecionar Personagem", False, _TITLE_COL)
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

        name_s = font_md.render(s["name"], False, _TITLE_COL)
        screen.blit(name_s, (r.x + int(64 * sc), r.y + int(12 * sc)))

        cls_label = s["class_id"].capitalize()
        info_s = font_sm.render(f"{cls_label}  —  Nivel {s['level']}", False, _TEXT_COL)
        screen.blit(info_s, (r.x + int(64 * sc), r.y + int(12 * sc) + name_s.get_height() + int(2 * sc)))

        if s["saved_at"]:
            date_str = s["saved_at"].replace("T", "  ")
            date_s = font_xs.render(date_str, False, _SUB_COL)
            screen.blit(date_s, (r.x + int(64 * sc), r.y + slot_h - date_s.get_height() - int(8 * sc)))

        play_r = _play_btn_rect(r, slot_h, sc)
        hov_p  = play_r.collidepoint(mx, my)
        pygame.draw.rect(screen, _BTN_HOV if hov_p else _BTN_BG, play_r, border_radius=4)
        pygame.draw.rect(screen, _SEL_BDR, play_r, 2, border_radius=4)
        play_s = font_sm.render("Jogar", False, _BTN_TXT)
        screen.blit(play_s, play_s.get_rect(center=play_r.center))

        del_r = _delete_btn_rect(r, slot_h, sc)
        hov_d = del_r.collidepoint(mx, my)
        pygame.draw.rect(screen, _DEL_HOV if hov_d else _DEL_BG, del_r, border_radius=4)
        pygame.draw.rect(screen, _DEL_TXT, del_r, 1, border_radius=4)
        del_s = font_xs.render("Excluir", False, _DEL_TXT)
        screen.blit(del_s, del_s.get_rect(center=del_r.center))

    create_r = _create_btn_rect(px, py, char_w, char_h, slot_h, slot_pad, sc)
    if can_create:
        hov_c = create_r.collidepoint(mx, my)
        pygame.draw.rect(screen, _NEW_HOV if hov_c else _NEW_BG, create_r, border_radius=5)
        pygame.draw.rect(screen, _NEW_BDR, create_r, 2, border_radius=5)
        c_s = font_md.render("+ Criar Personagem", False, _NEW_TXT)
        screen.blit(c_s, c_s.get_rect(center=create_r.center))
    else:
        pygame.draw.rect(screen, _PANEL_BG, create_r, border_radius=5)
        pygame.draw.rect(screen, _LOCK_COL, create_r, 1, border_radius=5)
        c_s = font_sm.render("Slots cheios (max 8)", False, _LOCK_TXT)
        screen.blit(c_s, c_s.get_rect(center=create_r.center))

    quit_r = _quit_btn_rect(px, py, char_w, char_h, sc)
    hov_q  = quit_r.collidepoint(mx, my)
    pygame.draw.rect(screen, _BTN_HOV if hov_q else _BTN_BG, quit_r, border_radius=5)
    pygame.draw.rect(screen, _BORDER, quit_r, 2, border_radius=5)
    q_s = font_md.render("Sair", False, _BTN_TXT)
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
    line1 = font_md.render("Excluir personagem?", False, (255, 200, 200))
    line2 = font_sm.render(f'"{name_str}" sera removido permanentemente.', False, _TEXT_COL)
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
        s = font_md.render(label, False, col)
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
