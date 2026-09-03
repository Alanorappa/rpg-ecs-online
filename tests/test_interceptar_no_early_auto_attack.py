"""
tests/test_interceptar_no_early_auto_attack.py — 31/08/2026, bug real
relatado pelo usuário: usar Interceptar fazia o alvo tomar um hit de
ataque automático ANTES da animação do dash acontecer. Confirmado por
log (`debug/logs/aoi_debug.log`, evento `AUTO_ATK_RANGE_OK` adicionado
pra investigar): `attacker_progress=0.0` (dash recém-iniciado) mas
`attacker_current_tile` já igual ao DESTINO do dash.

Causa raiz: `server/skill_processor.py::_process_skill_requests` snapava
`tile_move.current_tile_x/y` pro destino IMEDIATAMENTE ao processar a
skill (existia desde a implementação original do Interceptar,
24/05/2026) — sem esperar `TileMovementSystem` completar o tween.
`current_tile_x/y` é o MESMO campo que `_resolve_auto_attack`
(server/combat_processor.py) usa pra medir alcance de ataque automático.

Fix (usuário pediu explicitamente, rejeitou um "guard" alternativo em
cima do sintoma): remover o snap. `current_tile_x/y` só muda quando
`TileMovementSystem` completa o tween (`progress>=1.0`) — como já era o
design original desse campo, usado por qualquer outro movimento normal.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, run_ticks, first_mob, set_entity_tile, authorize_skill
from engine.components import CombatState, CharacterStats, PlayerSkills, TileMovement


def _ready_skill(ws, eid, sid):
    authorize_skill(ws, eid, sid)
    ps = ws.world.get_component(eid, PlayerSkills)
    from content.skill_config import SKILL_CATALOG
    sk = next((s for s in ps.skills if s and s.skill_id == sid), None)
    if sk is None:
        sk = PlayerSkills._make_skill(sid, SKILL_CATALOG)
        idx = ps.skills.index(None)
        ps.skills[idx] = sk
    sk.current_cooldown = 0.0
    return sk


class TestInterceptarNoEarlyAutoAttack(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "s1", 130, 374, class_id="guerreiro")
        run_ticks(self.ws, 50)
        self.mob = first_mob(self.ws)
        # Mesma área usada em test_interceptar_debug.py: lado oeste andável,
        # 3 tiles de distância — cast válido (INTERCEPT_MIN/MAX_RANGE_PX).
        set_entity_tile(self.ws, self.mob, 127, 374)
        cst = self.ws.world.get_component(self.eid, CombatState)
        cst.target_entity_id = self.mob
        cst.is_pursuing = True
        char = self.ws.world.get_component(self.eid, CharacterStats)
        char.rage = 100
        _ready_skill(self.ws, self.eid, "interceptar")

    def _cast_interceptar(self):
        self.ws._pending_skill_requests.append({
            "player_eid": self.eid, "sid": "interceptar",
            "tid": self.mob, "dir_x": 0.0, "dir_y": 0.0, "ts": 0, "dbg_seq": 0,
        })
        self.ws._process_skill_requests()

    def test_current_tile_nao_pula_pro_destino_no_cast(self):
        tm = self.ws.world.get_component(self.eid, TileMovement)
        _start_tile = (tm.current_tile_x, tm.current_tile_y)
        self._cast_interceptar()
        self.assertTrue(tm.is_dash)
        self.assertLess(tm.progress, 1.0)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y), _start_tile,
            "current_tile_x/y não deveria mudar antes do tween completar")
        self.assertNotEqual((tm.target_tile_x, tm.target_tile_y), _start_tile,
            "target_tile_x/y deveria refletir o destino do dash imediatamente")

    def test_auto_attack_nao_dispara_antes_do_dash_completar(self):
        self._cast_interceptar()
        self.ws._combat_this_tick.clear()
        self.ws._process_player_attacks(0.05, {})
        _early_hits = [c for c in self.ws._combat_this_tick
                       if c.get("attacker") == self.eid and c.get("source") == "auto"]
        self.assertEqual(_early_hits, [],
            "auto-attack disparou antes do dash completar (bug relatado pelo usuário)")

    def test_correcao_de_posicao_carrega_duration_explicito(self):
        """31/08/2026, 2ª parte do bug: mesmo com current_tile_x/y corrigido
        (não pula mais pro destino), a ANIMAÇÃO local do dash no cliente
        ficava enfileirada atrás de um passo normal já em andamento
        (`_self_move_queue`, client/network_handlers.py) sempre que a
        correção não trazia `duration` explícito — só entra no caminho de
        "preemptar e animar na hora" (mesmo usado por knockback) quando
        `duration` vem no payload. Sem isso, o dash podia demorar ~300ms+
        pra sequer COMEÇAR a animar no cliente, bem depois do servidor já
        ter resolvido tudo (inclusive o ataque automático seguinte)."""
        self.ws._skill_position_corrections.clear()
        self._cast_interceptar()
        tm = self.ws.world.get_component(self.eid, TileMovement)
        _corr = next(c for c in self.ws._skill_position_corrections
                     if c["player_eid"] == self.eid)
        self.assertEqual(_corr["duration"], tm.move_duration)
        self.assertGreater(_corr["duration"], 0)

    def test_move_normal_e_recusado_durante_dash_em_andamento(self):
        """31/08/2026, 3ª parte do bug: mesmo com o cliente animando o dash
        na hora certa, o usuário reportou "Nenhum alvo" voltando a
        acontecer — investigação confirmou uma corrida real MOVE×
        CAST_SKILL: um MOVE normal (residual de WASD, mandado quase junto
        com o cast) chegava enquanto o dash ainda estava em andamento e
        `move_player()` sobrescrevia current_tile_x/y E target_tile_x/y
        do dash incondicionalmente (sem checar is_dash) — o player
        "aterrissava" numa tile ERRADA (a do MOVE, não a do Interceptar).
        `combat_state.target_entity_id` continuava setado (não é isso que
        quebra), mas `_resolve_target` reconfere alcance a partir da
        posição REAL — errada — e recusa "Nenhum alvo". Fix: `move_player`
        agora rejeita um MOVE normal enquanto `is_dash=True` (mesma
        proteção que TileMovementSystem já dá pro caso inverso, dash não
        interrompido por stun/sleep)."""
        self._cast_interceptar()
        tm = self.ws.world.get_component(self.eid, TileMovement)
        self.assertTrue(tm.is_dash)
        _dash_target = (tm.target_tile_x, tm.target_tile_y)

        # MOVE normal "residual" chegando no meio do dash — não pode
        # sobrescrever o destino do dash. (129,374): lado oeste confirmado
        # andável nesta fixture (mesma área de test_interceptar_debug.py);
        # 131,374 é parede, não serviria pra provar o guard (seria recusado
        # por walkability de qualquer forma, mascarando o teste).
        _accepted = self.ws.move_player("s1", 129, 374)
        self.assertFalse(_accepted, "MOVE normal deveria ser recusado durante o dash")
        self.assertEqual((tm.target_tile_x, tm.target_tile_y), _dash_target,
            "o destino do dash não deveria ter sido sobrescrito pelo MOVE recusado")
        self.assertTrue(tm.is_dash, "o dash deveria continuar em andamento")

    def test_move_normal_funciona_normalmente_apos_dash_completar(self):
        self._cast_interceptar()
        tm = self.ws.world.get_component(self.eid, TileMovement)
        for _ in range(10):
            self.ws._global_tms.update(dt=0.05)
            if tm.progress >= 1.0 and not tm.is_moving:
                break
        self.assertFalse(tm.is_dash)
        _landed = (tm.current_tile_x, tm.current_tile_y)
        _accepted = self.ws.move_player("s1", _landed[0] + 1, _landed[1])
        self.assertTrue(_accepted,
            "MOVE normal deveria funcionar de novo depois que o dash completa")

    def test_auto_attack_dispara_normalmente_apos_dash_completar(self):
        self._cast_interceptar()
        tm = self.ws.world.get_component(self.eid, TileMovement)
        for _ in range(10):
            self.ws._global_tms.update(dt=0.05)
            if tm.progress >= 1.0 and not tm.is_moving:
                break
        self.assertFalse(tm.is_dash)
        self.assertEqual((tm.current_tile_x, tm.current_tile_y),
                         (tm.target_tile_x, tm.target_tile_y))

        self.ws._combat_this_tick.clear()
        self.ws._process_player_attacks(0.05, {})
        _hits = [c for c in self.ws._combat_this_tick
                 if c.get("attacker") == self.eid and c.get("source") == "auto"]
        self.assertEqual(len(_hits), 1,
            "auto-attack deveria disparar normalmente depois que o dash completa")


if __name__ == "__main__":
    unittest.main()
