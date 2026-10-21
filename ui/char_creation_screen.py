# char_creation_screen.py
"""
Tela de seleção e criação de personagens (estilo WoW).

Função pública: run_online(screen, char_list, net) -> bool (ver docstring).
Seleção e criação no visual do atlas de UI (ui/atlas_window.py).
"""
from __future__ import annotations
import math
import pygame
from ui import atlas_window as aw
from ui import ui_atlas
from ui.fonts import make as _font

# ── Paleta (fundo e textos fora das janelas do atlas) ───────────────────────
_BG        = (8, 8, 14)
_TITLE_COL = (210, 185, 255)
_DEL_TXT   = (255, 140, 140)

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


def _sc(screen: pygame.Surface) -> float:
    """Fator de escala baseado na altura da janela."""
    return screen.get_height() / 720.0


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
    Tela de seleção/criação de personagens (online).

    char_list : lista de dicts de personagem recebida no AUTH_OK.
    net       : NetworkClient conectado e autenticado.

    Retorna True  → LOGIN_OK + WORLD_STATE foram re-enfileirados em net.inbox,
                    GameEngine pode iniciar.
    Retorna False → usuário voltou ao login (sair).

    Visual: atlas de UI (ui/atlas_window.py) — janela escura do mockup com
    1 moldura por personagem, Jogar/Excluir em cada uma, Criar
    Personagem/Sair embaixo; botões agem no SOLTAR do mouse.
    """
    import time
    from shared.messages import MsgType as _MT
    from shared.constants import MAX_CHARACTERS_PER_ACCOUNT

    clock = pygame.time.Clock()
    sc    = screen.get_height() / 720.0
    sw, sh = screen.get_size()

    font_lg = _font(int(34 * sc))
    font_md = _font(int(24 * sc))
    font_sm = _font(int(19 * sc))
    font_xs = _font(int(15 * sc))

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
    press  = aw.PressState()
    scroll = aw.ScrollState()

    while True:
        clock.tick(60)

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

        # ── Eventos de teclado/janela ────────────────────────────────────────
        events = pygame.event.get()
        for event in events:
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

        # ── Render + cliques (botões do atlas agem no soltar) ────────────────
        if pending_action == "selecting":
            screen.fill(_BG)
            lbl = font_md.render("Entrando no mundo...", False, _TITLE_COL)
            screen.blit(lbl, lbl.get_rect(center=(sw // 2, sh // 2)))
            if status:
                e = font_sm.render(status, False, _DEL_TXT)
                screen.blit(e, e.get_rect(centerx=sw // 2, y=sh // 2 + int(36 * sc)))
            pygame.display.flip()
            continue

        can_create = len(chars) < MAX_CHARACTERS_PER_ACCOUNT
        confirming = confirm_del_id >= 0
        # Confirmação aberta: a lista de fundo é só desenho — sem eventos e
        # press=None (estado descartável, ver atlas_window.Frame).
        action, panel_rect = _draw_selection(
            screen, [] if confirming else events, chars, can_create,
            None if confirming else press, scroll,
            font_md, font_sm, font_xs, sc)

        if confirming:
            del_name = chars[confirm_del_idx]["name"] if 0 <= confirm_del_idx < len(chars) else "?"
            answer = _draw_confirm_delete(screen, events, del_name, press, font_md, font_sm)
            if answer == "yes":
                net.send(_MT.DELETE_CHARACTER, {"char_id": confirm_del_id})
                # Remoção otimista: já remove da lista local
                chars = [c for c in chars if c.get("id") != confirm_del_id]
                confirm_del_id  = -1
                confirm_del_idx = -1
            elif answer == "no":
                confirm_del_id  = -1
                confirm_del_idx = -1
        elif action is not None:
            kind = action[0]
            if kind == "play":
                i = action[1]
                net.send(_MT.SELECT_CHARACTER, {"char_id": chars[i].get("id", -1)})
                pending_action = "selecting"
                pending_start  = time.time()
                game_buffer.clear()
            elif kind == "delete":
                confirm_del_idx = action[1]
                confirm_del_id  = chars[action[1]].get("id", -1)
            elif kind == "create":
                result = _run_creation(screen, clock, sc, net=net)
                if result is not None:
                    # _run_creation já mandou CREATE_CHARACTER e esperou
                    # CHARACTER_CREATED internamente (erro tipo "nome já
                    # usado" mantém a tela de criação aberta com aviso —
                    # não passa disso pra cá) — result é o char pronto.
                    chars.append(result)
                    status = ""
                press.pressed = ""
            elif kind == "quit":
                return False

        if status:
            e = font_sm.render(status, False, _DEL_TXT)
            screen.blit(e, e.get_rect(centerx=sw // 2, bottom=panel_rect.top - int(6 * sc)))

        pygame.display.flip()


# ── Tela de criação ──────────────────────────────────────────────────────────
# Atlas de UI (20/10/2026): janela escura do mockup (canto inferior de
# assets/content/exemplo.png, mapeada tile a tile) — campo de nome +
# Sortear no topo, 1 card por classe (preview + nome + descrição), a
# classe ESCOLHIDA ganha a moldura do atlas (as outras ficam sem — decisão
# do usuário), Voltar/Confirmar "altos" encostados na borda de baixo.
# Cards alargam repetindo as bordas da moldura pra caber a descrição
# (quebrada em linhas). Botões agem no SOLTAR (ui/atlas_window.py).

_CRE_TOP_ROWS   = 4     # título (2) + linha do nome + linha da mensagem de erro
_CRE_CARD_MIN_W = 4     # tiles (mockup)
_CRE_DESC_W     = 6     # tiles de texto da descrição antes de quebrar
_CRE_CARD_PAD   = 6     # px RAW de folga interna do card
_CRE_PREVIEW_TOP = 14   # px RAW: topo do boneco visível (abaixo do contorno da moldura, ~8px pra dentro)

_CRE_NAME_COL = (230, 210, 160)
_CRE_DESC_COL = (185, 170, 140)
_CRE_LOCK_COL = (120, 112, 100)
_CRE_ERR_COL  = (255, 140, 140)


def _cre_layout(font_md, font_sm, font_xs, name_max_len: int) -> dict:
    """Tamanhos em tiles a partir do texto medido."""
    T, S = aw.T, aw.S
    wrap_px = _CRE_DESC_W * T * S
    descs = {}
    widest = 0
    for cls in _CLASSES:
        lines = []
        for para in cls["description"].split("\n"):
            lines += _wrap_text(para, wrap_px, font_xs)
        descs[cls["id"]] = lines
        widest = max([widest, font_sm.size(cls["label"])[0]] + [font_xs.size(l)[0] for l in lines])
    card_w = max(_CRE_CARD_MIN_W, math.ceil((widest / S + 2 * _CRE_CARD_PAD) / T))
    lh_sm = math.ceil(font_sm.get_linesize() / S)
    lh_xs = math.ceil(font_xs.get_linesize() / S)
    n_lines = max(len(v) for v in descs.values()) + 1   # + "[em breve]" em classe travada
    content_h = _CRE_PREVIEW_TOP + 32 + 4 + lh_sm + 4 + n_lines * lh_xs + _CRE_CARD_PAD
    card_h = max(6, math.ceil(content_h / T))
    field_w = aw.tiles_for(font_md, ["M" * name_max_len], 5)
    reroll_w = aw.button_tiles(font_md, ["Sortear"], 3)
    btn_w = aw.button_tiles(font_md, ["Voltar", "Confirmar", "Criando..."], 3)
    title_w = aw.tiles_for(font_md, ["Criar Personagem"], 1) + 2
    tw = max(len(_CLASSES) * card_w + 2, field_w + 1 + reroll_w + 2, 2 * btn_w + 3, title_w)
    card_w = (tw - 2) // len(_CLASSES)
    th = _CRE_TOP_ROWS + card_h + 2
    return {"tw": tw, "th": th, "card_w": card_w, "card_h": card_h, "descs": descs,
            "field_w": field_w, "reroll_w": reroll_w, "btn_w": btn_w,
            "lh_sm": lh_sm, "lh_xs": lh_xs}


def _wrap_text(text: str, max_px: int, font) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if cur and font.size(test)[0] > max_px:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _draw_creation(screen, events, st: dict, press, font_md, font_sm, font_xs,
                   name_max_len: int, hits: "dict | None" = None) -> "tuple | None":
    """Desenha a criação e devolve a ação disparada (no soltar): ("field",
    ativo), ("reroll",), ("class", id), ("confirm",), ("back",) ou None.
    `st`: name_text, name_active, suggest_pending, creating_pending,
    error_msg, selected_class. `hits` (opcional): rects de TELA pros testes."""
    T, S = aw.T, aw.S
    sw, sh = screen.get_size()
    screen.fill(_BG)
    lay = _cre_layout(font_md, font_sm, font_xs, name_max_len)
    tw, th, cw, ch = lay["tw"], lay["th"], lay["card_w"], lay["card_h"]
    f = aw.Frame(screen, events, tw, th, area=aw.safe_area(sw, sh, sh), press=press,
                 font=font_md, raw=ui_atlas.dark_list_window_raw(tw, th), overlay=False)
    action = None

    def _hit(hid, x0, y0, x1, y1):
        if hits is not None:
            hits[hid] = pygame.Rect(f.px + int(x0 * S), f.py + int(y0 * S),
                                    int((x1 - x0) * S), int((y1 - y0) * S))

    # ── Nome + Sortear (linha 2) ──────────────────────────────────────────
    fw, rw = lay["field_w"], lay["reroll_w"]
    fcol = (tw - (fw + 1 + rw)) / 2
    if st["name_text"]:
        cursor = "|" if st["name_active"] and pygame.time.get_ticks() % 1000 < 500 else ""
        shown, color = st["name_text"] + cursor, None
    elif st["suggest_pending"]:
        shown, color = "Gerando nome...", _CRE_LOCK_COL
    else:
        shown, color = ("|" if st["name_active"] else ""), None
    ay0, ay1 = ui_atlas.FIELD_ART_Y
    inset = ui_atlas.FIELD_ART_INSET_X
    fx0, fx1 = fcol * T + inset, (fcol + fw) * T - inset
    over_field = fx0 <= f.rx < fx1 and 2 * T + ay0 <= f.ry < 2 * T + ay1
    if f.text_field("cre:name", fcol, 2, fw, shown, active=st["name_active"]):
        action = ("field", True)
    elif f.clicked and not over_field and st["name_active"]:
        action = ("field", False)
    if color is not None:   # placeholder cinza (text_field só conhece normal/ativo)
        f.texts[-1] = f.texts[-1][:3] + (color,) + f.texts[-1][4:]
    _hit("cre:name", fx0, 2 * T + ay0, fx1, 2 * T + ay1)
    rcol = fcol + fw + 1
    if f.button("cre:reroll", rcol, 2, rw, "Sortear", tall=False,
                enabled=not st["suggest_pending"]):
        action = ("reroll",)
    by0, by1 = ui_atlas.button_art_y(False)
    _hit("cre:reroll", rcol * T, 2 * T + by0, (rcol + rw) * T, 2 * T + by1)
    if st["error_msg"]:
        f.text(st["error_msg"], tw * T / 2, 3 * T + T / 2, _CRE_ERR_COL, font=font_xs)

    # ── Cards de classe ───────────────────────────────────────────────────
    previews = []
    for idx, cls in enumerate(_CLASSES):
        cx0 = 1 + idx * cw
        x0, y0 = cx0 * T, _CRE_TOP_ROWS * T
        x1, y1 = (cx0 + cw) * T, (_CRE_TOP_ROWS + ch) * T
        locked = cls["locked"]
        selected = cls["id"] == st["selected_class"]
        if selected:
            ui_atlas.blit_frame(f.raw, cx0, _CRE_TOP_ROWS, cw, ch)
        if not locked and f.press(f"cre:class:{cls['id']}", x0, y0, x1, y1)[1]:
            action = ("class", cls["id"])
        _hit(f"cre:class:{cls['id']}", x0, y0, x1, y1)
        mid = (x0 + x1) / 2
        vis_top = y0 + _CRE_PREVIEW_TOP
        frame = None if locked else _get_class_preview_frame(cls["id"], 1.0)
        if frame is not None:
            # Boneco visível = 32x32 central do frame de 64 (128px a 2x).
            previews.append((frame, int(mid * S) - frame.get_width() // 2,
                             int((vis_top - 16) * S)))
        else:
            col = _CRE_LOCK_COL if locked else _CLASS_COLORS.get(cls["id"], (150, 150, 150))
            pygame.draw.rect(f.raw, col, (int(mid) - 10, vis_top + 6, 20, 20))
        ty = vis_top + 32 + 4
        f.text(cls["label"], mid, ty, _CRE_LOCK_COL if locked else
               (aw.ACTIVE_COL if selected else _CRE_NAME_COL), font=font_sm, anchor="midtop")
        ty += lay["lh_sm"] + 4
        lines = list(lay["descs"][cls["id"]]) + (["[em breve]"] if locked else [])
        for line in lines:
            f.text(line, mid, ty, _CRE_LOCK_COL if locked else _CRE_DESC_COL,
                   font=font_xs, anchor="midtop")
            ty += lay["lh_xs"]

    # ── Voltar / Confirmar (botões altos encostados na borda de baixo) ───
    bw = lay["btn_w"]
    by = th - 2
    if f.button("cre:back", 1, by, bw, "Voltar"):
        action = ("back",)
    pending = st["creating_pending"]
    if f.button("cre:confirm", tw - 1 - bw, by, bw, "Criando..." if pending else "Confirmar",
                enabled=not pending):
        action = ("confirm",)
    ty0, ty1 = ui_atlas.button_art_y(True)
    _hit("cre:back", T, by * T + ty0, (1 + bw) * T, by * T + ty1)
    _hit("cre:confirm", (tw - 1 - bw) * T, by * T + ty0, (tw - 1) * T, by * T + ty1)

    f.end("Criar Personagem")
    for frame, px, py in previews:
        screen.blit(frame, (f.px + px, f.py + py))
    return action


def _run_creation(screen, clock, sc: float, net=None) -> "dict | None":
    """Tela de criação de personagem. Retorna dict ou None (voltar).

    `net`: se fornecido (modo online), a caixa de nome já vem preenchida
    com uma sugestão verificada contra o banco (SUGGEST_NAME/
    NAME_SUGGESTION — só o servidor sabe quais nomes já existem) e o botão
    "Sortear" pede uma nova ao servidor. Sem `net`, a sugestão é gerada
    localmente e "Sortear" só gera outra local, sem round-trip de rede.

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

    st = {"name_text": "", "name_active": False, "suggest_pending": False,
          "creating_pending": False, "error_msg": "", "selected_class": "guerreiro"}
    name_user_edited = False   # trava a auto-sugestão assim que o jogador digita
    creating_start   = 0.0
    press = aw.PressState()

    def _request_suggestion():
        if net is not None:
            net.send(_MT_names.SUGGEST_NAME, {})
            st["suggest_pending"] = True
        else:
            st["name_text"] = generate_name_candidate()

    _request_suggestion()

    font_md = _font(int(24 * sc))
    font_sm = _font(int(19 * sc))
    font_xs = _font(int(15 * sc))

    while True:
        clock.tick(60)

        if net is not None:
            for mt, payload, _seq, _ts in net.poll():
                if mt == _MT_names.NAME_SUGGESTION:
                    st["suggest_pending"] = False
                    if not name_user_edited:
                        st["name_text"] = payload.get("name", "")
                elif mt == _MT_names.CHARACTER_CREATED:
                    return payload.get("char")
                elif mt == _MT_names.CHARACTER_ERROR:
                    st["creating_pending"] = False
                    st["error_msg"] = _ERROR_MESSAGES.get(
                        payload.get("reason"), "Erro ao criar personagem.")

            if st["creating_pending"] and time.time() - creating_start > 15.0:
                st["creating_pending"] = False
                st["error_msg"] = "Servidor não respondeu. Tente novamente."

        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                return None
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return None
                if st["name_active"]:
                    name_text = st["name_text"]
                    if event.key == pygame.K_BACKSPACE:
                        name_text = name_text[:-1]
                        name_user_edited = True
                        st["error_msg"] = ""
                    elif event.key == pygame.K_RETURN:
                        st["name_active"] = False
                    elif (len(name_text) < NAME_MAX_LEN and event.unicode
                          and is_valid_name_char(event.unicode)):
                        name_text += event.unicode
                        name_user_edited = True
                        st["error_msg"] = ""
                    # 1ª letra sempre maiúscula, digitada em qualquer caixa —
                    # jogador pode digitar tudo minúsculo sem se preocupar.
                    st["name_text"] = normalize_name(name_text)

        action = _draw_creation(screen, events, st, press, font_md, font_sm, font_xs, NAME_MAX_LEN)
        if action is not None:
            kind = action[0]
            if kind == "field":
                st["name_active"] = action[1]
            elif kind == "reroll":
                name_user_edited = False
                st["error_msg"] = ""
                _request_suggestion()
            elif kind == "class":
                st["selected_class"] = action[1]
            elif kind == "back":
                return None
            elif kind == "confirm":
                candidate = st["name_text"].strip()
                if not is_valid_name(candidate):
                    st["error_msg"] = (f"Nome precisa ter {NAME_MIN_LEN}-{NAME_MAX_LEN} "
                                       "letras, sem espaço/número/símbolo.")
                elif net is not None:
                    net.send(_MT_names.CREATE_CHARACTER, {
                        "name": candidate, "class_id": st["selected_class"]})
                    st["creating_pending"] = True
                    creating_start = time.time()
                    st["error_msg"] = ""
                else:
                    return {"name": candidate, "class_id": st["selected_class"]}

        pygame.display.flip()


# ── Preview de classe (19/10/2026, pedido do usuário) ───────────────────────
# Sprite idle-down animado (mesmo mecanismo do jogo,
# engine/character_animation.py) no lugar do retângulo colorido antigo, nas
# 2 telas (seleção e criação). Frame real é 64x64, mas o personagem
# desenhado ocupa só o tile de colisão central de 32x32 (mesma convenção de
# `CHAR_COLLISION_SIZE` — ver CLAUDE.md) — por isso usa o frame INTEIRO
# (nunca recorta/aumenta zoom tentando "preencher" a caixa), só escalado por
# `sc` igual todo o resto desta tela, pra ficar proporcional em qualquer
# resolução. `None` = sem sprite ainda pra essa classe/direção — cai pro
# retângulo de cor de sempre (mesmo padrão de fallback usado no resto do
# jogo). Ver PROBLEMAS_ARQUITETURA.md §291.
# 64 → 128 (19/10/2026, pedido do usuário — "aumentado em 200%, o boneco
# que tem 32x32 vai ter 64x64" — o CONTEÚDO real de 32x32 dentro do frame
# dobra pra 64x64, o frame inteiro de 64 dobra pra 128). Ver
# PROBLEMAS_ARQUITETURA.md §292.
_CLASS_PREVIEW_BASE_SIZE = 128


def _get_class_preview_frame(class_id: str, sc: float) -> "pygame.Surface | None":
    from engine.character_animation import get_character_frame
    frame = get_character_frame(class_id, "idle", "down", pygame.time.get_ticks())
    if frame is None:
        return None
    size_px = int(_CLASS_PREVIEW_BASE_SIZE * sc)
    if frame.get_width() != size_px:
        frame = pygame.transform.scale(frame, (size_px, size_px))
    return frame


# ── Render da seleção (atlas de UI) ──────────────────────────────────────────
# Janela escura do mockup (assets/content/exemplo.png, canto superior
# direito), mapeada tile a tile: título nas 2 primeiras linhas, 1 moldura
# de 3 linhas por personagem (Jogar/Excluir à direita, botões "baixos"),
# coluna da direita cromada pra barra de rolagem (só aparece quando há
# mais personagens do que cabe), Criar Personagem/Sair empilhados embaixo.
# Mínimo de 3 molduras (como no mockup): as que sobram mostram "Vazio".

_SEL_MIN_SLOTS   = 3   # molduras sempre visíveis (mockup)
_SEL_VIEW_SLOTS  = 3   # molduras que cabem antes de rolar
_SEL_SLOT_ROWS   = 3   # linhas (tiles) por moldura
_SEL_TOP_ROWS    = 2   # título
_SEL_BOTTOM_ROWS = 3   # 2 botões empilhados + borda
_SEL_TEXT_X      = 3 * aw.T + 8   # px RAW: texto do personagem, à direita do preview
_SEL_PREVIEW_CX  = 2 * aw.T       # px RAW: centro do preview na moldura

_SEL_NAME_COL  = (230, 210, 160)
_SEL_INFO_COL  = (185, 170, 140)
_SEL_EMPTY_COL = (120, 112, 100)


def _sel_info_text(c: dict) -> str:
    return f"{c.get('class_id', 'guerreiro').capitalize()}  —  Nível {c.get('level', 1)}"


def _sel_layout(chars: list, font_md, font_sm) -> dict:
    """Tamanhos em tiles a partir do TEXTO medido (fonte acompanha a escala
    da tela, o atlas não)."""
    T, S = aw.T, aw.S
    n_slots = max(_SEL_MIN_SLOTS, len(chars))
    play_w = aw.button_tiles(font_md, ["Jogar"], 3)
    del_w  = aw.button_tiles(font_md, ["Excluir"], 3)
    text_w = max([font_md.size(c.get("name", ""))[0] for c in chars]
                 + [font_sm.size(_sel_info_text(c))[0] for c in chars]
                 + [font_md.size("Vazio")[0]]) / S
    frame_w = max(16, math.ceil((_SEL_TEXT_X + text_w + 8) / T) + play_w + del_w + 1)
    bottom_w = aw.button_tiles(font_md, ["Criar Personagem", "Sair"], 3)
    title_w = aw.tiles_for(font_md, ["Selecionar Personagem"], 1) + 2
    tw = max(frame_w + 1, bottom_w + 2, title_w)
    frame_w = tw - 1
    view = min(n_slots, _SEL_VIEW_SLOTS)
    th = _SEL_TOP_ROWS + view * _SEL_SLOT_ROWS + _SEL_BOTTOM_ROWS
    return {"tw": tw, "th": th, "frame_w": frame_w, "n_slots": n_slots, "view": view,
            "play_w": play_w, "del_w": del_w, "bottom_w": bottom_w}


def _draw_selection(screen, events, chars, can_create, press, scroll,
                    font_md, font_sm, font_xs, sc, hits: "dict | None" = None) -> tuple:
    """Desenha a seleção e devolve (ação, rect_da_janela). Ação: ("play", i),
    ("delete", i), ("create",), ("quit",) ou None. `hits` (opcional) recebe
    o rect de TELA de cada botão — pros testes acharem onde clicar."""
    T, S = aw.T, aw.S
    sw, sh = screen.get_size()
    screen.fill(_BG)
    lay = _sel_layout(chars, font_md, font_sm)
    tw, th, fw = lay["tw"], lay["th"], lay["frame_w"]
    f = aw.Frame(screen, events, tw, th, area=aw.safe_area(sw, sh, sh), press=press,
                 font=font_md, raw=ui_atlas.dark_list_window_raw(tw, th), overlay=False)
    action = None
    ay0, ay1 = ui_atlas.button_art_y(False)

    def _hit(hid, col, w, row_px):
        if hits is not None:
            hits[hid] = pygame.Rect(f.px + col * T * S, f.py + int((row_px + ay0) * S),
                                    w * T * S, (ay1 - ay0) * S)

    view_y0 = _SEL_TOP_ROWS * T
    view_h  = lay["view"] * _SEL_SLOT_ROWS * T
    content_h = lay["n_slots"] * _SEL_SLOT_ROWS * T
    off = f.scrollbar(scroll, tw - 1, _SEL_TOP_ROWS, _SEL_TOP_ROWS + lay["view"] * _SEL_SLOT_ROWS - 1,
                      content_h - view_h)

    in_view = view_y0 <= f.ry < view_y0 + view_h and 0 <= f.rx < fw * T
    fc = f.child(pygame.Surface((fw * T, content_h), pygame.SRCALPHA),
                 f.ry - view_y0 + off if in_view else -10_000)
    previews = []
    for i in range(lay["n_slots"]):
        r0 = i * _SEL_SLOT_ROWS
        ui_atlas.blit_frame(fc.raw, 0, r0, fw, _SEL_SLOT_ROWS)
        cy = (r0 + _SEL_SLOT_ROWS / 2) * T
        if i >= len(chars):
            fc.text("Vazio", _SEL_TEXT_X, cy, _SEL_EMPTY_COL, anchor="midleft")
            continue
        c = chars[i]
        fc.text(c.get("name", "Aventureiro"), _SEL_TEXT_X, cy - 1, _SEL_NAME_COL, anchor="bottomleft")
        fc.text(_sel_info_text(c), _SEL_TEXT_X, cy + 1, _SEL_INFO_COL, font=font_sm, anchor="topleft")
        del_col = fw - 1 - lay["del_w"]
        play_col = del_col - lay["play_w"]
        if fc.button(f"sel:play:{i}", play_col, r0 + 1, lay["play_w"], "Jogar", tall=False):
            action = ("play", i)
        if fc.button(f"sel:del:{i}", del_col, r0 + 1, lay["del_w"], "Excluir", tall=False):
            action = ("delete", i)
        row_px = view_y0 + (r0 + 1) * T - off
        if view_y0 <= row_px and row_px + T <= view_y0 + view_h:
            _hit(f"sel:play:{i}", play_col, lay["play_w"], row_px)
            _hit(f"sel:del:{i}", del_col, lay["del_w"], row_px)
        frame = _get_class_preview_frame(c.get("class_id", "guerreiro"), 1.0)
        if frame is not None:
            previews.append((frame, _SEL_PREVIEW_CX * S - frame.get_width() // 2,
                             int(cy * S) - frame.get_height() // 2))
        else:
            col = _CLASS_COLORS.get(c.get("class_id"), (150, 150, 150))
            pygame.draw.rect(fc.raw, col, (_SEL_PREVIEW_CX - 9, int(cy) - 9, 18, 18))

    bw = lay["bottom_w"]
    bcol = (tw - bw) / 2        # centralizado de verdade (meio tile quando ímpar)
    by = th - _SEL_BOTTOM_ROWS
    if f.button("sel:create", bcol, by, bw, "Criar Personagem", tall=False, enabled=can_create):
        action = ("create",)
    if f.button("sel:quit", bcol, by + 1, bw, "Sair", tall=False):
        action = ("quit",)
    _hit("sel:create", bcol, bw, by * T)
    _hit("sel:quit", bcol, bw, (by + 1) * T)

    f.end("Selecionar Personagem")
    content = ui_atlas.scale_up(fc.raw)
    aw.draw_texts(content, fc.texts, font_md)
    # Preview por ÚLTIMO (sprite 128px "espia" por cima da moldura vizinha
    # em vez de ser cortado — mesma decisão de §292), recortado só pela
    # área visível da lista.
    for frame, px, py in previews:
        content.blit(frame, (px, py))
    view = content.subsurface((0, off * S, content.get_width(), view_h * S))
    screen.blit(view, (f.px, f.py + view_y0 * S))
    return action, pygame.Rect(f.px, f.py, f.pw, f.ph)


def _draw_confirm_delete(screen, events, name: str, press, font_md, font_sm,
                         hits: "dict | None" = None) -> "str | None":
    """Janela escura pequena "Excluir personagem?" por cima da seleção.
    Devolve "yes", "no" ou None. `hits`: ver _draw_selection."""
    sw, sh = screen.get_size()
    msg = f'"{name}" será removido permanentemente.'
    bw = aw.button_tiles(font_md, ["Excluir", "Cancelar"], 3)
    tw = max(aw.tiles_for(font_sm, [msg], 1) + 2,
             aw.tiles_for(font_md, ["Excluir personagem?"], 1) + 2, 2 * bw + 3)
    th = 6
    f = aw.Frame(screen, events, tw, th, area=aw.safe_area(sw, sh, sh), press=press,
                 font=font_md, style="dark")
    f.text(msg, tw * aw.T / 2, 2.5 * aw.T, _SEL_INFO_COL, font=font_sm)
    answer = None
    bx = (tw - (2 * bw + 1)) / 2        # botões centralizados como grupo
    if f.button("del:yes", bx, 3, bw, "Excluir"):
        answer = "yes"
    if f.button("del:no", bx + bw + 1, 3, bw, "Cancelar"):
        answer = "no"
    if hits is not None:
        ay0, ay1 = ui_atlas.button_art_y(True)
        for hid, col in (("del:yes", bx), ("del:no", bx + bw + 1)):
            hits[hid] = pygame.Rect(round(f.px + col * aw.T * aw.S), f.py + (3 * aw.T + ay0) * aw.S,
                                    bw * aw.T * aw.S, (ay1 - ay0) * aw.S)
    f.end("Excluir personagem?")
    return answer
