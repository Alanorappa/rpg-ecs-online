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
import asyncio
import sys

from server.auth import _get_pool


async def set_gm(username: str, is_gm: bool) -> bool:
    """Retorna True se a conta existia e foi atualizada."""
    pool = await _get_pool()
    result = await pool.execute(
        "UPDATE accounts SET is_gm=$1 WHERE username=$2", is_gm, username)
    # asyncpg devolve "UPDATE <n>" — n=0 quando nenhuma linha bateu.
    return not result.endswith(" 0")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m server.grant_gm <username> [--revoke]")
        sys.exit(1)
    _username = sys.argv[1]
    _revoke   = "--revoke" in sys.argv[2:]
    if asyncio.run(set_gm(_username, not _revoke)):
        print(f"[grant_gm] '{_username}' agora {'NÃO é' if _revoke else 'é'} GM.")
    else:
        print(f"[grant_gm] conta '{_username}' não encontrada.")
        sys.exit(1)
