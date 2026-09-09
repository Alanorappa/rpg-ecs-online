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
    rouba o lugar de outra skill já colocada). Usado pelos 3 pontos que
    colocam uma skill nova na hotbar: `ui/trainer_system.py::_do_learn`,
    `client/network_handlers.py::_handle_msg_skill_granted` e o bloco
    `skills_hotbar` (revelação de skill dentro da instância) — os 3
    confiam no valor de retorno já ser a decisão FINAL (nunca fazem seu
    próprio fallback de "primeiro slot vazio" mais).

    Fallback (09/09/2026, bug real relatado pelo usuário, ver
    PROBLEMAS_ARQUITETURA.md §129): "primeiro slot vazio" pula qualquer
    slot que seja a preferência salva de OUTRA skill — mesmo uma que
    ainda não existe/não desbloqueou. Sem isso, uma skill sem
    preferência própria (ex: Golpe Debilitante, nível 3 da BG, sem
    preferência salva porque o personagem real nunca a aprendeu fora)
    podia ocupar de passagem o slot que outra skill de nível MAIOR (ex:
    Vitória Iminente, nível 5, com preferência salva = slot 3) ia querer
    usar — quando a de nível maior finalmente desbloqueava, o slot já
    estava tomado, e a regra "nunca rouba slot ocupado" a jogava pra
    outro lugar, permanentemente (skill já conhecida nunca é remexida
    de novo, nem depois da interlopeira ser removida ao sair da BG). Se
    TODOS os slots vazios forem reserva de outra skill (raro — melhor
    conceder a skill em algum lugar do que não conceder), cai pro
    primeiro vazio puro, sem esse filtro."""
    char_name = _char_name(world, player_entity)
    prefs = {}
    if char_name:
        import config as _cfg
        prefs = (_cfg.load().get("characters", {})
                            .get(char_name, {})
                            .get("skill_slot_prefs", {}))

    idx = prefs.get(skill_id)
    if idx is not None and 0 <= idx < len(ps.skills) and ps.skills[idx] is None:
        return idx

    reserved_by_others = {v for k, v in prefs.items() if k != skill_id}
    for i, slot in enumerate(ps.skills):
        if slot is None and i not in reserved_by_others:
            return i

    try:
        return ps.skills.index(None)
    except ValueError:
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
