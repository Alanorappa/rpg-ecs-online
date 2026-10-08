"""
shared/auth_utils.py
Hash de senha usado no CLIENTE antes de sair pela rede (nunca texto puro —
ver CLAUDE.md, "Segurança"). Fonte única — `client/network.py` e o
Launcher (`launcher/`) usam a MESMA função, nunca uma 2ª implementação.
"""
from __future__ import annotations
import hashlib


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()
