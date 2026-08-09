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

3. Fase 7 (06/08/2026) — bug RECORRENTE relatado numa sessão de
   playtest posterior: "a barra de ações desconfigurou de novo, já
   tinha funcionado antes". Causa raiz — TERCEIRO ponto, diferente dos
   dois acima (que já cobriam cliente/config.json e slot preferido de
   instância): a PERSISTÊNCIA no banco. `client/save_sync_handlers.py::
   _collect_save_state` manda `skills: {"learned": [...]}` de propósito
   SEM "hotbar" (comentário: "layout é UI local"); `_handle_hotbar_update`
   (session.py) só atualiza o CACHE da sessão via merge parcial, nunca
   persiste sozinho; `_handle_save_state` faz REPLACE TOTAL desse cache
   (`session.last_client_payload = payload`) e já persiste na sequência
   — como o payload do cliente nunca tem "hotbar", esse replace apaga o
   que o merge parcial tinha posto lá, e a hotbar salva no banco fica
   sem layout. Qualquer SAVE_STATE "mudo" (disparado sem o jogador
   perceber — ex.: `client/network_handlers.py` manda um a cada
   STATS_UPDATE com inv_snapshot/equip_snapshot, o que acontece tanto ao
   ENTRAR quanto ao SAIR de battleground, ver instance_progression.py)
   reproduz isso. A hotbar AO VIVO nunca quebra (item 2 do fix anterior
   já garante isso) — só o valor GRAVADO no banco, que só aparece
   quebrado no PRÓXIMO login, daí a sensação de "funcionou, depois
   desconfigurou de novo" em vez de "nunca funcionou". Mesmo padrão de
   bug já corrigido uma vez pro equipamento (aljava sempre cheia no
   relogin, ver `_build_save_merge`) — mesmo fix: `live_hotbar`
   (`WorldServer.get_player_hotbar_data`, lido do `PlayerSkills` ao vivo
   na hora do save) substitui o cache do cliente pra esse subcampo,
   incondicional quando presente. Ver ARQUITETURA_ONLINE.md §34.74.47.

4. Fase 9 (06/08/2026) — TERCEIRA ocorrência do mesmo padrão (equipamento,
   hotbar acima), agora em TALENTOS: personagem saía da BG com TODOS os
   talentos no máximo. `enter_normalized_progression`
   (server/instance_progression.py) troca o TalentTree por um overlay
   com todo talento do build maxado (decisão de design da instância) —
   qualquer TALENT_UPDATE ou SAVE_STATE mandado enquanto dentro cacheia
   esse overlay em `client_payload["talents"]` (diferente de hotbar, o
   cliente NÃO omite "talents" de propósito), e nada limpa esse cache
   quando `exit_normalized_progression` restaura o TalentTree real —
   próximo save persistia o overlay maxado. Mesmo fix: `live_talents`
   (`WorldServer.get_player_talent_data`, lido do TalentTree ao vivo na
   hora do save — sempre o real, já que `_persist_character` recusa
   persistir dentro da instância) substitui o cache pra esse campo. Ver
   ARQUITETURA_ONLINE.md §34.74.49.

5. Fase 0 do roteiro de saneamento (07/08/2026, ver
   PROBLEMAS_ARQUITETURA.md §12/§13) — QUARTA e QUINTA ocorrência do
   mesmo padrão, achadas por auditoria (não por relato de playtest,
   ainda não tinham acontecido de verdade em produção):
   - `inventory`: nunca teve fallback ao vivo, só `client_p.get("inventory")`.
     Mesmo overlay de 6 slots da instância contamina o cache do mesmo jeito
     que equipment/talents contaminavam. Fix: `live_inventory`
     (`WorldServer.get_player_inventory_data`).
   - `skills["learned"]`: só `hotbar` tinha sido corrigido na Fase 7 —
     `learned` continuava preferindo o cache do cliente. Vetor real:
     desconectar DENTRO da instância (`on_disconnect` restaura o
     PlayerSkills real e persiste na sequência, sem round-trip de
     SAVE_STATE pra corrigir o cache antes). Fix: inverte a prioridade —
     `srv_data.get("skills")` (sempre ao vivo) vence por padrão.
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

    def test_skill_de_preenchimento_nao_rouba_slot_reservado_por_skill_tardia(self):
        """Fase 10 (06/08/2026, bug real de playtest): 'golpe_poderoso'
        (unlock_order[0], desbloqueia na ENTRADA) não está na hotbar real
        — cairia greedy no slot 0 (primeiro vazio). 'interceptar'
        (unlock_order[5], só desbloqueia no level 6) É o dono real do
        slot 0. Sem reserved_slots, golpe_poderoso rouba o slot 0 na
        entrada e interceptar cai em outro lugar quando finalmente
        desbloqueia — reproduz exatamente o relatado (hotbar da
        instância não bate com a real mesmo pra skill configurada)."""
        from server.instance_progression import (
            enter_normalized_progression, _process_instance_levelup)
        from engine.components import PlayerSkills, CharacterStats

        self._set_real_slot(0, "interceptar")  # slot 0 é o dono real — golpe_poderoso NÃO está na hotbar

        enter_normalized_progression(self.ws, self.eid)
        new_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertNotEqual(getattr(new_ps.skills[0], "skill_id", None), "golpe_poderoso",
            "golpe_poderoso (sem slot real) não deveria roubar o slot 0 — reservado pro interceptar")

        # Sobe até o cap pra garantir que interceptar (level 6) desbloqueia.
        char = self.ws.world.get_component(self.eid, CharacterStats)
        char.current_xp = 10 ** 9
        _process_instance_levelup(self.ws, self.eid)

        new_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertEqual(new_ps.skills[0].skill_id, "interceptar",
            "interceptar deveria nascer no slot 0 (igual à hotbar real), mesmo "
            "desbloqueando bem depois de golpe_poderoso na ordem fixa da instância")


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


# ─────────────────────────────────────────────────────────────────────────
# 4) Fase 7 (06/08/2026) — hotbar server-autoritativa NA PERSISTÊNCIA
#    (terceiro ponto, diferente de 1 e 2 acima — ver docstring do módulo).
# ─────────────────────────────────────────────────────────────────────────

class TestHotbarPersistedFromLiveECS(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login
        self.ws, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(self.mgr, "s1", "user_hbp", class_id="guerreiro")
        self.eid = self.ws._player_eids[self.session.session_id]

    async def _send_hotbar_update(self, skills: list) -> None:
        from shared.messages import encode, MsgType
        await self.mgr.on_message(self.session, encode(MsgType.HOTBAR_UPDATE, {
            "skills": skills, "consumables": [],
        }))

    async def _send_mudo_save_state(self, learned: list) -> None:
        """Reproduz o payload REAL que client/save_sync_handlers.py::
        _collect_save_state monta hoje — "skills" só com "learned", nunca
        "hotbar" (comentário no código: "layout é UI local"). Esse é o
        payload que qualquer SAVE_STATE automático manda, inclusive o
        disparado sem o jogador perceber ao entrar/sair de battleground
        (client/network_handlers.py, gatilho de inv_snapshot/equip_snapshot)."""
        from shared.messages import encode, MsgType
        await self.mgr.on_message(self.session, encode(MsgType.SAVE_STATE, {
            "skills": {"learned": learned},
        }))

    async def test_hotbar_sobrevive_a_save_state_mudo_sem_hotbar_no_payload(self):
        """Reproduz o bug real: reordena a hotbar (slot 3), depois manda
        um SAVE_STATE "mudo" (mesmo formato que o autosave de entrada/
        saída de BG dispara) — a hotbar persistida no banco não pode
        desaparecer, mesmo o payload do cliente nunca a incluindo."""
        from engine.components import PlayerSkills
        ps = self.ws.world.get_component(self.eid, PlayerSkills)
        ps.learned_skill_ids.add("golpe_poderoso")

        skills = [None] * len(ps.skills)
        skills[3] = "golpe_poderoso"
        await self._send_hotbar_update(skills)

        await self._send_mudo_save_state(["golpe_poderoso"])

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertIsNotNone(merged, "save deveria ter acontecido (personagem fora de instância)")
        hotbar = merged["skills"]["hotbar"]
        self.assertEqual(hotbar[3], "golpe_poderoso",
            "hotbar persistida no banco deveria refletir o PlayerSkills ao vivo, "
            "não o cache do cliente (que nunca inclui \"hotbar\" no SAVE_STATE real)")

    async def test_learned_bate_quando_cliente_e_servidor_concordam(self):
        """Caso comum (fluxo normal, fora de instância): cliente e ECS ao
        vivo concordam, então o resultado é o mesmo independente de qual
        fonte vence — não discrimina Fase 0 (ver
        test_learned_vem_do_servidor_ao_vivo_nunca_do_cache abaixo pro
        teste que realmente prova a mudança de prioridade)."""
        from engine.components import PlayerSkills
        ps = self.ws.world.get_component(self.eid, PlayerSkills)
        ps.learned_skill_ids.add("golpe_poderoso")
        ps.learned_skill_ids.add("interceptar")

        await self._send_mudo_save_state(["golpe_poderoso", "interceptar"])

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertEqual(set(merged["skills"]["learned"]), {"golpe_poderoso", "interceptar"})

    async def test_learned_vem_do_servidor_ao_vivo_nunca_do_cache(self):
        """Fase 0 (07/08/2026): reproduz o vetor real — desconectar DENTRO
        da instância. Entra na BG (PlayerSkills vira overlay pequeno),
        manda um SAVE_STATE nesse estado (cacheia o "learned" reduzido da
        instância — mesmo formato que o autosave de entrada dispara de
        verdade), sai da BG (PlayerSkills real restaurado, com TODAS as
        skills reais). O merge tem que persistir o conjunto REAL, não o
        cache contaminado pela instância."""
        from engine.components import PlayerSkills
        from server.instance_progression import (
            enter_normalized_progression, exit_normalized_progression)

        ps = self.ws.world.get_component(self.eid, PlayerSkills)
        ps.learned_skill_ids.update({"golpe_poderoso", "impacto", "interceptar"})

        enter_normalized_progression(self.ws, self.eid)
        overlay_ps = self.ws.world.get_component(self.eid, PlayerSkills)
        self.assertLess(len(overlay_ps.learned_skill_ids), 3,
            "setup inválido — overlay da instância deveria ter menos skills que o real")
        await self._send_mudo_save_state(list(overlay_ps.learned_skill_ids))

        exit_normalized_progression(self.ws, self.eid)

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertIsNotNone(merged, "save deveria ter acontecido (fora da instância)")
        self.assertEqual(set(merged["skills"]["learned"]),
            {"golpe_poderoso", "impacto", "interceptar"},
            "learned persistido deveria ser o conjunto REAL (restaurado na saída), "
            "não o reduzido da instância que ficou cacheado no SAVE_STATE")


# ─────────────────────────────────────────────────────────────────────────
# 5) Fase 9 (06/08/2026) — talentos server-autoritativos NA PERSISTÊNCIA
#    (terceira ocorrência do mesmo padrão — ver docstring do módulo).
# ─────────────────────────────────────────────────────────────────────────

class TestTalentsNotLeakedFromInstanceOverlay(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login
        self.ws, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(self.mgr, "s1", "user_talp", class_id="guerreiro")
        self.eid = self.ws._player_eids[self.session.session_id]

    async def _send_talent_update(self, talents: dict) -> None:
        from shared.messages import encode, MsgType
        await self.mgr.on_message(self.session, encode(MsgType.TALENT_UPDATE, {
            "talents": talents,
        }))

    async def test_talentos_maxados_da_instancia_nao_vazam_pra_persistencia(self):
        """Reproduz o bug real: entra na BG (talentos viram overlay
        maxado, decisão de design), manda um TALENT_UPDATE nesse estado
        (mesmo formato que o cache real receberia — ex.: fechar/reabrir
        o painel de talentos dentro da instância), sai da BG (TalentTree
        real restaurado) — os talentos persistidos precisam ser os REAIS
        (poucos pontos), não o overlay maxado que ficou cacheado."""
        from engine.components import TalentTree
        from server.instance_progression import (
            enter_normalized_progression, exit_normalized_progression)

        real_tt = self.ws.world.get_component(self.eid, TalentTree)
        real_tt.chosen_build = "cavaleiro"
        real_tt.allocated = {"talento_real_do_jogador": 2}
        real_tt.available_points = 0

        enter_normalized_progression(self.ws, self.eid)
        overlay_tt = self.ws.world.get_component(self.eid, TalentTree)
        self.assertGreater(len(overlay_tt.allocated), 1,
            "setup inválido — overlay da instância deveria ter vários talentos maxados")
        await self._send_talent_update({
            "chosen_build":      overlay_tt.chosen_build,
            "allocated":         dict(overlay_tt.allocated),
            "available_points":  overlay_tt.available_points,
        })

        exit_normalized_progression(self.ws, self.eid)

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertIsNotNone(merged, "save deveria ter acontecido (personagem fora de instância)")
        self.assertEqual(merged["talents"]["allocated"], {"talento_real_do_jogador": 2},
            "talentos persistidos deveriam ser os REAIS (restaurados na saída), "
            "não o overlay maxado da BG que ficou cacheado no TALENT_UPDATE")

    async def test_talentos_alocados_fora_de_instancia_continuam_normais(self):
        """Regressão: fluxo comum (nunca entra em BG) continua persistindo
        a alocação real via TALENT_UPDATE normalmente."""
        from content.talent_data import TALENTS
        from engine.components import TalentTree
        real_tt = self.ws.world.get_component(self.eid, TalentTree)
        # Escolhe um talento de verdade do build atual pra passar por
        # validate_talent_allocation (que descarta ids desconhecidos).
        _tid = next(tid for tid, t in TALENTS.items() if t.get("build") == real_tt.chosen_build)
        real_tt.available_points = 5

        await self._send_talent_update({
            "chosen_build": real_tt.chosen_build,
            "allocated": {_tid: 1},
            "available_points": 5,
        })

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertEqual(merged["talents"]["allocated"], {_tid: 1})


# ─────────────────────────────────────────────────────────────────────────
# 6) Fase 0 do roteiro de saneamento (07/08/2026) — inventário
#    server-autoritativo NA PERSISTÊNCIA (quarta ocorrência do mesmo
#    padrão — ver docstring do módulo).
# ─────────────────────────────────────────────────────────────────────────

class TestInventoryPersistedFromLiveECS(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        from tests.test_session import make_session_manager, fake_login
        self.ws, self.mgr = make_session_manager()
        self.session, self.fw = await fake_login(self.mgr, "s1", "user_invp", class_id="guerreiro")
        self.eid = self.ws._player_eids[self.session.session_id]

    async def _send_save_state_with_inventory(self, item_dicts: list) -> None:
        from shared.messages import encode, MsgType
        await self.mgr.on_message(self.session, encode(MsgType.SAVE_STATE, {
            "inventory": item_dicts,
        }))

    def _real_item(self, key: str):
        from content.item_table import ITEMS
        return ITEMS[key]()

    async def test_inventario_sobrevive_a_desconexao_dentro_da_instancia(self):
        """Reproduz o vetor real: entra na BG (Inventory vira overlay de 6
        slots, vazio), manda um SAVE_STATE nesse estado (cacheia o
        inventário vazio da instância), sai da BG (Inventory real
        restaurado, com o item de verdade) — o merge tem que persistir o
        item REAL, não o cache vazio contaminado pela instância."""
        from engine.components import Inventory
        real_item = self._real_item("training_sword")
        inv = self.ws.world.get_component(self.eid, Inventory)
        # Zera antes de popular — mesmo motivo do teste "vazio_de_verdade"
        # acima: username reaproveitado entre métodos desta classe pode
        # carregar item persistido por outro teste anterior no mesmo banco.
        inv.items.clear()
        inv.items.append(real_item)

        from server.instance_progression import (
            enter_normalized_progression, exit_normalized_progression)
        enter_normalized_progression(self.ws, self.eid)
        overlay_inv = self.ws.world.get_component(self.eid, Inventory)
        self.assertEqual(len(overlay_inv.items), 0,
            "setup inválido — overlay da instância deveria começar vazio")
        await self._send_save_state_with_inventory([])

        exit_normalized_progression(self.ws, self.eid)

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertIsNotNone(merged, "save deveria ter acontecido (fora da instância)")
        self.assertEqual(len(merged["inventory"]), 1,
            "inventário persistido deveria ter o item REAL (restaurado na saída), "
            "não a bag vazia da instância que ficou cacheada no SAVE_STATE")
        self.assertEqual(merged["inventory"][0]["name"], real_item.name)

    async def test_inventario_vazio_de_verdade_nao_e_tratado_como_indisponivel(self):
        """Regressão do cuidado com truthy vs `is not None`: um personagem
        SEM itens (bag real vazia, fora de instância) precisa persistir
        `[]`, não cair pro cache do cliente por engano. Limpa o Inventory
        ao vivo explicitamente em vez de assumir "personagem recém-logado
        = vazio" — outros testes desta classe podem ter persistido um
        item de verdade pro MESMO username antes (mesma conta reaproveitada
        entre métodos de teste, igual às outras classes deste arquivo),
        então a única forma confiável de garantir "vazio" é zerar o
        componente na hora, igual ao resto do arquivo faz pra hotbar/talents."""
        from engine.components import Inventory
        inv = self.ws.world.get_component(self.eid, Inventory)
        inv.items.clear()

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertIsNotNone(merged)
        self.assertEqual(merged["inventory"], [],
            "bag real vazia deveria persistir como lista vazia, não None/cache")

    async def test_inventario_normal_fora_de_instancia_continua_funcionando(self):
        """Regressão: fluxo comum (nunca entra em BG, item lootado/comprado
        de verdade) continua persistindo normalmente. INV_SYNC (não
        SAVE_STATE) é o canal real que mantém o Inventory AO VIVO
        sincronizado — mesmo mecanismo do cliente real (loot/compra),
        chamado por `_handle_inventory_update`/`sync_player_inventory`."""
        from shared.messages import encode, MsgType
        real_item = self._real_item("training_mace")
        await self.mgr.on_message(self.session, encode(MsgType.INV_SYNC, {
            "inventory": [{"name": real_item.name, "item_type": real_item.item_type}],
        }))

        merged = await self.mgr._persist_character(self.session, context="test")
        self.assertEqual(len(merged["inventory"]), 1)
        self.assertEqual(merged["inventory"][0]["name"], real_item.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
