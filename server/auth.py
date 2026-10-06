"""
server/auth.py
Autenticação — PostgreSQL via asyncpg (migrado de SQLite em 07/09/2026,
ver PROBLEMAS_ARQUITETURA.md §123, decisão explícita do usuário: servidor
dedicado em nuvem, múltiplos núcleos/processos no roadmap).

Por que asyncpg (não psycopg2 nem manter o padrão run_in_executor+lock de
antes): Postgres aceita MÚLTIPLOS escritores reais (MVCC) — o SQLite só
aceita 1, e toda a maquinaria de `_write_lock`/`_identity_lock`/
`_write_executor`/`_identity_executor`/`_conn_cache` das seções §113-122
existia SÓ pra contornar essa limitação de 1 escritor. Com Postgres, essa
serialização vira complexidade sem propósito — pior, um lock em memória de
UM processo Python nunca ajudaria de verdade quando tivermos múltiplos
processos (sharding por mapa/instância, próximo passo do roadmap). asyncpg
é o driver assíncrono nativo (sem precisar de thread executor pra não
bloquear o event loop) e já vem com pool de conexões pronto — mais simples
E mais correto pro alvo (múltiplos processos, múltiplos núcleos).

Unicidade de nome de personagem (era o TOCTOU corrigido por `_identity_lock`
no §119) agora é garantida por um ÍNDICE ÚNICO no próprio banco
(`characters_name_lower_idx`, case-insensitive) — funciona certo mesmo
com processos diferentes disputando ao mesmo tempo, o que um lock em
memória de 1 processo NUNCA garantiria de verdade.
"""
from __future__ import annotations
from server.log import log
import asyncio
import hashlib
import os
import re

import asyncpg

from shared.constants import RESPAWN_TILE

# String de conexão — variável de ambiente em produção (nunca hardcoded
# credencial real no código-fonte), com um default de DEV apontando pro
# container Docker local (ver README/PROBLEMAS_ARQUITETURA.md §123 pro
# comando `docker run` que sobe esse container).
DATABASE_URL = os.environ.get(
    "RPG_DATABASE_URL", "postgresql://postgres:devpassword@localhost:5432/rpg_ecs_online")

# Schema Postgres — "public" em produção/dev normal. Testes trocam pra um
# schema isolado próprio (mesmo espírito do DB_PATH temporário da era
# SQLite) — cada arquivo de teste usa 1 nome fixo e distinto, dropado e
# recriado do zero no início de cada teste (nunca precisa acumular limpeza
# entre execuções). Só aceita identificador simples (sem aspas/injeção) —
# nunca vem de input de rede, mas validar mesmo assim é barato.
_SCHEMA = "public"
_SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _quoted_schema() -> str:
    if not _SCHEMA_RE.match(_SCHEMA):
        raise ValueError(f"nome de schema inválido: {_SCHEMA!r}")
    return f'"{_SCHEMA}"'


# Pool de conexões — recriado quando o EVENT LOOP muda (mesmo motivo já
# documentado pro `_get_write_lock` da era SQLite: um pool asyncpg é preso
# ao loop em que foi criado; `unittest.IsolatedAsyncioTestCase` cria um
# loop novo por método de teste) OU quando DATABASE_URL/_SCHEMA mudam
# (testes trocam de schema). Pool antigo só recebe `close()` se ainda for
# do MESMO loop — de loop diferente, o loop já não roda mais, então só
# descartar a referência (nunca dar await num pool de loop morto).
_pool: "asyncpg.Pool | None" = None
_pool_loop = None
_pool_key = None


async def _get_pool() -> "asyncpg.Pool":
    global _pool, _pool_loop, _pool_key
    loop = asyncio.get_running_loop()
    key = (DATABASE_URL, _SCHEMA)
    if _pool is not None and _pool_loop is loop and _pool_key == key:
        return _pool
    if _pool is not None and _pool_loop is loop:
        await _pool.close()  # mesmo loop, só a config mudou — fecha limpo
    _pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=1, max_size=10,
        server_settings={"search_path": _SCHEMA})
    _pool_loop = loop
    _pool_key = key
    return _pool


async def _close_pool() -> None:
    """Fecha o pool atual, se existir. Produção NUNCA chama isso (1
    processo = 1 pool a vida toda, fechado só quando o processo morre).
    Existe pra TESTE: `unittest.IsolatedAsyncioTestCase` cria um event
    loop NOVO por método e o FECHA logo depois — um pool só consegue ser
    fechado de verdade enquanto o loop dele ainda está rodando (depois
    de fechado, não dá pra agendar nada nele); sem chamar isto de dentro
    de `asyncTearDown`/`addAsyncCleanup` (ainda dentro do loop do teste),
    cada teste vaza as conexões do pool anterior — Postgres tem um teto
    de conexões simultâneas (`max_connections`), e uma suíte de milhares
    de testes esgota esse teto bem antes do fim (achado real, 07/09/2026,
    §123: `TooManyConnectionsError` a partir de ~1500 testes rodados)."""
    global _pool, _pool_loop, _pool_key
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_loop = None
        _pool_key = None


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


# (username, password, class_id, tile_x, tile_y)
# Tiles próximos ao spawn padrão do mapa — área sabidamente walkable.
# "teste" spawna exatamente em RESPAWN_TILE (centro do cemitério); teste2/3
# usam offsets fixos pra não sobrepor quando os 3 conectam ao mesmo tempo.
_TEST_ACCOUNTS = [
    ("teste",  "123456", "guerreiro", RESPAWN_TILE[0],     RESPAWN_TILE[1]),
    ("teste2", "123456", "mago",      RESPAWN_TILE[0] + 2, RESPAWN_TILE[1]),
    ("teste3", "123456", "arqueiro",  RESPAWN_TILE[0] + 4, RESPAWN_TILE[1]),
]


async def init_db() -> None:
    """Cria schema/tabelas se não existirem. Chamado na inicialização do
    servidor. Schema criado ANTES do pool (o pool já conecta com
    search_path apontando pro schema — precisa existir primeiro; conexão
    de bootstrap separada, sem search_path customizado, resolve isso)."""
    if _SCHEMA != "public":
        boot_conn = await asyncpg.connect(DATABASE_URL)
        try:
            await boot_conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quoted_schema()}")
        finally:
            await boot_conn.close()

    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id            SERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt          TEXT,
                is_gm         BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    BIGINT NOT NULL DEFAULT extract(epoch FROM now())::bigint
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                id                    SERIAL PRIMARY KEY,
                account_id            INTEGER NOT NULL REFERENCES accounts(id),
                name                  TEXT NOT NULL,
                class_id              TEXT NOT NULL DEFAULT 'guerreiro',
                level                 INTEGER DEFAULT 1,
                xp                    INTEGER DEFAULT 0,
                tile_x                INTEGER DEFAULT 10,
                tile_y                INTEGER DEFAULT 10,
                map_id                TEXT DEFAULT 'map_main',
                hp                    INTEGER DEFAULT 0,
                mp                    INTEGER DEFAULT 100,
                stats_json            TEXT DEFAULT '{}',
                inventory_json        TEXT DEFAULT '[]',
                equipment_json        TEXT DEFAULT '{}',
                skills_json           TEXT DEFAULT '{}',
                talents_json          TEXT DEFAULT '{}',
                fog_json              TEXT DEFAULT '{}',
                skill_levels_json     TEXT DEFAULT '{}',
                quests_json           TEXT DEFAULT '{}',
                char_stats_json       TEXT DEFAULT '{}',
                learned_recipes_json  TEXT DEFAULT '[]',
                last_save             BIGINT NOT NULL DEFAULT extract(epoch FROM now())::bigint
            );
        """)
        # Unicidade de nome GLOBAL, case-insensitive — substitui o
        # check-then-insert protegido por `_identity_lock` da era SQLite
        # (§119). Garantido pelo BANCO agora, não por um lock de processo —
        # continua correto com múltiplos processos (sharding futuro).
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS characters_name_lower_idx "
            "ON characters (LOWER(name))")
        # Correio (25/09/2026) — primeira feature do projeto com entrega
        # ASSÍNCRONA de verdade (destinatário pode estar offline). Anexo
        # fica em CUSTÓDIA na própria linha (items_json/gold) até
        # reclamado — mesmo princípio de custódia que trade_processor.py
        # já usa, só que persistido em vez de em memória (trade nunca
        # sobrevive a um restart do servidor, correio precisa). Sem
        # coluna de expiração por decisão do usuário (25/09/2026) — fica
        # até o player deletar manualmente.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mail (
                id                 SERIAL PRIMARY KEY,
                recipient_char_id  INTEGER NOT NULL REFERENCES characters(id),
                sender_name        TEXT NOT NULL,
                subject            TEXT NOT NULL,
                body               TEXT NOT NULL DEFAULT '',
                items_json         TEXT NOT NULL DEFAULT '[]',
                gold               INTEGER NOT NULL DEFAULT 0,
                claimed_items_json TEXT NOT NULL DEFAULT '[]',
                gold_claimed       BOOLEAN NOT NULL DEFAULT FALSE,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mail_recipient ON mail (recipient_char_id)")
    log.info(f"[Auth] banco inicializado (PostgreSQL): schema={_SCHEMA}")
    await _seed_test_accounts()


async def _seed_test_accounts() -> None:
    """Garante que as contas de teste existam com personagens. Idempotente."""
    for username, password, class_id, tx, ty in _TEST_ACCOUNTS:
        ph      = _hash(password)
        created = await _register_account(username, ph)
        if created:
            acc_id = await _get_account_id(username)
            if acc_id:
                await _create_character(acc_id, username, class_id, tx, ty)
            log.info(f"[Auth] conta de teste criada: usuario='{username}'  "
                  f"classe={class_id}  tile=({tx},{ty})")


async def _get_account_id(username: str) -> "int | None":
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT id FROM accounts WHERE username=$1", username)
    return row["id"] if row else None


async def authenticate(username: str, password: str) -> "dict | None":
    """
    Compara credenciais. `password` é o client_hash (SHA-256 do texto, feito
    no cliente). No banco: sha256(salt + client_hash) com salt por conta —
    contas legadas (salt NULL, hash antigo = client_hash direto) são
    verificadas pelo esquema antigo e MIGRADAS transparentemente no próprio
    login bem-sucedido. Ver _server_hash pro racional.
    Retorna {account_id, characters:[...]}. None = credenciais inválidas.
    """
    import hmac
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT id, password_hash, salt, is_gm FROM accounts WHERE username=$1", username)
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
            await pool.execute(
                "UPDATE accounts SET salt=$1, password_hash=$2 WHERE id=$3",
                new_salt, _server_hash(new_salt, password), row["id"])
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
    chars = await pool.fetch(
        "SELECT id, name, class_id, level FROM characters "
        "WHERE account_id=$1 ORDER BY id LIMIT 3", row["id"])
    return {
        "account_id":  row["id"],
        "characters":  [dict(c) for c in chars],
        "is_gm":       bool(row["is_gm"]),
    }


async def register(username: str, password: str) -> bool:
    """Cria conta (sem personagem). password já vem hasheado pelo cliente.
    Fast-path: SELECT de existência ANTES do INSERT — caso comum de verdade
    é reconexão de conta já existente (todo cliente/bot manda REGISTER de
    novo a cada LOGIN), não registro de conta nova; evita pagar um INSERT
    que só ia falhar via violação de unicidade. Corrida rara (2 registros
    do MESMO username NOVO ao mesmo tempo) ainda cai corretamente na
    violação de unicidade dentro de `_register_account` — sem brecha de
    corretude, só menos trabalho no caminho comum."""
    if await _account_exists(username):
        return False
    return await _register_account(username, password)


async def _account_exists(username: str) -> bool:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT 1 FROM accounts WHERE username=$1", username)
    return row is not None


async def _register_account(username: str, password: str) -> bool:
    """Insere conta. `password` é o client_hash (SHA-256 do texto, cliente);
    armazenamento sempre com salt novo — ver _server_hash."""
    pool = await _get_pool()
    salt = _new_salt()
    try:
        await pool.execute(
            "INSERT INTO accounts (username, password_hash, salt) VALUES ($1,$2,$3)",
            username, _server_hash(salt, password), salt)
        return True
    except asyncpg.UniqueViolationError:
        return False   # username já existe


async def create_character(account_id: int, name: str,
                           class_id: str = "guerreiro") -> str:
    """Cria personagem pra conta, sempre no tile de spawn padrão
    (RESPAWN_TILE) — caminho real de rede. Ver `_create_character` pro
    corpo (aceita tile_x/tile_y customizado — usado só por
    `_seed_test_accounts`, que espalha as contas de teste em tiles
    vizinhos pra não sobrepor no boot)."""
    return await _create_character(account_id, name, class_id,
                                   RESPAWN_TILE[0], RESPAWN_TILE[1])


async def _create_character(account_id: int, name: str,
                            class_id: str = "guerreiro",
                            tile_x: int = RESPAWN_TILE[0],
                            tile_y: int = RESPAWN_TILE[1]) -> str:
    """Nome tem que ser único no banco INTEIRO (não só por conta) — nameplate/
    chat/trade mostram o nome do personagem pra todo mundo, então dois
    "Aventureiro" em contas diferentes colidem visualmente do mesmo jeito
    que colidiriam na mesma conta. Checagem case-insensitive reforçada pelo
    índice único do banco (`characters_name_lower_idx`) — a checagem abaixo
    é só o caminho feliz (mensagem de erro amigável); a corrida real (2
    criações concorrentes do MESMO nome) é fechada pelo BANCO, não por um
    lock de processo (correto mesmo com múltiplos processos futuros).
    Validação de formato reaproveita shared/character_names.py (mesma regra
    que o gerador de sugestão usa) — único ponto de verdade pro que é um
    nome válido, cliente e servidor concordam sem duplicar a regex.

    Retorna "ok" ou um motivo de rejeição ("invalid_name_format",
    "name_taken", "limit_reached", "creation_failed")."""
    from shared.character_names import is_valid_name, normalize_name
    name = normalize_name(name)
    if not is_valid_name(name):
        return "invalid_name_format"
    pool = await _get_pool()
    # Corrida aceita e documentada, não corrigida: 2 CREATE_CHARACTER
    # simultâneos da MESMA conta podem ambos ver count<3 e ambos inserir,
    # resultando em 4 personagens em vez do limite de 3 — cenário exige um
    # duplo-clique na MESMA fração de segundo da MESMA conta, sem impacto
    # de segurança/corretude de dado (só um soft-limit cosmético passando
    # por 1), não vale a complexidade de uma transação SERIALIZABLE com
    # retry pra fechar. Unicidade de NOME (o que realmente importa pra
    # nameplate/chat global) é a que tem proteção real, via índice único.
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM characters WHERE account_id=$1", account_id)
    if count >= 3:
        return "limit_reached"  # limite de 3 personagens por conta
    taken = await pool.fetchrow(
        "SELECT 1 FROM characters WHERE LOWER(name)=LOWER($1) LIMIT 1", name)
    if taken:
        return "name_taken"
    try:
        await pool.execute(
            "INSERT INTO characters (account_id, name, class_id, tile_x, tile_y)"
            " VALUES ($1,$2,$3,$4,$5)",
            account_id, name, class_id, tile_x, tile_y)
        return "ok"
    except asyncpg.UniqueViolationError:
        return "name_taken"  # corrida real fechada pelo índice único do banco
    except Exception:
        return "creation_failed"


_MAX_SUGGEST_ATTEMPTS = 8


async def suggest_character_name() -> str:
    """Gera um nome de fantasia (shared/character_names.py) e tenta até
    _MAX_SUGGEST_ATTEMPTS vezes achar um que ainda não existe no banco.
    Se todas colidirem (extremamente improvável — ver tamanho do pool de
    sílabas), devolve o último candidato mesmo assim: pior caso, o jogador
    vê CHARACTER_ERROR "name_taken" ao confirmar e digita outro nome —
    igual já acontece hoje pra qualquer nome escolhido à mão."""
    from shared.character_names import generate_name_candidate
    pool = await _get_pool()
    candidate = None
    for _ in range(_MAX_SUGGEST_ATTEMPTS):
        candidate = generate_name_candidate()
        taken = await pool.fetchrow(
            "SELECT 1 FROM characters WHERE LOWER(name)=LOWER($1)", candidate)
        if not taken:
            return candidate
    return candidate


async def delete_character(account_id: int, char_id: int) -> bool:
    """Remove personagem verificando que pertence à conta."""
    pool = await _get_pool()
    try:
        await pool.execute(
            "DELETE FROM characters WHERE id=$1 AND account_id=$2", char_id, account_id)
        return True
    except Exception:
        return False


async def get_character(account_id: int, char_id: int) -> "dict | None":
    """Retorna um personagem verificando que pertence à conta."""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM characters WHERE id=$1 AND account_id=$2", char_id, account_id)
    return dict(row) if row else None


async def save_character(char_id: int, data: dict) -> None:
    """Persiste estado do personagem. Chamado ao desconectar e periodicamente.
    Campos com valor None são ignorados (coluna não é atualizada)."""
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
    set_sql = ", ".join(f"{c}=${i+1}" for i, c in enumerate(cols)) + ", last_save=extract(epoch FROM now())::bigint"
    vals.append(char_id)
    pool = await _get_pool()
    await pool.execute(f"UPDATE characters SET {set_sql} WHERE id=${len(vals)}", *vals)


# ── Correio (25/09/2026) ────────────────────────────────────────────────
# `items`: lista de (item_key, stack) — MESMO formato de
# `QuestReward.items`/entries de `WorldServer.grant_items_to_player`
# (engine.quest_logic.normalize_reward_entry), nenhum catálogo paralelo.

async def create_mail(recipient_char_id: int, sender_name: str, subject: str,
                      body: str, items: list, gold: int) -> int:
    """Cria uma correspondência. Retorna o id gerado."""
    import json
    pool = await _get_pool()
    row = await pool.fetchrow(
        "INSERT INTO mail (recipient_char_id, sender_name, subject, body, items_json, gold) "
        "VALUES ($1,$2,$3,$4,$5,$6) RETURNING id",
        recipient_char_id, sender_name, subject, body, json.dumps(items), gold)
    return row["id"]


async def get_character_id_by_name(name: str) -> "int | None":
    """Resolve destinatário por nome (case-insensitive, mesma convenção de
    unicidade de `characters_name_lower_idx`)."""
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT id FROM characters WHERE LOWER(name)=LOWER($1)", name)
    return row["id"] if row else None


async def list_inbox(char_id: int) -> list:
    """Resumo (sem body/items completos — banda menor pro inbox). Ordenado
    do mais recente pro mais antigo."""
    import json
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT id, sender_name, subject, items_json, gold, created_at "
        "FROM mail WHERE recipient_char_id=$1 ORDER BY created_at DESC", char_id)
    result = []
    for r in rows:
        items = json.loads(r["items_json"])
        result.append({
            "mail_id":         r["id"],
            "sender_name":     r["sender_name"],
            "subject":         r["subject"],
            "has_attachment":  bool(items) or r["gold"] > 0,
            "created_at":      r["created_at"].timestamp() if r["created_at"] else 0,
        })
    return result


_RANKING_LIMIT = 100


async def get_ranking(category: str) -> list:
    """Ranking geral (06/10/2026, pedido do usuário) — `category` é
    "level" (nível de personagem) ou um dos `engine.components.
    SKILL_IDS` (nível de skill por uso, 0-200). Inclui TODOS os
    personagens já criados, online ou não — decisão explícita do
    usuário ("ranking de verdade", não só quem está jogando agora).

    Retorna até `_RANKING_LIMIT` entradas `{name, class_id, value}` já
    ordenadas (maior valor primeiro, nome alfabético como desempate) —
    `[]` se `category` não existe. "level" usa `ORDER BY` direto
    (coluna simples); skill usa `skill_levels_json` (TEXT, não JSONB —
    lido e ordenado em Python, mais simples/seguro que confiar em cast
    `::jsonb` dentro do SQL, e o volume de personagens ainda é pequeno
    o bastante pra isso não pesar)."""
    from engine.components import SKILL_IDS as _SKILL_IDS_RANK
    import json as _json_rank

    pool = await _get_pool()
    if category == "level":
        rows = await pool.fetch(
            "SELECT name, class_id, level AS value FROM characters "
            "ORDER BY level DESC, name ASC LIMIT $1", _RANKING_LIMIT)
        return [dict(r) for r in rows]

    if category not in _SKILL_IDS_RANK:
        return []

    rows = await pool.fetch("SELECT name, class_id, skill_levels_json FROM characters")
    entries = []
    for r in rows:
        value = 0
        try:
            data = _json_rank.loads(r["skill_levels_json"] or "{}")
            value = int(data.get("levels", {}).get(category, 0))
        except (ValueError, TypeError, AttributeError):
            pass
        entries.append({"name": r["name"], "class_id": r["class_id"], "value": value})
    entries.sort(key=lambda e: (-e["value"], e["name"]))
    return entries[:_RANKING_LIMIT]


async def get_mail_detail(mail_id: int, char_id: int) -> "dict | None":
    """Valida ownership pelo `char_id` — NUNCA confia só no `mail_id` vindo
    do cliente (outro player não pode abrir mail alheio adivinhando o id)."""
    import json
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM mail WHERE id=$1 AND recipient_char_id=$2", mail_id, char_id)
    if row is None:
        return None
    d = dict(row)
    d["items_json"]         = json.loads(d["items_json"])
    d["claimed_items_json"] = json.loads(d["claimed_items_json"])
    return d


async def claim_mail_item(mail_id: int, char_id: int, item_index: int) -> "dict | None":
    """Marca o item como reclamado e retorna {item_key, stack} — None se
    já reclamado, índice inválido, ou mail não pertence ao player
    (idempotência: uma 2ª tentativa do mesmo claim é recusada, não
    duplica a entrega). NÃO concede o item de verdade — isso é
    responsabilidade do CHAMADOR via `WorldServer.grant_items_to_player`,
    mesmo padrão de recompensa de quest. `FOR UPDATE` fecha a corrida de
    2 claims simultâneos do mesmo slot."""
    import json
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT items_json, claimed_items_json FROM mail "
                "WHERE id=$1 AND recipient_char_id=$2 FOR UPDATE", mail_id, char_id)
            if row is None:
                return None
            items   = json.loads(row["items_json"])
            claimed = json.loads(row["claimed_items_json"])
            if not (0 <= item_index < len(items)) or item_index in claimed:
                return None
            claimed.append(item_index)
            await conn.execute(
                "UPDATE mail SET claimed_items_json=$1 WHERE id=$2",
                json.dumps(claimed), mail_id)
            entry = items[item_index]
            return {"item_key": entry[0], "stack": entry[1]}


async def claim_mail_gold(mail_id: int, char_id: int) -> "int | None":
    """Retorna o gold reclamado, ou None se já reclamado/sem gold/mail não
    pertence ao player (mesma idempotência de `claim_mail_item`)."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT gold, gold_claimed FROM mail "
                "WHERE id=$1 AND recipient_char_id=$2 FOR UPDATE", mail_id, char_id)
            if row is None or row["gold_claimed"] or row["gold"] <= 0:
                return None
            await conn.execute("UPDATE mail SET gold_claimed=TRUE WHERE id=$1", mail_id)
            return row["gold"]


async def unclaim_mail_item(mail_id: int, char_id: int, item_index: int) -> None:
    """Desfaz `claim_mail_item` — usado só quando a concessão real (via
    `WorldServer.grant_items_to_player`) falha DEPOIS de marcar reclamado
    (ex: mochila cheia). Sem isso o item ficaria perdido pra sempre
    (marcado como reclamado no banco, mas nunca entregue de verdade)."""
    import json
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT claimed_items_json FROM mail "
                "WHERE id=$1 AND recipient_char_id=$2 FOR UPDATE", mail_id, char_id)
            if row is None:
                return
            claimed = json.loads(row["claimed_items_json"])
            if item_index in claimed:
                claimed.remove(item_index)
                await conn.execute(
                    "UPDATE mail SET claimed_items_json=$1 WHERE id=$2",
                    json.dumps(claimed), mail_id)


async def delete_mail(mail_id: int, char_id: int) -> bool:
    """True se de fato deletou (existia e pertencia ao player)."""
    pool = await _get_pool()
    result = await pool.execute(
        "DELETE FROM mail WHERE id=$1 AND recipient_char_id=$2", mail_id, char_id)
    return result.split()[-1] == "1"


async def count_unread_mail(char_id: int) -> int:
    """"Não reclamada" = tem item OU gold ainda pendente de reclamar —
    derivado, sem coluna de "lido" separada pra manter sincronizada à
    parte. Usado só pro indicador visual sobre a caixa de correio."""
    import json
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT items_json, claimed_items_json, gold, gold_claimed "
        "FROM mail WHERE recipient_char_id=$1", char_id)
    count = 0
    for r in rows:
        items   = json.loads(r["items_json"])
        claimed = json.loads(r["claimed_items_json"])
        if len(claimed) < len(items) or (r["gold"] > 0 and not r["gold_claimed"]):
            count += 1
    return count
