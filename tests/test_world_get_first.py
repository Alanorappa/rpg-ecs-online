"""
tests/test_world_get_first.py — 18/09/2026, ver
arquitetura/PROJECT_WIDE_REVIEW.md item #5:
`World.get_entities_with()` sempre aloca a lista INTEIRA, mesmo
quando o chamador só quer 1 resultado — padrão real e recorrente no
cliente (`for ..., comp in world.get_entities_with(Camera): ...;
break`), 37 ocorrências em `ui/`/`game.py` consultando tipos que só
têm 1 instância no mundo LOCAL do cliente (Camera, Tilemap, FogOfWar,
PlayerControlled, ...).

`World.get_first()` (novo, `engine/world.py`) espelha a MESMA lógica
de seleção de `get_entities_with()` (conjunto de índice mais
seletivo), só que devolve na 1ª entidade encontrada, sem montar a
lista.

Prova: (1) sem entidade compatível → `None` (não `[]`, formato
diferente de propósito); (2) com 1 entidade compatível → tupla igual
ao que `get_entities_with()[0]` devolveria; (3) com VÁRIAS entidades
compatíveis, devolve alguma delas de verdade (não quebra, não
inventa dado) — mesmo contrato "1ª que aparecer no índice" que
`get_entities_with()` já tem, só sem montar a lista inteira; (4)
múltiplos tipos de componente exigidos — só entidade com TODOS conta.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.world import World


class _CompA:
    def __init__(self, v):
        self.v = v


class _CompB:
    def __init__(self, v):
        self.v = v


class TestWorldGetFirst(unittest.TestCase):

    def test_sem_entidade_compativel_devolve_none(self):
        world = World()
        self.assertIsNone(world.get_first(_CompA))

    def test_sem_argumentos_devolve_none(self):
        world = World()
        self.assertIsNone(world.get_first())

    def test_uma_entidade_compativel_devolve_tupla_igual_a_get_entities_with(self):
        world = World()
        eid = world.create_entity()
        comp = _CompA(v=42)
        world.add_component(eid, comp)

        esperado = world.get_entities_with(_CompA)[0]
        self.assertEqual(world.get_first(_CompA), esperado)

    def test_varias_entidades_compativeis_devolve_uma_delas_de_verdade(self):
        world = World()
        eids = []
        for i in range(5):
            eid = world.create_entity()
            world.add_component(eid, _CompA(v=i))
            eids.append(eid)

        result = world.get_first(_CompA)
        self.assertIsNotNone(result)
        found_eid, found_comp = result
        self.assertIn(found_eid, eids)
        self.assertIs(found_comp, world.get_component(found_eid, _CompA))

    def test_exige_todos_os_tipos_pedidos(self):
        world = World()
        only_a = world.create_entity()
        world.add_component(only_a, _CompA(v=1))

        both = world.create_entity()
        world.add_component(both, _CompA(v=2))
        world.add_component(both, _CompB(v=99))

        result = world.get_first(_CompA, _CompB)
        self.assertIsNotNone(result)
        found_eid, comp_a, comp_b = result
        self.assertEqual(found_eid, both)
        self.assertEqual(comp_a.v, 2)
        self.assertEqual(comp_b.v, 99)

    def test_entidade_removida_nao_aparece_mais(self):
        world = World()
        eid = world.create_entity()
        world.add_component(eid, _CompA(v=1))
        world.remove_entity(eid)
        self.assertIsNone(world.get_first(_CompA))


if __name__ == "__main__":
    unittest.main()
