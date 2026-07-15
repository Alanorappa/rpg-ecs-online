"""
faction_data.py — Tabela central de facções e relações entre elas.

Fonte única de verdade pra "quem pode brigar com quem" (mob vs. player,
mob vs. mob, mob vs. NPC de combate, NPC vs. NPC) — mesmo padrão de
mob_definitions.py/skill_config.py: dict módulo-level, sistemas leem daqui,
nunca hardcodeiam facção/relação num call site.

RELATIONSHIP mapeia PARES de facção pro tier de relação entre elas. A
ordem do par não importa — get_relationship() busca nos dois sentidos —
então cada par só precisa aparecer UMA VEZ na tabela.

Tiers (estilo WoW — ver ARQUITETURA_ONLINE.md pra decisão + fontes):
  "hostil"    — ataca por proximidade (raio de aggro), sem precisar ser
                provocado. Aliados da mesma facção assistem (co-aggro).
  "neutro"    — nunca ataca por proximidade; só entra em combate se
                receber dano da outra facção. Reverte ao normal pelo
                mesmo mecanismo de leash/evasão que RETURNING já usa.
  "amigavel"  — nunca ataca, e dano entre as duas facções é bloqueado na
                origem (não só "não provoca") — evita AoE de área virando
                bug de "aliado virou hostil por acidente".

DEFAULT_RELATIONSHIP cobre qualquer par não listado explicitamente aqui —
"neutro" é o default mais seguro (não inicia briga sozinho, mas também não
impede combate se for atacado).
"""
from __future__ import annotations

PLAYER_FACTION = "jogadores"

# ("faction_a", "faction_b") -> "hostil" | "neutro" | "amigavel"
# Pares com "jogadores" definem a disposição do mob/NPC em relação ao
# player. Pares sem "jogadores" definem se duas facções não-jogador
# brigam entre si (mob vs. mob, mob vs. NPC de combate, NPC vs. NPC).
RELATIONSHIP: dict[tuple[str, str], str] = {
    # Monstros hostis "clássicos" (zumbi, elemental agressivo, etc.) —
    # atacam o player por proximidade, e brigam com guardas da vila.
    ("monstros_hostis", PLAYER_FACTION):    "hostil",
    ("monstros_hostis", "guardas_vila"):    "hostil",

    # Vida selvagem (lobo, etc.) — só briga se provocada; não tem motivo
    # pra atacar guardas da vila por conta própria.
    ("vida_selvagem", PLAYER_FACTION):      "neutro",
    ("vida_selvagem", "guardas_vila"):      "neutro",
    ("vida_selvagem", "monstros_hostis"):   "neutro",

    # Guardas da vila — protegem o player, nunca o atacam.
    ("guardas_vila", PLAYER_FACTION):       "amigavel",

    # Bandidos — hostis ao player E aos guardas (motiva NPC-vs-NPC).
    ("bandidos", PLAYER_FACTION):           "hostil",
    ("bandidos", "guardas_vila"):           "hostil",
    ("bandidos", "vida_selvagem"):          "neutro",
    ("bandidos", "monstros_hostis"):        "neutro",
}

DEFAULT_RELATIONSHIP = "neutro"


def get_relationship(faction_a: str, faction_b: str) -> str:
    """Tier de relação entre duas facções ("hostil"/"neutro"/"amigavel").
    Mesma facção = sempre amigável (nunca briga com o próprio grupo).
    Busca simétrica (ordem do par não importa); par não listado cai no
    DEFAULT_RELATIONSHIP."""
    if faction_a == faction_b:
        return "amigavel"
    return (RELATIONSHIP.get((faction_a, faction_b))
            or RELATIONSHIP.get((faction_b, faction_a))
            or DEFAULT_RELATIONSHIP)
