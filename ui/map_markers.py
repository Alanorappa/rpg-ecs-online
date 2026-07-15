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
from dataclasses import dataclass

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
_DEATH_FALLBACK_COLOR    = (150, 150, 150)


def collect_markers(world, player_entity: int, quest_dialog) -> list[MapMarker]:
    """Varre o mundo e devolve todos os marcadores ativos pro player local.

    quest_dialog: instância de QuestDialogSystem (fornece marker_for()).

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
    """
    from engine.components import GhostState, QuestGiver, Trainer, Merchant, TileMovement

    markers: list[MapMarker] = []

    gst = world.get_component(player_entity, GhostState)
    if gst is not None and gst.corpse_tx != -1:
        markers.append(MapMarker(gst.corpse_tx, gst.corpse_ty,
                                  "map_death", _DEATH_FALLBACK_COLOR))

    for eid, tm, _giver in world.get_entities_with(TileMovement, QuestGiver):
        marker = quest_dialog.marker_for(eid)
        if marker is None:
            continue
        icon_name, color, symbol = marker
        markers.append(MapMarker(tm.current_tile_x, tm.current_tile_y,
                                  icon_name, color, symbol))

    for eid, tm, trainer in world.get_entities_with(TileMovement, Trainer):
        color = _TRAINER_FALLBACK_COLORS.get(trainer.class_id, (200, 200, 200))
        markers.append(MapMarker(tm.current_tile_x, tm.current_tile_y,
                                  f"map_trainer_{trainer.class_id}", color, "T"))

    for eid, tm, _merch in world.get_entities_with(TileMovement, Merchant):
        markers.append(MapMarker(tm.current_tile_x, tm.current_tile_y,
                                  "map_merchant", _MERCHANT_FALLBACK_COLOR, "$"))

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
