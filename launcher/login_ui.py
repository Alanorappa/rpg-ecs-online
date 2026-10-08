"""
launcher/login_ui.py
Janela do Launcher (08/10/2026, ver CLAUDE.md) — tkinter (stdlib, zero
dependência nova; livre pra usar aqui porque é um .spec SEPARADO do
jogo — rpg_online_client.spec continua excluindo tkinter de propósito).

Toda chamada de rede roda em thread separada (nunca trava a janela) —
resultado volta pra thread principal via `Tk.after()`, única forma
segura de mexer em widget tkinter fora da main thread.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

import config
from paths import resource_path
from shared.auth_utils import hash_password
from launcher import update_client as uc


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
        self.title("RPG Online — Launcher")
        self.resizable(False, False)
        self.geometry("360x260")

        self._cfg = config.load()

        self._build_login_form()

    # ------------------------------------------------------------------
    def _build_login_form(self) -> None:
        for w in self.winfo_children():
            w.destroy()

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Usuário:").pack(anchor="w")
        self.user_var = tk.StringVar(value=self._cfg.get("remembered_username", ""))
        ttk.Entry(frame, textvariable=self.user_var).pack(fill="x")

        ttk.Label(frame, text="Senha:").pack(anchor="w", pady=(10, 0))
        self.pass_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pass_var, show="*").pack(fill="x")

        self.remember_var = tk.BooleanVar(value=self._cfg.get("remember_login", False))
        ttk.Checkbutton(frame, text="Lembrar login",
                        variable=self.remember_var).pack(anchor="w", pady=(10, 0))

        self.error_label = ttk.Label(frame, text="", foreground="red")
        self.error_label.pack(fill="x", pady=(10, 0))

        self.login_btn = ttk.Button(frame, text="Entrar", command=self._on_submit)
        self.login_btn.pack(pady=(10, 0))

        # Login automático se a senha também foi lembrada (hash salvo) —
        # senão o usuário só vê o campo usuário pré-preenchido e ainda
        # precisa digitar a senha (nunca guardamos a senha de verdade
        # pra reconstituir sozinho sem o hash já pronto).
        remembered_hash = self._cfg.get("remembered_password_hash", "")
        if self._cfg.get("remember_login") and self.user_var.get() and remembered_hash:
            self._do_login(self.user_var.get(), remembered_hash, remember=True)

    def _build_status_screen(self, text: str) -> None:
        for w in self.winfo_children():
            w.destroy()
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        self.status_label = ttk.Label(frame, text=text, wraplength=300)
        self.status_label.pack(pady=20)
        self.progress = ttk.Progressbar(frame, mode="determinate", length=280)
        self.progress.pack()

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
        uc.launch_game(self.install_dir)
        self.destroy()
