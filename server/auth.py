"""
server/auth.py
Autenticação simples para fase de desenvolvimento.
SQLite com hashes SHA-256. Fácil migrar para PostgreSQL depois.
"""
from __future__ import annotations
import asyncio
import hashlib
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "game.db")


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# (username, password, class_id, tile_x, tile_y)
_TEST_ACCOUNTS = [
    ("teste",  "123456", "guerreiro", 10, 10),
    ("teste2", "123456", "mago",      15, 10),
    ("teste3", "123456", "arqueiro",  20, 10),
]


def init_db() -> None:
    """Cria tabelas se não existirem. Chamado na inicialização do servidor."""
    with _get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at  INTEGER DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS characters (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id  INTEGER NOT NULL REFERENCES accounts(id),
            name        TEXT NOT NULL,
            class_id    TEXT NOT NULL DEFAULT 'guerreiro',
            level       INTEGER DEFAULT 1,
            xp          INTEGER DEFAULT 0,
            tile_x      INTEGER DEFAULT 10,
            tile_y      INTEGER DEFAULT 10,
            map_id      TEXT DEFAULT 'map_main',
            hp          INTEGER DEFAULT 100,
            mp          INTEGER DEFAULT 100,
            stats_json  TEXT DEFAULT '{}',
            inventory_json TEXT DEFAULT '[]',
            skills_json TEXT DEFAULT '{}',
            talents_json   TEXT DEFAULT '{}',
            last_save   INTEGER DEFAULT (strftime('%s','now'))
        );
        """)
    print(f"[Auth] banco inicializado: {DB_PATH}")
    _seed_test_accounts()


def _seed_test_accounts() -> None:
    """Garante que as contas de teste existam. Idempotente — não recria se já existirem."""
    for username, password, class_id, tx, ty in _TEST_ACCOUNTS:
        created = _register_sync(username, password, class_id, tx, ty)
        if created:
            print(f"[Auth] conta de teste criada: usuario='{username}'  "
                  f"classe={class_id}  tile=({tx},{ty})")


async def authenticate(username: str, password: str) -> dict | None:
    """
    Valida credenciais e retorna dados do personagem, ou None se inválido.
    Executado em thread separada para não bloquear o event loop.
    """
    return await asyncio.get_event_loop().run_in_executor(
        None, _authenticate_sync, username, password)


def _authenticate_sync(username: str, password: str) -> dict | None:
    # O cliente já envia SHA-256(password) — comparar direto, sem rehashear
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE username=? AND password_hash=?",
            (username, password)
        ).fetchone()
        if not row:
            return None

        char = conn.execute(
            "SELECT * FROM characters WHERE account_id=? LIMIT 1",
            (row["id"],)
        ).fetchone()
        if not char:
            return None

        return dict(char)


async def register(username: str, password: str,
                   class_id: str = "guerreiro") -> bool:
    """Cria conta + personagem. Retorna True se sucesso, False se username já existe."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _register_sync, username, password, class_id)


def _register_sync(username: str, password: str,
                   class_id: str = "guerreiro",
                   tile_x: int = 10, tile_y: int = 10) -> bool:
    ph = _hash(password)
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO accounts (username, password_hash) VALUES (?,?)",
                (username, ph)
            )
            account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO characters (account_id, name, class_id, tile_x, tile_y)"
                " VALUES (?,?,?,?,?)",
                (account_id, username, class_id, tile_x, tile_y)
            )
        return True
    except sqlite3.IntegrityError:
        return False   # username já existe


async def save_character(char_id: int, data: dict) -> None:
    """Persiste estado do personagem. Chamado ao desconectar e periodicamente."""
    await asyncio.get_event_loop().run_in_executor(
        None, _save_character_sync, char_id, data)


def _save_character_sync(char_id: int, data: dict) -> None:
    import json
    with _get_conn() as conn:
        conn.execute("""
            UPDATE characters SET
                tile_x=?, tile_y=?, hp=?, mp=?,
                stats_json=?, inventory_json=?, skills_json=?, talents_json=?,
                last_save=strftime('%s','now')
            WHERE id=?
        """, (
            data.get("tile_x", 10), data.get("tile_y", 10),
            data.get("hp", 100),    data.get("mp", 100),
            json.dumps(data.get("stats",     {})),
            json.dumps(data.get("inventory", [])),
            json.dumps(data.get("skills",    {})),
            json.dumps(data.get("talents",   {})),
            char_id,
        ))
