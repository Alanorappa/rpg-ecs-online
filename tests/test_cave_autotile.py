"""
tests/test_cave_autotile.py — regras de encaixe das paredes de caverna
(engine/cave_autotile.py). Gabarito = mapa de Aracnópolis decorado à mão
pelo usuário (24/10/2026): as regras têm que reproduzir as peças dele no
mesmo formato de parede.
"""
import os, sys, random, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import cave_autotile as CA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VAR = [{2, 3, 36}, {5, 34}, {8, 33}, {6, 7, 17, 18, 19, 22, 23}, {14, 15}, {10, 11}]


def _cls(p, terrain):
    if not p.startswith('cv'):
        return 'escuro' if terrain == 'c' else '.'
    n = int(p[2:])
    for g in _VAR:
        if n in g:
            return 'g' + str(min(g))
    return p


def _load(name):
    with open(os.path.join(ROOT, 'maps', name), encoding='utf-8') as f:
        return [r.split(',') for r in f.read().splitlines()]


OFF = 5   # formas longe da borda do mapa (fora do mapa conta como parede)


def _shape(art):
    rows = art.strip('\n').split('\n')
    return {(x + OFF, y + OFF) for y, r in enumerate(rows) for x, ch in enumerate(r) if ch == '#'}


def _at(G, x, y):
    return G.get((x + OFF, y + OFF))


class TestGabaritoAracnopolis(unittest.TestCase):

    def test_reproduz_o_mapa_feito_a_mao(self):
        T, O = _load('aracnopolis_dungeon_terrain.csv'), _load('aracnopolis_dungeon_objects.csv')
        H, W = min(len(O), 160), len(O[0])
        F = CA.footprint(T[:H], O[:H])
        G = CA.tile(F, W, H, seed=1)
        cells = [p for p in F if 0 <= p[1] < H]
        ok = sum(_cls(G.get(p, '.'), T[p[1]][p[0]]) == _cls(O[p[1]][p[0]], T[p[1]][p[0]]) for p in cells)
        self.assertGreaterEqual(ok / len(cells), 0.96)


class TestFormasBasicas(unittest.TestCase):

    def test_bloco_3x3_tem_borda_face_alta_e_baixa(self):
        G = CA.tile(_shape("###\n###\n###"), 20, 20, seed=1)
        self.assertEqual(_at(G, 0, 0), 'cv001')
        self.assertEqual(_at(G, 0, 1), 'cv009')
        self.assertEqual(_at(G, 2, 2), 'cv016')
        self.assertIn(_at(G, 1, 2), ('cv014', 'cv015'))

    def test_face_alta_casa_com_face_baixa(self):
        G = CA.tile(_shape("##########\n##########\n##########"), 20, 20, seed=3)
        for x in range(1, 9):
            self.assertEqual(_at(G, x, 1), CA.PAIR_HI[_at(G, x, 2)])

    def test_parede_de_2_fileiras_e_base(self):
        G = CA.tile(_shape("#####\n#####"), 20, 20, seed=1)
        self.assertEqual((_at(G, 0, 1), _at(G, 2, 1), _at(G, 4, 1)), ('cv020', 'cv031', 'cv021'))

    def test_modo_so_topo_nao_tem_face(self):
        G = CA.tile(_shape("####\n####\n####\n####"), 20, 20, faces=False, seed=1)
        faces = {'cv009', 'cv010', 'cv011', 'cv012', 'cv013', 'cv014', 'cv015', 'cv016',
                 'cv026', 'cv027', 'cv028', 'cv029'}
        self.assertFalse(faces & set(G.values()))
        self.assertEqual(_at(G, 1, 3), 'cv031')

    def test_escada_usa_coluna_alta_e_cobre_a_de_cima(self):
        art = "########\n########\n########\n.#######\n..######\n...#####"
        G = CA.tile(_shape(art), 20, 20, seed=1)
        self.assertEqual(_at(G, 1, 3), 'cv027')
        self.assertEqual(_at(G, 1, 2), CA.COVERED)

    def test_regiao_bate_com_o_calculo_inteiro(self):
        F = _shape("..######..\n.########.\n##########\n##########\n.###..###.\n.###..###.")
        full = CA.roles_for(F, 20, 20)
        region = {(x + OFF, y + OFF) for x in range(2, 8) for y in range(1, 5)}
        part = CA.roles_for(F, 20, 20, region=region)
        self.assertEqual(part, {p: full[p] for p in region if p in full})

    def test_mantem_peca_que_ainda_serve(self):
        F = _shape("####\n####\n####")
        roles = CA.roles_for(F, 20, 20)
        cur = {(1 + OFF, OFF): 'cv036', (1 + OFF, 1 + OFF): 'cv005'}
        out = CA.pick_pieces(roles, current=cur, rng=random.Random(1))
        self.assertEqual(out[(1 + OFF, OFF)], 'cv036')         # borda de cima: variação dele fica
        self.assertNotEqual(out[(1 + OFF, 1 + OFF)], 'cv005')  # virou face alta: troca

    def test_celula_fina_demais(self):
        F = _shape("#.###\n#.###\n#.###")
        self.assertEqual(CA.thin_cells(F, F, min_w=3), {(OFF, OFF), (OFF, OFF + 1), (OFF, OFF + 2)})


if __name__ == '__main__':
    unittest.main()
