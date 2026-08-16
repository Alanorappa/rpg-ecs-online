"""
tests/test_tile_los_visibility.py — Linha de visão de TERRENO,
UNIVERSAL (13/08/2026, §54→§57) — servidor nunca tinha checagem
contra `vision_height`, só proximidade (AOI) + zona de bush (sem dado
real em nenhum mapa, ver §51). Reaproveita a mesma malha que já
bloqueia visão no cliente (Fase 49-53). Ver
`server/tile_los_processor.py`, `server/session.py::_can_see`.

§57 — desenho final, depois de 2 correções do usuário sobre §54-56:
- Roda pra QUALQUER par de entidades, mundo aberto E BG/instância —
  sem filtro de Faction pro bloqueio de SÓLIDO (parede/pedra/tronco).
- Isenção de vegetação bloqueante (bush/copa) é sempre a partir da
  posição do PRÓPRIO viewer (nunca do alvo) — mesmo princípio do
  cliente (`engine/fov.py::local_vision_blob`, §50).
- Visão compartilhada de time (aliado dentro do blob revela pro time)
  só roda com Faction EXPLÍCITA — nunca mundo aberto.

Modelado em cima de `tests/test_bush_stealth.py` — mesmo padrão de
`spawn_player`/`Faction`/`set_entity_tile`.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from tests.helpers import make_world_server, spawn_player, set_entity_tile
from server.session import _can_see
from engine.components import Faction, MapLocation, Tilemap
from engine.tileset import WALL_TILE, FLOOR_TILE, OBJECT_MAPPING
from engine.entity_factory import create_minion, create_tower
from shared.constants import (ALLY_VISION_RADIUS_PLAYER, ALLY_VISION_RADIUS_TOWER,
                               ALLY_VISION_RADIUS_MINION)

# Bush real do catálogo (Fase 49-53, vision_height=2) — NÃO confundir
# com o `BUSH_TILE` legado de char único (`vision_height=1`, nunca
# bloqueia visão, sistema paralelo e não usado em nenhum mapa real).
_REAL_BUSH_TILE = OBJECT_MAPPING["pl_b1"]

_VIEWER   = (10, 10)
_TARGET   = (16, 10)   # mesma linha, 6 tiles à direita do viewer
_WALL_MID = (13, 10)   # exatamente no meio do caminho viewer→alvo


def _get_tilemap(ws):
    bundle = ws._map_bundles[ws._map_file]
    return ws.world.get_component(bundle.tilemap_entity, Tilemap)


def _clear_area(ws, x0: int, y0: int, x1: int, y1: int) -> None:
    """Sobrescreve um retângulo com FLOOR_TILE — mapa real (map_1.csv)
    pode ter terreno/objetos de propósito nessas coordenadas; os testes
    não podem depender do conteúdo real do mapa, só do que eles mesmos
    colocam."""
    tm = _get_tilemap(ws)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            tm.tile_matrix[y][x] = FLOOR_TILE


def _put_wall(ws, x: int, y: int) -> None:
    _get_tilemap(ws).tile_matrix[y][x] = WALL_TILE


def _put_bush(ws, x: int, y: int) -> None:
    _get_tilemap(ws).tile_matrix[y][x] = _REAL_BUSH_TILE


class TestTileLosUniversal(unittest.TestCase):
    """Bloqueio de SÓLIDO (parede/pedra/tronco) — vale pra QUALQUER par,
    com ou sem Faction, mundo aberto ou BG (§57)."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        self.viewer = spawn_player(self.ws, "s_viewer", *_VIEWER)
        self.target = spawn_player(self.ws, "s_target", *_TARGET)

    def test_parede_esconde_sem_faction_nenhuma_mundo_aberto(self):
        # Nenhum dos 2 tem Faction — antes do §57 o gate nem rodava
        # aqui. Prova a extensão universal (mundo aberto incluso).
        _put_wall(self.ws, *_WALL_MID)
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

    def test_parede_esconde_entre_adversarios(self):
        self.ws.world.add_component(self.viewer, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(self.target, Faction(faction_id="arena_time_b"))
        _put_wall(self.ws, *_WALL_MID)
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

    def test_parede_esconde_entre_aliados_tambem(self):
        # §57: bloqueio de sólido é universal — não existe mais exceção
        # pra "mesmo time". Antes (§54) esse par continuava se vendo;
        # agora não.
        self.ws.world.add_component(self.viewer, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(self.target, Faction(faction_id="arena_time_a"))
        _put_wall(self.ws, *_WALL_MID)
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

    def test_sem_obstrucao_continua_visivel(self):
        self.assertTrue(_can_see(self.ws, self.viewer, self.target))

    def test_alvo_em_cima_de_bush_fica_escondido_mesmo_sem_obstrucao_no_meio(self):
        # Viewer ADJACENTE ao alvo (sem nenhum tile intermediário) — só o
        # próprio tile do alvo é bush. Prova que o tile FINAL conta, não
        # só obstáculos "no meio do caminho" (bresenham_line_tiles inclui
        # o destino de propósito). Sem Faction nenhuma — já vale sozinho.
        tx, ty = _VIEWER[0] + 1, _VIEWER[1]
        set_entity_tile(self.ws, self.target, tx, ty)
        _put_bush(self.ws, tx, ty)
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

    def test_minion_atras_de_parede_tambem_esconde(self):
        map_file = self.ws.get_entity_map(self.viewer)
        minion = create_minion(self.ws.world, _TARGET[0], _TARGET[1], "minion_melee",
                               faction_id="qualquer_time", route=[_TARGET])
        self.ws.world.add_component(minion, MapLocation(map_file))
        _put_wall(self.ws, *_WALL_MID)
        self.assertFalse(_can_see(self.ws, self.viewer, minion))

    def test_bush_zone_antiga_continua_funcionando_isolada(self):
        # Regressão: o gate de zona de bush (§47) não foi afetado —
        # testado SEM nenhum tile de vision_height envolvido.
        zone_rect = (30, 30, 35, 35)
        map_file = self.ws.get_entity_map(self.viewer)
        self.ws._bush_zones_by_map[map_file] = [{"name": "Z", "rect": zone_rect}]
        set_entity_tile(self.ws, self.target, 32, 32)
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))
        set_entity_tile(self.ws, self.viewer, 32, 32)
        self.assertTrue(_can_see(self.ws, self.viewer, self.target))


class TestBushViewerOwnPositionExemption(unittest.TestCase):
    """§57 — isenção de vegetação bloqueante (bush/copa) é sempre a
    partir da posição do PRÓPRIO viewer, nunca do alvo. Cobre o bug
    relatado: viewer no MEIO de uma bush grande não enxergava quem
    tinha acabado de sair, porque a versão anterior (§55) calculava o
    blob a partir do ALVO."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        self.viewer = spawn_player(self.ws, "s_viewer", 10, 20)
        self.target = spawn_player(self.ws, "s_target", 10, 20)
        self.ws.world.add_component(self.viewer, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(self.target, Faction(faction_id="arena_time_b"))
        tm = _get_tilemap(self.ws)
        # Blob A: 3 tiles conectados em linha (20,20)-(21,20)-(22,20).
        # Blob B: 2 tiles conectados, SEPARADO (gap de tile livre).
        for x, y in ((20, 20), (21, 20), (22, 20)):
            tm.tile_matrix[y][x] = _REAL_BUSH_TILE
        for x, y in ((26, 20), (27, 20)):
            tm.tile_matrix[y][x] = _REAL_BUSH_TILE

    def test_viewer_no_meio_da_bush_ve_quem_saiu_cruzando_outras_celulas_da_mesma_bush(self):
        # Viewer fica na PONTA da bush A (20,20); alvo sai pra célula
        # aberta (23,20) — o caminho reto entre eles cruza (21,20) e
        # (22,20), OUTRAS células da MESMA bush que o viewer ocupa.
        # Antes (§55, blob do ALVO) isso bloqueava por engano; agora
        # (blob do VIEWER) essas células fazem parte do blob dele.
        set_entity_tile(self.ws, self.viewer, 20, 20)
        set_entity_tile(self.ws, self.target, 23, 20)
        self.assertTrue(_can_see(self.ws, self.viewer, self.target))

    def test_adversarios_na_mesma_bush_se_veem(self):
        set_entity_tile(self.ws, self.viewer, 20, 20)
        set_entity_tile(self.ws, self.target, 21, 20)
        self.assertTrue(_can_see(self.ws, self.viewer, self.target))

    def test_adversarios_em_bushes_diferentes_continuam_escondidos(self):
        set_entity_tile(self.ws, self.viewer, 20, 20)   # blob A
        set_entity_tile(self.ws, self.target, 26, 20)   # blob B, separado
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

    def test_atacar_revela_e_depois_volta_a_esconder(self):
        set_entity_tile(self.ws, self.viewer, 10, 20)     # fora de qualquer bush
        set_entity_tile(self.ws, self.target, 20, 20)    # sozinho na bush A
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

        from engine.core_systems import apply_damage_core, BaseCombatStateSystem
        from engine.components import CombatState
        from shared.constants import BUSH_REVEAL_DURATION_S

        # Alvo ataca alguém (não importa quem) — vira o killer_eid.
        apply_damage_core(self.ws.world, self.viewer, 1, killer_eid=self.target)
        self.assertTrue(_can_see(self.ws, self.viewer, self.target))

        target_cst = self.ws.world.get_component(self.target, CombatState)
        BaseCombatStateSystem._tick_bush_reveal_timer(target_cst, BUSH_REVEAL_DURATION_S + 0.1)
        self.assertEqual(target_cst.bush_reveal_timer, 0.0)
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

    def test_isencao_de_bush_nunca_cancela_parede(self):
        # Alvo com timer de reveal ativo ("acabou de atacar"), mas uma
        # parede DE VERDADE no meio do caminho — a isenção de bush não
        # deveria deixar ver através dela.
        set_entity_tile(self.ws, self.viewer, 18, 20)
        set_entity_tile(self.ws, self.target, 20, 20)   # em cima da bush A
        _put_wall(self.ws, 19, 20)                      # entre os 2

        from engine.components import CombatState
        target_cst = self.ws.world.get_component(self.target, CombatState)
        target_cst.bush_reveal_timer = 5.0  # "acabou de atacar"

        self.assertFalse(_can_see(self.ws, self.viewer, self.target),
            "parede sólida real não deveria ser ignorada por nenhuma isenção de bush")

    def test_minion_nao_ganha_bush_reveal_timer_ao_atacar(self):
        # Minion não tem CombatState (create_minion nunca anexa) — a
        # isenção "atacar revela" já escopa "só player" por construção.
        from engine.core_systems import apply_damage_core
        from engine.components import CombatState

        minion = create_minion(self.ws.world, 10, 20, "minion_melee",
                               faction_id="arena_time_b", route=[(10, 20)])
        apply_damage_core(self.ws.world, self.viewer, 1, killer_eid=minion)
        self.assertIsNone(self.ws.world.get_component(minion, CombatState))


class TestBushAllyVisionSharing(unittest.TestCase):
    """§57/§58 — visão compartilhada de time: um ALIADO (não
    necessariamente o próprio viewer) vendo o alvo revela ele pro time
    inteiro. Desde §58 (16/08/2026) isso é união de fontes via
    `ally_centers` (posição+raio próprio de cada aliado — mesmo
    formato que `SessionManager._compute_ally_vision_centers()`
    produz), passado explicitamente aqui pra testar a camada de LOS
    isolada da camada de proximidade/Faction (quem monta essa lista —
    `_compute_ally_vision_centers` — já garante Faction EXPLÍCITA,
    nunca mundo aberto)."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        tm = _get_tilemap(self.ws)
        for x, y in ((20, 20), (21, 20)):
            tm.tile_matrix[y][x] = _REAL_BUSH_TILE

    def test_aliado_dentro_da_bush_revela_pro_time_mesmo_o_viewer_estando_longe(self):
        viewer = spawn_player(self.ws, "s_viewer", 10, 10)   # longe, fora da bush
        ally   = spawn_player(self.ws, "s_ally", 21, 20)     # aliado, dentro da bush
        target = spawn_player(self.ws, "s_target", 20, 20)   # adversário, dentro da bush
        self.ws.world.add_component(viewer, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(ally,   Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(target, Faction(faction_id="arena_time_b"))

        ally_centers = [(21, 20, ALLY_VISION_RADIUS_PLAYER)]
        self.assertTrue(_can_see(self.ws, viewer, target, ally_centers=ally_centers),
            "aliado dentro da bush deveria revelar o adversário escondido lá pro time inteiro")

    def test_aliado_fora_do_proprio_raio_de_visao_nao_revela(self):
        # Mesmo cenário físico, mas o raio do aliado não alcança o alvo
        # (aliado a 1 tile de distância Chebyshev, raio declarado 0) —
        # prova que a união de visão respeita RAIO, ao contrário da
        # mecânica antiga (scan de Faction sem nenhum raio, achava
        # QUALQUER aliado em QUALQUER bush do mapa inteiro).
        viewer = spawn_player(self.ws, "s_viewer", 10, 10)
        ally   = spawn_player(self.ws, "s_ally", 21, 20)
        target = spawn_player(self.ws, "s_target", 20, 20)
        self.ws.world.add_component(viewer, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(ally,   Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(target, Faction(faction_id="arena_time_b"))

        ally_centers = [(21, 20, 0)]
        self.assertFalse(_can_see(self.ws, viewer, target, ally_centers=ally_centers),
            "aliado fora do próprio raio de visão não deveria revelar o alvo")

    def test_sem_faction_nenhuma_visao_nao_e_compartilhada_mundo_aberto(self):
        # Mundo aberto — ninguém tem Faction, e portanto nenhum
        # ally_centers é passado (quem monta a lista é o chamador —
        # SessionManager._compute_ally_vision_centers — que já exige
        # Faction explícita antes de incluir qualquer aliado).
        viewer  = spawn_player(self.ws, "s_viewer", 10, 10)
        bystander = spawn_player(self.ws, "s_bystander", 21, 20)  # na bush, sem Faction
        target  = spawn_player(self.ws, "s_target", 20, 20)       # na bush, sem Faction

        self.assertFalse(_can_see(self.ws, viewer, target),
            "sem Faction explícita não deveria haver visão compartilhada de bush")


class TestTeamVisionUnion(unittest.TestCase):
    """§58 (16/08/2026, 2ª rodada) — visão de time vira UNIÃO de fontes
    independentes, estilo LoL: "se um aliado (player, minion ou torre)
    vê, o time inteiro vê". Diferente da mecânica de bush (§57), que só
    isentava uma célula específica — aqui o obstáculo é uma PAREDE de
    verdade no raio do VIEWER, mas fora do raio da FONTE aliada (prova
    que a união cobre sólido, não só vegetação — sólido continua
    bloqueando cada raycast individual, só que agora há mais de um
    raycast possível). Cada fonte tem raio PRÓPRIO (`ally_centers`,
    mesmo formato que `SessionManager._compute_ally_vision_centers()`
    produz: player usa `ALLY_VISION_RADIUS_PLAYER`, torre usa o próprio
    `vision_radius_tiles`, minion usa `ALLY_VISION_RADIUS_MINION`)."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        self.viewer = spawn_player(self.ws, "s_viewer", *_VIEWER)   # (10,10)
        self.target = spawn_player(self.ws, "s_target", *_TARGET)   # (16,10)
        self.ws.world.add_component(self.viewer, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(self.target, Faction(faction_id="arena_time_b"))
        _put_wall(self.ws, *_WALL_MID)  # (13,10) — bloqueia o raio DIRETO do viewer
        self.map_file = self.ws.get_entity_map(self.viewer)

        # Confirma a premissa: sem nenhum aliado, a parede já esconde o
        # alvo do viewer (senão o teste de união não provaria nada).
        self.assertFalse(_can_see(self.ws, self.viewer, self.target))

    def test_torre_aliada_com_los_propria_revela_pro_time(self):
        # Torre 3 tiles abaixo do alvo, MESMA coluna — raio até o alvo
        # não cruza a parede (que está na linha do viewer, não na da torre).
        tower = create_tower(self.ws.world, 16, 13, "torre_de_flechas", faction_id="arena_time_a")
        self.ws.world.add_component(tower, MapLocation(self.map_file))
        ally_centers = [(16, 13, ALLY_VISION_RADIUS_TOWER)]
        self.assertTrue(_can_see(self.ws, self.viewer, self.target, ally_centers=ally_centers),
            "torre aliada com LOS própria deveria revelar o alvo pro time, mesmo com parede no raio do viewer")

    def test_minion_aliado_com_los_propria_revela_pro_time(self):
        minion = create_minion(self.ws.world, 16, 13, "minion_melee",
                               faction_id="arena_time_a", route=[(16, 13)])
        self.ws.world.add_component(minion, MapLocation(self.map_file))
        ally_centers = [(16, 13, ALLY_VISION_RADIUS_MINION)]
        self.assertTrue(_can_see(self.ws, self.viewer, self.target, ally_centers=ally_centers),
            "minion aliado com LOS própria deveria revelar o alvo pro time")

    def test_player_aliado_com_los_propria_revela_pro_time(self):
        # Responde diretamente o que o usuário perguntou: player aliado
        # tinha o MESMO problema que minion/torre (mesma causa raiz,
        # mesmo fix — não é um caminho de código separado).
        ally = spawn_player(self.ws, "s_ally", 16, 13)
        self.ws.world.add_component(ally, Faction(faction_id="arena_time_a"))
        ally_centers = [(16, 13, ALLY_VISION_RADIUS_PLAYER)]
        self.assertTrue(_can_see(self.ws, self.viewer, self.target, ally_centers=ally_centers),
            "player aliado com LOS própria deveria revelar o alvo pro time")

    def test_aliado_fora_do_proprio_raio_nao_revela(self):
        # Mesma fonte (minion), mas o raio DECLARADO não alcança o alvo
        # (distância real Chebyshev = 3, raio = 2) — prova que a união
        # respeita o raio de cada fonte, não é "qualquer aliado no mapa".
        minion = create_minion(self.ws.world, 16, 13, "minion_melee",
                               faction_id="arena_time_a", route=[(16, 13)])
        self.ws.world.add_component(minion, MapLocation(self.map_file))
        ally_centers = [(16, 13, 2)]
        self.assertFalse(_can_see(self.ws, self.viewer, self.target, ally_centers=ally_centers),
            "aliado fora do próprio raio de visão não deveria revelar o alvo")


class TestBushViewerMovedRevalidation(unittest.TestCase):
    """§56 (13/08/2026) — sair da bush precisa revalidar a PRÓPRIA visão
    de quem já era conhecido (não só esperar o alvo se mover de novo).
    Bug relatado: quem ficava na bush perdia a visão de quem saiu, e
    quem saiu continuava vendo quem ficou — o inverso do esperado — até
    o player de dentro andar pra outro tile."""

    def setUp(self):
        self.ws = make_world_server()
        _clear_area(self.ws, 8, 8, 40, 40)
        from server.session import SessionManager, Session

        self.mgr = SessionManager(self.ws)
        self.viewer = spawn_player(self.ws, "s_viewer", 20, 20)  # fica na bush
        self.target = spawn_player(self.ws, "s_target", 21, 20)  # entra e depois sai
        self.ws.world.add_component(self.viewer, Faction(faction_id="arena_time_a"))
        self.ws.world.add_component(self.target, Faction(faction_id="arena_time_b"))

        tm = _get_tilemap(self.ws)
        for x, y in ((20, 20), (21, 20)):
            tm.tile_matrix[y][x] = _REAL_BUSH_TILE

        self.viewer_sess = Session(ws=None, session_id="s_viewer")
        self.viewer_sess.entity_id = self.viewer
        self.target_sess = Session(ws=None, session_id="s_target")
        self.target_sess.entity_id = self.target

        # Estabelece conhecimento mútuo inicial (os 2 na mesma bush se veem
        # de verdade — confirmado por _can_see direto, não por suposição)
        # sem depender do fluxo completo de descoberta via login/sweep de
        # mob (Session leve aqui não passa por WORLD_STATE nem tem
        # mob_hash) — irrelevante pro que este teste cobre (o que acontece
        # ao SAIR, não como o conhecimento inicial se forma).
        self.assertTrue(_can_see(self.ws, self.viewer, self.target))
        self.assertTrue(_can_see(self.ws, self.target, self.viewer))
        self.viewer_sess.known_eids = {self.target}
        self.target_sess.known_eids = {self.viewer}

    def _exit_bush_delta(self) -> dict:
        # (20,30) — reto pra baixo na MESMA coluna do viewer (x=20), pra
        # não cruzar de volta pelo tile de bush que o alvo deixou (21,20),
        # que continua lá fisicamente e legitimamente bloquearia (não é
        # o que este teste quer verificar).
        set_entity_tile(self.ws, self.target, 20, 30)
        return {"moved": [{"eid": self.target, "tx": 20, "ty": 30,
                           "from_tx": 21, "from_ty": 20}]}

    def test_quem_fica_continua_vendo_quem_saiu(self):
        moved = self._exit_bush_delta()
        self.mgr._build_update_for_session(self.viewer_sess, moved, 20, 20)
        self.assertIn(self.target, self.viewer_sess.known_eids,
            "quem ficou na bush deveria continuar vendo quem saiu (olhar de dentro pra fora não bloqueia)")

    def test_quem_sai_perde_visao_de_quem_ficou_sem_precisar_andar_de_novo(self):
        moved = self._exit_bush_delta()
        result = self.mgr._build_update_for_session(self.target_sess, moved, 20, 30)
        self.assertNotIn(self.viewer, self.target_sess.known_eids,
            "quem saiu não deveria mais ver quem ficou na bush, sem precisar andar de novo")
        self.assertIn(self.viewer, result.get("despawned", []))


if __name__ == "__main__":
    unittest.main()
