"""
shared/fog_codec.py — (de)serialização do Fog of War (FogOfWar._explored_maps)
para persistência (fog_json no banco / campo "fog" no protocolo).

Formato antigo — {map_key: [[x,y], [x,y], ...]} — gastava ~11 bytes por
tile (coordenada crua em JSON), sem nenhuma compressão. Bug real relatado
pelo usuário 19/07/2026: numa conta com 3 personagens bem explorados, só o
fog_json somado passou de 1 MB (limite do frame WebSocket) e travava o
login com "message too big". Área explorada é sempre um "borrão" contíguo
(o personagem anda, não teleporta) — perfeito pra bitmap comprimido.

Formato novo — {map_key: {"x0","y0","w","h","bits": base64(zlib(bitmap))}}:
bitmap com 1 bit por tile dentro do bounding box do que foi explorado
NAQUELE mapa (não do mapa inteiro — mantém compacto mesmo cedo na
exploração, sem precisar saber as dimensões reais do mapa). Medido na
conta real do usuário: 46.837 tiles explorados, 532 KB no formato antigo
→ 738 bytes no novo (bitmap + zlib) — a área explorada compacta quase
inteira em zeros/uns repetidos.

`decode_fog` aceita os dois formatos (list = antigo, dict = novo) por
chave de mapa — migração transparente, sem precisar converter o banco em
massa: um personagem salvo no formato antigo continua carregando normal,
e vira formato novo no PRÓXIMO save.
"""
from __future__ import annotations

import base64
import zlib


def encode_fog(explored_maps: dict) -> dict:
    """explored_maps: {map_key: set[(x,y)] ou iterável de pares} →
    {map_key: {"x0","y0","w","h","bits"}}. Mapas sem nenhum tile explorado
    não entram no resultado."""
    result: dict = {}
    for map_key, tiles in explored_maps.items():
        tiles = list(tiles)
        if not tiles:
            continue
        xs = [t[0] for t in tiles]
        ys = [t[1] for t in tiles]
        x0, y0  = min(xs), min(ys)
        w, h    = max(xs) - x0 + 1, max(ys) - y0 + 1
        bitset  = bytearray((w * h + 7) // 8)
        for x, y in tiles:
            idx = (y - y0) * w + (x - x0)
            bitset[idx // 8] |= (1 << (idx % 8))
        packed = zlib.compress(bytes(bitset), 9)
        result[map_key] = {
            "x0": x0, "y0": y0, "w": w, "h": h,
            "bits": base64.b64encode(packed).decode("ascii"),
        }
    return result


def merge_and_encode_fog(srv_fog_data: dict, cli_fog_data: dict) -> "dict | None":
    """Decodifica o fog já salvo no servidor + o que o cliente mandou, faz
    union por mapa e recomprime — só deve ser chamado quando `cli_fog_data`
    tem conteúdo de verdade (ver server/session.py::_persist_character, que
    pula esta função inteira quando o cliente não mandou fog novo — o save
    então nem toca a coluna `fog_json`, mantendo o que já estava salvo).

    Entrada/saída são só dict/set/tupla puros (sem objeto de sessão/ECS) de
    propósito — 15/09/2026, item de performance §141: isto roda dentro de
    `loop.run_in_executor(ProcessPoolExecutor, ...)` (server/session.py),
    então precisa ser 100% "pickleable" e livre de estado externo. O custo
    real medido (~100-150ms numa conta bem explorada) é o laço Python de
    `encode_fog` montando o bitmap bit a bit — não a compressão zlib em si
    (~15-20% do tempo) — por isso o ganho de tirar isso do event loop
    principal é rodar em processo separado, não em outra estrutura de dado."""
    if isinstance(srv_fog_data, str):
        import json
        try:
            srv_fog_data = json.loads(srv_fog_data) if srv_fog_data else {}
        except Exception:
            srv_fog_data = {}
    srv_fog = decode_fog(srv_fog_data or {})
    cli_fog = decode_fog(cli_fog_data or {})
    merged_sets: dict = dict(srv_fog)
    for map_key, cli_set in cli_fog.items():
        map_key = map_key.replace("\\", "/")
        merged_sets[map_key] = merged_sets.get(map_key, set()) | cli_set
    return encode_fog(merged_sets) or None


def decode_fog(data: dict) -> dict:
    """→ {map_key: set[(x,y)]}. Aceita formato novo (dict com "bits") OU
    antigo (lista de [x,y]) por chave de mapa."""
    result: dict = {}
    for map_key, entry in (data or {}).items():
        if isinstance(entry, dict) and "bits" in entry:
            x0, y0, w, h = entry["x0"], entry["y0"], entry["w"], entry["h"]
            packed = zlib.decompress(base64.b64decode(entry["bits"]))
            tiles = set()
            for idx in range(w * h):
                if packed[idx // 8] & (1 << (idx % 8)):
                    tiles.add((x0 + idx % w, y0 + idx // w))
            result[map_key] = tiles
        else:
            result[map_key] = {tuple(t) for t in (entry or [])}
    return result
