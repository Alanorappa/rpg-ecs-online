"""
map_markers.py — Fonte única de "pontos de interesse pra mostrar no mapa (M)
e no minimapa": morte, quest givers, treinadores, mercadores... Consumida
por MapOverlay.render() e Minimap.render(), que só sabem desenhar a lista —
não conhecem regra de nenhum tipo específico de marcador.

Reaproveita a MESMA lógica de estado que já desenha indicador acima da
cabeça do NPC no mundo (QuestDialogSystem.marker_for, TrainerSystem
render_world) — nunca duplica regra.

Ícones: convenção map_<algo>.png em assets/icons/ (mesmo padrão skill_/
item_/enemy_ já usado por IconManager). Se o arquivo ainda não existe,
quem desenha (MapOverlay/Minimap) cai pro fallback (cor + símbolo).
"""
from __future__ import annotations
from dataclasses import dataclass, replace as dataclass_replace

# Tamanho de exibição (px de tela, fixo — nunca escala com zoom do mapa
# nem com tp do minimapa, ver ARQUITETURA_ONLINE.md 23.1/23.2) dos ícones
# de marcador. Era 8 (tamanho real do arquivo-fonte, sem upscale nenhum —
# pedido original do usuário), mas ficou minúsculo/ilegível de verdade em
# jogo (treinadores irreconhecíveis) — 16 (2x, nearest-neighbor, ainda
# pixel-perfect) resolve sem voltar a "esticar" feito antes do fix
# original. Ver conversa 11/07/2026.
MAP_ICON_SIZE = 16


@dataclass(frozen=True)
class MapMarker:
    tile_x:          int
    tile_y:          int
    icon_name:       str
    fallback_color:  tuple
    fallback_symbol: str = ""   # "" = só o círculo, sem glifo


# Cor de fallback por classe — mesma paleta usada nos treinadores hoje
# (guerreiro=vermelho/combate, mago=azul/arcano, arqueiro=verde/natureza).
_TRAINER_FALLBACK_COLORS = {
    "guerreiro": (200,  60,  60),
    "mago":      (80,  140, 220),
    "arqueiro":  (90,  190,  90),
}
_MERCHANT_FALLBACK_COLOR = (210, 175,  60)
# Mesma cor do canal de chat "grupo" (server/session.py::_handle_chat,
# [140,230,150]) — pedido explícito do usuário (06/10/2026): "a cor do
# sinal no minimapa... pode seguir a mesma cor da letra no chat em
# grupo". Única cor pra TODOS os companheiros (não distingue indivíduo
# por cor) — decisão do usuário, "a cor de todos pode ser igual".
_PARTY_MEMBER_FALLBACK_COLOR = (140, 230, 150)
_DEATH_FALLBACK_COLOR    = (150, 150, 150)


def collect_markers(world, player_entity: int, quest_dialog,
                    spawn_points: dict) -> list[MapMarker]:
    """Varre os dados ESTÁTICOS do mapa (spawn_points) e devolve todos os
    marcadores ativos pro player local.

    quest_dialog: instância de QuestDialogSystem (fornece marker_for_static()).

    NÃO filtra por Visible (tag dinâmica de FoW/linha de visão) — pedido
    do usuário 11/07/2026: esses ícones servem pra GUIAR o player (mapa/
    minimapa), diferente do indicador acima da cabeça no MUNDO (esse sim
    exige visão direta, faz sentido só aparecer quando o NPC está visível
    na tela). Um quest giver do outro lado de uma parede, ou fora do
    alcance de FoW atual mas na mesma zona/mapa carregado, deve continuar
    aparecendo no mapa/minimapa — é exatamente o cenário em que o jogador
    mais precisa do ícone pra se guiar. Morte segue a mesma regra por
    consistência: é a própria posição conhecida do player, sempre
    mostrada enquanto o corpo existir.

    Fonte TROCADA pra dados estáticos (06/10/2026, bug real relatado pelo
    usuário — PROBLEMAS_ARQUITETURA.md): antes varria o World AO VIVO
    (`world.get_entities_with(TileMovement, QuestGiver/Trainer/Merchant)`)
    — NPC de serviço só existe localmente enquanto dentro da AOI (mesmo
    spawn/despawn de mob), então o ícone sumia assim que o player se
    afastava, exatamente o oposto do que esta docstring já dizia ser a
    intenção. `spawn_points` é a MESMA fonte que já alimenta os círculos
    de rastreio de quest (`collect_quest_circles`) — sempre disponível,
    independente de AOI.
    """
    from engine.components import GhostState

    markers: list[MapMarker] = []

    gst = world.get_component(player_entity, GhostState)
    if gst is not None and gst.corpse_tx != -1:
        markers.append(MapMarker(gst.corpse_tx, gst.corpse_ty,
                                  "map_death", _DEATH_FALLBACK_COLOR))

    # quest_givers: (x, y, name, quest_ids, turn_in_ids, level, profession)
    # — ver engine/map_loader.py::_merge_entities_json.
    for qg in spawn_points.get("quest_givers", []):
        x, y, name, quest_ids, turn_in_ids = qg[0], qg[1], qg[2], qg[3], qg[4]
        marker = quest_dialog.marker_for_static(name, quest_ids, turn_in_ids)
        if marker is None:
            continue
        icon_name, color, symbol = marker
        markers.append(MapMarker(x, y, icon_name, color, symbol))

    # trainers: (x, y, name, class_id, quest_ids, turn_in_ids, level,
    # profession, unlock_quest_id)
    for tr in spawn_points.get("trainers", []):
        x, y, class_id = tr[0], tr[1], tr[3]
        color = _TRAINER_FALLBACK_COLORS.get(class_id, (200, 200, 200))
        markers.append(MapMarker(x, y, f"map_trainer_{class_id}", color, "T"))

    # merchants: (x, y, name, shop_id, level, profession)
    for m in spawn_points.get("merchants", []):
        markers.append(MapMarker(m[0], m[1], "map_merchant",
                                  _MERCHANT_FALLBACK_COLOR, "$"))

    return markers


def collect_party_markers(party_member_positions: dict) -> list[MapMarker]:
    """Marcador de posição de cada companheiro de grupo pro mapa (M)/
    minimapa — pedido do usuário (06/10/2026): "ver a localização dos
    companheiros de grupo... independente de estarem na fog ou não".

    `party_member_positions`: dict eid→{"tx","ty","name"}, já filtrado
    pelo SERVIDOR pra só conter quem está no MESMO mapa (ver
    shared/messages.py::AOI_UPDATE "party_positions") — este função não
    filtra nada de novo, só converte formato. Ícone = `map_party_member`
    (fallback: círculo verde — mesma cor do chat de grupo — com a
    PRIMEIRA LETRA do nome, já que todos os companheiros compartilham a
    mesma cor por decisão do usuário)."""
    markers: list[MapMarker] = []
    for data in party_member_positions.values():
        name = data.get("name", "?")
        markers.append(MapMarker(
            data["tx"], data["ty"], "map_party_member",
            _PARTY_MEMBER_FALLBACK_COLOR, name[:1].upper() if name else "?"))
    return markers


def deconflict_positions(points: "list[tuple[float, float]]", min_dist: float) -> "list[tuple[float, float]]":
    """Recebe posições de TELA (não tile) já calculadas — centro de cada
    ícone — e devolve a mesma lista, na mesma ordem, com qualquer posição
    que colidiria (centro a menos de min_dist de outra já resolvida)
    empurrada pra um slot livre. Usado por MapOverlay/Minimap ANTES de
    desenhar, pra dois marcadores no mesmo tile (ou tiles vizinhos, quando
    o zoom deixa pouco espaço entre eles) não ficarem um em cima do outro.

    Busca em espiral (8 direções, raio crescente) a partir da posição
    original — determinístico, sem oscilação, custo desprezível pro número
    de marcadores esperado (dezenas, não milhares)."""
    import math
    placed: list[tuple[float, float]] = []
    result: list[tuple[float, float]] = []
    for x, y in points:
        nx, ny = x, y
        if any((nx - px) ** 2 + (ny - py) ** 2 < min_dist ** 2 for px, py in placed):
            for radius in range(1, 8):
                found = False
                for angle_deg in range(0, 360, 45):
                    rad = math.radians(angle_deg)
                    tx = x + radius * min_dist * math.cos(rad)
                    ty = y + radius * min_dist * math.sin(rad)
                    if all((tx - px) ** 2 + (ty - py) ** 2 >= min_dist ** 2 for px, py in placed):
                        nx, ny, found = tx, ty, True
                        break
                if found:
                    break
        placed.append((nx, ny))
        result.append((nx, ny))
    return result


# ── Rastreio de objetivo de quest no mapa/minimapa (03/09/2026) ────────────
# Fonte única de "onde desenhar o círculo do alvo de cada objetivo de quest
# ativa". Mesma filosofia do resto do arquivo: devolve dados prontos
# (QuestCircle), MapOverlay/Minimap só sabem desenhar — não conhecem regra
# de nenhum tipo de objetivo. Nunca reimplementar essa resolução em outro
# lugar (ex: um hit-test de tooltip que recalculasse a posição sozinho) —
# ver PROBLEMAS_ARQUITETURA.md pelo desenho completo do sistema.

# Tamanho em TILES não faz sentido nessas 2 telas (04/09/2026, pedido do
# usuário, depois de ver o círculo de raio real — 30 tiles — cobrindo boa
# parte do mapa no zoom alto do M) — 1 tile no jogo é sempre 32px, mas a
# escala de exibição do mapa/minimapa varia (zoom livre no M, janela fixa
# no minimapa), então "raio real em tiles" ou fica gigante (zoom alto) ou
# some (zoom baixo/minimapa). Todo círculo tem tamanho de TELA fixo — só a
# POSIÇÃO (tile_x/tile_y) continua em tile-space, convertida por cada
# renderer como qualquer outro marcador (mesmo princípio de
# `map_markers.py::MAP_ICON_SIZE`, que já era fixo desde sempre).
TARGET_MARKER_RADIUS_PX   = 15   # "30px de dimensão" (diâmetro) pedido do usuário
FRIENDLY_MARKER_RADIUS_PX = MAP_ICON_SIZE // 2   # mesmo tamanho do ícone de quest (!)/(?)
HARVESTABLE_OFFSET_PX     = 10   # deslocamento fixo (não mais em tiles) do
                                  # círculo de item coletável — mantém a
                                  # graça de não centralizar em cima da
                                  # posição real, sem depender de zoom


@dataclass(frozen=True)
class QuestCircle:
    """shape="circle": usa tile_x/tile_y (posição, tile-space) sempre, e
    EXATAMENTE UM dos dois raios:
    - `radius_tiles` > 0: raio REAL cadastrado no `{map}_entities.json`
      (spawn_zones/harvestable_zones — zona de verdade, sem limite,
      escala com o zoom igual o resto do mapa — pedido explícito do
      usuário 04/09/2026: "não é pra ter limite, o raio deve ser pego do
      raio do respawn").
    - `radius_px` (quando `radius_tiles == 0`): tamanho de TELA fixo —
      usado quando não existe uma "área real" (posição pontual: NPC,
      boneco de treino, item único, marcador de transição). `offset_px`
      (deslocamento, também fixo) só se aplica junto com `radius_px`.
    shape="rect": usa x0/y0/x1/y1 em tile-space (reach_tile — sempre
    retângulo, mesmo quando o objetivo é um ponto único, aí x0==x1 e
    y0==y1; representa uma área real do mundo, escala com o zoom).
    style: "hostile" (mob/spawn_zone/dummy) | "friendly" (NPC/treinador,
    desenhado com "...") | "zone" (item coletável) | "transition"
    (indicador de mudança de mapa)."""
    shape:           str
    tile_x:          float = 0.0
    tile_y:          float = 0.0
    radius_tiles:    float = 0.0
    radius_px:       float = 0.0
    offset_px:       "tuple[float, float]" = (0.0, 0.0)
    x0: int = 0
    y0: int = 0
    x1: int = 0
    y1: int = 0
    style:           str = "hostile"
    quest_title:     str = ""
    objective_label: str = ""


def _deterministic_offset(seed_x: int, seed_y: int, magnitude: float) -> "tuple[float, float]":
    """Vetor de deslocamento SEMPRE igual pro mesmo par (seed_x, seed_y) —
    usado pro círculo de harvestable fixo não ficar centralizado no item
    (pedido do usuário: centralizado entregaria a posição exata de cara).
    Determinístico (não usa random/hash() built-in, que varia por processo)
    pra minimapa e mapa cheio desenharem exatamente o mesmo deslocamento."""
    import math
    h = ((seed_x * 73856093) ^ (seed_y * 19349663)) & 0xFFFFFFFF
    angle = (h % 360) * math.pi / 180.0
    return (magnitude * math.cos(angle), magnitude * math.sin(angle))


def _item_in_list(loot_item: str, items: list) -> bool:
    for entry in items:
        key = entry[0] if isinstance(entry, tuple) else entry
        if key == loot_item:
            return True
    return False


def _find_spawn_zones(spawn_points: dict, race: str) -> "list[dict]":
    """Todas as zonas cadastradas pra essa raça — um mob pode ter vários
    pontos de spawn espalhados pelo mesmo mapa (ex: "Lobo" em 5 posições
    diferentes em map_1). Antes retornava só a PRIMEIRA zona encontrada —
    bug real reportado pelo usuário (04/09/2026): quest de matar Lobo só
    mostrava 1 dos 5 spawns, o resto ficava invisível no rastreio."""
    return [zone for zone in spawn_points.get("spawn_zones", [])
            if zone.get("race") == race]


# Identidade do boneco de treino — TEM que bater com
# `engine/entity_factory.py::create_training_dummy` (`EntityIdentity(name=
# "Boneco de treino", race="Mecânico", ...)`). Boneco de treino é 100%
# genérico pro jogo (`use_skill` com `on_dummy=True` só checa "foi usado
# em QUALQUER boneco", nunca em qual boneco específico) — por isso é
# tratado exatamente como um mob por nome/raça pro rastreio, SEM precisar
# de id próprio por instância (decisão revertida 04/09/2026: a 1ª versão
# exigia `params["dummy_id"]`, complexidade que o próprio jogo nunca
# pediu — ver PROBLEMAS_ARQUITETURA.md).
_TRAINING_DUMMY_NAME = "Boneco de treino"
_TRAINING_DUMMY_RACE = "Mecânico"


def _dummy_positions(spawn_points: dict) -> "list[tuple[int, int]]":
    return [(x, y) for x, y in spawn_points.get("training_dummies", [])]


def _hostile_target_circles(spawn_points: dict, target: str) -> "list[QuestCircle]":
    """Resolve o(s) círculo(s) de um alvo "físico" (mob OU boneco de
    treino) por nome/raça — usado por kill/auto_attack_hit/
    use_item_on_target e pelo fallback de collect_item. Um mesmo `target`
    nunca bate em spawn_zone E training_dummy ao mesmo tempo (nomes/raças
    não colidem), mas a busca é feita nos dois catálogos pra não exigir
    que quem escreve a quest saiba de antemão "isso é mob, isso é
    boneco" — o objetivo já distingue isso sozinho pelo texto do target."""
    zones = _find_spawn_zones(spawn_points, target)
    if zones:
        return _zone_circles(zones)
    if target in (_TRAINING_DUMMY_NAME, _TRAINING_DUMMY_RACE):
        return [QuestCircle(shape="circle", tile_x=x, tile_y=y,
                            radius_px=TARGET_MARKER_RADIUS_PX, style="hostile")
                for x, y in _dummy_positions(spawn_points)]
    return []


def _find_harvestable(spawn_points: dict, loot_item: str) -> "dict | None":
    for hv in spawn_points.get("harvestables", []):
        if _item_in_list(loot_item, hv.get("items", [])):
            return hv
    return None


def _find_harvestable_zone(spawn_points: dict, loot_item: str) -> "dict | None":
    for z in spawn_points.get("harvestable_zones", []):
        for sp in z.get("spawns", []):
            if _item_in_list(loot_item, sp.get("items", [])):
                return z
    return None


def _find_trainer(spawn_points: dict, trainer_id: str) -> "tuple | None":
    for t in spawn_points.get("trainers", []):
        if t[2] == trainer_id:   # (x, y, name, class_id, ...)
            return (t[0], t[1])
    return None


def _find_npc_by_name(spawn_points: dict, name: str) -> "tuple | None":
    for key in ("quest_givers", "merchants", "blacksmiths", "trainers"):
        for e in spawn_points.get(key, []):
            if e[2] == name:   # (x, y, name, ...) — mesma convenção nas 4 listas
                return (e[0], e[1])
    for c in spawn_points.get("combat_npcs", []):
        if c.get("name") == name:
            return (c["x"], c["y"])
    return None


def _find_transition_to(spawn_points: dict, target_map: str) -> "tuple | None":
    for tr in spawn_points.get("transitions", []):
        if tr.get("target_map") == target_map:
            return (tr["x"], tr["y"])
    return None


def _objective_target_map(obj, current_map_file: str) -> str:
    """reach_tile já usa `target` como filtro de mapa (convenção antiga,
    mantida) — os outros tipos usam o campo `map` novo. "" / "*" = mapa
    atual (sem cross-map)."""
    if obj.type == "reach_tile":
        return obj.target if obj.target and obj.target != "*" else current_map_file
    return obj.map or current_map_file


def _zone_circles(zones: "list[dict]") -> "list[QuestCircle]":
    return [QuestCircle(shape="circle", tile_x=z["x"], tile_y=z["y"],
                        radius_tiles=z["radius"], style="hostile") for z in zones]


def _resolve_objective_shapes(obj, spawn_points: dict) -> "list[QuestCircle]":
    """Resolve a posição/estilo do(s) ALVO(s) do objetivo, assumindo que
    está(ão) no mapa cujo `spawn_points` foi passado (cross-map já foi
    resolvido pelo chamador antes de chegar aqui). Devolve uma LISTA —
    um mob pode ter vários spawn_zones no mesmo mapa (ex: "Lobo" em 5
    posições diferentes), todas rastreáveis ao mesmo tempo, não só a
    1ª encontrada."""
    t = obj.type

    if t == "reach_tile" and obj.location:
        loc = obj.location
        if len(loc) == 2:
            x0 = x1 = loc[0]
            y0 = y1 = loc[1]
        else:
            x0, y0, x1, y1 = loc
        return [QuestCircle(shape="rect", x0=x0, y0=y0, x1=x1, y1=y1, style="hostile")]

    if t in ("kill", "auto_attack_hit", "use_item_on_target") and obj.target != "*":
        return _hostile_target_circles(spawn_points, obj.target)

    if t == "collect_item":
        if obj.target == "harvestables":
            hv = _find_harvestable(spawn_points, obj.loot_item)
            if hv is None:
                return []
            off = _deterministic_offset(hv["x"], hv["y"], HARVESTABLE_OFFSET_PX)
            return [QuestCircle(shape="circle", tile_x=hv["x"], tile_y=hv["y"],
                               radius_px=TARGET_MARKER_RADIUS_PX, offset_px=off, style="zone")]
        if obj.target == "harvestable_zones":
            z = _find_harvestable_zone(spawn_points, obj.loot_item)
            if z is None:
                return []
            return [QuestCircle(shape="circle", tile_x=z["x"], tile_y=z["y"],
                               radius_tiles=z["radius"], style="zone")]
        if obj.target != "*":
            return _hostile_target_circles(spawn_points, obj.target)
        return []

    if t == "use_skill" and obj.params.get("on_dummy"):
        # Boneco de treino: você USA a skill nele (interação física, igual
        # mob) — não é "conversa" (talk), fica no grupo visual do alvo
        # grande, não no grupo pequeno com "..." (decisão do usuário,
        # 04/09/2026, separado explicitamente do treinador/learn_skill).
        # Nunca exige QUAL boneco (não existe "dummy_id" — o próprio jogo
        # trata qualquer boneco como equivalente, `on_dummy` só checa se
        # o alvo TEM o componente TrainingDummy, nunca qual instância) —
        # circula TODOS os bonecos do mapa, mesmo espírito de várias
        # spawn_zones do mesmo mob.
        return [QuestCircle(shape="circle", tile_x=x, tile_y=y,
                           radius_px=TARGET_MARKER_RADIUS_PX, style="hostile")
                for x, y in _dummy_positions(spawn_points)]

    if t == "learn_skill":
        pos = _find_trainer(spawn_points, obj.params.get("trainer_id", ""))
        if pos is None:
            return []
        return [QuestCircle(shape="circle", tile_x=pos[0], tile_y=pos[1],
                           radius_px=FRIENDLY_MARKER_RADIUS_PX, style="friendly")]

    if t == "talk_to_npc" and obj.target != "*":
        pos = _find_npc_by_name(spawn_points, obj.target)
        if pos is None:
            return []
        return [QuestCircle(shape="circle", tile_x=pos[0], tile_y=pos[1],
                           radius_px=FRIENDLY_MARKER_RADIUS_PX, style="friendly")]

    return []   # kill/*, use_skill sem dummy, use_consumable, reach_level,
                # equip_item, use_skill sem on_dummy: sem alvo físico


def collect_quest_circles(world, player_entity: int, quest_system,
                          current_map_file: str, spawn_points: dict) -> "list[QuestCircle]":
    """Varre as quests ativas do player (mesmo corte do HUD lateral —
    `quest_system.MAX_HUD_QUESTS`/`_sorted_active_items` — nunca mais
    círculos no mapa do que quests visíveis no HUD) e devolve os círculos
    dos objetivos AINDA não completos. Objetivo completo (prog[i] >=
    obj.count) não aparece — já não precisa mais ser rastreado."""
    from engine.components import QuestLog
    from content.quests_data import QUESTS

    ql = world.get_component(player_entity, QuestLog)
    if ql is None or not ql.active:
        return []

    circles: "list[QuestCircle]" = []
    for qid, prog in quest_system._sorted_active_items(ql)[:quest_system.MAX_HUD_QUESTS]:
        qdef = QUESTS.get(qid)
        if qdef is None:
            continue
        for i, obj in enumerate(qdef.objectives):
            if i >= len(prog) or prog[i] >= obj.count:
                continue
            label = quest_system._obj_label(obj, prog[i])
            target_map = _objective_target_map(obj, current_map_file)

            if target_map != current_map_file:
                pos = _find_transition_to(spawn_points, target_map)
                if pos is None:
                    continue   # sem transição direta — não rastreável ainda
                circles.append(QuestCircle(
                    shape="circle", tile_x=pos[0], tile_y=pos[1],
                    radius_px=TARGET_MARKER_RADIUS_PX, style="transition",
                    quest_title=qdef.title, objective_label=label))
                continue

            for shape in _resolve_objective_shapes(obj, spawn_points):
                circles.append(dataclass_replace(shape, quest_title=qdef.title,
                                                 objective_label=label))
    return circles


# ── Desenho (compartilhado por Minimap.render/MapOverlay.render) ──────────
# Único lugar que sabe COMO desenhar um QuestCircle na tela — os dois
# consumidores só convertem tile→px (geometria própria de cada um, raio e
# zoom diferentes) e chamam isto. Nunca duplicar o desenho tracejado/
# translúcido/reticências em cada um dos dois arquivos.

QUEST_CIRCLE_COLOR = (230, 205, 60)   # amarelo — pedido do usuário, mesma
                                       # cor pro alvo real E pro marcador de
                                       # transição de mapa (03/09/2026)
QUEST_CIRCLE_FILL_ALPHA = 64   # 25% de 255 — pedido do usuário (04/09/2026,
                               # era 110/90 ≈ 43%/35%); mesmo valor pro
                               # círculo E pro retângulo (reach_tile), nunca
                               # 2 números divergentes pro mesmo preenchimento


def draw_quest_circle_marker(surface, circle: "QuestCircle", sx: int, sy: int,
                             radius_px: float, font) -> None:
    """Desenha um QuestCircle de shape="circle" já convertido pro espaço de
    tela (sx, sy, radius_px). style="friendly": círculo pequeno sólido +
    "...". Outros styles (hostile/zone/transition): tracejado + preenchimento
    translúcido (QUEST_CIRCLE_FILL_ALPHA), raio = radius_px."""
    import pygame
    from ui.ui_helpers import draw_dashed_circle, draw_translucent_filled_circle

    if circle.style == "friendly":
        r = max(3, int(radius_px))
        pygame.draw.circle(surface, QUEST_CIRCLE_COLOR, (sx, sy), r)
        glyph = font.render("...", False, (20, 20, 20))
        surface.blit(glyph, (sx - glyph.get_width() // 2, sy - glyph.get_height() // 2))
        return

    draw_translucent_filled_circle(surface, QUEST_CIRCLE_COLOR, (sx, sy), radius_px,
                                   alpha=QUEST_CIRCLE_FILL_ALPHA)
    draw_dashed_circle(surface, QUEST_CIRCLE_COLOR, (sx, sy), radius_px, width=2)


def draw_quest_rect_marker(surface, rect) -> None:
    """Desenha um QuestCircle de shape="rect" (reach_tile) já convertido
    pro espaço de tela — mesmo tratamento visual (tracejado + preenchimento
    translúcido) dos círculos, só que retangular."""
    from ui.ui_helpers import draw_dashed_rect
    import pygame
    tmp = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
    tmp.fill((*QUEST_CIRCLE_COLOR, QUEST_CIRCLE_FILL_ALPHA))
    surface.blit(tmp, rect.topleft)
    draw_dashed_rect(surface, QUEST_CIRCLE_COLOR, rect, width=2)


def quest_circle_hit_test(circle: "QuestCircle", sx: int, sy: int,
                          radius_px: float, mouse_pos: tuple) -> bool:
    """Hit-test do hover pro tooltip — MESMA geometria (sx, sy, radius_px)
    já usada pra desenhar, nunca recalculada à parte (mesmo padrão de
    `ui/systems.py::_build_effects_row`)."""
    mx, my = mouse_pos
    r = max(4, int(radius_px))   # piso pra friendly (raio pequeno) continuar clicável
    return (mx - sx) ** 2 + (my - sy) ** 2 <= r ** 2


def quest_rect_hit_test(rect, mouse_pos: tuple) -> bool:
    return rect.collidepoint(mouse_pos)
