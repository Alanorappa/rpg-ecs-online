"""
tools/migrate_sqlite_to_postgres.py — migração ÚNICA (roda 1x, não faz
parte do runtime do servidor) do banco SQLite antigo (`data/game.db`)
pro PostgreSQL (07/09/2026, ver PROBLEMAS_ARQUITETURA.md §123 — decisão
do usuário de ir pra servidor dedicado em nuvem).

NUNCA destrutivo: lê o SQLite só pra LEITURA (nunca escreve nele),
preserva IDs originais (accounts/characters — `characters.account_id`
depende de bater com o id migrado da conta), e nunca decide sozinho
apagar/pular um personagem em conflito — nome de personagem duplicado
(achado real no banco dev: várias contas de teste antigas, de antes da
regra de unicidade existir, ~45 grupos de nomes duplicados) recebe um
sufixo `__dupN` visível e é logado, nunca descartado silenciosamente.

Uso (com o Postgres alvo já rodando e `RPG_DATABASE_URL` apontando pra
ele, se não for o default de dev):
    py -3.10 -m tools.migrate_sqlite_to_postgres
    py -3.10 -m tools.migrate_sqlite_to_postgres --sqlite-path caminho/pro/game.db
    py -3.10 -m tools.migrate_sqlite_to_postgres --dry-run   # só relatório, não escreve nada
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg

import server.auth as auth

_DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "game.db")


def _read_sqlite(sqlite_path: str) -> tuple[list[dict], list[dict]]:
    """Só LEITURA — nunca escreve no SQLite. Retorna (accounts, characters)
    como listas de dict, na ordem original (por id)."""
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        accounts = [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id")]
        characters = [dict(r) for r in conn.execute("SELECT * FROM characters ORDER BY id")]
        return accounts, characters
    finally:
        conn.close()


def _dedup_character_names(characters: list[dict]) -> list[dict]:
    """Nome de personagem tem índice ÚNICO case-insensitive no Postgres
    (ver server/auth.py::init_db) — o SQLite antigo NUNCA teve essa
    restrição no banco (só checagem de aplicação, adicionada depois que
    já existiam dados). Personagem cujo nome (case-insensitive) já foi
    visto antes nesta mesma migração ganha sufixo `__dup2`/`__dup3`/...
    — preserva TODOS os dados (nunca descarta), só evita colidir com o
    índice único. Primeiro a aparecer (menor id) NUNCA é renomeado."""
    seen: dict[str, int] = {}
    renamed = []
    for char in characters:
        key = char["name"].lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            new_name = f'{char["name"]}__dup{seen[key]}'
            renamed.append((char["id"], char["name"], new_name))
            char["name"] = new_name
    return renamed


async def migrate(sqlite_path: str, dry_run: bool = False) -> None:
    accounts, characters = _read_sqlite(sqlite_path)
    print(f"Lido do SQLite: {len(accounts)} conta(s), {len(characters)} personagem(ns).")

    renamed = _dedup_character_names(characters)
    if renamed:
        print(f"\n{len(renamed)} personagem(ns) com nome duplicado (case-insensitive) "
             "no SQLite antigo — renomeados com sufixo pra caber no índice único novo:")
        for char_id, old_name, new_name in renamed:
            print(f"  id={char_id}: '{old_name}' -> '{new_name}'")
        print("(nada foi apagado — o dono original de cada nome continua com o nome sem sufixo.)\n")

    if dry_run:
        print("--dry-run: nada foi escrito no Postgres. Rode sem --dry-run pra migrar de verdade.")
        return

    await auth.init_db()  # garante schema/tabelas/índice já existirem no destino
    pool = await auth._get_pool()

    # init_db() acima já criou as 3 contas de teste padrão (_seed_test_accounts,
    # sempre roda — mesmo comportamento de sempre, servidor real também
    # semeia isso no boot). Removidas ANTES da checagem de "banco vazio"
    # abaixo — são só placeholders com usuário/senha fixos e conhecidos,
    # nunca dado real; qualquer OUTRA conta/personagem já presente aborta
    # a migração normalmente.
    await pool.execute(
        "DELETE FROM characters WHERE account_id IN "
        "(SELECT id FROM accounts WHERE username IN ('teste','teste2','teste3'))")
    await pool.execute(
        "DELETE FROM accounts WHERE username IN ('teste','teste2','teste3')")

    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval("SELECT COUNT(*) FROM accounts")
            if existing:
                raise RuntimeError(
                    f"Postgres (schema={auth._SCHEMA}) já tem {existing} conta(s) — "
                    "migração só roda contra um banco vazio, pra nunca sobrescrever "
                    "dado que já esteja lá. Aponte RPG_DATABASE_URL/schema pra um "
                    "banco novo, ou limpe manualmente antes de rodar de novo.")

            for acc in accounts:
                await conn.execute(
                    "INSERT INTO accounts (id, username, password_hash, salt, is_gm, created_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6)",
                    acc["id"], acc["username"], acc["password_hash"], acc["salt"],
                    bool(acc["is_gm"]), acc["created_at"])
            await conn.execute(
                "SELECT setval(pg_get_serial_sequence('accounts','id'), "
                "(SELECT COALESCE(MAX(id),1) FROM accounts))")

            for char in characters:
                await conn.execute(
                    "INSERT INTO characters (id, account_id, name, class_id, level, xp, "
                    "tile_x, tile_y, map_id, hp, mp, stats_json, inventory_json, "
                    "equipment_json, skills_json, talents_json, fog_json, "
                    "skill_levels_json, quests_json, char_stats_json, "
                    "learned_recipes_json, last_save) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,"
                    "$17,$18,$19,$20,$21,$22)",
                    char["id"], char["account_id"], char["name"], char["class_id"],
                    char["level"], char["xp"], char["tile_x"], char["tile_y"],
                    char["map_id"], char["hp"], char["mp"], char["stats_json"],
                    char["inventory_json"], char["equipment_json"], char["skills_json"],
                    char["talents_json"], char["fog_json"], char["skill_levels_json"],
                    char["quests_json"], char["char_stats_json"],
                    char["learned_recipes_json"], char["last_save"])
            await conn.execute(
                "SELECT setval(pg_get_serial_sequence('characters','id'), "
                "(SELECT COALESCE(MAX(id),1) FROM characters))")

    print(f"Migração concluída: {len(accounts)} conta(s) + {len(characters)} "
         f"personagem(ns) inseridos no Postgres (schema={auth._SCHEMA}).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-path", default=_DEFAULT_SQLITE_PATH)
    parser.add_argument("--dry-run", action="store_true",
                       help="só mostra o que faria (duplicatas, contagens) — não escreve nada")
    args = parser.parse_args()
    asyncio.run(migrate(args.sqlite_path, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
