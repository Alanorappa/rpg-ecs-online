"""
launcher/login_ui.py
Janela do Launcher (08/10/2026, ver CLAUDE.md) — tkinter (stdlib, zero
dependência nova; livre pra usar aqui porque é um .spec SEPARADO do
jogo — rpg_online_client.spec continua excluindo tkinter de propósito).

Toda chamada de rede roda em thread separada (nunca trava a janela) —
resultado volta pra thread principal via `Tk.after()`, única forma
segura de mexer em widget tkinter fora da main thread.

Visual (08/10/2026, pedido do usuário): arte de fundo própria
(`assets/hud/Launcher.png`, arte do usuário) — janela do TAMANHO EXATO
da imagem, widgets posicionados sobre a faixa escura de pedra na
metade de baixo da arte (reservada pra isso no próprio desenho).
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
_FIELD_BG   = "#2a2d33"

_ASSET_PATH = "assets/hud/Launcher.png"


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
        # margens usadas por TODAS as telas (login/status), só o
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
        panel_w = self._panel_x1 - self._panel_x0
        e = tk.Entry(self._canvas, textvariable=textvariable, show=show,
                    bg=_FIELD_BG, fg=_TEXT, insertbackground=_TEXT,
                    relief="flat", highlightthickness=1,
                    highlightbackground=_GOLD_DIM, highlightcolor=_GOLD,
                    font=("Georgia", 11), width=1)
        return e

    def _label(self, text: str, fg: str = _TEXT, font=("Georgia", 10)) -> tk.Label:
        return tk.Label(self._canvas, text=text, bg=_BG_PANEL, fg=fg, font=font)

    def _gold_button(self, text: str, command) -> tk.Button:
        return tk.Button(
            self._canvas, text=text, command=command,
            bg=_GOLD, fg="#1b1d21", activebackground="#e0c04a",
            activeforeground="#1b1d21", relief="flat",
            font=("Georgia", 11, "bold"), cursor="hand2",
            padx=16, pady=6, bd=0)

    # ------------------------------------------------------------------
    def _build_login_form(self) -> None:
        self._clear_screen()
        panel_w = self._panel_x1 - self._panel_x0

        self._place(self._label("Usuário"), 0, 0)
        self.user_var = tk.StringVar(value=self._cfg.get("remembered_username", ""))
        user_entry = self._entry(textvariable=self.user_var)
        self._place(user_entry, 0, 22, width=panel_w)

        self._place(self._label("Senha"), 0, 58)
        self.pass_var = tk.StringVar()
        pass_entry = self._entry(textvariable=self.pass_var, show="•")
        self._place(pass_entry, 0, 80, width=panel_w)

        self.remember_var = tk.BooleanVar(value=self._cfg.get("remember_login", False))
        remember_chk = tk.Checkbutton(
            self._canvas, text="Lembrar login", variable=self.remember_var,
            bg=_BG_PANEL, fg=_TEXT_DIM, activebackground=_BG_PANEL,
            activeforeground=_TEXT, selectcolor=_FIELD_BG,
            font=("Georgia", 9), bd=0, highlightthickness=0)
        self._place(remember_chk, 0, 118)

        self.error_label = self._label("", fg=_ERROR, font=("Georgia", 9))
        self._place(self.error_label, 0, 150, width=panel_w)

        self.login_btn = self._gold_button("Entrar", self._on_submit)
        self._place(self.login_btn, panel_w // 2 - 45, 178)

        user_entry.focus_set()

        # Login automático se a senha também foi lembrada (hash salvo) —
        # senão o usuário só vê o campo usuário pré-preenchido e ainda
        # precisa digitar a senha (nunca guardamos a senha de verdade
        # pra reconstituir sozinho sem o hash já pronto).
        remembered_hash = self._cfg.get("remembered_password_hash", "")
        if self._cfg.get("remember_login") and self.user_var.get() and remembered_hash:
            self._do_login(self.user_var.get(), remembered_hash, remember=True)

    def _build_status_screen(self, text: str) -> None:
        self._clear_screen()
        panel_w = self._panel_x1 - self._panel_x0

        self.status_label = self._label(text, font=("Georgia", 11))
        self._place(self.status_label, 0, 60, width=panel_w)

        self.progress = ttk.Progressbar(self._canvas, mode="determinate", length=panel_w,
                                        style="Launcher.Horizontal.TProgressbar")
        self._place(self.progress, 0, 100)

    # ------------------------------------------------------------------
    def _on_submit(self) -> None:
        username = self.user_var.get().strip()
        password = self.pass_var.get()
        if not username or not password:
            self.error_label.config(text="Preencha usuário e senha.")
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
        self.error_label.config(text=message)

    def _on_login_result(self, ok: bool, username: str, password_hash: str, remember: bool) -> None:
        if not ok:
            self.login_btn.config(state="normal")
            self.error_label.config(text="Usuário ou senha inválidos.")
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
