"""
quests_data.py — Dados puros do sistema de quests (sem lógica).

Adicionar uma nova quest: inserir uma entrada em QUESTS.
Adicionar um item de quest: inserir uma lambda em QUEST_ITEMS.

Tipos de objetivo (ObjectiveDef.type):
    kill            Matar N inimigos. target = nome | raça | "*" (qualquer)
    collect_item    Coletar N de loot_item de target. Drop condicional via loot_chance.
    reach_tile      Chegar em location=(tx, ty) ou área (x0, y0, x1, y1).
    use_skill       Usar skill_id N vezes.
    use_consumable  Usar consumível N vezes. target = nome do item | "*".
    reach_level     Alcançar o nível count. (target ignorado)
    talk_to_npc     Interagir com mercador. target = nome | "*".
    equip_item      Equipar item. target = nome | item_type | "*".
"""
from __future__ import annotations
from typing import NamedTuple
from components import Item


# ---------------------------------------------------------------------------
# Tipos de dados
# ---------------------------------------------------------------------------

class ObjectiveDef(NamedTuple):
    type:        str            # tipo do objetivo (ver docstring do módulo)
    target:      str   = "*"   # alvo; "*" = qualquer
    count:       int   = 1     # quantidade necessária
    location:    tuple = ()    # (tx, ty) ou (x0, y0, x1, y1) para reach_tile
    loot_item:   str   = ""    # nome do item a dropar condicionalmente (collect_item)
    loot_chance: float = 1.0   # chance de drop do item condicional (0.0–1.0)


class QuestReward(NamedTuple):
    xp:   int = 0
    gold: int = 0


class QuestDef(NamedTuple):
    title:       str
    description: str
    objectives:  tuple           # tuple[ObjectiveDef, ...]
    reward:      QuestReward
    auto_start:  bool  = False   # inicia automaticamente sem NPC
    repeatable:  bool  = False   # reseta ao completar
    requires:    tuple = ()      # tuple[quest_id, ...] pré-requisitos
    next_quest:  str   = ""      # quest_id a iniciar automaticamente ao completar
    level_req:   int   = 0       # nível mínimo para aceitar a quest


# ---------------------------------------------------------------------------
# Itens de quest (materiais drop-only, sem slot de equipamento)
# ---------------------------------------------------------------------------

QUEST_ITEMS: dict[str, callable] = {
    "Pelo de Urso":     lambda: Item("Pelo de Urso",     "material", slot=None, rarity="common", value=3, max_stack=10),
    "Presa de Lobo":    lambda: Item("Presa de Lobo",    "material", slot=None, rarity="common", value=2, max_stack=10),
    "Veneno de Aranha": lambda: Item("Veneno de Aranha", "material", slot=None, rarity="common", value=4, max_stack=10),
    "Cauda de Escorpião": lambda: Item("Cauda de Escorpião", "material", slot=None, rarity="common", value=3, max_stack=10),
    "Escama de Cobra":  lambda: Item("Escama de Cobra",  "material", slot=None, rarity="common", value=2, max_stack=10),
    "Osso de Goblin":   lambda: Item("Osso de Goblin",   "material", slot=None, rarity="common", value=2, max_stack=10),
}


# ---------------------------------------------------------------------------
# Tabela de quests
# ---------------------------------------------------------------------------

QUESTS: dict[str, QuestDef] = {

    # ── Introdução ───────────────────────────────────────────────────────────
    "first_blood": QuestDef(
        title="Primeiro Sangue",
        description="Mate seu primeiro inimigo.",
        objectives=(
            ObjectiveDef(type="kill", target="*", count=1),
        ),
        reward=QuestReward(xp=50),
        next_quest="survivor",
    ),

    "survivor": QuestDef(
        title="Sobrevivente",
        description="Alcance o Nível 3.",
        objectives=(
            ObjectiveDef(type="reach_level", count=3),
        ),
        reward=QuestReward(xp=120, gold=5),
        requires=("first_blood",),
    ),

    # ── Caça ─────────────────────────────────────────────────────────────────
    "bear_hunter": QuestDef(
        title="Caçador de Ursos",
        description="Mate 5 Ursos.",
        objectives=(
            ObjectiveDef(type="kill", target="Urso", count=5),
        ),
        reward=QuestReward(xp=200, gold=10),
        next_quest="bear_pelt",
    ),

    "bear_pelt": QuestDef(
        title="Peles Valiosas",
        description="Colete 3 Pelos de Urso.",
        objectives=(
            ObjectiveDef(
                type="collect_item",
                target="Urso",
                count=3,
                loot_item="Pelo de Urso",
                loot_chance=0.75,
            ),
        ),
        reward=QuestReward(xp=150, gold=15),
        requires=("bear_hunter",),
    ),

    "wolf_fangs": QuestDef(
        title="Presas Afiadas",
        description="Colete 5 Presas de Lobo.",
        objectives=(
            ObjectiveDef(
                type="collect_item",
                target="Lobo",
                count=5,
                loot_item="Presa de Lobo",
                loot_chance=0.6,
            ),
        ),
        reward=QuestReward(xp=180, gold=12),
    ),

    "spider_venom": QuestDef(
        title="Veneno Mortal",
        description="Colete 3 frascos de Veneno de Aranha.",
        objectives=(
            ObjectiveDef(
                type="collect_item",
                target="Aranha",
                count=3,
                loot_item="Veneno de Aranha",
                loot_chance=0.7,
            ),
        ),
        reward=QuestReward(xp=130, gold=10),
    ),

    "beast_slayer": QuestDef(
        title="Matador de Feras",
        description="Mate 10 criaturas da raça Fera.",
        objectives=(
            ObjectiveDef(type="kill", target="Fera", count=10),
        ),
        reward=QuestReward(xp=300, gold=20),
        requires=("first_blood",),
        level_req=5,
    ),

    # ── Habilidades ───────────────────────────────────────────────────────────
    "warrior_trial": QuestDef(
        title="Prova do Guerreiro",
        description="Use Golpe Poderoso 3 vezes.",
        objectives=(
            ObjectiveDef(type="use_skill", target="golpe_poderoso", count=3),
        ),
        reward=QuestReward(xp=80),
        next_quest="executioner",
    ),

    "executioner": QuestDef(
        title="O Executor",
        description="Use Executar 5 vezes.",
        objectives=(
            ObjectiveDef(type="use_skill", target="executar", count=5),
        ),
        reward=QuestReward(xp=150, gold=8),
        requires=("warrior_trial",),
        level_req=3,
    ),

    # ── Social ────────────────────────────────────────────────────────────────
    "merchant_greeting": QuestDef(
        title="Contatos Locais",
        description="Fale com um Mercador.",
        objectives=(
            ObjectiveDef(type="talk_to_npc", target="*", count=1),
        ),
        reward=QuestReward(xp=30, gold=5),
    ),

    # ── Equipamento ───────────────────────────────────────────────────────────
    "first_equip": QuestDef(
        title="Armado e Perigoso",
        description="Equipe uma arma.",
        objectives=(
            ObjectiveDef(type="equip_item", target="weapon", count=1),
        ),
        reward=QuestReward(xp=60),
    ),
}
