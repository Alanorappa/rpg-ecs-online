"""
tests/test_quest_logic.py — engine/quest_logic.py::format_quest_text
(23/07/2026, pedido do usuário): placeholder `{player_name}` em texto de
quest (description/completion, content/quests_data.py::QuestDef).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.quest_logic import format_quest_text


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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
