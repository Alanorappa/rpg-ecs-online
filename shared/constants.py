"""
shared/constants.py
Constantes compartilhadas entre servidor e cliente.
Qualquer valor aqui deve ser idêntico nos dois lados.
"""

# ── Rede ──────────────────────────────────────────────────────────────────────
SERVER_HOST        = "localhost"
SERVER_PORT        = 8765
TICK_RATE          = 20          # ticks por segundo
TICK_INTERVAL      = 1.0 / TICK_RATE   # 0.05s = 50ms

# Janela de lag compensation: servidor aceita inputs com até N ms de atraso
LAG_COMP_WINDOW_MS = 200

# ── Mundo ─────────────────────────────────────────────────────────────────────
TILE_SIZE          = 32          # pixels por tile (deve ser igual ao cliente)
AOI_RADIUS         = 15          # tiles de visão ao redor do jogador
FOG_RADIUS         = 15          # deve coincidir com AOI_RADIUS

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

# ── Sincronização de stats de combate (PLAYER_STAT_SYNC) ──────────────────────
# Mapeamento: nome_efetivo → base_attr no CombatStats
# O cliente envia os valores efetivos; o servidor atualiza os base_attrs
# correspondentes e recalcula os efetivos.
# Para adicionar uma nova stat: basta inserir uma entrada neste dict.
COMBAT_SYNC_STATS: dict[str, str] = {
    "max_hp":          "base_stamina",        # max_hp  = stamina = base_stamina + mods
    "attack_power":    "base_attack_power",   # AP efetivo (inclui arma + equipamento)
    "armor":           "base_armor",          # armadura total
    "crit_rating":     "base_crit_rating",    # % crit
    "parry_rating":    "base_parry_rating",   # % aparo
    "dodge_rating":    "base_dodge_rating",   # % esquiva
    "attack_interval": "base_attack_interval",# velocidade de ataque
}
