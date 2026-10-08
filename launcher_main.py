# launcher_main.py
"""Entry point do Launcher (08/10/2026, ver CLAUDE.md) — login + checagem
de atualização antes de abrir RPG_Online.exe. Ver launcher/login_ui.py
pra UI, launcher/update_client.py pra lógica de rede/update (pura, sem
tkinter)."""
import os
import sys

if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from launcher.login_ui import LauncherApp


def main() -> None:
    install_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    app = LauncherApp(install_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
