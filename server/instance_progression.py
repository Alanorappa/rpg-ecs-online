"""
instance_progression.py — progressão normalizada de instância (31/07/2026,
base pro futuro modo "Battlefield" — ver arquitetura/ARQUITETURA_ONLINE.md
pra decisão completa e next_implementations/battlefield_design.md, §2,
"Modelo 1: leva tudo do personagem principal" — SUPERADO por esta decisão).

ATIVO desde 04-05/08/2026 (corrigido 06/08/2026 — este docstring dizia
"INERTE" e ficou desatualizado por um mês; ver PROBLEMAS_ARQUITETURA.md
§12): `server/bg_queue_processor.py` chama enter_/exit_normalized_progression
de verdade na fila real de battleground. Os 2 pontos de integração abaixo
documentam o padrão geral (útil pra um futuro modo Battlefield/dungeon
adicional), mas não é mais teórico — é o caminho real de todo player que
entra numa partida de BG hoje.

Toggle por modo (retrátil, pedido do usuário): qualquer dict de config de
modo (ex.: um futuro BATTLEFIELD_MODES, espelhando ARENA_MODES em
server/match_processor.py) pode ter uma chave opcional
`"progression_mode": "normalized" | "real"`, lida com
`mode_cfg.get("progression_mode", "real")` — default "real" sempre que
ausente. A Arena nunca terá essa chave, então fica inerte por padrão, não
por um branch que alguém possa esquecer de excluir. Um futuro processador
de Battlefield deve chamar:
  - ENTRADA: logo após transfer_player + entrada no time/Faction (mesmo
    ponto que server/match_processor.py::request_arena_accept já faz pra
    Arena, linha ~283), chamar enter_normalized_progression(ws, player_eid)
    se progression_mode == "normalized".
  - SAÍDA: dentro do ponto único de saída por-jogador (equivalente a
    _arena_leave_now — chamar SEMPRE desse chokepoint, nunca duplicado em
    cada gatilho de forfeit/disconnect/timeout), chamar
    exit_normalized_progression(ws, player_eid) incondicionalmente (é
    idempotente, não precisa checar o modo ali).

`ws` (primeiro parâmetro de toda função pública aqui) é qualquer objeto
com `.world` (o World do ECS) + `._apply_talent_modifiers(eid, allocated)`
+ `._apply_equipment_modifiers(eid)` — na prática, sempre uma instância de
WorldServer (server/world_server.py). Reaproveita esses dois métodos (já
usados por login/EQUIP_SYNC/TALENT_UPDATE) em vez de reimplementar a
aplicação de modifiers de talento/equipamento em CombatStats.
"""
from __future__ import annotations

from engine.components import (
    CharacterStats, CombatStats, PermanentStats, TalentTree, PlayerSkills,
    Wallet, Inventory, Equipment, InstanceProgressionSnapshot,
)
from engine.stats_system import (
    CLASS_BASE_STATS, CLASS_LEVEL_GAINS,
    apply_char_stats_to_combat, sync_attack_interval,
)
from engine.utils import chebyshev
from content.skill_config import INSTANCE_SKILL_UNLOCK_ORDER, SKILL_CATALOG
from content.talent_data import TALENTS, CLASS_BUILD_MAP

INSTANCE_LEVEL_CAP = 15
INSTANCE_INVENTORY_SLOTS = 6
INSTANCE_STARTING_GOLD = 150
# Recompensa de ouro por matar um player inimigo dentro da instância
# (01/08/2026, pedido do usuário — "quando um oponente morre deve ir
# automaticamente pro inventário do player que o matou"). Minion/torre
# usam o `gold_min`/`gold_max` da própria definição
# (content/minion_definitions.py/tower_definitions.py) — só player não
# tem uma tabela de definição própria pra guardar isso.
INSTANCE_PLAYER_KILL_GOLD = 50


def is_in_normalized_progression(ws, eid: int) -> bool:
    return ws.world.get_component(eid, InstanceProgressionSnapshot) is not None


def players_in_normalized_progression_near(ws, tx: int, ty: int,
                                            map_file: "str | None",
                                            radius: int) -> list[int]:
    """Players em progressão normalizada dentro do raio (chebyshev) de
    (tx,ty), no MESMO mapa — usado pro split de XP de minion por
    proximidade (02/08/2026, pedido do usuário: "a xp não é para ser
    necessário bater no mob, se o mob morrer perto dos players, tem que
    ser dividido entre os players, sem necessidade de dar um hit sequer
    nos minions" — LoL-style, diferente do XP de mob comum, que continua
    proporcional por dano via damage_log). Mesmo padrão de
    `PartyProcessorMixin._party_members_in_range`, generalizado pra
    QUALQUER player em instância (não só grupo)."""
    from engine.components import TileMovement as _TMProx
    result = []
    for eid in ws._player_eids.values():
        if not is_in_normalized_progression(ws, eid):
            continue
        if map_file is not None and ws.get_entity_map(eid) != map_file:
            continue
        tm = ws.world.get_component(eid, _TMProx)
        if tm is None:
            continue
        if chebyshev(tx, ty, tm.current_tile_x, tm.current_tile_y) <= radius:
            result.append(eid)
    return result


def _real_slot_by_skill_id(real_ps: "PlayerSkills | None") -> dict[str, int]:
    """Mapa skill_id→índice a partir da hotbar REAL do player (04/08/2026,
    pedido do usuário: "a config de atalhos deve permanecer dentro da BG —
    se uma skill está no slot 3 e for a primeira a ser desbloqueada, ela
    deve aparecer no slot 3"). `config.json` é 100% client-local (o
    servidor não tem acesso), mas a hotbar REAL já vive espelhada aqui via
    `HOTBAR_UPDATE`/save — não precisa de protocolo novo, só consultar o
    `PlayerSkills` real ANTES de conceder a skill de instância."""
    if real_ps is None:
        return {}
    return {s.skill_id: i for i, s in enumerate(real_ps.skills) if s}


def _grant_instance_skill(ps: PlayerSkills, sid: str,
                          real_slot_by_sid: "dict[str, int] | None" = None,
                          reserved_slots: "set[int] | None" = None) -> None:
    """Concede `sid` (learned_skill_ids + slot da hotbar) — idempotente,
    não faz nada se já concedida.

    Slot (04/08/2026, redesign — pedido do usuário): usa o MESMO slot que
    `sid` ocupa na hotbar REAL (`real_slot_by_sid`, ver
    `_real_slot_by_skill_id`), se esse slot ainda estiver livre na
    instância — cai pro primeiro slot vazio se a skill nunca foi colocada
    numa hotbar real (decisão #10 original) ou se o slot preferido já foi
    ocupado por outra skill concedida antes.

    `reserved_slots` (06/08/2026, bug real de playtest — ver
    ARQUITETURA_ONLINE.md §34.74.50): conjunto de TODOS os slots que
    alguma skill da hotbar real ocupa (`set(real_slot_by_sid.values())`),
    conhecido por inteiro desde a entrada na instância — mesmo pra
    skills que ainda não desbloquearam. `INSTANCE_SKILL_UNLOCK_ORDER` é
    uma ordem FIXA por classe, independente da hotbar real do player —
    sem este parâmetro, uma skill "de preenchimento" (sem slot
    preferido, ou cujo preferido já foi ocupado) que desbloqueia CEDO
    podia roubar greedy o slot que uma skill REAL só vai reivindicar
    DEPOIS (ex.: Interceptar, level 6) — quando essa skill tardia
    finalmente desbloqueava, o slot dela já tinha sido tomado, e ela
    caía em outro lugar (bug real: hotbar da instância não batia com a
    real mesmo pra skills configuradas). Fallback agora faz 2 passadas:
    primeiro só slots FORA de `reserved_slots` (nunca rouba o lugar de
    uma skill real ainda não revelada); só usa um slot reservado como
    último recurso, se não sobrar nenhum outro (mais skills que slots).

    Skills desbloqueadas por TALENTO no jogo real (ex.: punho_no_queixo,
    escudo_fogo) já autorizam de graça, sem precisar bumpar nada aqui: a
    árvore de talentos da instância vem PRÉ-ALOCADA no máximo desde
    `enter_normalized_progression` (02/08/2026, pedido do usuário — ver
    docstring lá). Esta função só cuida da REVELAÇÃO (hotbar/
    learned_skill_ids), no mesmo ritmo de INSTANCE_SKILL_UNLOCK_ORDER de
    sempre — antes disso, bumpava só o talento mínimo pra autorizar a
    skill nova, mas nunca reaplicava os OUTROS efeitos desse talento (ex.:
    bônus de HP/stat) em CombatStats, porque `_apply_talent_modifiers` só
    rodava 1x, na entrada, com a árvore ainda vazia — classe de bug
    eliminada pré-alocando tudo de uma vez."""
    if sid in ps.learned_skill_ids:
        return
    ps.learned_skill_ids.add(sid)
    preferred = (real_slot_by_sid or {}).get(sid)
    if preferred is not None and preferred < len(ps.skills) and ps.skills[preferred] is None:
        ps.skills[preferred] = PlayerSkills._make_skill(sid, SKILL_CATALOG)
        return
    _reserved = reserved_slots or set()
    for i, slot in enumerate(ps.skills):
        if slot is None and i not in _reserved:
            ps.skills[i] = PlayerSkills._make_skill(sid, SKILL_CATALOG)
            return
    # Último recurso: nenhum slot livre "não-reservado" sobrou (mais
    # skills reveladas até agora do que slots fora da hotbar real).
    for i, slot in enumerate(ps.skills):
        if slot is None:
            ps.skills[i] = PlayerSkills._make_skill(sid, SKILL_CATALOG)
            break


def enter_normalized_progression(ws, eid: int) -> None:
    """Entra em progressão normalizada: level/atributos brutos vão pro piso
    da classe (nível 1), skill/gold/itens viram um overlay vazio (decisão
    #8: concede de graça a 1ª skill de INSTANCE_SKILL_UNLOCK_ORDER).

    Talento (02/08/2026, redesign — pedido do usuário): a árvore de
    talentos da instância reaproveita a MESMA árvore/build real da classe
    (decisão #1 original), mas vem PRÉ-ALOCADA no máximo desde o início —
    "ignoramos o sistema de talentos, iniciamos com todos os pontos no
    máximo, e liberamos as skills provenientes de talento junto da lista
    de skills". Sem isso, `_grant_instance_skill` só bumpava o talento
    MÍNIMO necessário pra autorizar cada skill nova, mas nunca reaplicava
    os OUTROS efeitos desse talento (bônus de HP/stat) em CombatStats —
    `_apply_talent_modifiers` só rodava 1x aqui, com a árvore vazia.
    Pré-alocar tudo de uma vez elimina essa classe de bug inteira: não há
    mais "pontos" pra gerenciar dentro da instância, só a mesma
    INSTANCE_SKILL_UNLOCK_ORDER de sempre decidindo quando cada skill
    (normal ou de talento) aparece na hotbar.

    Cura 100% ao entrar (02/08/2026, pedido do usuário) — sem isso,
    `current_hp` real (ex.: 300/340) só era CLAMPADO contra o novo
    max_hp baixo (`recalculate_combat_stats`), nunca restaurado ao cheio.

    Idempotente: no-op se já estiver dentro."""
    if is_in_normalized_progression(ws, eid):
        return

    char = ws.world.get_component(eid, CharacterStats)
    tt   = ws.world.get_component(eid, TalentTree)
    ps   = ws.world.get_component(eid, PlayerSkills)
    if char is None or tt is None or ps is None:
        return
    wallet = ws.world.get_component(eid, Wallet)
    inv    = ws.world.get_component(eid, Inventory)
    equip  = ws.world.get_component(eid, Equipment)
    perm   = ws.world.get_component(eid, PermanentStats)

    snapshot = InstanceProgressionSnapshot(
        real_level=char.level,
        real_current_xp=char.current_xp,
        real_xp_to_next_level=char.xp_to_next_level,
        real_strength=char.strength,
        real_intelligence=char.intelligence,
        real_agility=char.agility,
        real_vitality=char.vitality,
        real_defense=char.defense,
        real_talent_tree=tt,
        real_player_skills=ps,
        real_wallet=wallet,
        real_inventory=inv,
        real_equipment=equip,
        real_permanent_stats=perm,
    )

    base = CLASS_BASE_STATS.get(char.class_id, {})
    char.level            = 1
    char.current_xp       = 0
    char.xp_to_next_level = CharacterStats.xp_for_level(1)
    char.strength     = base.get("strength", char.strength)
    char.intelligence = base.get("intelligence", char.intelligence)
    char.agility      = base.get("agility", char.agility)
    char.vitality     = base.get("vitality", char.vitality)
    char.defense      = base.get("defense", char.defense)

    new_tt = TalentTree()
    new_tt.chosen_build = CLASS_BUILD_MAP.get(char.class_id, tt.chosen_build)
    new_tt.allocated = {tid: t["max_points"] for tid, t in TALENTS.items()
                        if t.get("build") == new_tt.chosen_build}
    ws.world.add_component(eid, new_tt)

    new_ps = PlayerSkills()
    unlock_order = INSTANCE_SKILL_UNLOCK_ORDER.get(char.class_id, [])
    if unlock_order:
        _real_slots = _real_slot_by_skill_id(ps)
        _grant_instance_skill(new_ps, unlock_order[0], _real_slots, set(_real_slots.values()))
    ws.world.add_component(eid, new_ps)

    ws.world.add_component(eid, Wallet(gold=INSTANCE_STARTING_GOLD))
    ws.world.add_component(eid, Inventory(items=[], max_slots=INSTANCE_INVENTORY_SLOTS))
    ws.world.add_component(eid, Equipment())
    # Zera PermanentStats durante a instância (02/08/2026 — ver docstring de
    # InstanceProgressionSnapshot): sem isso, bônus LEGADOS de personagens
    # antigos vazam pro cálculo de HP/atributos "normalizados" via
    # apply_char_stats_to_combat (chamada logo abaixo, dentro de
    # _apply_talent_modifiers).
    ws.world.add_component(eid, PermanentStats())

    ws.world.add_component(eid, snapshot)

    ws._apply_talent_modifiers(eid, new_tt.allocated)
    ws._apply_equipment_modifiers(eid)

    cs = ws.world.get_component(eid, CombatStats)
    if cs is not None:
        cs.current_hp = cs.max_hp

    _push_stats_update(ws, eid, char, new_tt, in_instance=True,
                       inv=ws.world.get_component(eid, Inventory),
                       equip=ws.world.get_component(eid, Equipment),
                       ps=new_ps)


def exit_normalized_progression(ws, eid: int) -> None:
    """Sai da progressão normalizada: restaura level/xp/atributos brutos e
    reanexa os componentes reais salvos (talento/skill/gold/itens) —
    overlay é 100% descartado, sem conversão em nada permanente (decisão
    #4). No-op se o entity não estava em progressão normalizada
    (idempotente — seguro chamar de qualquer ponto de saída/disconnect).

    Cura 100% ao sair (02/08/2026, pedido do usuário) — mesmo espírito da
    cura ao entrar: o player nunca volta pro mundo real com HP baixo/
    errado só por causa do tempo que passou na instância."""
    snap = ws.world.get_component(eid, InstanceProgressionSnapshot)
    if snap is None:
        return

    char = ws.world.get_component(eid, CharacterStats)
    if char is not None:
        char.level            = snap.real_level
        char.current_xp       = snap.real_current_xp
        char.xp_to_next_level = snap.real_xp_to_next_level
        char.strength     = snap.real_strength
        char.intelligence = snap.real_intelligence
        char.agility      = snap.real_agility
        char.vitality     = snap.real_vitality
        char.defense      = snap.real_defense

    ws.world.add_component(eid, snap.real_talent_tree)
    ws.world.add_component(eid, snap.real_player_skills)
    ws.world.add_component(eid, snap.real_wallet)
    ws.world.add_component(eid, snap.real_inventory)
    ws.world.add_component(eid, snap.real_equipment)
    if snap.real_permanent_stats is not None:
        ws.world.add_component(eid, snap.real_permanent_stats)
    else:
        ws.world.remove_component(eid, PermanentStats)

    ws.world.remove_component(eid, InstanceProgressionSnapshot)

    ws._apply_talent_modifiers(eid, snap.real_talent_tree.allocated)
    ws._apply_equipment_modifiers(eid)

    cs = ws.world.get_component(eid, CombatStats)
    if cs is not None:
        cs.current_hp = cs.max_hp

    if char is not None:
        _push_stats_update(ws, eid, char, snap.real_talent_tree, in_instance=False,
                           inv=snap.real_inventory, equip=snap.real_equipment,
                           ps=snap.real_player_skills)


def _push_stats_update(ws, eid: int, char: CharacterStats, tt: TalentTree, *,
                        in_instance: bool = None, inv: Inventory = None,
                        equip: Equipment = None, ps: PlayerSkills = None) -> None:
    """Empurra STATS_UPDATE privado pro dono com o que mudou ao
    entrar/sair da progressão normalizada — sem isso, o client nunca
    fica sabendo (bug real relatado pelo usuário, 01/08/2026: "o level
    do Player não está aparecendo 1"). `level` é um campo NOVO no
    schema de `queue_stats_update` (server/world_server.py) — override
    direto, não passa pelo `process_levelups` orientado a XP que o
    client já usa pro level-up normal. `hp`/`hp_max` refletem o
    `CombatStats` já recalculado por `_apply_talent_modifiers`/
    `_apply_equipment_modifiers` (chamados ANTES desta função, nos dois
    call sites). `gold`/`talent_points` cobrem os 2 outros campos que
    `queue_stats_update` já sabe sincronizar de graça.

    `in_instance`/`inv`/`equip` (01/08/2026, pedido do usuário — painel
    HUD dedicado de inventário de instância): fecham o gap de
    inventário/equipamento documentado abaixo. `inv_snapshot`/
    `equip_snapshot` serializam via `WorldServer._item_data_from_obj`
    (mesmo formato de BUY_RESULT/save de equipamento — cliente
    desserializa com `self._item_from_data`, já usado por BUY_RESULT/
    TRADE_STATE). `in_instance` liga/desliga o painel HUD novo no
    cliente (`InstanceInventoryUIState.active`).

    `talent_allocated` (02/08/2026, pedido do usuário — "a cada level os
    pontos de talento acumulam, mesmo eu usando"): fecha o gap de
    `allocated{}` documentado abaixo — sem isso, o `TalentTree` LOCAL do
    cliente nunca era trocado/resetado (só o servidor tinha
    `enter_/exit_normalized_progression` de verdade), então o painel de
    talentos do cliente continuava mostrando/mutando a alocação REAL
    (pré-instância) enquanto o servidor validava contra a árvore da
    INSTÂNCIA (vazia) — o cliente parecia "ganhar" pontos de volta a cada
    alocação porque `ui/talent_system.py::apply_talent_effects()` reembolsa
    tudo quando `chosen_build` não bate com a build esperada, e o
    `available_points` da instância (via `talent_points` abaixo) só
    corrigia o TOTAL, nunca a alocação em si.

    `strength`/`intelligence`/`agility`/`vitality`/`defense` (02/08/2026,
    bug real relatado pelo usuário: "comprei um item e o HP ficou
    140/380" — 380 era o max_hp REAL, fora da instância): gap simétrico
    ao de `talent_allocated` acima. `CombatStats.max_hp`/outros
    `base_*` no cliente são recalculados do ZERO (a partir de
    `CharacterStats` + `PermanentStats`) toda vez que
    `stat_fns.recalculate_combat_stats` roda de novo (equipar/desequipar
    item, qualquer add_modifier/remove_modifier) — client/
    inventory_handlers.py::_equip_item, chamado no auto-equip da compra
    de instância, é UM desses gatilhos. `hp`/`hp_max` (acima) só
    sobrescrevem o RESULTADO uma vez; sem sincronizar os atributos
    brutos que ALIMENTAM o próximo recálculo, o cliente guardava a
    força/vitalidade/etc REAIS (nunca resetadas localmente) e qualquer
    gatilho de recálculo trazia o max_hp real de volta — mesma classe de
    bug do `talent_allocated`/`skills_hotbar`, só que pra atributos
    brutos. Client aplica em `CharacterStats` E re-roda
    `apply_char_stats_to_combat` localmente (ver
    client/network_handlers.py::_handle_msg_stats_update) — `hp`/
    `hp_max` explícitos acima continuam tendo a palavra final.

    Gap restante, NÃO coberto aqui (sem mecanismo de push em massa
    existente pra isso — ver pesquisa em ARQUITETURA_ONLINE.md): hotbar/
    skills aprendidas completas. O client só aprende essas de verdade na
    próxima vez que logar (spawn_player já lê tudo do banco corretamente
    — a instância nunca escreve no banco)."""
    cs = ws.world.get_component(eid, CombatStats)
    wallet = ws.world.get_component(eid, Wallet)
    entry = {
        "player_eid": eid,
        "level": char.level,
        "talent_points": tt.available_points,
        "talent_allocated": dict(tt.allocated),
        "strength":     char.strength,
        "intelligence": char.intelligence,
        "agility":      char.agility,
        "vitality":     char.vitality,
        "defense":      char.defense,
    }
    if cs is not None:
        entry["hp"] = cs.current_hp
        entry["hp_max"] = cs.max_hp
    if wallet is not None:
        entry["gold"] = wallet.gold
    if in_instance is not None:
        entry["in_instance"] = in_instance
    if inv is not None:
        entry["inv_snapshot"] = [ws._item_data_from_obj(it) for it in inv.items]
        entry["inv_max_slots"] = inv.max_slots
    if equip is not None:
        entry["equip_snapshot"] = {
            slot: (ws._item_data_from_obj(it) if it is not None else None)
            for slot, it in equip.slots.items()
        }
    if ps is not None:
        # skills_hotbar/learned_skill_ids (02/08/2026, pedido do usuário —
        # a barra de ações continuava mostrando as skills REAIS dentro da
        # instância): diferente de item/equipamento, skill é 100% definida
        # por dado ESTÁTICO compartilhado (content/skill_config.py::
        # SKILL_CATALOG, cliente e servidor têm os dois) — só precisa
        # mandar os IDs, o cliente reconstrói via PlayerSkills._make_skill.
        entry["skills_hotbar"] = [s.skill_id if s else None for s in ps.skills]
        entry["learned_skill_ids"] = list(ps.learned_skill_ids)
    ws.queue_stats_update(entry)


def _process_instance_levelup(ws, eid: int) -> None:
    """Processa level-ups de instância pendentes (chamado por
    grant_instance_xp). Mesma forma de engine/stats_system.py::
    process_levelups, mas com curva/cap próprias (level cap 15) — não
    modifica a função real, que continua servindo o level persistente do
    personagem. Talento NÃO ganha pontos por level (02/08/2026, redesign
    — já vem pré-alocado no máximo desde `enter_normalized_progression`,
    ver docstring lá); só resta decidir QUANDO cada skill da
    INSTANCE_SKILL_UNLOCK_ORDER aparece."""
    char = ws.world.get_component(eid, CharacterStats)
    tt   = ws.world.get_component(eid, TalentTree)
    ps   = ws.world.get_component(eid, PlayerSkills)
    if char is None or tt is None or ps is None:
        return

    snap = ws.world.get_component(eid, InstanceProgressionSnapshot)
    real_slot_by_sid = _real_slot_by_skill_id(snap.real_player_skills if snap else None)
    reserved_slots = set(real_slot_by_sid.values())
    unlock_order = INSTANCE_SKILL_UNLOCK_ORDER.get(char.class_id, [])
    gains = CLASS_LEVEL_GAINS.get(char.class_id, {})
    leveled = False
    while (char.current_xp >= char.xp_to_next_level
           and char.level < INSTANCE_LEVEL_CAP):
        char.current_xp      -= char.xp_to_next_level
        char.level            += 1
        char.xp_to_next_level  = CharacterStats.xp_for_level(char.level)
        char.strength     += gains.get("strength", 0)
        char.intelligence += gains.get("intelligence", 0)
        char.agility      += gains.get("agility", 0)
        char.vitality     += gains.get("vitality", 0)
        char.defense      += gains.get("defense", 0)
        if char.level - 1 < len(unlock_order):
            _grant_instance_skill(ps, unlock_order[char.level - 1], real_slot_by_sid, reserved_slots)
        leveled = True

    if char.level >= INSTANCE_LEVEL_CAP:
        char.current_xp = 0   # decisão #9: XP além do cap é descartado, sem banking

    if leveled:
        cs   = ws.world.get_component(eid, CombatStats)
        perm = ws.world.get_component(eid, PermanentStats)
        if cs is not None:
            apply_char_stats_to_combat(char, cs, perm)
            sync_attack_interval(cs, ws.world.get_component(eid, Equipment))
            # Cura 100% ao subir de level (02/08/2026, bug real relatado
            # pelo usuário: "a cada level que o player ganha, em vez de
            # subir o current_hp junto com o max_hp, só sobe max_hp") —
            # mesmo comportamento de engine/stats_system.py::
            # process_levelups (`cs.current_hp = cs.max_hp`) no jogo real,
            # que este módulo nunca replicava.
            cs.current_hp = cs.max_hp
        # Sem isso o dono nunca fica sabendo que subiu de level DENTRO da
        # instância (mesmo gap que enter_/exit_normalized_progression já
        # tinham antes do fix de 01/08/2026 — ver _push_stats_update).
        # ps aqui também garante que a NOVA skill desbloqueada neste level
        # (_grant_instance_skill acima) chegue na barra de ações do cliente.
        _push_stats_update(ws, eid, char, tt, ps=ps)


def grant_instance_xp(ws, eid: int, amount: int) -> None:
    """Concede XP de instância a `eid` (kill de minion/torre/player dentro
    da progressão normalizada — chamado por `server/world_server.py` no
    lugar do XP real quando o destinatário `is_in_normalized_progression`,
    01/08/2026). No-op se o entity não estiver em progressão normalizada,
    se já estiver no cap, ou se `amount` não for positivo."""
    if amount <= 0 or not is_in_normalized_progression(ws, eid):
        return
    char = ws.world.get_component(eid, CharacterStats)
    if char is None or char.level >= INSTANCE_LEVEL_CAP:
        return
    char.current_xp += amount
    _process_instance_levelup(ws, eid)


def grant_instance_gold(ws, eid: int, amount: int) -> None:
    """Concede ouro de instância a `eid` — recompensa de kill (decisão do
    usuário, 01/08/2026: "quando um oponente morre deve ir automaticamente
    pro inventário do player que o matou", só pra quem dá o dano que
    MATA — ver `killer_eid`/`PendingDeath.killer_entity_id`, NUNCA
    `first_attacker_eid`/dono de loot, que é "quem bateu primeiro").
    No-op se o entity não estiver em progressão normalizada ou `amount`
    não for positivo. Empurra STATS_UPDATE pro dono ver o gold subir na
    hora (mesmo canal que compra/venda de loja já usa)."""
    if amount <= 0 or not is_in_normalized_progression(ws, eid):
        return
    wallet = ws.world.get_component(eid, Wallet)
    if wallet is None:
        return
    wallet.gold += amount
    ws.queue_stats_update({"player_eid": eid, "gold": wallet.gold})
