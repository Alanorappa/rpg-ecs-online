"""
server/grant_gm.py — concede/revoga a flag de GM de uma conta (CLI direto
no banco, mesmo padrão do "account set gmlevel" do AzerothCore — de
propósito SEM UI e SEM endpoint de rede pra isso, só quem tem acesso ao
servidor consegue conceder).

Uso:
    python -m server.grant_gm <username>          # concede
    python -m server.grant_gm <username> --revoke  # revoga
"""
from __future__ import annotations
import sys

from server.auth import _get_conn


def set_gm(username: str, is_gm: bool) -> bool:
    """Retorna True se a conta existia e foi atualizada."""
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE accounts SET is_gm=? WHERE username=?",
            (1 if is_gm else 0, username),
        )
        return cur.rowcount > 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m server.grant_gm <username> [--revoke]")
        sys.exit(1)
    _username = sys.argv[1]
    _revoke   = "--revoke" in sys.argv[2:]
    if set_gm(_username, not _revoke):
        print(f"[grant_gm] '{_username}' agora {'NÃO é' if _revoke else 'é'} GM.")
    else:
        print(f"[grant_gm] conta '{_username}' não encontrada.")
        sys.exit(1)
