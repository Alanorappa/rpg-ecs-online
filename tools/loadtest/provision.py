"""
provision.py — cria (ou confirma já existentes) N contas+personagens de
bot de carga, e concede as skills treináveis da classe direto no banco
(04/09/2026, ver plano/PROBLEMAS_ARQUITETURA.md). Rodar 1x com o
servidor já de pé, ANTES de `swarm.py`. Idempotente — rodar de novo só
confirma que já está tudo pronto, nunca duplica/recria.

Uso:
    py -3.10 tools/loadtest/provision.py --count 300
    py -3.10 tools/loadtest/provision.py --count 300 --host 127.0.0.1 --port 8765

Fase A (rede, protocolo real — REGISTER/LOGIN/CREATE_CHARACTER): nunca
reimplementar hash de senha/validação de nome na mão, o servidor já faz
isso certo; reimplementar seria duplicar lógica sensível a segurança e
divergir se o servidor mudar.

Fase B (asyncpg direto): só a concessão de skill, que não tem mensagem
de protocolo própria — é um atalho de ferramenta de teste, não uma
feature de jogo (por isso não vira MsgType novo).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.messages import MsgType
from shared.constants import PROTOCOL_VERSION
from tools.loadtest.identity import (
    bot_username, bot_password, bot_class, bot_char_name, starter_skills_for_class,
)
from tools.loadtest.protocol_client import ProtocolClient


def _hash_password(raw: str) -> str:
    """Mesmo hash que o client real manda — SHA-256 do texto puro, nunca
    a senha crua na rede (regra de segurança do projeto)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _provision_one(i: int, host: str, port: int) -> "dict | None":
    """Garante que a conta+personagem do bot `i` existem. Retorna
    {"char_id": int, "class_id": str} pra Fase B, ou None se algo deu
    errado (logado, não interrompe o lote inteiro)."""
    username = bot_username(i)
    password = _hash_password(bot_password(i))
    class_id = bot_class(i)

    client = ProtocolClient(host, port)
    try:
        await client.connect()

        await client.send(MsgType.REGISTER, {"username": username, "password": password})
        reg_reply = await client.recv_one()
        if reg_reply is None:
            print(f"[{username}] sem resposta ao REGISTER (timeout) — pulando")
            return None
        reg_type, reg_payload, _, _ = reg_reply
        if reg_type == MsgType.REGISTER_ERROR and reg_payload.get("reason") != "username_taken":
            print(f"[{username}] REGISTER_ERROR: {reg_payload.get('reason')} — pulando")
            return None

        await client.send(MsgType.LOGIN, {
            "username": username, "password": password, "version": PROTOCOL_VERSION,
        })
        login_reply = await client.recv_one()
        if login_reply is None:
            print(f"[{username}] sem resposta ao LOGIN (timeout) — pulando")
            return None
        login_type, login_payload, _, _ = login_reply
        if login_type != MsgType.AUTH_OK:
            print(f"[{username}] LOGIN falhou: {login_payload.get('reason')} — pulando")
            return None

        characters = login_payload.get("characters") or []
        if not characters:
            await client.send(MsgType.CREATE_CHARACTER, {
                "name": bot_char_name(i), "class_id": class_id,
            })
            create_reply = await client.recv_one()
            if create_reply is None:
                print(f"[{username}] sem resposta ao CREATE_CHARACTER (timeout) — pulando")
                return None
            create_type, create_payload, _, _ = create_reply
            if create_type != MsgType.CHARACTER_CREATED:
                print(f"[{username}] CREATE_CHARACTER falhou: {create_payload.get('reason')} — pulando")
                return None
            char = create_payload["char"]
        else:
            char = characters[0]

        await client.send(MsgType.LOGOUT, {})
        return {"char_id": int(char["id"]), "class_id": char.get("class_id", class_id)}
    finally:
        await client.close()


async def _grant_starter_skills(provisioned: list) -> None:
    """Fase B — escreve `characters.skills_json` direto, sem passar pelo
    protocolo de rede (não existe mensagem pra "aprender skill em lote").
    Idempotente (funde com o que já existir, nunca duplica). Usa o MESMO
    pool asyncpg de `server.auth` (07/09/2026, §123 — migração de
    SQLite) — Postgres aceita escritores concorrentes de verdade, não
    precisa mais do retry-por-banco-travado da era SQLite."""
    if not provisioned:
        return
    from server.auth import _get_pool
    pool = await _get_pool()
    granted = 0
    for entry in provisioned:
        char_id = entry["char_id"]
        class_id = entry["class_id"]
        wanted = starter_skills_for_class(class_id)
        if not wanted:
            continue
        row = await pool.fetchrow("SELECT skills_json FROM characters WHERE id=$1", char_id)
        if row is None:
            continue
        try:
            data = json.loads(row["skills_json"]) if row["skills_json"] else {}
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        learned = list(data.get("learned") or [])
        changed = False
        for sid in wanted:
            if sid not in learned:
                learned.append(sid)
                changed = True
        if changed:
            data["learned"] = learned
            await pool.execute(
                "UPDATE characters SET skills_json=$1 WHERE id=$2",
                json.dumps(data), char_id)
            granted += 1
    print(f"Fase B: skills concedidas/atualizadas em {granted} personagem(ns).")


async def _run(count: int, host: str, port: int, batch_size: int, batch_interval_s: float) -> None:
    print(f"Provisionando {count} bot(s) contra {host}:{port} "
         f"(lotes de {batch_size}, {batch_interval_s}s entre lotes)...")

    async def _staggered(i: int):
        await asyncio.sleep((i // batch_size) * batch_interval_s)
        return await _provision_one(i, host, port)

    results = await asyncio.gather(*(_staggered(i) for i in range(count)))
    provisioned = [r for r in results if r is not None]
    print(f"Fase A: {len(provisioned)}/{count} conta(s)+personagem(ns) confirmados.")

    await _grant_starter_skills(provisioned)
    print("Provisionamento concluído.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--batch-interval-s", type=float, default=1.0)
    args = parser.parse_args()
    asyncio.run(_run(args.count, args.host, args.port, args.batch_size, args.batch_interval_s))


if __name__ == "__main__":
    main()
