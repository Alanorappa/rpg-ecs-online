"""
tests/test_fatiador_de_corpos.py — generalização do padrão do piloto do
Fase 3 (Calamidade Flamejante) pro Fatiador de Corpos (07/08/2026, ver
PROBLEMAS_ARQUITETURA.md §13). Sem componente novo (não usa `Channeling`
— é self-centered, dano físico, sem etapa de mira, formato genuinamente
diferente) — só a mesma disciplina: nenhum literal duplicado fora do
catálogo, cliente nunca aplica dano de verdade.

Cobre:
  - `SkillSystem.update()` (cliente, ui/systems.py) não chama mais
    `_fatiador_aoe_tick` — só decrementa o timer (necessário pra barra de
    canalização na hotbar, client/hotbar_handlers.py). Prova sintética:
    alvo local com Enemy+CombatStats dentro do raio não perde HP mesmo
    depois de várias janelas de tick.
  - O reset de `fatiador_tick` (tanto client quanto server) usa
    `skill.params["tick_interval"]` de verdade, não o literal `1.0`
    hardcoded que existia nos dois lugares antes — prova diferencial via
    catálogo mudado.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, authorize_skill, run_ticks, set_entity_tile
from content.skill_config import SKILL_CATALOG
from engine.components import CharacterStats, CombatStats, PlayerSkills, CombatState, Visible, MapLocation
from engine.entity_factory import create_enemy


class TestFatiadorTickIntervalDoCatalogo(unittest.TestCase):
    """Servidor: reset de fatiador_tick usa skill.params, não 1.0 hardcoded."""

    def setUp(self):
        self.ws = make_world_server()
        self.warrior = spawn_player(self.ws, "fat_srv", 130, 374, class_id="guerreiro")
        authorize_skill(self.ws, self.warrior, "fatiador_de_corpos")

    def test_reset_usa_tick_interval_do_catalogo_nao_literal_1(self):
        original = SKILL_CATALOG["fatiador_de_corpos"]["params"]["tick_interval"]
        SKILL_CATALOG["fatiador_de_corpos"]["params"]["tick_interval"] = 3.7
        try:
            char = self.ws.world.get_component(self.warrior, CharacterStats)
            # Simula "acabou de tickar" — próximo tick do loop do servidor
            # deve resetar fatiador_tick pro valor do catálogo, não 1.0.
            char.fatiador_timer = 5.0
            char.fatiador_tick  = 0.01
            run_ticks(self.ws, 1)  # 1 tick real (dt do servidor) cruza o zero
            self.assertAlmostEqual(char.fatiador_tick, 3.7, delta=0.06)
        finally:
            SKILL_CATALOG["fatiador_de_corpos"]["params"]["tick_interval"] = original

    def test_ativacao_real_usa_duration_e_tick_interval_do_catalogo(self):
        params = SKILL_CATALOG["fatiador_de_corpos"]["params"]
        sid = self.ws._player_eid_to_sid[self.warrior]
        self.ws.queue_skill(sid, "fatiador_de_corpos", -1, 0.0, 0.0, 0)
        run_ticks(self.ws, 1)
        char = self.ws.world.get_component(self.warrior, CharacterStats)
        self.assertAlmostEqual(char.fatiador_timer, params["duration"], delta=0.06)


class TestFatiadorClienteNaoAplicaDanoLocal(unittest.TestCase):
    """Cliente: SkillSystem.update() decrementa o timer (pra UI) mas nunca
    mais chama _fatiador_aoe_tick — prova sintética com alvo local que a
    query antiga teria acertado."""

    def test_alvo_local_enemy_combatstats_nao_perde_hp(self):
        from engine.world import World
        from ui.systems import SkillSystem
        from engine.components import Position, Enemy, TileMovement

        world  = World()
        screen = pygame.Surface((100, 100))
        caster = world.create_entity()
        char = CharacterStats(class_id="guerreiro")
        char.fatiador_timer = 5.0
        char.fatiador_tick  = 0.05
        world.add_component(caster, char)
        world.add_component(caster, TileMovement())
        ps = PlayerSkills()
        from content.skill_config import SKILL_CATALOG as _SC
        sk = PlayerSkills._make_skill("fatiador_de_corpos", _SC)
        ps.skills[0] = sk
        world.add_component(caster, ps)

        sysobj = SkillSystem(world, caster, screen)

        target = world.create_entity()
        world.add_component(target, Position(x=0.0, y=0.0))
        world.add_component(target, TileMovement())  # mesmo tile do caster (0,0)
        world.add_component(target, Enemy())
        tcs = CombatStats()
        tcs.current_hp = 100
        tcs.max_hp     = 100
        world.add_component(target, tcs)

        for _ in range(10):  # 0.5s a dt=0.05 -> cruza tick_interval (1.0) ou não, mas nunca aplica dano
            sysobj.update(events=[], dt=0.05)

        self.assertEqual(tcs.current_hp, 100,
                         "SkillSystem.update() aplicou dano local do Fatiador — código morto voltou")
        # Timer continua decrementando de verdade (necessário pra barra da hotbar)
        self.assertLess(char.fatiador_timer, 5.0)


class TestFatiadorTickIncluiSidParaAnimacaoDeGolpe(unittest.TestCase):
    """10/09/2026, pedido do usuário — animação de golpe em loop durante
    a duração real da skill (client-side, engine/character_animation.py
    ::_LOOPING_ATTACK_SKILLS) depende do cliente saber que cada tick é
    do Fatiador de Corpos especificamente. Sem `sid` no payload de
    `_combat_this_tick`, o cliente nunca identificaria a skill (bug real
    pego implementando — o campo simplesmente não existia nesse ponto)."""

    def setUp(self):
        self.ws = make_world_server()
        self.warrior = spawn_player(self.ws, "fat_sid_srv", 130, 374, class_id="guerreiro")
        authorize_skill(self.ws, self.warrior, "fatiador_de_corpos")
        self.mob = create_enemy(self.ws.world, 131, 374, faction="monstros_hostis")
        self.ws._mob_eids.add(self.mob)
        self.ws.world.add_component(self.mob, CombatState())
        self.ws.world.add_component(self.mob, Visible())
        self.ws.world.add_component(self.mob, MapLocation(self.ws._map_file))
        set_entity_tile(self.ws, self.mob, 131, 374)

    def test_tick_do_fatiador_manda_sid_no_combat_this_tick(self):
        """`_combat_this_tick` é limpo (`.clear()`) dentro do MESMO tick
        que o popula (WorldServer já monta e despacha o snapshot pros
        clientes antes do fim do tick) — por isso a lista real de
        produção é substituída por uma subclasse que guarda uma cópia
        justo antes de esvaziar, só pra este teste conseguir inspecionar
        o que teria sido mandado pro cliente."""
        captured = []

        class _SnapshotList(list):
            def clear(self):
                captured.append(list(self))
                super().clear()

        self.ws._combat_this_tick = _SnapshotList(self.ws._combat_this_tick)

        sid_session = self.ws._player_eid_to_sid[self.warrior]
        self.ws.queue_skill(sid_session, "fatiador_de_corpos", -1, 0.0, 0.0, 0)
        run_ticks(self.ws, 1)   # ativa a skill

        char = self.ws.world.get_component(self.warrior, CharacterStats)
        # Força o próximo tick de dano a acontecer agora.
        char.fatiador_tick = 0.0

        run_ticks(self.ws, 1)

        all_entries = [e for snapshot in captured for e in snapshot]
        fatiador_entries = [e for e in all_entries
                           if e.get("attacker") == self.warrior and e.get("target") == self.mob]
        self.assertTrue(fatiador_entries, "esperava pelo menos 1 entrada de dano do Fatiador neste tick")
        for entry in fatiador_entries:
            self.assertEqual(entry.get("sid"), "fatiador_de_corpos")


if __name__ == "__main__":
    unittest.main(verbosity=2)
