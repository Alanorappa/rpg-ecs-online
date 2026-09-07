"""
server/auth.py
Autenticação simples para fase de desenvolvimento.
SQLite com hashes SHA-256. Fácil migrar para PostgreSQL depois.
"""
from __future__ import annotations
from server.log import log
import asyncio
import concurrent.futures
import hashlib
import sqlite3
import threading
import os

from shared.constants import RESPAWN_TILE

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "game.db")

# Executores dedicados de 1 worker cada (nunca o executor padrão do
# asyncio, que despacharia cada chamada pra uma thread DIFERENTE do pool —
# até min(32, cpu+4) threads — impedindo `_get_conn()` de convergir numa
# única conexão persistente reaproveitada por fila, ver `_get_conn`).
# 2 FILAS separadas por PESO da operação, não 1 fila única (06/09/2026 (2),
# §119→§120, PROBLEMAS_ARQUITETURA.md — achado ao investigar por que o
# teste de carga SUSTENTADA, chegada contínua ~2 bots/s por 500s, mediu o
# `_write_lock` único do §119 piorando o sintoma que devia resolver:
# reconexões 1181→2768, bots conectados no fim ~225/250→109/250).
# `save_character` roda em TODO disconnect (`SessionManager.
# _persist_character`, contexto "disconnect") além de autosave/SAVE_STATE/
# talento/quest, com payload JSON pesado (mediana ~1KB, cauda real até
# ~600KB de fog-of-war). Sob rajada de RECONEXÃO — não só conexão nova —
# cada disconnect gera 1 save pesado que, numa fila FIFO única, entra na
# MESMA fila que o `register` do bot tentando voltar: um register atrás de
# N saves pesados já enfileirados espera todos os N antes de rodar, mesmo
# sendo quase instantâneo — o disconnect que causou a reconexão é, ele
# mesmo, quem atrasa a reconexão seguinte. É a classe clássica de fila
# heterogênea sem prioridade; mercado nunca mistura requisição rápida e job
# pesado numa fila FIFO só, separa por fila/prioridade (fila rápida pra
# metadata/identidade, fila lenta pra payload grande) — exatamente o
# desenho abaixo: `_identity_executor`+`_identity_lock` (register/
# create_character/delete_character, leves, sensíveis a latência de
# CONEXÃO) nunca esperam atrás de `_write_executor`+`_write_lock`
# (save_character, pesado, tolera fila).
_write_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="sqlite-writer")
_identity_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="sqlite-identity")

# Ponto único de serialização de ESCRITA real no SQLite do processo inteiro
# (06/09/2026, §113/§118, PROBLEMAS_ARQUITETURA.md) — cobre TODO caminho que
# faz INSERT/UPDATE/DELETE de verdade: save_character (disconnect/autosave/
# save periódico), register (INSERT conta), create_character (INSERT
# personagem) e delete_character (DELETE personagem). SQLite só aceita 1
# escritor por vez mesmo com WAL (`_get_conn` liga WAL só pra permitir
# LEITOR concorrente durante um escritor — não elimina "1 escritor por
# vez"); sem serializar, uma rajada de conexão (REGISTER+LOGIN+
# CREATE_CHARACTER de várias contas quase juntas — teste de carga real, ou
# pico de patch day/reinício de servidor em produção) faz cada
# `run_in_executor` competir pelo lock de escrita do arquivo, com
# `sqlite3.connect(...)`'s timeout default de 5s por tentativa — medido em
# §113: rampa agressiva do teste de carga gerava até 1181 reconexões em
# 500s, bot entrando em loop de backoff exponencial re-tentando
# REGISTER/LOGIN por minutos.
#
# ATUALIZADO NO §120: este lock (`_write_lock`) agora cobre SÓ
# `save_character` (fila LENTA) — register/create_character/
# delete_character passaram pra `_identity_lock` (fila RÁPIDA, ver
# `_get_identity_lock()` logo abaixo e comentário completo acima dos 2
# executores). Medido nesta máquina (script de benchmark ad-hoc, ver
# PROBLEMAS_ARQUITETURA.md §120): INSERT leve sequencial ≈1000-1700
# escritas/s (fsync-bound, teto real); UPDATE pesado (~1KB-50KB JSON) por
# rowid ≈30000-80000 escritas/s — ambos MUITO acima da demanda real (~2
# conexões novas/s sustentado); o SQLite em si NÃO é o gargalo nesta
# escala, era o acoplamento leve+pesado numa fila só.
#
# Vive AQUI (módulo que de fato abre a conexão) e é acionado DE DENTRO de
# cada wrapper async (register/create_character/delete_character/
# save_character), não pelo chamador — assim qualquer call site futuro
# (rede, ferramenta, script) ganha a serialização de graça, sem precisar
# lembrar de envolver a chamada manualmente. Isso importa de verdade: além
# de `session.py`, `tools/loadtest/provision.py` Fase A chama
# register/create_character via protocolo de rede real (passa por aqui);
# um lock preso a `SessionManager` (como `_save_lock`, que corrigiu o
# problema IRMÃO de desconexão em massa, §118) cobriria só os handlers que
# lembrassem de usá-lo — replicar lock por call site é exatamente a
# proliferação que este projeto evita pra outros subsistemas (dano,
# teleporte, etc.) via "ponto único de verdade"; persistência merece a
# mesma disciplina. (`_register_account_sync`/`_create_character_sync`
# chamados direto e SÍNCRONOS por testes/seed de startup ficam fora de
# propósito — não são concorrentes entre si.)
#
# NUNCA usado por `authenticate`/`get_character` (LEITURA pura no caso
# comum) — LOGIN é o caminho mais sensível a latência da rajada de conexão
# (jogador esperando entrar no jogo percebe atraso como "travou", diferente
# de alguém que já desconectou) e é predominantemente SELECT; serializar
# leitura contra escrita jogaria fora o ganho do WAL (leitor concorrente
# durante escritor) bem na rajada que mais dói. Padrão de mercado (MMOs
# sob rajada de patch day/restart): autenticação fica no caminho rápido de
# leitura, só a escrita de fato (criar conta/personagem) é serializada.
# Exceção conhecida e aceita: o upgrade transparente de salt legado dentro
# de `_authenticate_sync` faz 1 UPDATE — não passa por este lock de
# propósito (só afeta contas pré-salt, nenhuma delas existe nas contas de
# bot/teste, que já nascem com salt via `_register_account_sync`); pior
# caso de colisão é raríssimo e não-destrutivo (login não migra desta vez,
# migra no próximo).
#
# `_get_write_lock()` (função, não um `asyncio.Lock()` de módulo usado
# direto) por um motivo concreto, não estético: um `asyncio.Lock` só é
# "amarrado" ao event loop em que roda na PRIMEIRA vez que alguém DISPUTA
# ele de verdade — `Lock.acquire()` só chama `_get_loop()` no caminho onde
# já está locked/tem esperando (`asyncio/locks.py`); se ninguém nunca
# disputou, nunca amarra. Um lock de módulo criado 1x e reusado por vários
# testes (`unittest.IsolatedAsyncioTestCase` cria um event loop NOVO por
# método de teste) funciona sem erro enquanto nenhum teste gerar disputa
# real nele — mas o PRIMEIRO teste que gerar disputa de verdade amarra o
# lock ao loop DAQUELE teste, e qualquer teste POSTERIOR que também
# dispute o MESMO lock (loop diferente, já fechado) quebra com
# "RuntimeError: ... is bound to a different event loop" — reproduzido
# isoladamente ao escrever esta correção (2 `IsolatedAsyncioTestCase`
# triviais gerando disputa real no mesmo `asyncio.Lock()` de módulo; o 2º
# sempre quebra, o 1º sempre passa). Em produção nunca importa (1 processo
# = 1 loop pra sempre, criado 1x no boot), mas quebraria a suíte de testes
# de forma imprevisível — só no dia em que 2+ testes futuros chegarem a
# disputar o lock de verdade ao mesmo tempo (ex.: um teste de concorrência
# pra validar esta própria correção). `_get_write_lock()` recria o Lock
# sempre que o loop em execução mudou desde a última chamada — seguro nos
# dois casos, sem custo extra no caso comum (1 comparação de objeto).
_write_lock: "asyncio.Lock | None" = None
_write_lock_loop = None
_identity_lock: "asyncio.Lock | None" = None
_identity_lock_loop = None


def _get_write_lock() -> asyncio.Lock:
    """Fila LENTA — só `save_character` (payload JSON pesado). Ver §120."""
    global _write_lock, _write_lock_loop
    loop = asyncio.get_running_loop()
    if _write_lock is None or _write_lock_loop is not loop:
        _write_lock = asyncio.Lock()
        _write_lock_loop = loop
    return _write_lock


def _get_identity_lock() -> asyncio.Lock:
    """Fila RÁPIDA — register/create_character/delete_character (payload
    leve, sensível a latência de conexão). Separada de `_get_write_lock()`
    de propósito — ver comentário acima dos executores (§120)."""
    global _identity_lock, _identity_lock_loop
    loop = asyncio.get_running_loop()
    if _identity_lock is None or _identity_lock_loop is not loop:
        _identity_lock = asyncio.Lock()
        _identity_lock_loop = loop
    return _identity_lock


def _hash(password: str) -> str:
    """SHA-256 simples — é o que o CLIENTE calcula e manda na rede
    (client_hash). Usado por seeds/testes pra derivar o client_hash de uma
    senha em texto. NÃO é mais o que fica no banco — ver _server_hash."""
    return hashlib.sha256(password.encode()).hexdigest()


def _server_hash(salt: str, client_hash: str) -> str:
    """Hash de ARMAZENAMENTO: sha256(salt + client_hash), salt aleatório por
    conta. Antes o banco guardava o client_hash direto — vazou o banco,
    logava-se com o próprio hash (pass-the-hash) e sha256 sem salt cai em
    rainbow table. Com salt por conta, o dump do banco não autentica ninguém
    (login exige o client_hash, pré-imagem) nem quebra em tabela pronta.
    Limite conhecido e aceito em dev: o client_hash na REDE continua sendo
    'a senha' (falta TLS/wss — ver PROBLEMAS_ARQUITETURA.md §11 item C1);
    argon2/bcrypt ficam pra migração de produção junto do TLS."""
    return hashlib.sha256((salt + client_hash).encode()).hexdigest()


def _new_salt() -> str:
    import secrets
    return secrets.token_hex(16)


# Migração lazy (1x por processo): a coluna `salt` precisa existir em QUALQUER
# caminho que abra o banco — testes e utilitários usam _get_conn() direto sem
# passar por init_db() (que também migra, mas só roda no startup do servidor).
_schema_ensured = False

# Conexão PERSISTENTE por thread do SO, reaproveitada entre chamadas — nunca
# um `sqlite3.connect()` novo por operação (06/09/2026, §120,
# PROBLEMAS_ARQUITETURA.md). Achado por medição, não suposição: um teste de
# carga sustentada (chegada contínua de conexão, não um burst único) contra
# `server.auth` de verdade mostrou o arquivo `-wal` CRESCENDO SEM PARAR
# (nunca fazia checkpoint de volta pro `.db` principal) enquanto havia
# conexões novas sendo abertas/fechadas em volume alto e concorrente —
# SQLite só consegue fazer checkpoint de verdade (WAL "auto_checkpoint",
# >1000 páginas por padrão) quando NENHUMA conexão está segurando uma
# leitura em andamento; um fluxo de "abre conexão nova, lê/escreve
# 1 linha, fecha" o tempo todo em paralelo (register+authenticate+
# save_character disputando a mesma rajada de reconexão) nunca dá essa
# janela — o WAL cresce sem limite e CADA operação (leitura E escrita) fica
# mais lenta à medida que o arquivo cresce (precisa escanear mais páginas de
# WAL), até o checkpoint forçado (limite físico) travar tudo por vários
# segundos com 100% de CPU. Reduzir pra 1 conexão por thread, reaberta só
# quando `DB_PATH` muda (testes trocam por um banco temporário a cada
# `setUp`) elimina esse padrão de abre/fecha em massa. Cache é
# thread-local de propósito: sqlite3.Connection não é thread-safe pra
# compartilhar entre threads (default `check_same_thread=True`) — cada
# thread dos 2 executores dedicados (`_write_executor`/`_identity_executor`,
# 1 worker cada) e cada thread do pool padrão (`authenticate`/
# `get_character`, leitura) precisa da sua própria conexão cacheada.
_conn_cache = threading.local()


def _get_conn() -> sqlite3.Connection:
    global _schema_ensured
    cached = getattr(_conn_cache, "conn", None)
    cached_path = getattr(_conn_cache, "path", None)
    if cached is not None and cached_path == DB_PATH:
        return cached
    if cached is not None:
        try:
            cached.close()
        except Exception:
            pass  # best-effort — troca de DB_PATH em teste, conexão velha pode já estar solta
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if not _schema_ensured:
        _schema_ensured = True
        try:
            conn.execute("ALTER TABLE accounts ADD COLUMN salt TEXT DEFAULT NULL")
        except Exception:
            pass  # coluna já existe (ou tabela ainda não existe — init_db cria com ela)
        try:
            conn.execute("ALTER TABLE accounts ADD COLUMN is_gm INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # coluna já existe (ou tabela ainda não existe — init_db cria com ela)
        # WAL: persiste no header do arquivo .db (1x por arquivo, sobrevive
        # a restart do processo) — permite leitor concorrente durante
        # escrita, complementar ao `_write_lock` deste módulo (que serializa
        # ESCRITOR contra escritor: register/create_character/
        # delete_character/save_character — SQLite só aceita 1 escritor por
        # vez mesmo em WAL). Sem WAL o modo default (DELETE) exige lock
        # exclusivo do arquivo até pra leitura durante um write em voo.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass  # :memory: (usado em teste) não suporta WAL — no-op inofensivo
    # synchronous=NORMAL (não persiste no arquivo — precisa ser setado por
    # CONEXÃO nova, ao contrário de journal_mode) — seguro com WAL (doc
    # oficial SQLite: WAL+NORMAL nunca corrompe o banco, só pode perder as
    # últimas transações num crash de SO/queda de energia, não num crash da
    # aplicação). FULL (default) força fsync em todo commit; medido neste
    # projeto: com conexão persistente, FULL≈0.8ms/commit vs NORMAL≈0.04ms/
    # commit no disco de dev — ganho real sob a MESMA garantia de
    # durabilidade que já era aceita (dev/SQLite, sem replicação).
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    _conn_cache.conn = conn
    _conn_cache.path = DB_PATH
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
            salt        TEXT DEFAULT NULL,
            is_gm       INTEGER NOT NULL DEFAULT 0,
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
            skill_levels_json TEXT DEFAULT '{}',
            quests_json    TEXT DEFAULT '{}',
            char_stats_json TEXT DEFAULT '{}',
            learned_recipes_json TEXT DEFAULT '[]',
            last_save   INTEGER DEFAULT (strftime('%s','now'))
        );
        """)
    # Migração: adiciona colunas em bancos existentes (coluna não existia antes)
    with _get_conn() as conn:
        try:
            # Salt por conta (item C1, PROBLEMAS_ARQUITETURA.md §11). NULL =
            # conta legada (hash antigo, sem salt) — upgrade transparente no
            # próximo login bem-sucedido (ver _authenticate_sync).
            conn.execute("ALTER TABLE accounts ADD COLUMN salt TEXT DEFAULT NULL")
        except Exception:
            pass  # coluna já existe
        try:
            # Flag de GM (07/08/2026) — concedida só via server/grant_gm.py
            # (CLI direto no banco, sem UI/endpoint de rede), mesmo padrão
            # do "account set gmlevel" do AzerothCore.
            conn.execute("ALTER TABLE accounts ADD COLUMN is_gm INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # coluna já existe
        try:
            conn.execute("ALTER TABLE characters ADD COLUMN fog_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # coluna já existe
        try:
            conn.execute("ALTER TABLE characters ADD COLUMN skill_levels_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # coluna já existe
        try:
            conn.execute("ALTER TABLE characters ADD COLUMN quests_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # coluna já existe
        try:
            conn.execute("ALTER TABLE characters ADD COLUMN char_stats_json TEXT DEFAULT '{}'")
        except Exception:
            pass  # coluna já existe
        try:
            # Receitas de crafting aprendidas por pergaminho — débito A4
            # (11/08/2026, ver PROBLEMAS_ARQUITETURA.md). Nunca existia
            # coluna pra isso no sistema online (só o save-slot offline
            # antigo, já morto, persistia isso).
            conn.execute("ALTER TABLE characters ADD COLUMN learned_recipes_json TEXT DEFAULT '[]'")
        except Exception:
            pass  # coluna já existe
    log.info(f"[Auth] banco inicializado: {DB_PATH}")
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
            log.info(f"[Auth] conta de teste criada: usuario='{username}'  "
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
    Compara credenciais. `password` é o client_hash (SHA-256 do texto, feito
    no cliente). No banco: sha256(salt + client_hash) com salt por conta —
    contas legadas (salt NULL, hash antigo = client_hash direto) são
    verificadas pelo esquema antigo e MIGRADAS transparentemente no próprio
    login bem-sucedido. Ver _server_hash pro racional.
    Retorna {account_id, characters:[...]}. None = credenciais inválidas.
    """
    import hmac
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash, salt, is_gm FROM accounts WHERE username=?",
            (username,)
        ).fetchone()
        if not row:
            return None
        salt = row["salt"]
        if salt:
            ok = hmac.compare_digest(row["password_hash"], _server_hash(salt, password))
        else:
            # Conta legada (pré-salt): hash armazenado == client_hash direto.
            ok = hmac.compare_digest(row["password_hash"], password)
            if ok:
                # Upgrade transparente: re-armazena com salt novo. A partir
                # daqui o dump do banco não serve mais pra logar nesta conta.
                new_salt = _new_salt()
                conn.execute(
                    "UPDATE accounts SET salt=?, password_hash=? WHERE id=?",
                    (new_salt, _server_hash(new_salt, password), row["id"])
                )
        if not ok:
            return None
        # Só os campos que a tela de seleção de personagem realmente usa
        # (ui/char_creation_screen.py: id/name/class_id/level) — NUNCA os
        # blobs JSON grandes (inventory/equipment/stats/skills/talents/
        # fog/skill_levels/quests): _handle_select_character já busca o
        # personagem escolhido de novo, por inteiro, via get_character()
        # (server/session.py), então mandar tudo aqui é só peso morto. Bug
        # real 19/07/2026: fog_json (grid de fog-of-war, cresce sem limite
        # por tile explorado) somado nos 3 personagens de uma conta passou
        # de 1 MB — o frame WebSocket inteiro (limite 1048576 bytes) era
        # rejeitado com "message too big", derrubando a conexão logo após
        # autenticar, antes até da tela de escolha de personagem aparecer.
        chars = conn.execute(
            "SELECT id, name, class_id, level FROM characters "
            "WHERE account_id=? ORDER BY id LIMIT 3",
            (row["id"],)
        ).fetchall()
        return {
            "account_id":  row["id"],
            "characters":  [dict(c) for c in chars],
            "is_gm":       bool(row["is_gm"]),
        }


async def register(username: str, password: str) -> bool:
    """Cria conta (sem personagem). password já vem hasheado pelo cliente.
    Fast-path (§120): SELECT de existência (leitura pura, nunca disputa
    fila de escrita) ANTES de `_identity_lock` — caso comum de verdade é
    reconexão de conta já existente (todo cliente/bot manda REGISTER de
    novo a cada LOGIN), não registro de conta nova. Sem isso, cada
    reconexão pagava uma aquisição inteira de fila + INSERT que falha via
    IntegrityError só pra descobrir que não tinha nada a escrever. Corrida
    rara (2 registros do MESMO username NOVO ao mesmo tempo) ainda cai
    corretamente no IntegrityError dentro da fila — sem brecha de
    corretude, só menos disputa no caminho comum."""
    if await asyncio.get_running_loop().run_in_executor(
            None, _account_exists_sync, username):
        return False
    async with _get_identity_lock():
        return await asyncio.get_running_loop().run_in_executor(
            _identity_executor, _register_account_sync, username, password)


def _account_exists_sync(username: str) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM accounts WHERE username=? LIMIT 1", (username,)
        ).fetchone()
        return row is not None


def _register_account_sync(username: str, password: str) -> bool:
    """Insere conta. `password` é o client_hash (SHA-256 do texto, cliente);
    armazenamento sempre com salt novo — ver _server_hash."""
    try:
        salt = _new_salt()
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO accounts (username, password_hash, salt) VALUES (?,?,?)",
                (username, _server_hash(salt, password), salt)
            )
        return True
    except sqlite3.IntegrityError:
        return False   # username já existe


async def create_character(account_id: int, name: str,
                           class_id: str = "guerreiro") -> str:
    """Cria personagem para a conta. Retorna "ok" ou um motivo de rejeição
    ("invalid_name_format", "name_taken", "limit_reached", "creation_failed")
    — ver _create_character_sync."""
    async with _get_identity_lock():
        return await asyncio.get_running_loop().run_in_executor(
            _identity_executor, _create_character_sync, account_id, name, class_id)


def _create_character_sync(account_id: int, name: str,
                           class_id: str = "guerreiro",
                           tile_x: int = RESPAWN_TILE[0],
                           tile_y: int = RESPAWN_TILE[1]) -> str:
    """Nome tem que ser único no banco INTEIRO (não só por conta) — nameplate/
    chat/trade mostram o nome do personagem pra todo mundo, então dois
    "Aventureiro" em contas diferentes colidem visualmente do mesmo jeito
    que colidiriam na mesma conta. Checagem case-insensitive (LOWER) — senão
    "Juugo" e "juugo" driblam a unicidade só trocando a caixa. Validação de
    formato reaproveita shared/character_names.py (mesma regra que o
    gerador de sugestão usa) — único ponto de verdade pro que é um nome
    válido, cliente e servidor concordam sem duplicar a regex."""
    from shared.character_names import is_valid_name, normalize_name
    name = normalize_name(name)
    if not is_valid_name(name):
        return "invalid_name_format"
    try:
        with _get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM characters WHERE account_id=?", (account_id,)
            ).fetchone()[0]
            if count >= 3:
                return "limit_reached"  # limite de 3 personagens por conta
            taken = conn.execute(
                "SELECT 1 FROM characters WHERE LOWER(name)=LOWER(?) LIMIT 1", (name,)
            ).fetchone()
            if taken:
                return "name_taken"
            conn.execute(
                "INSERT INTO characters (account_id, name, class_id, tile_x, tile_y)"
                " VALUES (?,?,?,?,?)",
                (account_id, name, class_id, tile_x, tile_y)
            )
        return "ok"
    except Exception:
        return "creation_failed"


async def suggest_character_name() -> str:
    """Gera um nome de fantasia (shared/character_names.py) e tenta até
    _MAX_SUGGEST_ATTEMPTS vezes achar um que ainda não existe no banco.
    Se todas colidirem (extremamente improvável — ver tamanho do pool de
    sílabas), devolve o último candidato mesmo assim: pior caso, o jogador
    vê CHARACTER_ERROR "name_taken" ao confirmar e digita outro nome —
    igual já acontece hoje pra qualquer nome escolhido à mão."""
    return await asyncio.get_running_loop().run_in_executor(
        None, _suggest_character_name_sync)


_MAX_SUGGEST_ATTEMPTS = 8


def _suggest_character_name_sync() -> str:
    from shared.character_names import generate_name_candidate
    with _get_conn() as conn:
        for _ in range(_MAX_SUGGEST_ATTEMPTS):
            candidate = generate_name_candidate()
            taken = conn.execute(
                "SELECT 1 FROM characters WHERE LOWER(name)=LOWER(?) LIMIT 1",
                (candidate,)
            ).fetchone()
            if not taken:
                return candidate
        return candidate


async def delete_character(account_id: int, char_id: int) -> bool:
    """Remove personagem verificando que pertence à conta."""
    async with _get_identity_lock():
        return await asyncio.get_running_loop().run_in_executor(
            _identity_executor, _delete_character_sync, account_id, char_id)


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
    """Persiste estado do personagem. Chamado ao desconectar e periodicamente.
    Serializado por `_write_lock` (fila LENTA, §120) — separada de
    `_identity_lock` (register/create_character/delete_character) de
    propósito: este save roda em TODO disconnect (além de autosave/
    SAVE_STATE/talento/quest) com payload JSON pesado, e não pode atrasar
    a reconexão de outro jogador."""
    async with _get_write_lock():
        await asyncio.get_running_loop().run_in_executor(
            _write_executor, _save_character_sync, char_id, data)


def _save_character_sync(char_id: int, data: dict) -> None:
    """Persiste estado do personagem. Campos com valor None são ignorados (coluna não é atualizada)."""
    import json
    _stats = data.get("stats", {})
    # Campos server-autoritativos — sempre atualizados
    cols = ["tile_x", "tile_y", "hp", "mp", "level", "stats_json", "map_id", "char_stats_json"]
    vals = [
        data.get("tile_x", 10), data.get("tile_y", 10),
        data.get("hp",     100), data.get("mp", 100),
        _stats.get("level", 1),
        json.dumps(_stats),
        data.get("map_id") or "map_main",
        json.dumps(data.get("char_stats") or {}),
    ]
    # Campos client-autoritativos — só atualiza se não for None
    for key, col in (("inventory", "inventory_json"),
                     ("equipment", "equipment_json"),
                     ("skills",    "skills_json"),
                     ("talents",   "talents_json"),
                     ("fog",       "fog_json"),
                     ("skill_levels", "skill_levels_json"),
                     ("quests",    "quests_json"),
                     ("learned_recipes", "learned_recipes_json")):
        v = data.get(key)
        if v is not None:
            cols.append(col)
            vals.append(json.dumps(v))
    set_sql = ", ".join(f"{c}=?" for c in cols) + ", last_save=strftime('%s','now')"
    vals.append(char_id)
    with _get_conn() as conn:
        conn.execute(f"UPDATE characters SET {set_sql} WHERE id=?", vals)
