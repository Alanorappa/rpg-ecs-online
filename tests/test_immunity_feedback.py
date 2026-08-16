"""
tests/test_immunity_feedback.py — "Imune" no floating text (07/08/2026,
ver PROBLEMAS_ARQUITETURA.md/ARQUITETURA_ONLINE.md). Pedido do usuário,
generalizado a partir de uma característica esquecida do Fatiador de
Corpos: (1) imunidade de DANO mostra "Imune" (gap existente — o outcome
"immune" já existia em `deal_damage()` mas nunca disparava feedback);
(2) imunidade de CONTROLE (stun/sleep/fear/root/polymorph/disoriented/
slow — tudo que "tira o controle do jogador" ou mobilidade, NUNCA dano)
também mostra "Imune" quando algo tenta aplicar.

Mecanismo (revisado 07/08/2026 — 1ª versão usava um flag booleano em
CombatState, específico de slow/root; usuário pediu algo dinâmico,
reusável por qualquer skill futura, cobrindo TODO efeito de controle):
a imunidade é o PRÓPRIO status effect `"cc_immune"` (mesma infra de
duração/expiração/ícone/sync de qualquer outro efeito — StatusEffectSystem
cuida da expiração sozinho, nenhum flag bespoke). `apply_effect()`
(engine/core_systems.py) bloqueia genericamente qualquer efeito com
`blocks_move`/`blocks_act` (EFFECT_DEFS) + "slow" quando o alvo tem
`StatusEffects.has("cc_immune")` — nenhum nome de efeito hardcoded, cobre
efeito de controle NOVO automaticamente. Referência: AzerothCore separa
imunidade de MECÂNICA (SPELL_AURA_MECHANIC_IMMUNITY_MASK) de imunidade a
dano — mesma separação aqui.

Cobre:
  - `apply_effect()` bloqueia qualquer efeito de controle (stun/root/
    polymorph/slow) quando o alvo tem "cc_immune" ativo, aplica normal
    sem — prova diferencial. DoT (dano) nunca é bloqueado.
  - `deal_damage()` (melee) retorna outcome "immune" pra alvo com
    `CombatState.is_immune` — já existia, confirma que continua certo.
  - Fatiador de Corpos: ativação real (CAST_SKILL) concede "cc_immune",
    dispela QUALQUER efeito de controle já ativo (não só slow/root),
    genericamente — nenhuma lista de nomes hardcoded no handler.
  - Dano mágico servidor (Nova Congelante) contra alvo imune não fica
    mais silencioso — outcome "immune" chega no relatório.
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()

from tests.helpers import make_world_server, spawn_player, authorize_skill, run_ticks
from engine.components import (
    CombatState, StatusEffects, ActiveEffect, CharacterStats, CombatStats,
)


class TestApplyEffectBloqueiaControleComImunidade(unittest.TestCase):

    def setUp(self):
        from engine.world import World
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, CombatState())

    def _grant_immunity(self):
        from engine.core_systems import apply_effect
        apply_effect(self.world, self.eid, "cc_immune", duration=5.0)

    def _apply(self, effect_type: str, magnitude: float = 0.5):
        from engine.core_systems import apply_effect
        apply_effect(self.world, self.eid, effect_type, duration=3.0, magnitude=magnitude)

    def test_sem_imunidade_aplica_normal(self):
        self._apply("slow")
        sfx = self.world.get_component(self.eid, StatusEffects)
        self.assertIsNotNone(sfx)
        self.assertTrue(sfx.has("slow"))

    def test_com_imunidade_bloqueia_slow(self):
        self._grant_immunity()
        self._apply("slow")
        sfx = self.world.get_component(self.eid, StatusEffects)
        self.assertFalse(sfx.has("slow"))

    def test_com_imunidade_bloqueia_root(self):
        self._grant_immunity()
        self._apply("root")
        sfx = self.world.get_component(self.eid, StatusEffects)
        self.assertFalse(sfx.has("root"))

    def test_com_imunidade_bloqueia_stun(self):
        """Generalização pedida pelo usuário (07/08/2026): não é só slow/
        root, é qualquer coisa que tire o controle — stun tem
        blocks_move=blocks_act=True no catálogo, o guard genérico pega
        sem precisar listar "stun" em lugar nenhum do código."""
        self._grant_immunity()
        self._apply("stun", magnitude=0.0)
        sfx = self.world.get_component(self.eid, StatusEffects)
        self.assertFalse(sfx.has("stun"))

    def test_com_imunidade_bloqueia_polymorph(self):
        self._grant_immunity()
        self._apply("polymorph", magnitude=0.0)
        sfx = self.world.get_component(self.eid, StatusEffects)
        self.assertFalse(sfx.has("polymorph"))

    def test_imunidade_nao_bloqueia_dano_periodico(self):
        """"A única coisa que ele não é imune é a dano" (usuário,
        verbatim) — DoT continua aplicando normalmente com cc_immune
        ativo, só efeito de controle é bloqueado."""
        self._grant_immunity()
        self._apply("poison", magnitude=5.0)
        sfx = self.world.get_component(self.eid, StatusEffects)
        self.assertTrue(sfx.has("poison"))

    def test_prova_diferencial_sem_conceder_cc_immune_aplicaria(self):
        """Confirma que é mesmo a concessão de "cc_immune" que bloqueia —
        sem chamar _grant_immunity(), o slow aplica normalmente. Documenta
        o oposto do teste 'com_imunidade' acima como prova de que não é
        coincidência."""
        sfx_before = self.world.get_component(self.eid, StatusEffects)
        self.assertFalse(sfx_before is not None and sfx_before.has("cc_immune"))
        self._apply("slow")
        sfx = self.world.get_component(self.eid, StatusEffects)
        self.assertTrue(sfx.has("slow"))


class TestFatiadorImunidadeESaidaDeEfeitos(unittest.TestCase):

    def setUp(self):
        self.ws = make_world_server()
        self.warrior = spawn_player(self.ws, "fatimm_srv", 130, 374, class_id="guerreiro")
        authorize_skill(self.ws, self.warrior, "fatiador_de_corpos")

    def _cast_fatiador(self):
        sid = self.ws._player_eid_to_sid[self.warrior]
        self.ws.queue_skill(sid, "fatiador_de_corpos", -1, 0.0, 0.0, 0)
        run_ticks(self.ws, 1)

    def test_ativacao_concede_status_effect_cc_immune(self):
        self._cast_fatiador()
        sfx = self.ws.world.get_component(self.warrior, StatusEffects)
        self.assertIsNotNone(sfx)
        self.assertTrue(sfx.has("cc_immune"))

    def test_ativacao_dispela_slow_e_root_ativos_via_loop_dinamico(self):
        """Nenhum efeito é listado por nome no handler do Fatiador — o
        loop dinâmico (engine/core_systems.EFFECT_DEFS) que pega
        todos, não só os 2 que o pedido original mencionava.

        SÓ slow/root aqui de propósito — stun/sleep/fear/polymorph/
        disoriented têm blocks_act=True (EFFECT_DEFS), então
        is_action_locked() já rejeita o CAST_SKILL do próprio Fatiador
        ANTES de qualquer handler rodar (achado real ao escrever este
        teste: incluir stun/polymorph aqui fazia o cast inteiro falhar,
        não só o dispel — comportamento CORRETO, mesma lógica de
        qualquer MMO: não dá pra castar parado enquanto atordoado, então
        essas duas não são "escapáveis" via auto-cast, só via imunidade
        PRÉVIA — já coberto por test_com_imunidade_bloqueia_stun/
        _polymorph na outra classe)."""
        from engine.core_systems import apply_effect
        apply_effect(self.ws.world, self.warrior, "slow", duration=10.0, magnitude=0.5)
        apply_effect(self.ws.world, self.warrior, "root", duration=10.0)
        sfx_before = self.ws.world.get_component(self.warrior, StatusEffects)
        for _eff in ("slow", "root"):
            self.assertTrue(sfx_before.has(_eff), f"setup falhou pra {_eff}")

        self._cast_fatiador()

        sfx_after = self.ws.world.get_component(self.warrior, StatusEffects)
        for _eff in ("slow", "root"):
            self.assertFalse(sfx_after.has(_eff), f"{_eff} não foi dispelado")
        # Efeito derivado (velocidade) também precisa refletir na hora, não
        # só na próxima tick natural do StatusEffectSystem.
        from engine.components import TileMovement
        tm = self.ws.world.get_component(self.warrior, TileMovement)
        self.assertEqual(tm.slow_mult, 1.0)

    def test_ativacao_nao_dispela_dano_periodico(self):
        from engine.core_systems import apply_effect
        apply_effect(self.ws.world, self.warrior, "poison", duration=10.0, magnitude=5.0)
        self._cast_fatiador()
        sfx = self.ws.world.get_component(self.warrior, StatusEffects)
        self.assertTrue(sfx.has("poison"))

    def test_imunidade_expira_sozinha_apos_a_duracao(self):
        """cc_immune é um status effect de verdade agora (não um flag
        manual) — StatusEffectSystem já expira sozinho, mesma infra de
        qualquer outro efeito do jogo. Duração vem do catálogo (skill.params
        via _skill_fatiador_de_corpos), não hardcoded aqui."""
        from content.skill_config import SKILL_CATALOG
        duration = SKILL_CATALOG["fatiador_de_corpos"]["params"]["duration"]
        self._cast_fatiador()
        sfx = self.ws.world.get_component(self.warrior, StatusEffects)
        self.assertTrue(sfx.has("cc_immune"))
        run_ticks(self.ws, int(duration / 0.05) + 20)  # além da duração real
        sfx_after = self.ws.world.get_component(self.warrior, StatusEffects)
        self.assertFalse(sfx_after is not None and sfx_after.has("cc_immune"))


class TestOutcomeImuneNoDanoMagicoDoServidor(unittest.TestCase):
    """Nova Congelante (self-centered AOE, sem projétil) contra alvo com
    CombatState.is_immune=True não pode ficar silencioso — antes desta
    correção, o servidor simplesmente não reportava nada."""

    def setUp(self):
        self.ws = make_world_server()
        self.mage  = spawn_player(self.ws, "immg_mage", 130, 374, class_id="mago")
        self.victim = spawn_player(self.ws, "immg_victim", 131, 374, class_id="guerreiro")
        self.assertIsNone(self.ws.request_duel(self.mage, self.victim))
        self.ws.respond_duel_invite(self.victim, accept=True)
        authorize_skill(self.ws, self.mage, "nova_congelante")
        char = self.ws.world.get_component(self.mage, CharacterStats)
        char.mana = 999

    def test_alvo_imune_reporta_outcome_immune_nao_fica_mudo(self):
        """Nova Congelante NÃO tem projétil — o resultado da conclusão do
        cast vai por SKILL_RESULT (WorldServer.consume_skill_results()),
        canal separado de _combat_this_tick/_collect_deltas (esse último é
        só pra dano fora do fluxo de cast, ex.: auto-attack/canalização)."""
        cst = self.ws.world.get_component(self.victim, CombatState)
        cst.is_immune = True

        sid = self.ws._player_eid_to_sid[self.mage]
        self.ws.queue_skill(sid, "nova_congelante", -1, 0.0, 0.0, 0)
        all_results = []
        for _ in range(40):  # tempo suficiente pro cast completar
            self.ws._tick(0.05)
            all_results.extend(self.ws.consume_skill_results())

        targets_hit = [t for r in all_results for t in r.get("targets", [])
                       if t.get("eid") == self.victim]
        self.assertTrue(targets_hit, "nenhum resultado chegou pro alvo imune — "
                                     "ficou mudo de novo")
        # Não afirma exatamente QUANDO chega (depende do cast_time), só que
        # quando chegar, o outcome é "immune" — nunca dano de verdade.
        for t in targets_hit:
            self.assertEqual(t["outcome"], "immune")
            self.assertEqual(t["damage"], 0)


class TestOutcomePorAlvoEmAoeSemTid(unittest.TestCase):
    """Pirofagia (cone self-centered, sem "tid" explícito — não precisa de
    alvo pra usar) contra 2 mobs no cone, 1 imune e 1 normal — cada um
    precisa reportar o PRÓPRIO outcome (10/08/2026, PROBLEMAS_ARQUITETURA.md
    §14: achado real do usuário durante revisão do fix de "Imune" nesta
    mesma sessão — pesquisa em Veloren (Outcome::HealthChange carrega
    target: Uid) e AzerothCore (TargetInfo por alvo, 1 pacote de dano por
    alvo) confirmou que outcome tem que ser por-alvo, nunca 1 valor
    compartilhado pro cast inteiro).

    Antes desta correção: `server/skill_processor.py` montava a lista de
    resultados por alvo (`results_targets`), mas o campo `outcome` de CADA
    entrada vinha de UMA variável só (`last_outcome`, populada pelo dano
    FÍSICO de alvo único) — e um alvo com damage==0 só entrava no relatório
    se fosse o "tid" explícito. Pirofagia nunca tem tid (cone sem alvo) —
    então um mob imune atingido pelo cone NUNCA aparecia no relatório,
    nem o dano (óbvio, é 0) nem o "Imune" (não tinha como saber)."""

    def setUp(self):
        self.ws = make_world_server()
        run_ticks(self.ws, 50)  # SpawnZoneSystem precisa de ticks pra spawnar mobs
        self.mage = spawn_player(self.ws, "aoe_mage", 130, 374, class_id="mago")
        authorize_skill(self.ws, self.mage, "pirofagia")
        char = self.ws.world.get_component(self.mage, CharacterStats)
        char.mana = 999

        from engine.components import TrainingDummy, NPC, Tower
        candidatos = [eid for eid in self.ws._mob_eids
                     if self.ws.world.get_component(eid, TrainingDummy) is None
                     and self.ws.world.get_component(eid, NPC) is None
                     and self.ws.world.get_component(eid, Tower) is None]
        self.assertGreaterEqual(len(candidatos), 2,
                                "mapa de teste precisa de pelo menos 2 mobs reais")
        self.mob_immune, self.mob_normal = candidatos[0], candidatos[1]

        from tests.helpers import teleport_mob_to_player
        # Ambos dentro do cone da Pirofagia apontado pra +x (dir_x=1,dir_y=0
        # → cone unrotated, tiles (1,0) e (2,0) da lista _PIRO_CONE).
        teleport_mob_to_player(self.ws, self.mob_immune, self.mage, offset_x=1, offset_y=0)
        teleport_mob_to_player(self.ws, self.mob_normal, self.mage, offset_x=2, offset_y=0)
        for eid in (self.mob_immune, self.mob_normal):
            cs = self.ws.world.get_component(eid, CombatStats)
            cs.current_hp = cs.max_hp
        cst = self.ws.world.get_component(self.mob_immune, CombatState)
        cst.is_immune = True

    def _cast_pirofagia(self) -> list[dict]:
        self.ws._pending_skill_requests.append({
            "player_eid": self.mage, "sid": "pirofagia",
            "tid": -1, "dir_x": 1.0, "dir_y": 0.0,
        })
        self.ws._process_skill_requests()
        results = self.ws.consume_skill_results()
        return [t for r in results for t in r.get("targets", [])]

    def test_alvo_imune_no_cone_reporta_immune_alvo_normal_reporta_dano(self):
        targets = self._cast_pirofagia()
        by_eid = {t["eid"]: t for t in targets}

        self.assertIn(self.mob_immune, by_eid,
                      "alvo imune sumiu do relatório — bug antigo (outcome "
                      "compartilhado, sem tid) voltou")
        self.assertEqual(by_eid[self.mob_immune]["outcome"], "immune")
        self.assertEqual(by_eid[self.mob_immune]["damage"], 0)

        self.assertIn(self.mob_normal, by_eid)
        self.assertEqual(by_eid[self.mob_normal]["outcome"], "hit")
        self.assertGreater(by_eid[self.mob_normal]["damage"], 0)

    def test_prova_diferencial_sem_last_damage_outcomes_alvo_imune_sumiria(self):
        """Substitui LAST_DAMAGE_OUTCOMES por um dict cujo .get() sempre
        retorna None (leitura por-alvo nunca acha nada — mesmo efeito de
        "nunca populou", simulando o comportamento pré-fix onde
        skill_processor.py só tinha o outcome físico compartilhado) e
        confirma que o alvo imune de fato desaparece do relatório — prova
        que o teste acima falharia sem o fix, não é um teste vazio.
        `.get` não pode ser sobrescrito numa INSTÂNCIA de dict (built-in),
        por isso a substituição é via subclasse."""
        class _NeutralizedOutcomes(dict):
            def get(self, *a, **k):
                return None

        from engine import core_systems as _cs_mod
        original = _cs_mod.LAST_DAMAGE_OUTCOMES
        _cs_mod.LAST_DAMAGE_OUTCOMES = _NeutralizedOutcomes()  # DIFFERENTIAL-PROOF-TEMP
        try:
            targets = self._cast_pirofagia()
        finally:
            _cs_mod.LAST_DAMAGE_OUTCOMES = original

        by_eid = {t["eid"]: t for t in targets}
        self.assertNotIn(self.mob_immune, by_eid,
                         "prova diferencial falhou — alvo imune apareceu MESMO "
                         "com LAST_DAMAGE_OUTCOMES neutralizado; o teste "
                         "principal não está provando o que diz provar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
