"""
shared/character_names.py — validação e geração de nomes de personagem.

Regras (feedback do usuário, 20/07/2026): 3-16 caracteres, só letras
(com acentos — jogo é PT-BR), sem espaço/número/símbolo. Unicidade é
responsabilidade do SERVIDOR (server/auth.py — precisa consultar o
banco); este módulo só sabe validar formato e sugerir candidatos, sem
tocar em estado nenhum — por isso pode ser usado tanto pelo cliente
(sugestão instantânea no modo offline, sem banco) quanto pelo servidor
(sugestão verificada contra o banco no modo online).
"""
import random
import re

NAME_MIN_LEN = 3
NAME_MAX_LEN = 16

# Letras ASCII + acentuadas comuns em PT-BR (À-ÖØ-öø-ÿ cobre á/à/ã/â/ç/é/ê/í/ó/ô/õ/ú/ü etc,
# excluindo × e ÷ que caem no meio dessa faixa Unicode).
_NAME_CHAR_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+$")


def is_valid_name_char(ch: str) -> bool:
    """True se `ch` (1 caractere) é permitido num nome — usado pelo cliente
    pra filtrar teclas digitadas em tempo real (letra sim, espaço/número/
    símbolo não)."""
    return bool(_NAME_CHAR_RE.fullmatch(ch))


def normalize_name(name: str) -> str:
    """1ª letra maiúscula, resto como veio — jogador pode digitar tudo
    minúsculo sem se preocupar. Único ponto de verdade: cliente aplica
    a cada tecla (ui/char_creation_screen.py) e o servidor aplica nesse
    mesmo formato antes de gravar (server/auth.py), defesa contra um
    cliente modificado que mande o nome cru."""
    return name[:1].upper() + name[1:] if name else name


def is_valid_name(name: str) -> bool:
    """Valida formato completo: 3-16 chars, só letras (com acento). NÃO
    valida unicidade — isso exige consulta ao banco (server/auth.py)."""
    if not name or not (NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN):
        return False
    return bool(_NAME_RE.match(name))


# ── Gerador de nomes (sílabas + consoante opcional no meio) ─────────────────

_START = [
    "Ka", "Mor", "Thal", "Bra", "Dun", "Sil", "Ery", "Vor", "Ith", "Nal",
    "Fen", "Wren", "Ael", "Syl", "Dra", "Gor", "Lys", "Ban", "Rok", "Tir",
    "Zan", "Quil", "Bry", "Hal", "Or", "Ys", "Ju", "Na", "Ara", "Vol", "Xa",
    "Bal", "Mel", "Zun",
]
_MID_CONSONANTS = ["m", "n", "s", "r", "l", "d", "t"]
_SUFFIX = [
    "dor", "wyn", "ric", "as", "on", "ir", "eth", "ian", "ux", "yn",
    "el", "ara", "in", "or", "ys", "go", "thor", "jin", "azar"
]

_MID_CHANCE = 0.35   # chance de inserir uma consoante extra entre sílabas
_MAX_ATTEMPTS = 20   # tentativas até achar um candidato dentro do tamanho


def generate_name_candidate(rng: "random.Random | None" = None) -> str:
    """Gera um nome de fantasia (sílaba inicial + consoante opcional +
    sufixo), sempre dentro de NAME_MIN_LEN..NAME_MAX_LEN e só com
    caracteres válidos. Não verifica unicidade — quem chama (servidor)
    decide se aceita o candidato ou pede outro."""
    r = rng or random
    for _ in range(_MAX_ATTEMPTS):
        start = r.choice(_START)
        suffix = r.choice(_SUFFIX)
        mid = r.choice(_MID_CONSONANTS) if r.random() < _MID_CHANCE else ""
        raw = start + mid + suffix
        name = raw[0].upper() + raw[1:].lower()
        if is_valid_name(name):
            return name
    return "Aventureiro"  # nunca deveria chegar aqui — combinações cabem no limite
