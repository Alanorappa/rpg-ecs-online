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

# ── Morte/respawn: fluxo de espírito (ghost) + cemitério ───────────────────────
GHOST_GRAVEYARD_RADIUS_TILES = 5   # raio (tiles) do cemitério p/ revive automático
GHOST_CORPSE_RADIUS_TILES    = 3   # raio (tiles) do corpo p/ prompt "Reviver agora?"
GHOST_GRAVEYARD_REVIVE_S     = 45.0  # segundos contínuos no cemitério p/ revive full HP
GHOST_CORPSE_REVIVE_HP_FRAC  = 0.15  # fração de HP ao reviver no corpo

