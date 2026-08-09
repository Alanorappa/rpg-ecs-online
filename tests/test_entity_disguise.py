"""
tests/test_entity_disguise.py — engine/entity_disguise.py (07/08/2026,
ver PROBLEMAS_ARQUITETURA.md §12/§13). Mecanismo genérico de aparência-
sobreposta-temporária: sprite parametrizado por `base_name` (não
hardcoded pra uma skill) + resolução de qual override está ativo numa
entidade via tabela de checadores (`_APPEARANCE_SOURCES`), não um
if/elif por skill. Cobre especificamente:
  - discover_sprite_variants()/get_animated_disguise_frame() reusáveis
    por qualquer base_name, não só "camuflagem".
  - get_active_appearance_override() resolve Camuflagem e Polimorfia
    a partir do estado que cada uma JÁ mantém (CombatStats/StatusEffects),
    sem componente novo nem sincronização de rede nova.
  - Mutuamente exclusivas na prática (decisão do usuário) — Camuflagem
    tem prioridade sobre Polimorfia quando as duas coincidem (não
    deveria acontecer no jogo real, mas o resolver precisa de uma ordem
    determinística mesmo assim).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

import unittest
from engine.world import World
from engine.components import CombatStats, StatusEffects, ActiveEffect
from engine.entity_disguise import (
    discover_sprite_variants, get_animated_disguise_frame,
    get_active_appearance_override,
)


class TestSpriteVariantsGenericPorBaseName(unittest.TestCase):
    """Confirma que as funções de sprite não têm 'camuflagem' hardcoded
    — qualquer base_name funciona igual, inclusive um que não existe."""

    def test_base_name_inexistente_retorna_lista_vazia(self):
        variants = discover_sprite_variants("skill_que_nao_existe_ainda")
        self.assertEqual(variants, [])

    def test_frame_de_base_name_sem_asset_retorna_none(self):
        frame = get_animated_disguise_frame("skill_que_nao_existe_ainda", "", False, 0)
        self.assertIsNone(frame)

    def test_cache_e_por_base_name_nao_global(self):
        """Duas bases diferentes não podem compartilhar cache — se
        compartilhassem, a 2ª leitura ecoaria o resultado da 1ª."""
        discover_sprite_variants("base_fake_a")
        discover_sprite_variants("base_fake_b")
        from engine.entity_disguise import _variants_cache
        self.assertIn("base_fake_a", _variants_cache)
        self.assertIn("base_fake_b", _variants_cache)
        self.assertIsNot(_variants_cache["base_fake_a"], _variants_cache["base_fake_b"])


class TestActiveAppearanceOverrideResolver(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()

    def test_sem_nenhum_efeito_retorna_none(self):
        self.world.add_component(self.eid, CombatStats())
        self.assertIsNone(get_active_appearance_override(self.world, self.eid))

    def test_camuflagem_ativa_resolve_com_variante(self):
        cs = CombatStats()
        cs.camouflage_timer  = 3.0
        cs.camouflage_object = "_2"
        self.world.add_component(self.eid, cs)
        result = get_active_appearance_override(self.world, self.eid)
        self.assertEqual(result, ("camuflagem", "_2"))

    def test_camuflagem_expirada_nao_resolve(self):
        """timer <= 0 não deve contar como ativo, mesmo se
        camouflage_object ainda tiver um valor residual não limpo."""
        cs = CombatStats()
        cs.camouflage_timer  = 0.0
        cs.camouflage_object = "_3"  # residual, não deveria importar
        self.world.add_component(self.eid, cs)
        self.assertIsNone(get_active_appearance_override(self.world, self.eid))

    def test_polimorfia_ativa_resolve(self):
        sfx = StatusEffects()
        sfx.effects["polymorph"] = ActiveEffect("polymorph", 6.0, 10, 1.0, 0.0)
        self.world.add_component(self.eid, sfx)
        result = get_active_appearance_override(self.world, self.eid)
        self.assertEqual(result, ("polimorfia", ""))

    def test_camuflagem_tem_prioridade_sobre_polimorfia_se_ambas_ativas(self):
        """Não deveria acontecer no jogo real (mutuamente exclusivas,
        decisão do usuário), mas o resolver precisa de uma ordem
        determinística mesmo assim — primeira da tabela vence."""
        cs = CombatStats()
        cs.camouflage_timer  = 3.0
        cs.camouflage_object = ""
        self.world.add_component(self.eid, cs)
        sfx = StatusEffects()
        sfx.effects["polymorph"] = ActiveEffect("polymorph", 6.0, 10, 1.0, 0.0)
        self.world.add_component(self.eid, sfx)
        result = get_active_appearance_override(self.world, self.eid)
        self.assertEqual(result[0], "camuflagem")

    def test_entidade_sem_combatstats_nem_statuseffects_nao_quebra(self):
        """Entidade "vazia" (sem os componentes checados) não deve
        lançar exceção — deve simplesmente não ter override."""
        self.assertIsNone(get_active_appearance_override(self.world, self.eid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
