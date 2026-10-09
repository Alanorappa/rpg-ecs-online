"""
tests/test_quest_marker_above_remote_npc_name.py
Bug real relatado pelo usuário via screenshot (09/10/2026, ver
PROBLEMAS_ARQUITETURA.md): o marcador de quest/correio acima da cabeça
de um NPC SINCRONIZADO VIA REDE (a imensa maioria em jogo online de
verdade — client/remote_entity_handlers.py::_draw_mob_hp_bars) aparecia
ABAIXO do nome, não acima — porque game.py chamava
quest_dialog.render_world()/mail_system.render_world() ANTES de
_draw_mob_hp_bars() no loop de frame, e WORLD_LABELS empilha por ORDEM
DE CHAMADA (quem empilha por último fica no topo) — badge/nome do NPC
remoto, adicionados DEPOIS, acabavam por cima do ícone de quest.

Só o caminho de NPC LOCAL/offline (ui/render_system.py::RenderSystem,
chamado ANTES de quest_dialog.render_world()) já tinha a ordem certa —
por isso o bug só aparecia em jogo online de verdade, nunca num teste
sintético que só monta RenderSystem.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()
pygame.display.set_mode((320, 240))

from engine.world import World
from engine.components import Position, Renderable, RemoteEntityMeta, EntityIdentity, QuestGiver
from client.network_handlers import NetworkHandlers as _NH
from client.remote_entity_handlers import RemoteEntityHandlers as _REH
from ui.quest_system import QuestSystem, QuestDialogSystem
from ui.world_labels import WORLD_LABELS


class _Fixture(_NH, _REH):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._remote_mobs = {}


class TestOrdemCorretaChamaQuestDepoisDeDrawMobHpBars(unittest.TestCase):
    """Verifica o CONTRATO que game.py precisa respeitar: quem monta o
    frame tem que chamar quest_dialog.render_world()/mail_system.
    render_world() DEPOIS de _draw_mob_hp_bars() — nunca antes. Reproduz
    o mecanismo exato do bug (ordem de empilhamento em WORLD_LABELS),
    não só um cenário sintético genérico."""

    def setUp(self):
        WORLD_LABELS._pending.clear()
        WORLD_LABELS._stack_offset.clear()

    def _build_remote_quest_giver(self):
        world = World()
        player = world.create_entity()
        fx = _Fixture(world, player)
        fx._zoom_surf = pygame.Surface((200, 200))
        fx._nameplate_mode = 0
        fx.font_sm = pygame.font.Font(None, 16)

        eid = world.create_entity()
        x, y = 300, 300
        world.add_component(eid, Position(x=x, y=y))
        world.add_component(eid, Renderable(color=(255, 215, 0), width=28, height=28))
        world.add_component(eid, RemoteEntityMeta(server_eid=eid, hp=100, hp_max=100))
        world.add_component(eid, EntityIdentity(name="Andre Von Han'Duch", race="humano",
                                                entity_class="npc", level=60))
        world.add_component(eid, QuestGiver(quest_ids=("qualquer_quest",), turn_in_ids=()))
        fx._remote_mobs[eid] = eid

        qs = QuestSystem(world, player)
        qd = QuestDialogSystem(world, player, pygame.Surface((200, 200)), qs)
        # Marker fixo (não depende de dados reais de quest) — só a ORDEM
        # de chamada importa pra este teste, não o estado da quest.
        qd.marker_for = lambda npc_id: ("map_quest_available", qd.COL_GOLD, "!")
        return fx, qd, eid

    def test_chamando_na_ordem_certa_quest_fica_acima_do_nome(self):
        fx, qd, eid = self._build_remote_quest_giver()

        # Ordem CORRETA (pós-fix): _draw_mob_hp_bars primeiro, quest depois.
        fx._draw_mob_hp_bars(0, 0)
        qd.render_world(0, 0, zoom=1.0)

        entries = [e for e in WORLD_LABELS._pending]
        self.assertGreaterEqual(len(entries), 2, "esperava pelo menos nome+ícone de quest")

        def _sy(entry):
            world_x, world_y, surf, gap = entry
            return (world_y - 0) * 1.0 - gap - surf.get_height()

        # Quest icon é o ÚLTIMO item enfileirado (quest chamado por último).
        quest_sy = _sy(entries[-1])
        # Qualquer outro item (badge/nome do NPC remoto) tem sy MAIOR
        # (mais abaixo na tela) que o ícone de quest.
        for other in entries[:-1]:
            self.assertLess(quest_sy, _sy(other),
                            "ícone de quest deveria renderizar ACIMA (sy menor) do nome/badge do NPC")

    def test_chamando_na_ordem_errada_quest_fica_abaixo_do_nome_regressao(self):
        """Prova NEGATIVA (revert-to-confirm manual): com a ordem ERRADA
        (a que causava o bug original), o ícone de quest fica ABAIXO do
        nome — se este teste um dia passar a FALHAR sozinho é sinal de
        que a geometria de WORLD_LABELS mudou, não que o bug sumiu."""
        fx, qd, eid = self._build_remote_quest_giver()

        # Ordem ERRADA (pré-fix): quest primeiro, _draw_mob_hp_bars depois.
        qd.render_world(0, 0, zoom=1.0)
        fx._draw_mob_hp_bars(0, 0)

        entries = [e for e in WORLD_LABELS._pending]

        def _sy(entry):
            world_x, world_y, surf, gap = entry
            return (world_y - 0) * 1.0 - gap - surf.get_height()

        quest_sy = _sy(entries[0])   # quest foi o PRIMEIRO enfileirado aqui
        for other in entries[1:]:
            self.assertGreater(quest_sy, _sy(other),
                               "com a ordem errada, quest deveria ficar ABAIXO (sy maior)")


class TestGamePyChamaNaOrdemCerta(unittest.TestCase):
    """Guarda direta contra reordenar sem querer de volta — game.py
    PRECISA chamar _draw_mob_hp_bars() antes de
    quest_dialog.render_world()/mail_system.render_world() dentro do
    mesmo frame, sempre (ver os 2 testes acima pro MECANISMO de por quê).
    """

    def test_draw_mob_hp_bars_vem_antes_de_quest_e_correio_no_frame(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(project_root, "game.py"), encoding="utf-8") as f:
            src = f.read()

        idx_hp_bars = src.index("self._draw_mob_hp_bars(cam_x, cam_y)")
        idx_quest = src.index("self._quest_dialog.render_world(cam_x, cam_y, self._zoom)")
        idx_mail = src.index("self._mail_system.render_world(cam_x, cam_y)")

        self.assertLess(idx_hp_bars, idx_quest,
                        "_draw_mob_hp_bars precisa ser chamado ANTES de quest_dialog.render_world()")
        self.assertLess(idx_hp_bars, idx_mail,
                        "_draw_mob_hp_bars precisa ser chamado ANTES de mail_system.render_world()")


if __name__ == "__main__":
    unittest.main()
