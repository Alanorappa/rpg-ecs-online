# paths.py
"""Resolve caminhos de recursos tanto em execução normal quanto em executável PyInstaller."""
import os
import sys


def resource_path(relative_path: str) -> str:
    """Retorna o caminho absoluto para um recurso.

    Build (PyInstaller onedir): assets/ e maps/ são distribuídos como pastas
    AO LADO do executável (não embutidos) — fáceis de inspecionar/moddar e o
    config.json fica visível. Em execução normal, usa o diretório do projeto.
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)
