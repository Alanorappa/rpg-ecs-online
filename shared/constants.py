"""
shared/constants.py
Constantes compartilhadas entre servidor e cliente.
Qualquer valor aqui deve ser idêntico nos dois lados.
"""

# ── Rede ──────────────────────────────────────────────────────────────────────
SERVER_HOST        = "localhost"
SERVER_PORT        = 8765
TICK_RATE          = 30          # ticks por segundo
TICK_INTERVAL      = 1.0 / TICK_RATE   # ~33ms

# Janela de lag compensation: servidor aceita inputs com até N ms de atraso
LAG_COMP_WINDOW_MS = 200

# ── Mundo ─────────────────────────────────────────────────────────────────────
TILE_SIZE          = 32          # pixels por tile (deve ser igual ao cliente)
AOI_RADIUS         = 15          # tiles de visão ao redor do jogador
FOG_RADIUS         = 15          # deve coincidir com AOI_RADIUS

# Histerese de saída do AOI: uma entidade só é despawnada ao passar de
# AOI_RADIUS + AOI_EXIT_BUFFER, não exatamente em AOI_RADIUS (continua
# entrando em AOI_RADIUS normalmente — buffer só no critério de SAÍDA).
# Sem isso, um mob cujo caminho "raspa" a borda do raio (ex: RETURNING pro
# spawn cruzando perto de 15 tiles) gera spawn/despawn repetido a cada
# tick que cruza a fronteira — o cliente nunca chega a renderizar o mob de
# forma estável, "nunca aparece" mesmo recebendo os dados corretamente.
# Ver arquitetura/PROBLEMAS_ARQUITETURA.md.
AOI_EXIT_BUFFER    = 3

# Centro do cemitério — spawn padrão de personagem novo E respawn pós-morte.
# Fonte única: server/respawn_system.py e server/auth.py importam daqui em vez
# de hardcoded — mover o cemitério só exige mudar este valor.
RESPAWN_TILE: tuple[int, int] = (115, 389)

# Instâncias
MAX_DUNGEON_PLAYERS = 6
MAX_RAID_PLAYERS    = 20

# ── Servidor ──────────────────────────────────────────────────────────────────
MAX_PLAYERS_PER_WORLD = 500
SNAPSHOT_HISTORY      = 10       # quantos ticks de estado o servidor guarda p/ lag comp

# ── Autenticação ──────────────────────────────────────────────────────────────
SESSION_TOKEN_BYTES = 32
SESSION_TTL_S       = 86_400     # 24h

# ── Versão do protocolo ───────────────────────────────────────────────────────
PROTOCOL_VERSION = 1             # incrementar ao quebrar compatibilidade

# ── Versão do jogo (SemVer: MAJOR.MINOR.PATCH) ────────────────────────────────
# Só cosmético (exibido na tela de login) — não confundir com PROTOCOL_VERSION
# acima (esse sim trava compatibilidade cliente/servidor). Atualizar aqui e
# criar a tag git correspondente (`git tag vX.Y.Z`) a cada commit relevante:
# PATCH = correção, MINOR = feature nova, MAJOR = mudança grande/quebra de
# compatibilidade (decisão do usuário, 20/07/2026 — ver ARQUITETURA_ONLINE.md).
GAME_VERSION = "0.7.2"

# ── Morte/respawn: fluxo de espírito (ghost) + cemitério ───────────────────────
GHOST_GRAVEYARD_RADIUS_TILES = 5   # raio (tiles) do cemitério p/ revive automático
GHOST_CORPSE_RADIUS_TILES    = 3   # raio (tiles) do corpo p/ prompt "Reviver agora?"
GHOST_GRAVEYARD_REVIVE_S     = 45.0  # segundos contínuos no cemitério p/ revive full HP
GHOST_CORPSE_REVIVE_HP_FRAC  = 0.15  # fração de HP ao reviver no corpo

# ── Trade (player↔player) ──────────────────────────────────────────────────────
# Distância máxima (chebyshev) entre os dois players pra abrir/manter um trade.
# Checada 1x/tick — estourar cancela a sessão (reason="distance").
TRADE_MAX_DIST_TILES = 5

# ── Duelo (contexto PvP por convite) ───────────────────────────────────────────
# Convite exige a mesma proximidade do trade (TRADE_MAX_DIST_TILES); durante o
# duelo o par pode se espalhar até este raio — estourar encerra por "distance"
# (checado 1x/tick, mesmo padrão do trade). Maior que o do trade de propósito:
# a luta precisa de espaço (kite de arqueiro/mago).
DUEL_MAX_DIST_TILES = 20

# ── Party/Grupo ──────────────────────────────────────────────────────────────
# Tamanho máximo do grupo (decisão do usuário 17/07/2026). Sem checagem de
# distância pra convidar/permanecer agrupado (diferente de trade/duelo) —
# replica o /invite de WoW, que funciona no mapa inteiro.
PARTY_MAX_SIZE = 5
# Raio (chebyshev, mesmo mapa) pra XP compartilhado alcançar um membro do
# grupo que não bateu no mob — reusa AOI_RADIUS (mesma noção de "por perto"
# já usada pra visibilidade/broadcast em todo o resto do projeto).
PARTY_XP_SHARE_RADIUS_TILES = AOI_RADIUS

