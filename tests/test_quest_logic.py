"""
tests/test_quest_logic.py — engine/quest_logic.py::format_quest_text
(23/07/2026, pedido do usuário): placeholder `{player_name}` em texto de
quest (description/completion, content/quests_data.py::QuestDef).

Também cobre normalize_reward_entry/resolve_reward_item_factory (mesmo
dia, pedido do usuário: recompensa de itens fixos + escolha em
QuestReward.items/choice).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.quest_logic import (format_quest_text, normalize_reward_entry,
                                resolve_reward_item_factory)


def test_substitui_o_placeholder_pelo_nome():
    text = "Bem-vindo, {player_name}! Prove seu valor."
    assert format_quest_text(text, "Malthor") == "Bem-vindo, Malthor! Prove seu valor."


def test_substitui_todas_as_ocorrencias():
    text = "{player_name}, ouviu falar de {player_name}?"
    assert format_quest_text(text, "Aria") == "Aria, ouviu falar de Aria?"


def test_texto_sem_placeholder_fica_intacto():
    text = "Nenhuma variável aqui."
    assert format_quest_text(text, "Malthor") == text


def test_nome_vazio_nao_quebra_e_mantem_texto_original():
    """Nome vazio (ex.: CharacterStats ausente) não deveria produzir um
    texto com "{player_name}" trocado por string vazia (ilegível) — mantém
    o placeholder intacto pra ficar óbvio que algo não carregou, em vez de
    silenciosamente sumir com a palavra."""
    text = "Olá, {player_name}!"
    assert format_quest_text(text, "") == text


# ── normalize_reward_entry ────────────────────────────────────────────────

def test_normalize_string_vira_stack_1():
    assert normalize_reward_entry("training_sword") == ("training_sword", 1)


def test_normalize_tupla_preserva_stack():
    assert normalize_reward_entry(("Poção de Mana", 3)) == ("Poção de Mana", 3)


# ── resolve_reward_item_factory ──────────────────────────────────────────

def test_resolve_chave_do_catalogo_principal():
    factory = resolve_reward_item_factory("training_sword")
    assert factory is not None
    item = factory()
    assert item.name == "Espada de treinamento"


def test_resolve_chave_de_quest_items_como_fallback():
    factory = resolve_reward_item_factory("Pelo de Urso")
    assert factory is not None
    item = factory()
    assert item.name == "Pelo de Urso"


def test_resolve_chave_inexistente_retorna_none():
    assert resolve_reward_item_factory("isso_nao_existe_de_verdade") is None


# ── try_start: objetivo collect_item pré-completo se o item já está na bag ──
# Feature nova (25/07/2026, Fase M4 revisada — item concede quest ACEITA
# via popup, não mais automático no loot): quando o jogador aceita a quest
# DEPOIS de já ter o item na bag, o evento "collect_item" (que só dispara
# em pickups NOVOS) nunca chegaria a fechar o objetivo — try_start precisa
# checar a Inventory na hora de iniciar.

def _make_try_start_world(item_name: str = "", stack: int = 1):
    from engine.world import World
    from engine.components import QuestLog, Inventory, Item
    world = World()
    player = world.create_entity()
    ql = QuestLog()
    world.add_component(player, ql)
    inv = Inventory()
    if item_name:
        _it = Item(item_name, "material", slot=None, max_stack=10)
        _it.stack = stack
        inv.items.append(_it)
    world.add_component(player, inv)
    return world, player, ql


def test_try_start_collect_item_ja_na_bag_nasce_completo():
    from engine.quest_logic import try_start
    from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
    qid = "qz_try_start_ja_tem"
    QUESTS[qid] = QuestDef(
        title="Teste", description="d",
        objectives=(ObjectiveDef(type="collect_item", target="*",
                                 loot_item="Relíquia de Teste", count=1),),
        reward=QuestReward(xp=1),
    )
    try:
        world, player, ql = _make_try_start_world("Relíquia de Teste")
        started = try_start(world, player, ql, qid)
        assert started is True
        assert ql.active[qid] == [1]
    finally:
        QUESTS.pop(qid, None)


def test_try_start_collect_item_sem_o_item_nasce_zerado():
    from engine.quest_logic import try_start
    from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
    qid = "qz_try_start_sem_item"
    QUESTS[qid] = QuestDef(
        title="Teste", description="d",
        objectives=(ObjectiveDef(type="collect_item", target="*",
                                 loot_item="Relíquia de Teste", count=1),),
        reward=QuestReward(xp=1),
    )
    try:
        world, player, ql = _make_try_start_world()   # bag vazia
        started = try_start(world, player, ql, qid)
        assert started is True
        assert ql.active[qid] == [0]
    finally:
        QUESTS.pop(qid, None)


def test_try_start_collect_item_respeita_count_maior_que_1():
    from engine.quest_logic import try_start
    from content.quests_data import QUESTS, QuestDef, QuestReward, ObjectiveDef
    qid = "qz_try_start_count3"
    QUESTS[qid] = QuestDef(
        title="Teste", description="d",
        objectives=(ObjectiveDef(type="collect_item", target="*",
                                 loot_item="Relíquia de Teste", count=3),),
        reward=QuestReward(xp=1),
    )
    try:
        world, player, ql = _make_try_start_world("Relíquia de Teste", stack=2)
        started = try_start(world, player, ql, qid)
        assert started is True
        assert ql.active[qid] == [2]   # tem 2, precisa de 3 — parcial, não estoura
    finally:
        QUESTS.pop(qid, None)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
