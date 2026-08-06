"""
tests/test_hotbar_bg_leak.py — dois bugs relatados pelo usuário (04/08/2026)
depois de testar a fila real de Battleground:

1. "A barra de ações fica totalmente desconfigurada depois que o
   personagem sai da BG, e a bag também está sofrendo alterações... seja
   por qualquer motivo, fechar o jogo enquanto está na BG ou qualquer
   outra coisa." Causa raiz: `game.py::_save_config()` (chamada de 17
   pontos diferentes — autosave periódico, fechar o jogo, editor de
   hotbar...) serializava `PlayerSkills.skills`/`ConsumableBar.slots` AO
   VIVO pra `config.json` sem checar se o player estava dentro da BG —
   um autosave (ou fechar o jogo) no meio de uma partida gravava o layout
   TEMPORÁRIO da instância por cima do real, pra sempre. Mesma classe de
   bug do incidente §34.74.15 (servidor persistindo estado de instância
   no banco), nunca coberta no lado do cliente. Fix: chokepoint único —
   `_save_config()` preserva o que já estava salvo enquanto
   `InstanceInventoryUIState.active`.

2. "A config de atalhos do player deve permanecer dentro da BG — se uma
   skill está no slot 3 e for a primeira a ser desbloqueada, ela deve
   aparecer no slot 3." Causa raiz: `server/instance_progression.py::
   _grant_instance_skill` sempre usava "primeiro slot vazio", ignorando
   o slot que a skill ocupa na hotbar REAL do player. Fix:
   `_real_slot_by_skill_id` consulta a hotbar real (já espelhada no
   servidor via HOTBAR_UPDATE) e usa o MESMO slot quando disponível.
"""
import os, sys, unittest
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player


# ─────────────────────────────────────────────────────────────────────────
# 1) Cliente — _save_config() preserva hotbar/consumable_bar reais na BG
# ─────────────────────────────────────────────────────────────────────────

class _FakeCfgFile:
    """Substitui config.load/config.save — evita tocar o config.json real
    do dev."""
    def __init__(self, existing: dict):
        self._existing = existing
        self.saved: "dict | None" = None

    def load(self) -> dict:
        return dict(self._existing)

    def save(self, data: dict) -> None:
        self.saved = data


class _SaveConfigFixture:
    """Só o suficiente pra exercitar GameEngine._save_config isolado (mesmo
    padrão de tests/test_client_ui.py::_make_remote_player_fixture —
    atribui o método REAL da classe grande numa fixture leve)."""
    def __init__(self, world, player_entity):
        self.world              = world
        self.player_entity      = player_entity
        self._scale              = 1.0
        self._menu_keys          = {}
        self._window_mode_pref   = "windowed"
        self._logged_char_name   = "TestChar"
        self.hotbar_calls        = 0
        self.consumable_calls    = 0
        self.send_hotbar_calls   = 0
        self.load_menu_keys_calls = 0

    def _hotbar_to_dict(self) -> dict:
        self.hotbar_calls += 1
        return {"slots": ["INSTANCE_SKILL"], "keybinds": ["1"]}

    def _consumable_bar_to_dict(self) -> dict:
        self.consumable_calls += 1
        return {"slots": [None] * 5, "keybinds": []}

    def _load_menu_keys(self) -> None:
        self.load_menu_keys_calls += 1

    def _send_hotbar_update(self) -> None:
        self.send_hotbar_calls += 1


def _make_save_config_fixture(active: bool):
    from engine.world import World
    from engine.components import PlayerControlled
    from ui.ui_components import InstanceInventoryUIState
    from game import GameEngine as _GE

    world = World()
    eid = world.create_entity()
    world.add_component(eid, PlayerControlled())
    iius = InstanceInventoryUIState()
    iius.active = active
    world.add_component(eid, iius)

    fx = _SaveConfigFixture(world, eid)
    _SaveConfigFixture._save_config = _GE._save_config
    return fx


def test_save_config_preserva_hotbar_real_enquanto_na_bg(monkeypatch):
    fx = _make_save_config_fixture(active=True)
    real_hotbar = {"slots": ["REAL_SKILL"], "keybinds": ["3"]}
    fake_cfg = _FakeCfgFile({"characters": {
        "TestChar": {"hotbar": real_hotbar, "consumable_bar": {"slots": ["Poção"], "keybinds": []}},
    }})
    monkeypatch.setattr("config.load", fake_cfg.load)
    monkeypatch.setattr("config.save", fake_cfg.save)

    fx._save_config()

    assert fx.hotbar_calls == 0, "não deveria nem serializar a hotbar da instância"
    assert fx.consumable_calls == 0
    assert fx.send_hotbar_calls == 0, "não deveria mandar HOTBAR_UPDATE com estado de instância"
    saved_char = fake_cfg.saved["characters"]["TestChar"]
    assert saved_char["hotbar"] == real_hotbar, "hotbar real deveria continuar intacta em config.json"
    assert saved_char["consumable_bar"] == {"slots": ["Poção"], "keybinds": []}


def test_save_config_grava_normal_fora_da_bg(monkeypatch):
    fx = _make_save_config_fixture(active=False)
    fake_cfg = _FakeCfgFile({"characters": {}})
    monkeypatch.setattr("config.load", fake_cfg.load)
    monkeypatch.setattr("config.save", fake_cfg.save)

    fx._save_config()

    assert fx.hotbar_calls == 1
    assert fx.consumable_calls == 1
    assert fx.send_hotbar_calls == 1
    saved_char = fake_cfg.saved["characters"]["TestChar"]
    assert saved_char["hotbar"]["slots"] == ["INSTANCE_SKILL"]


def test_save_config_outras_settings_continuam_salvando_na_bg(monkeypatch):
    """scale/volume/etc não são de personagem — não devem ser bloqueados
    pelo guard de instância."""
    fx = _make_save_config_fixture(active=True)
    fx._scale = 2.5
    fake_cfg = _FakeCfgFile({"characters": {}})
    monkeypatch.setattr("config.load", fake_cfg.load)
    monkeypatch.setattr("config.save", fake_cfg.save)

    fx._save_config()

    assert fake_cfg.saved["scale"] == 2.5


# ─────────────────────────────────────────────────────────────────────────
# 2) Servidor — skill concedida na BG nasce no MESMO slot da hotbar real
# ─────────────────────────────────────────────────────────────────────────

class TestInstanceSkillPreferredSlot(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.eid = spawn_player(self.ws, "p1", 10, 10, class_id="guerreiro")

    def _set_real_slot(self, slot_idx: int, skill_id: str) -> None:
        from engine.components import PlayerSkills
        from content.skill_config import SKILL_CATALOG
        ps = self.ws.world.get_component(self.eid, PlayerSkills)
        ps.skills[slot_idx] = PlayerSkills._make_skill(skill_id, SKILL_CATALOG)
        ps.learned_skill_ids.add(skill_id)

    def test_primeira_skill_desbloqueada_nasce_no_slot_real(self):
        """guerreiro: unlock_order[0] == 'golpe_poderoso'. Jogador de
        verdade tem essa skill no slot 3 (índice 3) da hotbar — dentro da
        BG ela deve nascer no MESMO slot, não no primeiro vazio (índice 0)."""
        from server.instance_progression import enter_normalized_progression
        from engine.components import PlayerSkills

        self._set_real_slot(3, "golpe_poderoso")

        enter_normalized_progression(self.ws, self.eid)

        new_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertEqual(new_ps.skills[3].skill_id, "golpe_poderoso",
            "skill deveria nascer no slot 3, igual à hotbar real")
        self.assertIsNone(new_ps.skills[0],
            "slot 0 deveria continuar vazio (não é o preferido nem o único livre)")

    def test_skill_sem_slot_real_cai_no_primeiro_vazio(self):
        """Se o player nunca colocou a skill numa hotbar de verdade
        (comportamento original, decisão #10), continua caindo no
        primeiro slot livre."""
        from server.instance_progression import enter_normalized_progression
        from engine.components import PlayerSkills

        enter_normalized_progression(self.ws, self.eid)

        new_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertEqual(new_ps.skills[0].skill_id, "golpe_poderoso")


# ─────────────────────────────────────────────────────────────────────────
# 3) HOTBAR_UPDATE aplica no PlayerSkills AO VIVO (05/08/2026, causa raiz
#    real do fix 2 acima "não funcionar": editar a hotbar durante a sessão
#    (sem relogar) nunca chegava no componente ao vivo — só num cache de
#    save — então enter_normalized_progression sempre lia dado do LOGIN,
#    não da edição recente.
# ─────────────────────────────────────────────────────────────────────────

class TestHotbarUpdateAppliesLive(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login
        self.ws, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(self.mgr, "s1", "user_hbu", class_id="guerreiro")
        self.eid = self.ws._player_eids[self.session.session_id]

    async def _send_hotbar_update(self, skills: list) -> None:
        from shared.messages import encode, MsgType
        await self.mgr.on_message(self.session, encode(MsgType.HOTBAR_UPDATE, {
            "skills": skills, "consumables": [],
        }))

    async def test_reordenar_hotbar_reflete_no_playerskills_ao_vivo(self):
        from engine.components import PlayerSkills
        ps = self.ws.world.get_component(self.eid, PlayerSkills)
        ps.learned_skill_ids.add("golpe_poderoso")

        skills = [None] * len(ps.skills)
        skills[3] = "golpe_poderoso"
        await self._send_hotbar_update(skills)

        self.assertEqual(ps.skills[3].skill_id, "golpe_poderoso",
            "PlayerSkills ao vivo deveria refletir a edição imediatamente, sem precisar relogar")

    async def test_hotbar_update_nao_concede_skill_nao_aprendida(self):
        """Segurança: HOTBAR_UPDATE reordena, nunca CONCEDE — um cliente
        mandando um skill_id que o player não tem aprendido não deveria
        conseguir a skill de graça."""
        from engine.components import PlayerSkills
        ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertNotIn("calamidade_flamejante", ps.learned_skill_ids)

        skills = [None] * len(ps.skills)
        skills[3] = "calamidade_flamejante"
        await self._send_hotbar_update(skills)

        self.assertIsNone(ps.skills[3], "skill não aprendida não deveria aparecer no slot")

    async def test_reordenacao_ao_vivo_alimenta_slot_preferido_da_instancia(self):
        """Ponta a ponta: edita a hotbar via mensagem de rede (sem
        relogar) e SÓ DEPOIS entra na BG — a skill de instância deve
        nascer no slot que acabou de ser configurado."""
        from engine.components import PlayerSkills
        from server.instance_progression import enter_normalized_progression
        ps = self.ws.world.get_component(self.eid, PlayerSkills)
        ps.learned_skill_ids.add("golpe_poderoso")

        skills = [None] * len(ps.skills)
        skills[3] = "golpe_poderoso"
        await self._send_hotbar_update(skills)

        enter_normalized_progression(self.ws, self.eid)

        new_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertEqual(new_ps.skills[3].skill_id, "golpe_poderoso",
            "slot editado na MESMA sessão (sem relogar) deveria valer pro slot preferido da instância")


if __name__ == "__main__":
    unittest.main(verbosity=2)
