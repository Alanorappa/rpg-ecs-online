"""tests/test_instance_persist_guard.py — server/session.py::
_persist_character.

INCIDENTE REAL relatado pelo usuário (02/08/2026): personagem real
"totalmente desconfigurado" (talentos/skills sumidos) depois de usar o
battleground de teste. Causa raiz: `is_in_normalized_progression` só era
checado no caminho de DISCONNECT antes de salvar — os outros 4 pontos de
persistência (SAVE_STATE automático do cliente, TALENT_UPDATE, entrega de
quest, autosave periódico de 5 em 5 minutos) nunca verificavam, e
qualquer um deles enquanto o player estivesse dentro da instância
escrevia o overlay RESETADO (level 1, talentos/skills/inventário/gold da
instância) no banco como se fosse o personagem real, permanentemente.

Fix: único chokepoint `SessionManager._persist_character` — nenhum save
acontece enquanto `is_in_normalized_progression` for True. Estes testes
provam que TODOS os 3 pontos de entrada mais fáceis de exercitar em
teste (autosave, SAVE_STATE, TALENT_UPDATE) — e o helper em si — nunca
chamam `save_character` enquanto o player está na instância, e voltam a
salvar normalmente depois que ele sai.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import patch

from tests.test_session import make_session_manager, fake_login
from server.instance_progression import (
    enter_normalized_progression, exit_normalized_progression,
)


class TestPersistCharacterSkipsInsideInstance(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(
            self.mgr, "s1", "persistguarda", class_id="guerreiro")

    async def test_persist_character_no_op_dentro_da_instancia(self):
        enter_normalized_progression(self.ws_server, self.session.entity_id)
        with patch("server.auth.save_character") as mock_save:
            result = await self.mgr._persist_character(self.session, context="teste")
        mock_save.assert_not_called()
        self.assertIsNone(result)

    async def test_persist_character_salva_normalmente_fora_da_instancia(self):
        """Prova que o guard não é largo demais — fora da instância,
        salvar continua funcionando."""
        with patch("server.auth.save_character") as mock_save:
            result = await self.mgr._persist_character(self.session, context="teste")
        mock_save.assert_called_once()
        self.assertIsNotNone(result)

    async def test_persist_character_volta_a_salvar_apos_sair_da_instancia(self):
        enter_normalized_progression(self.ws_server, self.session.entity_id)
        with patch("server.auth.save_character") as mock_save_dentro:
            await self.mgr._persist_character(self.session, context="teste")
        mock_save_dentro.assert_not_called()

        exit_normalized_progression(self.ws_server, self.session.entity_id)
        with patch("server.auth.save_character") as mock_save_fora:
            await self.mgr._persist_character(self.session, context="teste")
        mock_save_fora.assert_called_once()

    async def test_patch_fn_aplicado_quando_persiste_fora_da_instancia(self):
        """world_server.py::kill-XP usa patch_fn pra sobrescrever talentos
        com o TalentTree ATUAL do ECS (last_client_payload pode estar
        desatualizado logo após um level-up, ver docstring de
        _persist_character)."""
        def _patch(merged):
            merged["stats"]["gold"] = 99999
        with patch("server.auth.save_character") as mock_save:
            result = await self.mgr._persist_character(
                self.session, context="teste", patch_fn=_patch)
        mock_save.assert_called_once()
        self.assertEqual(result["stats"]["gold"], 99999)

    async def test_patch_fn_nao_roda_dentro_da_instancia(self):
        """Guard de instância é checado ANTES do patch_fn — mesmo com
        patch_fn passado, nada persiste (nem o patch é aplicado)."""
        enter_normalized_progression(self.ws_server, self.session.entity_id)
        _patch_calls = []
        with patch("server.auth.save_character") as mock_save:
            result = await self.mgr._persist_character(
                self.session, context="teste", patch_fn=_patch_calls.append)
        mock_save.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(_patch_calls, [])

    async def test_autosave_all_pula_sessao_dentro_da_instancia(self):
        enter_normalized_progression(self.ws_server, self.session.entity_id)
        with patch("server.auth.save_character") as mock_save:
            await self.mgr._autosave_all()
        mock_save.assert_not_called()

    async def test_handle_save_state_nao_persiste_dentro_da_instancia(self):
        enter_normalized_progression(self.ws_server, self.session.entity_id)
        with patch("server.auth.save_character") as mock_save:
            await self.mgr._handle_save_state(self.session, {
                "inventory": [], "talents": {"allocated": {}, "available_points": 0},
                "skills": {"learned": ["golpe_poderoso"], "hotbar": []},
            }, ts=0)
        mock_save.assert_not_called()

    async def test_handle_talent_update_nao_persiste_dentro_da_instancia(self):
        enter_normalized_progression(self.ws_server, self.session.entity_id)
        with patch("server.auth.save_character") as mock_save:
            await self.mgr._handle_talent_update(self.session, {
                "talents": {"allocated": {}, "available_points": 0},
            }, ts=0)
        mock_save.assert_not_called()


class TestKillXpSaveUsesSharedChokepoint(unittest.IsolatedAsyncioTestCase):
    """server/world_server.py — o save de XP/level pós-kill (02/08/2026,
    refatorado a pedido do usuário pra unificar com todo outro save do
    jogo) precisa passar por `SessionManager._persist_character` (nunca
    mais montar get_player_save_data/_build_save_merge/save_character na
    mão) — mesmo chokepoint único que fecha o incidente de §34.74.15."""

    async def asyncSetUp(self):
        self.ws_server, self.mgr = make_session_manager()
        self.ws_server._session_manager = self.mgr  # normalmente feito por server/main.py
        # Tile isolado (10,10), longe da praça populada (115,389) — mob de
        # teste spawnado lá era morto por um guarda hostil pré-existente do
        # mapa ANTES do teste conseguir controlar a morte (bug do teste,
        # descoberto só na suíte completa: passava isolado porque nenhum
        # NPC concorrente existia ainda no tick, mas falhava com o mundo
        # populado — mesmo padrão de tile "seguro" já usado por outros
        # testes desta sessão, ex.: tests/test_towers.py).
        self.session, self.fw = await fake_login(
            self.mgr, "s1", "killxpguarda", 10, 10, class_id="guerreiro")

    async def test_kill_de_mob_aciona_persist_character_via_chokepoint(self):
        import asyncio
        from unittest.mock import AsyncMock
        from engine.entity_factory import create_enemy
        from engine.components import CombatStats, MapLocation, PendingDeath

        mob_eid = create_enemy(self.ws_server.world, 11, 10, race="Lobo")
        self.ws_server.world.add_component(mob_eid, MapLocation(self.ws_server.MAP_FILE))
        self.ws_server._tick(0.05)
        cs = self.ws_server.world.get_component(mob_eid, CombatStats)
        cs.current_hp = 0
        self.ws_server.world.add_component(
            mob_eid, PendingDeath(killer_entity_id=self.session.entity_id))
        self.ws_server._mob_damage_log[mob_eid] = {self.session.entity_id: 999}

        with patch.object(self.mgr, "_persist_character", new=AsyncMock(return_value=None)) as mock_persist:
            self.ws_server._tick(0.05)
            await asyncio.sleep(0)  # deixa o ensure_future do save rodar

        mock_persist.assert_called_once()
        _args, _kwargs = mock_persist.call_args
        self.assertEqual(_args[0], self.session)
        self.assertEqual(_kwargs.get("context"), "kill_xp")
        self.assertIsNotNone(_kwargs.get("patch_fn"))


if __name__ == "__main__":
    unittest.main()
