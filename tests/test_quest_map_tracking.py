"""
tests/test_quest_map_tracking.py — Testes do resolvedor de rastreio de
quest no mapa/minimapa (ui/map_markers.py::collect_quest_circles).

Reproduz o desenho combinado com o usuário (03/09/2026, PROBLEMAS_
ARQUITETURA.md): posição/estilo do círculo por tipo de objetivo, corte
igual ao HUD lateral (MAX_HUD_QUESTS/_sorted_active_items), objetivo já
completo não aparece, cross-map vira marcador de transição.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

import unittest

from engine.world import World
from engine.components import QuestLog
from content.quests_data import ObjectiveDef, QuestDef, QuestReward
from ui.map_markers import (collect_quest_circles, QuestCircle,
                            TARGET_MARKER_RADIUS_PX, FRIENDLY_MARKER_RADIUS_PX)
from ui.quest_system import QuestSystem


_SPAWN_POINTS = {
    "spawn_zones": [
        {"x": 80, "y": 40, "radius": 30, "race": "Elemental"},
        {"x": 10, "y": 10, "radius": 6,  "race": "Urso"},
    ],
    "harvestables": [
        {"x": 50, "y": 50, "name": "Caixa", "items": ["semente_rara"], "coins": 0},
    ],
    "harvestable_zones": [
        {"x": 60, "y": 60, "radius": 8, "spawns": [
            {"name": "Vomito", "items": ["vomito_zumbi"]},
        ]},
    ],
    "training_dummies": [
        (120, 387, "dummy_01"),
        (120, 388, "dummy_02"),
    ],
    "trainers": [
        (115, 393, "Avido Faseo", "guerreiro", (), (), 1, "Guerreiro"),
    ],
    "quest_givers": [
        (100, 100, "Missiveiro Zeca", (), (), 1, "Missiveiro"),
    ],
    "merchants": [],
    "blacksmiths": [],
    "combat_npcs": [],
    "transitions": [
        {"x": 12, "y": 316, "target_map": "maps/map_cave_west.csv", "target_x": 78, "target_y": 19},
    ],
}


def _make_qs_world(objectives, prog):
    world = World()
    player = world.create_entity()
    ql = QuestLog()
    ql.active["q1"] = prog
    world.add_component(player, ql)
    qs = QuestSystem(world, player)
    qdef = QuestDef(title="Quest Teste", description="", objectives=tuple(objectives),
                    reward=QuestReward())
    return world, player, qs, {"q1": qdef}


def _circles_for(objectives, prog, current_map="maps/map_1.csv", quests=None, spawn_points=None):
    world, player, qs, qmap = _make_qs_world(objectives, prog)
    import content.quests_data as qd
    old_quests = dict(qd.QUESTS)
    qd.QUESTS.clear()
    qd.QUESTS.update(quests or qmap)
    try:
        return collect_quest_circles(world, player, qs, current_map,
                                     spawn_points if spawn_points is not None else _SPAWN_POINTS)
    finally:
        qd.QUESTS.clear()
        qd.QUESTS.update(old_quests)


class TestCollectQuestCircles(unittest.TestCase):
    def test_kill_target_asterisco_sem_circulo(self):
        circles = _circles_for([ObjectiveDef(type="kill", target="*", count=3)], [0])
        self.assertEqual(circles, [])

    def test_kill_com_target_resolve_spawn_zone(self):
        circles = _circles_for([ObjectiveDef(type="kill", target="Urso", count=1)], [0])
        self.assertEqual(len(circles), 1)
        c = circles[0]
        self.assertEqual(c.style, "hostile")
        # Spawn de mob usa o raio REAL cadastrado no {map}_entities.json
        # (sem limite, escala com o zoom — pedido explícito do usuário
        # 04/09/2026), não o tamanho fixo.
        self.assertEqual((c.tile_x, c.tile_y, c.radius_tiles), (10, 10, 6))
        self.assertEqual(c.radius_px, 0.0)

    def test_objetivo_completo_nao_aparece(self):
        circles = _circles_for([ObjectiveDef(type="kill", target="Urso", count=1)], [1])
        self.assertEqual(circles, [])

    def test_collect_item_harvestables_marcador_deslocado_e_raio_minimo(self):
        obj = ObjectiveDef(type="collect_item", target="harvestables",
                           loot_item="semente_rara", count=1)
        circles = _circles_for([obj], [0])
        self.assertEqual(len(circles), 1)
        c = circles[0]
        self.assertEqual(c.style, "zone")
        self.assertEqual(c.radius_px, TARGET_MARKER_RADIUS_PX)
        # Posição continua a real (50,50) — o deslocamento "não
        # centralizado" agora é em offset_px (aplicado pelo renderer, não
        # embutido em tile_x/tile_y, pra não depender do zoom).
        self.assertEqual((c.tile_x, c.tile_y), (50, 50))
        self.assertNotEqual(c.offset_px, (0.0, 0.0))

    def test_collect_item_harvestable_zone_usa_raio_cadastrado_centralizado(self):
        obj = ObjectiveDef(type="collect_item", target="harvestable_zones",
                           loot_item="vomito_zumbi", count=1)
        circles = _circles_for([obj], [0])
        self.assertEqual(len(circles), 1)
        c = circles[0]
        # harvestable_zones também usa raio real (mesma decisão do usuário
        # aplicada a spawn_zones — qualquer coisa com raio cadastrado de
        # verdade no JSON usa esse raio, sem limite).
        self.assertEqual((c.tile_x, c.tile_y, c.radius_tiles), (60, 60, 8))
        self.assertEqual(c.offset_px, (0.0, 0.0))

    def test_use_skill_on_dummy_resolve_por_dummy_id(self):
        """Boneco de treino entra no grupo visual do alvo grande (style
        "hostile"), não no grupo pequeno de "..." — decisão do usuário
        04/09/2026: dummy é interação física (usar skill nele), diferente
        de conversa (talk_to_npc/learn_skill)."""
        obj = ObjectiveDef(type="use_skill", target="bola_de_fogo", count=1,
                           params={"on_dummy": True, "dummy_id": "dummy_02"})
        circles = _circles_for([obj], [0])
        self.assertEqual(len(circles), 1)
        c = circles[0]
        self.assertEqual(c.style, "hostile")
        self.assertEqual(c.radius_px, TARGET_MARKER_RADIUS_PX)
        self.assertEqual((c.tile_x, c.tile_y), (120, 388))

    def test_use_skill_on_dummy_sem_dummy_id_nao_quebra(self):
        obj = ObjectiveDef(type="use_skill", target="bola_de_fogo", count=1,
                           params={"on_dummy": True})
        circles = _circles_for([obj], [0])
        self.assertEqual(circles, [])

    def test_use_skill_sem_on_dummy_sem_circulo(self):
        circles = _circles_for([ObjectiveDef(type="use_skill", target="executar", count=1)], [0])
        self.assertEqual(circles, [])

    def test_learn_skill_resolve_por_trainer_id(self):
        obj = ObjectiveDef(type="learn_skill", target="golpe_poderoso", count=1,
                           params={"trainer_id": "Avido Faseo"})
        circles = _circles_for([obj], [0])
        self.assertEqual(len(circles), 1)
        c = circles[0]
        self.assertEqual(c.style, "friendly")
        self.assertEqual(c.radius_px, FRIENDLY_MARKER_RADIUS_PX)
        self.assertEqual((c.tile_x, c.tile_y), (115, 393))

    def test_talk_to_npc_resolve_por_nome(self):
        obj = ObjectiveDef(type="talk_to_npc", target="Missiveiro Zeca", count=1)
        circles = _circles_for([obj], [0])
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].style, "friendly")

    def test_reach_tile_ponto_unico_vira_rect_degenerado(self):
        obj = ObjectiveDef(type="reach_tile", location=(5, 7), count=1)
        circles = _circles_for([obj], [0])
        self.assertEqual(len(circles), 1)
        c = circles[0]
        self.assertEqual(c.shape, "rect")
        self.assertEqual((c.x0, c.y0, c.x1, c.y1), (5, 7, 5, 7))

    def test_reach_tile_area_vira_rect(self):
        obj = ObjectiveDef(type="reach_tile", location=(1, 2, 9, 8), count=1)
        circles = _circles_for([obj], [0])
        c = circles[0]
        self.assertEqual((c.x0, c.y0, c.x1, c.y1), (1, 2, 9, 8))

    def test_reach_tile_mapa_diferente_usa_target_como_filtro_de_mapa(self):
        obj = ObjectiveDef(type="reach_tile", target="maps/map_cave_west.csv",
                           location=(78, 19), count=1)
        circles = _circles_for([obj], [0], current_map="maps/map_1.csv")
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].style, "transition")
        self.assertEqual((circles[0].tile_x, circles[0].tile_y), (12, 316))

    def test_objetivo_map_novo_cross_map_gera_marcador_de_transicao(self):
        obj = ObjectiveDef(type="talk_to_npc", target="Alguem", count=1,
                           map="maps/map_cave_west.csv")
        circles = _circles_for([obj], [0], current_map="maps/map_1.csv")
        self.assertEqual(len(circles), 1)
        c = circles[0]
        self.assertEqual(c.style, "transition")
        self.assertEqual(c.radius_px, TARGET_MARKER_RADIUS_PX)
        self.assertEqual((c.tile_x, c.tile_y), (12, 316))

    def test_cross_map_sem_transicao_direta_nao_quebra(self):
        obj = ObjectiveDef(type="talk_to_npc", target="Alguem", count=1,
                           map="maps/map_inexistente.csv")
        circles = _circles_for([obj], [0], current_map="maps/map_1.csv")
        self.assertEqual(circles, [])

    def test_corte_mesmo_do_hud_max_hud_quests(self):
        """Nunca mais quests com círculo no mapa do que no HUD lateral."""
        world = World()
        player = world.create_entity()
        ql = QuestLog()
        import content.quests_data as qd
        old_quests = dict(qd.QUESTS)
        qd.QUESTS.clear()
        try:
            n = QuestSystem.MAX_HUD_QUESTS + 2
            for i in range(n):
                qid = f"q{i}"
                ql.active[qid] = [0]
                qd.QUESTS[qid] = QuestDef(
                    title=f"Quest {i}", description="",
                    objectives=(ObjectiveDef(type="kill", target="Urso", count=1),),
                    reward=QuestReward())
            world.add_component(player, ql)
            qs = QuestSystem(world, player)
            circles = collect_quest_circles(world, player, qs, "maps/map_1.csv", _SPAWN_POINTS)
            self.assertLessEqual(len(circles), QuestSystem.MAX_HUD_QUESTS)
        finally:
            qd.QUESTS.clear()
            qd.QUESTS.update(old_quests)

    def test_sem_questlog_devolve_lista_vazia(self):
        world = World()
        player = world.create_entity()
        qs = QuestSystem(world, player)
        circles = collect_quest_circles(world, player, qs, "maps/map_1.csv", _SPAWN_POINTS)
        self.assertEqual(circles, [])


class TestUiHelpersDrawingPrimitivesNaoQuebramFuncoesVizinhas(unittest.TestCase):
    """Regressão real (03/09/2026): ao anexar draw_dashed_circle/draw_
    translucent_filled_circle/draw_dashed_rect/draw_box_tooltip no fim de
    ui/ui_helpers.py, o `return lines` de item_tooltip_lines ficou preso
    ENTRE o corpo dela e as novas funções — item_tooltip_lines passou a
    devolver None sempre (sem erro na hora, só quando algo tentava iterar
    o resultado) e um `return lines` órfão sobrou dentro de
    draw_box_tooltip (que por acaso também tem um parâmetro `lines`, então
    nem dava erro de sintaxe — só devolvia algo sem sentido, sem uso).
    Sintoma real do usuário: crash ao completar quest (hover no ícone de
    skill de recompensa → _flush_tooltip → 'NoneType' object is not
    iterable, client/tooltip_handlers.py)."""

    def test_item_tooltip_lines_devolve_lista_com_item_real(self):
        from content.item_table import ITEMS
        from ui.ui_helpers import item_tooltip_lines
        key = next(iter(ITEMS))
        item = ITEMS[key]()
        lines = item_tooltip_lines(item)
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

    def test_draw_box_tooltip_nao_devolve_nada(self):
        from ui.ui_helpers import draw_box_tooltip
        screen = pygame.display.get_surface()
        result = draw_box_tooltip(screen, pygame.font.Font(None, 16), pygame.font.Font(None, 14),
                                  10, 10, "Quest Teste", ["Objetivo (0/1)"])
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
