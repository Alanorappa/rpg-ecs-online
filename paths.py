# paths.py
"""Resolve caminhos de recursos tanto em execução normal quanto em executável PyInstaller."""
import os
import sys


def resource_path(relative_path: str) -> str:
    """Retorna o caminho absoluto para um recurso.

    Quando empacotado pelo PyInstaller, os arquivos de dados ficam em
    sys._MEIPASS. Em execução normal, usa o diretório do projeto.
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)
