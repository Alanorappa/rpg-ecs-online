"""
ui/skill_slot_prefs.py
Preferência de slot da hotbar por skill_id (26/08/2026) — ponto único de
verdade usado por 3 chamadores que NÃO compartilham uma classe comum:
`GameEngine` (game.py), `NetworkHandlers` (client/network_handlers.py,
mixin de GameEngine) e `TrainerSystem` (ui/trainer_system.py, objeto
separado, sem referência de volta pro GameEngine). Fica em `ui/` porque
`client/` já importa de `ui/` livremente (padrão estabelecido em vários
arquivos), nunca o contrário — e recebe `(world, player_entity)` em vez
de depender de um nome de personagem guardado em algum objeto: o nome
já existe no ECS (`CharacterStats.name`, a MESMA fonte que
`GameEngine._logged_char_name` usava), então nenhum chamador precisa
guardar/repassar estado próprio pra usar isso.

Bug real que motivou a extração (26/08/2026): a função nasceu como
método de `GameEngine` (`_preferred_slot_for_skill`) — funcionava via
mixin pra `NetworkHandlers`, mas `TrainerSystem` é instanciado à parte
(`game.py`, sem referência de volta pro engine), então
`self._preferred_slot_for_skill` nunca existia ali — crash real
relatado pelo usuário (`AttributeError`) ao treinar uma skill. Os
testes não pegaram porque monkeypatchavam o método direto em fixtures
leves (`FixtureClass._preferred_slot_for_skill = GameEngine.
_preferred_slot_for_skill`), mascarando a falta da ligação real entre
`TrainerSystem` e o GameEngine em produção.
"""
from __future__ import annotations


def _char_name(world, player_entity: int) -> str:
    from engine.components import CharacterStats
    char = world.get_component(player_entity, CharacterStats)
    return char.name if char else ""


def preferred_slot_for_skill(world, player_entity: int, ps: "PlayerSkills",
                             skill_id: str) -> "int | None":
    """Slot que o jogador já escolheu pra essa skill antes (dentro OU
    fora da BG, mesma preferência) — só retorna um slot LIVRE (nunca
    rouba o lugar de outra skill já colocada, cai pro fallback de
    primeiro slot vazio nesse caso, igual sempre foi). Usado pelos 3
    pontos que colocam uma skill nova na hotbar: `ui/trainer_system.py::
    _do_learn`, `client/network_handlers.py::_handle_msg_skill_granted`
    e o bloco `skills_hotbar` (revelação de skill dentro da instância)."""
    char_name = _char_name(world, player_entity)
    if not char_name:
        return None
    import config as _cfg
    prefs = (_cfg.load().get("characters", {})
                        .get(char_name, {})
                        .get("skill_slot_prefs", {}))
    idx = prefs.get(skill_id)
    if idx is not None and 0 <= idx < len(ps.skills) and ps.skills[idx] is None:
        return idx
    return None


def skill_slot_prefs_to_dict(ps: "PlayerSkills | None", existing: dict) -> dict:
    """Preferência de slot por skill_id — MERGE com o que já existia em
    config.json, nunca substitui o mapa inteiro: só atualiza a entrada
    das skills que estão na hotbar AGORA (as outras, configuradas antes
    num contexto diferente — dentro ou fora da BG — continuam
    intactas). Ver `preferred_slot_for_skill` (leitura)."""
    merged = dict(existing)
    if ps:
        for i, s in enumerate(ps.skills):
            if s:
                merged[s.skill_id] = i
    return merged
