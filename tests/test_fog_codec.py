"""
tests/test_fog_codec.py — shared/fog_codec.py (bitmap+zlib pro Fog of War
persistido). Bug real relatado pelo usuário 19/07/2026: fog_json no
formato antigo (lista de [x,y] por tile, sem compressão) passava de 1 MB
numa conta bem explorada, travando o login com "message too big" (limite
do frame WebSocket). Ver arquitetura/PROBLEMAS_ARQUITETURA.md.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import unittest

from shared.fog_codec import encode_fog, decode_fog


class TestFogCodec(unittest.TestCase):

    def test_roundtrip_preserva_tiles_exatos(self):
        tiles = {(x, y) for x in range(10, 20) for y in range(10, 15)}
        encoded = encode_fog({"maps/map_1.csv": tiles})
        decoded = decode_fog(encoded)
        self.assertEqual(decoded["maps/map_1.csv"], tiles)

    def test_mapa_vazio_nao_entra_no_resultado(self):
        encoded = encode_fog({"maps/map_1.csv": set()})
        self.assertNotIn("maps/map_1.csv", encoded)

    def test_decode_aceita_formato_antigo_lista_de_coordenadas(self):
        """Migração transparente — personagem salvo antes do fix continua
        carregando normal, sem precisar converter o banco em massa."""
        old_format = {"maps/map_1.csv": [[1, 2], [3, 4], [1, 2]]}
        decoded = decode_fog(old_format)
        self.assertEqual(decoded["maps/map_1.csv"], {(1, 2), (3, 4)})

    def test_decode_aceita_dict_vazio_ou_none(self):
        self.assertEqual(decode_fog({}), {})
        self.assertEqual(decode_fog(None), {})

    def test_mesmos_dados_no_formato_antigo_e_novo_decodificam_igual(self):
        tiles = {(5, 5), (6, 5), (5, 6), (100, 200)}
        old_format = {"m": [list(t) for t in tiles]}
        new_format = encode_fog({"m": tiles})
        self.assertEqual(decode_fog(old_format)["m"], decode_fog(new_format)["m"])

    def test_bitmap_e_muito_menor_que_lista_de_coordenadas_para_area_contigua(self):
        """Área explorada real é sempre contígua (personagem anda, não
        teleporta) — o cenário onde a compressão realmente importa."""
        tiles = {(x, y) for x in range(0, 200) for y in range(0, 200)}
        old_size = len(json.dumps({"m": [list(t) for t in tiles]}).encode())
        new_size = len(json.dumps(encode_fog({"m": tiles})).encode())
        self.assertLess(new_size, old_size / 50,
                        "bitmap+zlib deveria ser pelo menos 50x menor pra área contígua")

    def test_varios_mapas_independentes(self):
        data = {
            "maps/map_1.csv":        {(1, 1), (2, 2)},
            "maps/map_cave_west.csv": {(10, 10)},
        }
        decoded = decode_fog(encode_fog(data))
        self.assertEqual(decoded, data)


if __name__ == "__main__":
    unittest.main()
