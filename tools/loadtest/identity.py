"""
identity.py — fonte única de identidade de bot (04/09/2026). Usado por
`provision.py` (cria conta+personagem) E `swarm.py` (loga como esses
mesmos bots) — NUNCA duplicar a regra de nome/senha/classe em cada um,
senão os dois lados divergem e o swarm não acha as contas que o
provision criou.

`bot_char_name` só usa LETRAS (sem dígito) de propósito — nome de
personagem no jogo é validado por `shared/character_names.py::
is_valid_name` (regra do usuário, 20/07/2026: "3-16 caracteres, só
letras, sem espaço/número/símbolo"). O índice do bot vira um sufixo em
base-26 (a-z) em vez de dígito — só o USERNAME da conta (sem essa
restrição, só precisa de 3-20 caracteres) usa o índice numérico direto.
"""
from __future__ import annotations

from content.skill_config import SKILL_ORDER_BY_CLASS

_CLASSES = ("guerreiro", "mago", "arqueiro")

# Facções nunca atacadas mesmo quando kind=="enemy" (guarda/NPC civil) —
# ver shared/messages.py e content/faction_data.py pela lista completa;
# essas 2 são as únicas "amigavel" ao player por padrão.
FRIENDLY_NPC_FACTIONS = frozenset({"guardas_vila", "civis"})

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _index_to_letters(i: int, width: int = 4) -> str:
    """Converte um índice inteiro numa string de `width` letras minúsculas
    (base-26) — suficiente unicidade pra qualquer contagem de bot razoável
    (26**4 = ~456 mil combinações) sem usar dígito nenhum."""
    chars = []
    n = i
    for _ in range(width):
        n, rem = divmod(n, len(_ALPHABET))
        chars.append(_ALPHABET[rem])
    return "".join(reversed(chars))


def bot_username(i: int) -> str:
    """Login da CONTA — sem restrição de formato além de 3-20 chars
    (server/session.py::_handle_register), dígito é permitido aqui."""
    return f"loadbot{i:04d}"


def bot_password(i: int) -> str:
    """Determinística, não-secreta — ferramenta de dev local, nunca
    aponta pra ambiente real."""
    return f"loadbot-pw-{i:04d}"


def bot_class(i: int) -> str:
    return _CLASSES[i % len(_CLASSES)]


def bot_char_name(i: int) -> str:
    """Só letras (ver docstring do módulo) — 'LoadBot' + 4 letras do
    índice, ex.: i=0 → 'LoadBotaaaa', i=299 → 'LoadBotalcz'. Servidor
    normaliza a 1ª letra pra maiúscula sozinho (`normalize_name`)."""
    return "LoadBot" + _index_to_letters(i)


def starter_skills_for_class(class_id: str) -> list:
    """Todas as skills TREINÁVEIS da classe (as vendidas pelo treinador,
    `content/skill_config.py::SKILL_ORDER_BY_CLASS`) — pedido do usuário
    de liberar todas, não só 1. NÃO inclui skills de
    `INSTANCE_SKILL_UNLOCK_ORDER` que dependem também de talento alocado
    (`engine/world_systems.py::is_skill_authorized` checa talento além
    de learned_skill_ids) — simular alocação de talento é escopo à parte,
    não muda o objetivo do teste (gerar variedade de skill/dano)."""
    return list(SKILL_ORDER_BY_CLASS.get(class_id, []))
