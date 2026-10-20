"""
launcher/login_ui.py
Janela do Launcher (08/10/2026, ver CLAUDE.md) — tkinter (stdlib, zero
dependência nova; livre pra usar aqui porque é um .spec SEPARADO do
jogo — rpg_online_client.spec continua excluindo tkinter de propósito).

Toda chamada de rede roda em thread separada (nunca trava a janela) —
resultado volta pra thread principal via `Tk.after()`, única forma
segura de mexer em widget tkinter fora da main thread.

Visual (08/10/2026, pedido do usuário): arte de fundo própria
(`assets/ui/Launcher.png`, arte do usuário, movida de `assets/hud/`
em 19/10/2026 — ver CLAUDE.md) — janela do TAMANHO EXATO
da imagem, widgets posicionados sobre a faixa escura de pedra na
metade de baixo da arte (reservada pra isso no próprio desenho).

Abas Entrar/Criar conta (09/10/2026, pedido do usuário) — mesma UX do
cadastro já existente dentro do jogo (`ui/login_screen.py`), mesma
validação local (usuário mín. 3 chars, senha não vazia, confirmação
precisa bater) — nunca lógica nova, só espelha o que já existe.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import config
from paths import resource_path
from shared.auth_utils import hash_password
from launcher import update_client as uc

# Paleta tirada da própria arte (moldura dourada do emblema, painel de
# pedra escura embaixo) — nunca cor tkinter padrão (cinza de sistema
# destoa MUITO do fundo).
_BG_PANEL   = "#1b1d21"   # pedra escura do painel inferior
_GOLD       = "#c9a227"   # moldura/faixa do emblema
_GOLD_DIM   = "#8a7232"   # bordas discretas (entry/separadores)
_TEXT       = "#e8e1c9"   # texto claro (combina com o pergaminho)
_TEXT_DIM   = "#a39f8e"
_ERROR      = "#e05a5a"
_OK         = "#56c27a"
_FIELD_BG   = "#2a2d33"

_ASSET_PATH = "assets/ui/Launcher.png"

# Layout compacto de campo (09/10/2026, pedido do usuário — campo "muito
# largo"): label ao lado do entry (não em cima), largura fixa menor,
# centralizado como unidade dentro do painel — em vez de ocupar a
# largura toda. Libera espaço vertical de quebra pra caber a aba nova
# sem precisar mudar o tamanho da janela (fixo = tamanho da arte).
_FIELD_LABEL_W = 78
_FIELD_GAP     = 8
_FIELD_ENTRY_W = 210
_FIELD_UNIT_W  = _FIELD_LABEL_W + _FIELD_GAP + _FIELD_ENTRY_W

_TAB_W = 150
_TAB_H = 26


def _base_url(cfg: dict) -> str:
    scheme = "https" if cfg.get("use_tls") else "http"
    host = cfg.get("server_host", "localhost")
    from shared.constants import LAUNCHER_API_PORT
    return f"{scheme}://{host}:{LAUNCHER_API_PORT}"


def _ssl_context(cfg: dict):
    if not cfg.get("use_tls"):
        return uc.build_ssl_context(None)
    return uc.build_ssl_context(resource_path("certs/server_cert.pem"))


class LauncherApp(tk.Tk):

    def __init__(self, install_dir: str):
        super().__init__()
        self.install_dir = install_dir
        self._logged_username = ""
        self._logged_password_hash = ""

        self._tab = "login"
        # Mensagem/usuário repassados de uma tela pra outra (ex: cadastro
        # concluído → volta pro login com usuário preenchido + aviso
        # verde) — consumidos uma única vez por _build_login_form.
        self._prefill_username  = ""
        self._pending_status_msg = ""
        self._pending_status_ok  = False
        self._auto_login_attempted = False

        self._bg_image = tk.PhotoImage(file=resource_path(_ASSET_PATH))
        W, H = self._bg_image.width(), self._bg_image.height()

        self.title("RPG Online — Launcher")
        self.resizable(False, False)
        self.geometry(f"{W}x{H}")
        self.configure(bg=_BG_PANEL)

        self._canvas = tk.Canvas(self, width=W, height=H, highlightthickness=0, bg=_BG_PANEL)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.create_image(0, 0, image=self._bg_image, anchor="nw")

        # Faixa escura do painel (reservada na própria arte) — mesmas
        # margens usadas por TODAS as telas (login/registro/status), só o
        # conteúdo interno muda.
        self._panel_x0, self._panel_y0 = 40, 365
        self._panel_x1, self._panel_y1 = W - 40, H - 25

        self._cfg = config.load()
        self._widgets: list = []   # limpos a cada troca de tela

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Launcher.Horizontal.TProgressbar",
                        troughcolor=_FIELD_BG, background=_GOLD,
                        bordercolor=_BG_PANEL, lightcolor=_GOLD, darkcolor=_GOLD)

        self._build_login_form()

    # ------------------------------------------------------------------
    def _clear_screen(self) -> None:
        for w in self._widgets:
            w.destroy()
        self._widgets = []

    def _place(self, widget, x: int, y: int, **kwargs) -> None:
        """Posiciona um widget no canvas (coordenadas relativas ao painel
        escuro) e registra pra limpeza na próxima troca de tela."""
        self._canvas.create_window(self._panel_x0 + x, self._panel_y0 + y,
                                   window=widget, anchor="nw", **kwargs)
        self._widgets.append(widget)

    def _entry(self, textvariable=None, show=None) -> tk.Entry:
        return tk.Entry(self._canvas, textvariable=textvariable, show=show,
                        bg=_FIELD_BG, fg=_TEXT, insertbackground=_TEXT,
                        relief="flat", highlightthickness=1,
                        highlightbackground=_GOLD_DIM, highlightcolor=_GOLD,
                        font=("Georgia", 11), width=1)

    def _label(self, text: str, fg: str = _TEXT, font=("Georgia", 10)) -> tk.Label:
        return tk.Label(self._canvas, text=text, bg=_BG_PANEL, fg=fg, font=font)

    def _gold_button(self, text: str, command) -> tk.Button:
        return tk.Button(
            self._canvas, text=text, command=command,
            bg=_GOLD, fg="#1b1d21", activebackground="#e0c04a",
            activeforeground="#1b1d21", relief="flat",
            font=("Georgia", 11, "bold"), cursor="hand2",
            padx=16, pady=6, bd=0)

    def _field_row(self, label_text: str, textvariable, y: int, show=None) -> tk.Entry:
        """1 linha label+entry lado a lado, centralizada como unidade
        dentro do painel (09/10/2026) — substitui o layout antigo de
        label em cima/entry ocupando a largura toda."""
        panel_w = self._panel_x1 - self._panel_x0
        left = (panel_w - _FIELD_UNIT_W) // 2
        lbl = self._label(label_text, font=("Georgia", 10))
        lbl.configure(anchor="e", justify="right")
        self._place(lbl, left, y, width=_FIELD_LABEL_W, height=24)
        entry = self._entry(textvariable=textvariable, show=show)
        self._place(entry, left + _FIELD_LABEL_W + _FIELD_GAP, y, width=_FIELD_ENTRY_W)
        return entry

    def _build_tab_bar(self) -> None:
        panel_w = self._panel_x1 - self._panel_x0
        left = (panel_w - _TAB_W * 2) // 2
        for i, (tid, label) in enumerate((("login", "Entrar"), ("register", "Criar conta"))):
            active = (tid == self._tab)
            btn = tk.Button(
                self._canvas, text=label, command=lambda t=tid: self._switch_tab(t),
                bg=_GOLD if active else _FIELD_BG,
                fg="#1b1d21" if active else _TEXT_DIM,
                activebackground=_GOLD if active else _FIELD_BG,
                activeforeground="#1b1d21" if active else _TEXT,
                relief="flat", bd=0, cursor="hand2",
                font=("Georgia", 10, "bold" if active else "normal"))
            self._place(btn, left + i * _TAB_W, 0, width=_TAB_W, height=_TAB_H)

    def _switch_tab(self, tab: str) -> None:
        if tab == self._tab:
            return
        if tab == "login":
            self._build_login_form()
        else:
            self._build_register_form()

    # ------------------------------------------------------------------
    def _build_login_form(self) -> None:
        self._tab = "login"
        self._clear_screen()
        panel_w = self._panel_x1 - self._panel_x0
        left = (panel_w - _FIELD_UNIT_W) // 2
        self._build_tab_bar()

        self.user_var = tk.StringVar(
            value=self._prefill_username or self._cfg.get("remembered_username", ""))
        user_entry = self._field_row("Usuário", self.user_var, 36)

        self.pass_var = tk.StringVar()
        pass_entry = self._field_row("Senha", self.pass_var, 68, show="•")

        self.remember_var = tk.BooleanVar(value=self._cfg.get("remember_login", False))
        remember_chk = tk.Checkbutton(
            self._canvas, text="Lembrar login", variable=self.remember_var,
            bg=_BG_PANEL, fg=_TEXT_DIM, activebackground=_BG_PANEL,
            activeforeground=_TEXT, selectcolor=_FIELD_BG,
            font=("Georgia", 9), bd=0, highlightthickness=0)
        self._place(remember_chk, left + _FIELD_LABEL_W + _FIELD_GAP, 100)

        self.error_label = self._label(
            self._pending_status_msg, fg=_OK if self._pending_status_ok else _ERROR,
            font=("Georgia", 9))
        self._place(self.error_label, 0, 128, width=panel_w)

        self.login_btn = self._gold_button("Entrar", self._on_submit)
        self._place(self.login_btn, panel_w // 2 - 50, 150)

        had_prefill = bool(self._prefill_username)
        self._prefill_username = ""
        self._pending_status_msg = ""
        self._pending_status_ok = False
        (pass_entry if had_prefill else user_entry).focus_set()

        # Login automático se a senha também foi lembrada (hash salvo) —
        # senão o usuário só vê o campo usuário pré-preenchido e ainda
        # precisa digitar a senha (nunca guardamos a senha de verdade
        # pra reconstituir sozinho sem o hash já pronto). Só na 1ª
        # construção da tela (senão reabrir a aba de login depois de
        # trocar pra "Criar conta" tentaria de novo).
        remembered_hash = self._cfg.get("remembered_password_hash", "")
        if (not self._auto_login_attempted and not had_prefill
                and self._cfg.get("remember_login") and self.user_var.get() and remembered_hash):
            self._auto_login_attempted = True
            self._do_login(self.user_var.get(), remembered_hash, remember=True)

    def _build_register_form(self) -> None:
        self._tab = "register"
        self._clear_screen()
        panel_w = self._panel_x1 - self._panel_x0
        self._build_tab_bar()

        self.reg_user_var = tk.StringVar()
        user_entry = self._field_row("Usuário", self.reg_user_var, 36)

        self.reg_pass_var = tk.StringVar()
        self._field_row("Senha", self.reg_pass_var, 68, show="•")

        self.reg_pass2_var = tk.StringVar()
        self._field_row("Confirmar", self.reg_pass2_var, 100, show="•")

        self.reg_error_label = self._label("", fg=_ERROR, font=("Georgia", 9))
        self._place(self.reg_error_label, 0, 128, width=panel_w)

        self.register_btn = self._gold_button("Criar conta", self._on_register_submit)
        self._place(self.register_btn, panel_w // 2 - 58, 150)

        user_entry.focus_set()

    def _build_status_screen(self, text: str) -> None:
        self._clear_screen()
        panel_w = self._panel_x1 - self._panel_x0

        self.status_label = self._label(text, font=("Georgia", 11))
        self._place(self.status_label, 0, 60, width=panel_w)

        self.progress = ttk.Progressbar(self._canvas, mode="determinate", length=panel_w,
                                        style="Launcher.Horizontal.TProgressbar")
        self._place(self.progress, 0, 100)

    # ── Login ──────────────────────────────────────────────────────────
    def _on_submit(self) -> None:
        username = self.user_var.get().strip()
        password = self.pass_var.get()
        if not username or not password:
            self.error_label.config(text="Preencha usuário e senha.", fg=_ERROR)
            return
        self.login_btn.config(state="disabled")
        self.error_label.config(text="")
        password_hash = hash_password(password)
        self._do_login(username, password_hash, remember=self.remember_var.get())

    def _do_login(self, username: str, password_hash: str, remember: bool) -> None:
        def work():
            base_url = _base_url(self._cfg)
            ctx = _ssl_context(self._cfg)
            try:
                ok = uc.check_login(base_url, username, password_hash, ctx)
            except Exception as e:
                self.after(0, self._on_login_error, f"Não foi possível contatar o servidor: {e}")
                return
            self.after(0, self._on_login_result, ok, username, password_hash, remember)
        threading.Thread(target=work, daemon=True).start()

    def _on_login_error(self, message: str) -> None:
        self.login_btn.config(state="normal")
        self.error_label.config(text=message, fg=_ERROR)

    def _on_login_result(self, ok: bool, username: str, password_hash: str, remember: bool) -> None:
        if not ok:
            self.login_btn.config(state="normal")
            self.error_label.config(text="Usuário ou senha inválidos.", fg=_ERROR)
            return
        # Guardado pra repassar pro jogo em _launch_and_exit (08/10/2026,
        # ver CLAUDE.md) — nunca a senha crua, só o hash já usado pra
        # logar aqui.
        self._logged_username = username
        self._logged_password_hash = password_hash
        if remember:
            config.save({
                "remember_login": True,
                "remembered_username": username,
                "remembered_password_hash": password_hash,
            })
        else:
            config.save({
                "remember_login": False,
                "remembered_username": "",
                "remembered_password_hash": "",
            })
        self._check_version()

    # ── Cadastro (09/10/2026) ──────────────────────────────────────────
    def _on_register_submit(self) -> None:
        """Mesma validação local do cadastro dentro do jogo
        (ui/login_screen.py::_submit_register) — nunca regra nova."""
        username = self.reg_user_var.get().strip()
        password = self.reg_pass_var.get()
        password2 = self.reg_pass2_var.get()
        if len(username) < 3:
            self.reg_error_label.config(text="Usuário: mínimo 3 caracteres.", fg=_ERROR)
            return
        if not password:
            self.reg_error_label.config(text="Senha não pode ser vazia.", fg=_ERROR)
            return
        if password != password2:
            self.reg_error_label.config(text="As senhas não coincidem.", fg=_ERROR)
            return
        self.register_btn.config(state="disabled")
        self.reg_error_label.config(text="")
        password_hash = hash_password(password)

        def work():
            base_url = _base_url(self._cfg)
            ctx = _ssl_context(self._cfg)
            try:
                data = uc.register_account(base_url, username, password_hash, ctx)
            except Exception as e:
                self.after(0, self._on_register_error, f"Não foi possível contatar o servidor: {e}")
                return
            self.after(0, self._on_register_result, data, username)
        threading.Thread(target=work, daemon=True).start()

    def _on_register_error(self, message: str) -> None:
        self.register_btn.config(state="normal")
        self.reg_error_label.config(text=message, fg=_ERROR)

    def _on_register_result(self, data: dict, username: str) -> None:
        if data.get("ok"):
            # Mesmo comportamento do cadastro dentro do jogo: NUNCA loga
            # automaticamente — volta pro login com usuário preenchido +
            # aviso de sucesso (ui/login_screen.py linha ~235).
            self._prefill_username   = username
            self._pending_status_msg = "Conta criada! Faça login."
            self._pending_status_ok  = True
            self._build_login_form()
            return
        self.register_btn.config(state="normal")
        reason = data.get("reason", "")
        message = {
            "username_taken": "Nome de usuário já existe.",
            "invalid_input":  "Dados inválidos (usuário: 3–20 caracteres).",
        }.get(reason, f"Erro: {reason}")
        self.reg_error_label.config(text=message, fg=_ERROR)

    # ------------------------------------------------------------------
    def _check_version(self) -> None:
        self._build_status_screen("Verificando versão...")

        def work():
            base_url = _base_url(self._cfg)
            ctx = _ssl_context(self._cfg)
            try:
                manifest = uc.fetch_version_manifest(base_url, ctx)
            except Exception as e:
                self.after(0, self._on_version_check_error, str(e))
                return
            local_version = uc.get_local_version(self.install_dir)
            self.after(0, self._on_version_result, manifest, local_version)
        threading.Thread(target=work, daemon=True).start()

    def _on_version_check_error(self, message: str) -> None:
        messagebox.showerror("RPG Online", f"Não foi possível verificar a versão:\n{message}")
        self._build_login_form()

    def _on_version_result(self, manifest: dict, local_version: str) -> None:
        remote_version = manifest.get("version", "")
        if remote_version == local_version:
            self._launch_and_exit()
            return
        answer = messagebox.askyesno(
            "RPG Online",
            f"Nova versão disponível ({remote_version}). Atualizar agora?")
        if not answer:
            # NUNCA abre o jogo desatualizado — decisão do usuário.
            self._build_login_form()
            return
        self._download_and_apply(manifest)

    # ------------------------------------------------------------------
    def _download_and_apply(self, manifest: dict) -> None:
        self._build_status_screen("Baixando atualização...")

        def progress_cb(written: int, total: int) -> None:
            pct = int(written * 100 / total) if total else 0
            self.after(0, lambda: self.progress.config(value=pct))

        def work():
            import tempfile, os as _os
            base_url = _base_url(self._cfg)
            ctx = _ssl_context(self._cfg)
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            _os.close(tmp_fd)
            try:
                uc.download_release(base_url, tmp_path, ctx, progress_cb=progress_cb)
                if not uc.verify_sha256(tmp_path, manifest.get("sha256", "")):
                    self.after(0, self._on_update_error,
                              "Download corrompido (checksum não confere). Tente de novo.")
                    return
                uc.apply_update(tmp_path, self.install_dir)
            except Exception as e:
                self.after(0, self._on_update_error, str(e))
                return
            finally:
                try:
                    _os.remove(tmp_path)
                except OSError:
                    pass
            self.after(0, self._launch_and_exit)
        threading.Thread(target=work, daemon=True).start()

    def _on_update_error(self, message: str) -> None:
        messagebox.showerror("RPG Online", f"Falha ao atualizar:\n{message}")
        self._build_login_form()

    def _launch_and_exit(self) -> None:
        uc.launch_game(self.install_dir, self._logged_username, self._logged_password_hash)
        self.destroy()
