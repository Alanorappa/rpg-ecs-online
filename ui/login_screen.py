"""
login_screen.py
Tela de login e cadastro exibida antes do GameEngine iniciar.

run(screen, host, port) -> (username, password, net_client, char_list) | None
  Retorna ao receber AUTH_OK do servidor (autenticado, ainda sem entrar no mundo).
  O chamador usa net_client e char_list para a tela de seleção de personagem.
  Retorna None se o usuário fechou a janela.
"""
from __future__ import annotations
import time
import pygame
from shared.messages import MsgType
from shared.constants import GAME_VERSION

_BG        = (6,  8, 14)
_PANEL_BG  = (14, 16, 26)
_BORDER    = (55, 45, 90)
_TITLE_COL = (210, 185, 255)
_LABEL_COL = (150, 135, 185)
_TEXT_COL  = (220, 210, 240)
_ERR_COL   = (220,  80,  80)
_OK_COL    = (80,  210, 120)
_BTN_NRM   = (40,  32,  68)
_BTN_HOV   = (65,  52, 108)
_BTN_TXT   = (220, 205, 255)
_BTN_PRI   = (70,  45, 130)
_BTN_PRI_H = (100,  65, 175)
_TAB_ACT   = (50,  40,  88)
_TAB_INACT = (22,  18,  38)
_INP_BG    = (10,   9,  20)
_INP_BDR   = (70,  55, 110)
_INP_ACT   = (150, 115, 220)

_TIMEOUT = 10.0


class _TextInput:
    def __init__(self, rect, placeholder="", password=False, max_len=24):
        self.rect, self.placeholder = rect, placeholder
        self.password, self.max_len = password, max_len
        self.text   = ""
        self.active = False
        self._cur_on = True
        self._cur_t  = 0.0

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.active = self.rect.collidepoint(event.pos)
            return self.active
        if not self.active:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif event.key not in (pygame.K_RETURN, pygame.K_TAB, pygame.K_ESCAPE):
                if len(self.text) < self.max_len and event.unicode.isprintable():
                    self.text += event.unicode
        return True

    def update(self, dt):
        self._cur_t += dt
        if self._cur_t >= 0.5:
            self._cur_t -= 0.5
            self._cur_on = not self._cur_on

    def draw(self, surf, font):
        bdr = _INP_ACT if self.active else _INP_BDR
        pygame.draw.rect(surf, _INP_BG, self.rect, border_radius=4)
        pygame.draw.rect(surf, bdr,     self.rect, 1, border_radius=4)
        disp = ("*" * len(self.text)) if self.password else self.text
        if self.active and self._cur_on:
            disp += "|"
        col = _TEXT_COL if (self.text or self.active) else _LABEL_COL
        txt = disp if (self.text or self.active) else self.placeholder
        lbl = font.render(txt, False, col)
        cy  = self.rect.y + (self.rect.h - lbl.get_height()) // 2
        clip = pygame.Rect(self.rect.x + 2, self.rect.y + 2,
                           self.rect.w - 4, self.rect.h - 4)
        old = surf.get_clip(); surf.set_clip(clip)
        surf.blit(lbl, (self.rect.x + 10, cy))
        surf.set_clip(old)


def _btn(surf, rect, label, font, hov, primary=False):
    bg = (_BTN_PRI_H if hov else _BTN_PRI) if primary else (_BTN_HOV if hov else _BTN_NRM)
    pygame.draw.rect(surf, bg,      rect, border_radius=5)
    pygame.draw.rect(surf, _BORDER, rect, 1, border_radius=5)
    lbl = font.render(label, False, _BTN_TXT)
    surf.blit(lbl, lbl.get_rect(center=rect.center))


def _panel(surf, rect):
    pygame.draw.rect(surf, _PANEL_BG, rect, border_radius=8)
    pygame.draw.rect(surf, _BORDER,   rect, 1, border_radius=8)


def _safe_disconnect(net):
    if net is None: return
    try: net.disconnect()
    except Exception: pass


def run(screen: pygame.Surface, host: str = "localhost", port: int = 8765):
    """
    Loop de login/cadastro.
    Retorna (username, password, net_client, char_list) ou None (quit).
    """
    from client.network import NetworkClient

    W, H  = screen.get_size()
    clock = pygame.time.Clock()

    font_title = pygame.font.Font(None, 38)
    font_md    = pygame.font.Font(None, 24)
    font_sm    = pygame.font.Font(None, 20)

    st = {
        "tab":          "login",
        "status_msg":   "",
        "status_ok":    False,
        "net":          None,
        "pending":      "",        # "login" | "register" | ""
        "pending_time": 0.0,
    }

    PW, PH = 420, 340
    px = W // 2 - PW // 2
    py = H // 2 - PH // 2

    f_user_l = _TextInput(pygame.Rect(px + 20, py + 118, PW - 40, 36), "Usuário")
    f_pass_l = _TextInput(pygame.Rect(px + 20, py + 178, PW - 40, 36),
                          "Senha", password=True)
    f_user_r  = _TextInput(pygame.Rect(px + 20, py + 118, PW - 40, 36),
                           "Usuário (3–20 chars)")
    f_pass_r  = _TextInput(pygame.Rect(px + 20, py + 178, PW - 40, 36),
                           "Senha", password=True)
    f_pass_r2 = _TextInput(pygame.Rect(px + 20, py + 238, PW - 40, 36),
                           "Confirmar senha", password=True)

    def _inputs():
        return [f_user_l, f_pass_l] if st["tab"] == "login" else [f_user_r, f_pass_r, f_pass_r2]

    def _set_status(msg, ok=False):
        st["status_msg"] = msg; st["status_ok"] = ok

    def _start_request(action):
        _safe_disconnect(st["net"])
        st["net"] = None
        net = NetworkClient(host=host, port=port)
        net.connect()
        st["net"] = net; st["pending"] = action
        st["pending_time"] = time.time()
        _set_status("Conectando...")

    def _submit_login():
        u, p = f_user_l.text.strip(), f_pass_l.text
        if not u or not p: _set_status("Preencha usuário e senha."); return
        _start_request("login")

    def _submit_register():
        u, p, p2 = f_user_r.text.strip(), f_pass_r.text, f_pass_r2.text
        if len(u) < 3:  _set_status("Usuário: mínimo 3 caracteres."); return
        if not p:        _set_status("Senha não pode ser vazia.");     return
        if p != p2:      _set_status("As senhas não coincidem.");      return
        _start_request("register")

    while True:
        dt     = clock.tick(60) / 1000.0
        events = pygame.event.get()

        for event in events:
            if event.type == pygame.QUIT:
                _safe_disconnect(st["net"]); return None
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    _safe_disconnect(st["net"]); return None
                if event.key == pygame.K_RETURN and not st["pending"]:
                    if st["tab"] == "login": _submit_login()
                    continue
                if event.key == pygame.K_TAB:
                    inps = _inputs()
                    for i, f in enumerate(inps):
                        if f.active:
                            f.active = False
                            inps[(i + 1) % len(inps)].active = True
                            break
                    continue
            for f in _inputs():
                f.handle_event(event)

        for f in _inputs():
            f.update(dt)

        # ── Poll de rede ──────────────────────────────────────────────────────
        net = st["net"]
        if net and st["pending"]:
            if net.connected and st["status_msg"] == "Conectando...":
                if st["pending"] == "login":
                    net.login(f_user_l.text.strip(), f_pass_l.text)
                    _set_status("Autenticando...")
                else:
                    net.register(f_user_r.text.strip(), f_pass_r.text)
                    _set_status("Criando conta...")

            for msg_type, payload, _seq, _ts in net.poll():
                if st["pending"] == "login":
                    if msg_type == MsgType.AUTH_OK:
                        # Autenticado — repassa net e lista de chars para main.py
                        chars = payload.get("characters", [])
                        return (f_user_l.text.strip(), f_pass_l.text, net, chars)
                    elif msg_type == MsgType.LOGIN_ERROR:
                        r = payload.get("reason", "")
                        _set_status({
                            "invalid_credentials": "Usuário ou senha incorretos.",
                            "already_online":      "Usuário já está conectado.",
                            "version_mismatch":    "Versão incompatível com o servidor.",
                        }.get(r, f"Erro: {r}"))
                        _safe_disconnect(net); st["net"] = None; st["pending"] = ""

                elif st["pending"] == "register":
                    if msg_type == MsgType.REGISTER_OK:
                        _set_status("Conta criada! Faça login.", ok=True)
                        _safe_disconnect(net); st["net"] = None; st["pending"] = ""
                        f_user_l.text = f_user_r.text.strip(); f_pass_l.text = ""
                        st["tab"] = "login"
                    elif msg_type == MsgType.REGISTER_ERROR:
                        r = payload.get("reason", "")
                        _set_status({
                            "username_taken": "Nome de usuário já existe.",
                            "invalid_input":  "Dados inválidos (usuário: 3–20 chars).",
                        }.get(r, f"Erro: {r}"))
                        _safe_disconnect(net); st["net"] = None; st["pending"] = ""

            if st["pending"] and time.time() - st["pending_time"] > _TIMEOUT:
                _set_status("Servidor não respondeu. Verifique se está rodando.")
                _safe_disconnect(net); st["net"] = None; st["pending"] = ""

        # ── Render ────────────────────────────────────────────────────────────
        screen.fill(_BG)
        _panel(screen, pygame.Rect(px, py, PW, PH))
        title = font_title.render("RPG ECS Online", False, _TITLE_COL)
        screen.blit(title, title.get_rect(centerx=W // 2, y=py + 14))

        tab_w, tab_h, tab_y = PW // 2, 34, py + 58
        mx, my  = pygame.mouse.get_pos()
        clicked = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)

        for tid, label in (("login", "Entrar"), ("register", "Criar conta")):
            tr  = pygame.Rect(px + (0 if tid == "login" else tab_w), tab_y, tab_w, tab_h)
            act = (tid == st["tab"])
            bg  = _TAB_ACT if act else _TAB_INACT
            if not act and tr.collidepoint(mx, my):
                bg = tuple(min(255, c + 14) for c in bg)
            pygame.draw.rect(screen, bg, tr, border_radius=4)
            pygame.draw.rect(screen, _BORDER, tr, 1, border_radius=4)
            lbl = font_md.render(label, False, _TEXT_COL if act else _LABEL_COL)
            screen.blit(lbl, lbl.get_rect(center=tr.center))
            if not act and tr.collidepoint(mx, my) and clicked and not st["pending"]:
                st["tab"] = tid; st["status_msg"] = ""

        btn_y = py + PH - 56

        if st["tab"] == "login":
            screen.blit(font_sm.render("Usuário", False, _LABEL_COL), (px + 20, py + 104))
            f_user_l.draw(screen, font_md)
            screen.blit(font_sm.render("Senha", False, _LABEL_COL), (px + 20, py + 164))
            f_pass_l.draw(screen, font_md)
        else:
            screen.blit(font_sm.render("Usuário", False, _LABEL_COL), (px + 20, py + 104))
            f_user_r.draw(screen, font_md)
            screen.blit(font_sm.render("Senha", False, _LABEL_COL), (px + 20, py + 164))
            f_pass_r.draw(screen, font_md)
            screen.blit(font_sm.render("Confirmar senha", False, _LABEL_COL), (px + 20, py + 224))
            f_pass_r2.draw(screen, font_md)

        btn_lbl  = "Entrar" if st["tab"] == "login" else "Criar conta"
        btn_rect = pygame.Rect(px + PW // 2 - 90, btn_y, 180, 38)
        hov_btn  = btn_rect.collidepoint(mx, my)
        _btn(screen, btn_rect, btn_lbl, font_md, hov_btn, primary=True)
        if hov_btn and clicked and not st["pending"]:
            if st["tab"] == "login": _submit_login()
            else:                    _submit_register()

        if st["status_msg"]:
            col  = _OK_COL if st["status_ok"] else _ERR_COL
            smsg = font_sm.render(st["status_msg"], False, col)
            screen.blit(smsg, smsg.get_rect(centerx=W // 2, y=btn_y - 22))

        if st["pending"]:
            dots = "." * (int(time.time() * 2) % 4)
            spin = font_sm.render(f"Aguardando{dots}", False, _LABEL_COL)
            screen.blit(spin, spin.get_rect(centerx=W // 2, y=btn_y + 46))

        ver_s = font_sm.render(f"v{GAME_VERSION}", False, _LABEL_COL)
        screen.blit(ver_s, (W - ver_s.get_width() - 10, H - ver_s.get_height() - 8))

        pygame.display.flip()
