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

from shared.constants import RESPAWN_TILE

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "game.db")


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# (username, password, class_id, tile_x, tile_y)
# Tiles próximos ao spawn padrão do mapa — área sabidamente walkable.
# "teste" spawna exatamente em RESPAWN_TILE (centro do cemitério); teste2/3
# usam offsets fixos pra não sobrepor quando os 3 conectam ao mesmo tempo.
_TEST_ACCOUNTS = [
    ("teste",  "123456", "guerreiro", RESPAWN_TILE[0],     RESPAWN_TILE[1]),
    ("teste2", "123456", "mago",      RESPAWN_TILE[0] + 2, RESPAWN_TILE[1]),
    ("teste3", "123456", "arqueiro",  RESPAWN_TILE[0] + 4, RESPAWN_TILE[1]),
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
            hp          INTEGER DEFAULT 0,
            mp          INTEGER DEFAULT 100,
            stats_json     TEXT DEFAULT '{}',
            inventory_json TEXT DEFAULT '[]',
            equipment_json TEXT DEFAULT '{}',
            skills_json    TEXT DEFAULT '{}',
            talents_json   TEXT DEFAULT '{}',
            fog_json       TEXT DEFAULT '{}',
            last_save   INTEGER DEFAULT (strftime('%s','now'))
        );
        """)
    # Migração: adiciona fog_json em bancos existentes (coluna não existia antes)
    with _get_conn() as conn:
        try:
            conn.execute("ALTER TABLE characters ADD COLUMN fog_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # coluna já existe
    print(f"[Auth] banco inicializado: {DB_PATH}")
    _seed_test_accounts()


def _seed_test_accounts() -> None:
    """Garante que as contas de teste existam com personagens. Idempotente."""
    for username, password, class_id, tx, ty in _TEST_ACCOUNTS:
        # Seed accounts: hasha a senha em texto-plano antes de registrar
        ph      = _hash(password)
        created = _register_account_sync(username, ph)
        if created:
            acc_id = _get_account_id_sync(username)
            if acc_id:
                _create_character_sync(acc_id, username, class_id, tx, ty)
            print(f"[Auth] conta de teste criada: usuario='{username}'  "
                  f"classe={class_id}  tile=({tx},{ty})")


def _get_account_id_sync(username: str) -> "int | None":
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE username=?", (username,)
        ).fetchone()
        return row["id"] if row else None


async def authenticate(username: str, password: str) -> "dict | None":
    """Valida credenciais. Retorna dict com dados da conta/personagem ou None."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _authenticate_sync, username, password)


def _authenticate_sync(username: str, password: str) -> "dict | None":
    """
    Compara credenciais. password já vem como SHA-256 do cliente — sem rehashear.
    Retorna {account_id, characters:[...]}. None = credenciais inválidas.
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE username=? AND password_hash=?",
            (username, password)
        ).fetchone()
        if not row:
            return None
        chars = conn.execute(
            "SELECT * FROM characters WHERE account_id=? ORDER BY id LIMIT 3",
            (row["id"],)
        ).fetchall()
        return {
            "account_id":  row["id"],
            "characters":  [dict(c) for c in chars],
        }


async def register(username: str, password: str) -> bool:
    """Cria conta (sem personagem). password já vem hasheado pelo cliente."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _register_account_sync, username, password)


def _register_account_sync(username: str, password: str) -> bool:
    """Insere conta. password deve ser SHA-256 do texto-plano."""
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO accounts (username, password_hash) VALUES (?,?)",
                (username, password)
            )
        return True
    except sqlite3.IntegrityError:
        return False   # username já existe


async def create_character(account_id: int, name: str,
                           class_id: str = "guerreiro") -> bool:
    """Cria personagem para a conta. Retorna True se sucesso."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _create_character_sync, account_id, name, class_id)


def _create_character_sync(account_id: int, name: str,
                           class_id: str = "guerreiro",
                           tile_x: int = RESPAWN_TILE[0],
                           tile_y: int = RESPAWN_TILE[1]) -> bool:
    try:
        with _get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM characters WHERE account_id=?", (account_id,)
            ).fetchone()[0]
            if count >= 3:
                return False  # limite de 3 personagens por conta
            conn.execute(
                "INSERT INTO characters (account_id, name, class_id, tile_x, tile_y)"
                " VALUES (?,?,?,?,?)",
                (account_id, name, class_id, tile_x, tile_y)
            )
        return True
    except Exception:
        return False


async def delete_character(account_id: int, char_id: int) -> bool:
    """Remove personagem verificando que pertence à conta."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _delete_character_sync, account_id, char_id)


def _delete_character_sync(account_id: int, char_id: int) -> bool:
    try:
        with _get_conn() as conn:
            conn.execute(
                "DELETE FROM characters WHERE id=? AND account_id=?",
                (char_id, account_id)
            )
        return True
    except Exception:
        return False


async def get_character(account_id: int, char_id: int) -> "dict | None":
    """Retorna um personagem verificando que pertence à conta."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _get_character_sync, account_id, char_id)


def _get_character_sync(account_id: int, char_id: int) -> "dict | None":
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM characters WHERE id=? AND account_id=?",
            (char_id, account_id)
        ).fetchone()
        return dict(row) if row else None


async def save_character(char_id: int, data: dict) -> None:
    """Persiste estado do personagem. Chamado ao desconectar e periodicamente."""
    await asyncio.get_running_loop().run_in_executor(
        None, _save_character_sync, char_id, data)


def _save_character_sync(char_id: int, data: dict) -> None:
    """Persiste estado do personagem. Campos com valor None são ignorados (coluna não é atualizada)."""
    import json
    _stats = data.get("stats", {})
    # Campos server-autoritativos — sempre atualizados
    cols = ["tile_x", "tile_y", "hp", "mp", "level", "stats_json"]
    vals = [
        data.get("tile_x", 10), data.get("tile_y", 10),
        data.get("hp",     100), data.get("mp", 100),
        _stats.get("level", 1),
        json.dumps(_stats),
    ]
    # Campos client-autoritativos — só atualiza se não for None
    for key, col in (("inventory", "inventory_json"),
                     ("equipment", "equipment_json"),
                     ("skills",    "skills_json"),
                     ("talents",   "talents_json"),
                     ("fog",       "fog_json")):
        v = data.get(key)
        if v is not None:
            cols.append(col)
            vals.append(json.dumps(v))
    set_sql = ", ".join(f"{c}=?" for c in cols) + ", last_save=strftime('%s','now')"
    vals.append(char_id)
    with _get_conn() as conn:
        conn.execute(f"UPDATE characters SET {set_sql} WHERE id=?", vals)
