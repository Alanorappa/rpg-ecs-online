"""tests/test_client_instance_sync.py — client/network_handlers.py::
_handle_msg_stats_update, campos de sync de inventário de instância
(inv_snapshot/equip_snapshot/in_instance).

Bug real relatado pelo usuário (02/08/2026): "não consigo comprar os
itens" na loja de instância. Causa raiz: `server/session.py::
_handle_buy_request` valida espaço de inventário contra
`session.last_client_payload["inventory"]` (o último SAVE_STATE que o
CLIENTE mandou), não contra o `Inventory` ao vivo — sem o cliente mandar
um SAVE_STATE logo após aplicar o swap de instância (inv_snapshot),
`last_client_payload` continuava com a contagem da bag REAL (ex.: 18
itens) enquanto `Inventory.max_slots` ao vivo já tinha virado 6, e toda
compra caía em "inventory_full" na origem. Fix: `_handle_msg_stats_update`
chama `self._send_save_state()` assim que aplica inv_snapshot/
equip_snapshot, garantindo que o servidor aprenda o tamanho/conteúdo
NOVO da bag imediatamente.
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from engine.world import World
from engine.components import (
    Inventory, Equipment, Wallet, PlayerControlled, Item, TalentTree,
    CharacterStats, CombatStats, PlayerSkills, ConsumableBar,
)
from client.network_handlers import NetworkHandlers
from client.save_sync_handlers import SaveSyncHandlers
from client.inventory_handlers import InventoryHandlers


class _FakeNet:
    def __init__(self):
        self.connected = True
        self.sent = []

    def send(self, msg_type, payload):
        self.sent.append((msg_type, payload))


class FakeClient(NetworkHandlers, SaveSyncHandlers, InventoryHandlers):
    def __init__(self, world, player_entity):
        self.world = world
        self.player_entity = player_entity
        self._my_eid = player_entity
        self._net = _FakeNet()
        self._remote_players = {}
        self._remote_mobs = {}


class TestInstanceInventorySyncSendsSaveState(unittest.TestCase):

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        # Bag REAL, quase cheia (18/20) — estado ANTES de entrar na instância.
        self.world.add_component(self.eid, Inventory(
            items=[Item(f"Item Real {i}", "misc", "") for i in range(18)],
            max_slots=20))
        self.world.add_component(self.eid, Equipment())
        self.world.add_component(self.eid, Wallet(gold=500))
        self.client = FakeClient(self.world, self.eid)

    def test_inv_snapshot_dispara_save_state_imediato(self):
        """Sem isso, session.last_client_payload no servidor ficaria com
        18 itens/20 slots (stale) mesmo depois do swap pra 6 slots vazios
        — toda compra seguinte seria rejeitada como inventory_full."""
        payload = {
            "eid": self.eid, "in_instance": True,
            "inv_snapshot": [], "inv_max_slots": 6,
            "equip_snapshot": {"mainhand": None, "offhand": None},
        }
        self.client._handle_msg_stats_update(payload)

        self.assertTrue(self.client._net.sent, "deveria ter mandado SAVE_STATE")
        from shared.messages import MsgType
        msg_type, sent_payload = self.client._net.sent[-1]
        self.assertEqual(msg_type, MsgType.SAVE_STATE)
        # O SAVE_STATE mandado precisa refletir a bag JÁ TROCADA (vazia),
        # não a real de 18 itens — senão o bug persiste do mesmo jeito
        # (session.last_client_payload["inventory"] é lido cru, sem
        # json.loads, por server/session.py::_handle_buy_request).
        self.assertEqual(sent_payload.get("inventory"), [])

    def test_stats_update_sem_snapshot_nao_dispara_save_state(self):
        """STATS_UPDATE comum (ex.: sync de hp/gold em combate) não deveria
        mandar SAVE_STATE a cada tick — só quando inv_snapshot/equip_snapshot
        realmente vêm no payload (enter/exit de instância)."""
        payload = {"eid": self.eid, "hp": 50, "hp_max": 100}
        self.client._handle_msg_stats_update(payload)
        self.assertEqual(self.client._net.sent, [])


class TestInstanceTalentAllocatedSync(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): "os pontos de talento
    parecem acumular, mesmo eu usando" — o TalentTree.allocated local
    nunca era trocado/resetado ao entrar/sair da instância. Fix:
    `talent_allocated` novo no payload de STATS_UPDATE, substituição
    COMPLETA (não merge) — mesmo espírito de inv_snapshot/equip_snapshot."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        tt = TalentTree()
        tt.allocated = {"cav_reflexos": 2}  # alocação REAL, pré-instância
        tt.available_points = 1
        self.world.add_component(self.eid, tt)
        self.client = FakeClient(self.world, self.eid)

    def test_talent_allocated_substitui_alocacao_local_por_completo(self):
        payload = {"eid": self.eid, "talent_points": 0, "talent_allocated": {}}
        self.client._handle_msg_stats_update(payload)
        tt = self.world.get_component(self.eid, TalentTree)
        self.assertEqual(tt.allocated, {}, "instância deveria zerar a alocação local")

    def test_talent_allocated_ausente_nao_mexe_na_alocacao_local(self):
        payload = {"eid": self.eid, "hp": 50, "hp_max": 100}
        self.client._handle_msg_stats_update(payload)
        tt = self.world.get_component(self.eid, TalentTree)
        self.assertEqual(tt.allocated, {"cav_reflexos": 2})


class TestInstanceAttributeSyncSurvivesRecompute(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): "entrei na BG, HP
    140/140 — comprei um item e o HP ficou 140/380" (380 = max_hp REAL,
    fora da instância). Causa raiz: CharacterStats.vitality (e demais
    atributos brutos) LOCAL nunca era sincronizado ao entrar na
    instância — só hp/hp_max (o RESULTADO) eram sobrescritos uma vez.
    Equipar um item comprado (_equip_item → add_modifier →
    recalculate_combat_stats) recalcula CombatStats.max_hp do zero a
    partir de CombatStats.base_stamina, que só `apply_char_stats_to_
    combat` sabe reconstruir corretamente a partir de CharacterStats —
    sem sincronizar os atributos brutos, o PRÓXIMO recálculo local
    (qualquer equip/unequip) trazia o max_hp REAL de volta."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        char = CharacterStats(name="p1", class_id="guerreiro")
        char.vitality = 28  # personagem real, level alto
        self.world.add_component(self.eid, char)
        cs = CombatStats()
        from engine.stats_system import apply_char_stats_to_combat
        apply_char_stats_to_combat(char, cs, None)
        cs.current_hp = cs.max_hp
        self.world.add_component(self.eid, cs)
        self.real_max_hp = cs.max_hp  # inflado pela vitalidade real
        self.world.add_component(self.eid, Equipment())
        self.world.add_component(self.eid, Inventory())
        self.client = FakeClient(self.world, self.eid)

    def test_max_hp_sobrevive_a_um_equip_apos_stats_update_de_instancia(self):
        cs = self.world.get_component(self.eid, CombatStats)
        char = self.world.get_component(self.eid, CharacterStats)

        # max_hp "piso" ESPERADO pros atributos de instância abaixo —
        # calculado com a MESMA função pura que o servidor usa (nunca um
        # número arbitrário: precisa bater com o que o próximo recálculo
        # local também vai produzir a partir dos MESMOS atributos).
        from engine.stats_system import apply_char_stats_to_combat as _calc_floor
        _floor_char = CharacterStats(name="p1", class_id="guerreiro")
        _floor_char.strength, _floor_char.intelligence = 3, 1
        _floor_char.agility, _floor_char.vitality, _floor_char.defense = 1, 3, 2
        _floor_cs = CombatStats()
        _calc_floor(_floor_char, _floor_cs, None)
        floor_max_hp = _floor_cs.max_hp

        # STATS_UPDATE de entrada na instância — vitality no piso (3),
        # hp/hp_max já refletindo o servidor (mesmo valor calculado acima,
        # já que o servidor usa a mesma função pura).
        self.client._handle_msg_stats_update({
            "eid": self.eid, "in_instance": True,
            "strength": 3, "intelligence": 1, "agility": 1,
            "vitality": 3, "defense": 2,
            "hp": floor_max_hp, "hp_max": floor_max_hp,
        })
        self.assertEqual(cs.max_hp, floor_max_hp)
        self.assertLess(cs.max_hp, self.real_max_hp)

        # Gatilho de recálculo (equipar item comprado na loja de
        # instância) — max_hp NÃO deveria voltar pro valor real.
        # PRECISA ter >=1 modifier: _equip_item só chama add_modifier()
        # (que dispara recalculate_combat_stats) por modifier do item —
        # um item sem nenhum (ex.: arma básica sem bônus) nunca aciona o
        # recálculo, e mascararia esta prova (falso positivo já visto
        # nesta investigação: "Espada de treinamento" tem modifiers=[]).
        from engine.components import Modifier as _ModTest
        item = Item("Anel de teste", "jewelry", "ring",
                    modifiers=[_ModTest("armor", 5.0, "flat")])
        inv = self.world.get_component(self.eid, Inventory)
        inv.items.append(item)
        self.client._equip_item(item)

        self.assertEqual(cs.max_hp, floor_max_hp,
                         "equipar não deveria reverter max_hp pro valor real "
                         "(base_stamina precisa refletir o atributo sincronizado)")
        self.assertNotEqual(cs.max_hp, self.real_max_hp)

    def test_max_hp_nao_vaza_bonus_legado_de_permanent_stats_ao_recalcular(self):
        """Bug real relatado pelo usuário (03/08/2026, jungo): "comprei o
        Arco do Caçador e o HP foi pra 160, mas o item não dá atributo de
        vida nenhum" — não era o item (só `crit_rating`). Causa raiz:
        `PermanentStats` (bônus legado roguelike) é zerado pelo SERVIDOR
        ao entrar na instância, mas a cópia LOCAL do cliente nunca era
        avisada — um personagem estabelecido com bônus legado real (>0,
        "jungo" é um desses) vazava de volta pro recálculo disparado por
        QUALQUER equip, somado em cima do atributo já normalizado."""
        from engine.components import PermanentStats as _PermTest
        from ui.ui_components import InstanceInventoryUIState as _IIUSTest
        perm = _PermTest()
        perm.vitality = 25  # bônus legado real, tipo "jungo"
        self.world.add_component(self.eid, perm)
        self.world.add_component(self.eid, _IIUSTest())

        cs = self.world.get_component(self.eid, CombatStats)
        from engine.stats_system import apply_char_stats_to_combat as _calc_floor2
        _floor_char = CharacterStats(name="p1", class_id="guerreiro")
        _floor_char.strength, _floor_char.intelligence = 3, 1
        _floor_char.agility, _floor_char.vitality, _floor_char.defense = 1, 3, 2
        _floor_cs = CombatStats()
        _calc_floor2(_floor_char, _floor_cs, None)  # SEM bônus legado — mesmo que o servidor
        floor_max_hp = _floor_cs.max_hp

        self.client._handle_msg_stats_update({
            "eid": self.eid, "in_instance": True,
            "strength": 3, "intelligence": 1, "agility": 1,
            "vitality": 3, "defense": 2,
            "hp": floor_max_hp, "hp_max": floor_max_hp,
        })
        self.assertEqual(cs.max_hp, floor_max_hp)

        from engine.components import Modifier as _ModTest2
        item = Item("Anel de teste", "jewelry", "ring",
                    modifiers=[_ModTest2("armor", 5.0, "flat")])
        inv = self.world.get_component(self.eid, Inventory)
        inv.items.append(item)
        self.client._equip_item(item)

        self.assertEqual(cs.max_hp, floor_max_hp,
                         "bônus legado de PermanentStats não deveria vazar pro "
                         "max_hp recalculado dentro da instância")

    def test_vitality_ausente_nao_mexe_nos_atributos_locais(self):
        char = self.world.get_component(self.eid, CharacterStats)
        vit_antes = char.vitality
        self.client._handle_msg_stats_update({"eid": self.eid, "hp": 50, "hp_max": 100})
        self.assertEqual(char.vitality, vit_antes)

    def test_max_hp_nao_vaza_modifier_de_equipamento_real_ao_entrar_na_instancia(self):
        """Bug real relatado pelo usuário (03/08/2026, "jungo", confirmado
        via print das Estatísticas): comprar o Arco do Caçador (só
        `crit_rating`, sem nenhum atributo de vida) fazia o HP saltar de
        140 pra 160 — o painel de Estatísticas já mostrava "+20 de Itens"
        em HP/Estamina com a mochila de equipamento TOTALMENTE VAZIA,
        antes mesmo de comprar qualquer coisa (`PermanentStats` foi
        investigado e descartado como causa: não é persistido no banco,
        sempre zero — ver ARQUITETURA_ONLINE.md).

        Causa raiz real: `equip_snapshot` (enter/exit_normalized_
        progression) só trocava `Equipment.slots` — nunca limpava os
        `Modifier(source="equipment")` correspondentes já presentes em
        `CombatStats.modifiers` (adicionados no LOGIN por `_restore_save_
        state`, quando o personagem REAL tinha itens equipados). Essas
        duas listas são independentes: trocar o Equipment pra vazio não
        removia os modifiers REAIS (armadura de cabeça/peito/botas, no
        caso de "jungo") — eles ficavam invisíveis (a atualização
        explícita de "hp"/"hp_max" no mesmo payload escondia o valor
        errado) até o PRÓXIMO recalculate_effective_stats (disparado por
        QUALQUER add_modifier — equipar o Arco do Caçador, mesmo ele não
        dando HP nenhum) reaplicar tudo de novo, revelando o vazamento."""
        from engine.components import Modifier as _ModRealEq
        cs = self.world.get_component(self.eid, CombatStats)
        equip = self.world.get_component(self.eid, Equipment)

        # Simula "jungo" ANTES de entrar: peito real equipado com +2 stamina
        # (mesmo efeito de "Colete de Couro" real) — mimetiza exatamente o
        # que _restore_save_state faz no login de um personagem estabelecido.
        real_chest = Item("Colete de Couro", "armor", "chest",
                          modifiers=[_ModRealEq("stamina", 2, "flat")])
        equip.slots["chest"] = real_chest
        from engine.stat_fns import add_modifier as _add_mod_real
        for mod in real_chest.modifiers:
            _add_mod_real(cs, mod)

        floor_char = CharacterStats(name="p1", class_id="guerreiro")
        floor_char.strength, floor_char.intelligence = 3, 1
        floor_char.agility, floor_char.vitality, floor_char.defense = 1, 3, 2
        floor_cs = CombatStats()
        from engine.stats_system import apply_char_stats_to_combat as _calc_floor3
        _calc_floor3(floor_char, floor_cs, None)
        floor_max_hp = floor_cs.max_hp

        # ENTRY: equip_snapshot vem TODO vazio (troca real → instância)
        self.client._handle_msg_stats_update({
            "eid": self.eid, "in_instance": True,
            "strength": 3, "intelligence": 1, "agility": 1,
            "vitality": 3, "defense": 2,
            "hp": floor_max_hp, "hp_max": floor_max_hp,
            "equip_snapshot": {slot: None for slot in equip.slots},
        })
        self.assertEqual(cs.max_hp, floor_max_hp,
                         "modifier de equipamento REAL (peito) não deveria "
                         "sobreviver à troca pra Equipment vazio da instância")
        self.assertFalse(any(m.source == "equipment" for m in cs.modifiers),
                         "cs.modifiers não deveria ter sobra de equipamento real")

        # Comprar/equipar um item SEM atributo de vida não deveria mudar nada
        bow = Item("Arco do Caçador", "weapon", "mainhand",
                  modifiers=[_ModRealEq("crit_rating", 0.01, "flat")])
        inv = self.world.get_component(self.eid, Inventory)
        inv.items.append(bow)
        self.client._equip_item(bow)

        self.assertEqual(cs.max_hp, floor_max_hp,
                         "equipar um item só com crit_rating não deveria "
                         "alterar max_hp")


class TestInstanceShopBuyAutoEquip(unittest.TestCase):
    """Pedido do usuário (02/08/2026): "a ideia era os itens já valerem
    como equipados no momento que eu compro" — o painel dedicado de 6
    slots foi removido por causa disso (a bag normal já mostra certo o
    inventário da instância); em troca, comprar um item equipável dentro
    da instância equipa direto, sem precisar de um clique extra."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        self.world.add_component(self.eid, Inventory(items=[], max_slots=6))
        self.world.add_component(self.eid, Equipment())
        self.world.add_component(self.eid, Wallet(gold=150))
        self.world.add_component(self.eid, CharacterStats(class_id="guerreiro"))
        self.world.add_component(self.eid, CombatStats())
        from ui.ui_components import InstanceInventoryUIState
        iius = InstanceInventoryUIState()
        iius.active = True
        self.world.add_component(self.eid, iius)
        self.client = FakeClient(self.world, self.eid)

    def _buy_payload(self, **item_overrides):
        item = {
            "name": "Espada de Ferro", "item_type": "weapon", "slot": "mainhand",
            "rarity": "common", "value": 55, "consumable": None, "max_stack": 1,
            "stack": 1, "modifiers": [], "level_requirement": 1,
        }
        item.update(item_overrides)
        return {"success": True, "item": item, "quantity": 1, "new_gold": 95, "price": 55}

    def test_item_equipavel_vai_direto_pro_equipment_nao_pra_bag(self):
        self.client._handle_msg_buy_result(self._buy_payload())
        inv = self.world.get_component(self.eid, Inventory)
        equip = self.world.get_component(self.eid, Equipment)
        self.assertEqual(inv.items, [], "não deveria sobrar na bag")
        self.assertIsNotNone(equip.slots["mainhand"])
        self.assertEqual(equip.slots["mainhand"].name, "Espada de Ferro")

    def test_fora_da_instancia_compra_continua_indo_pra_bag(self):
        from ui.ui_components import InstanceInventoryUIState
        iius = self.world.get_component(self.eid, InstanceInventoryUIState)
        iius.active = False
        self.client._handle_msg_buy_result(self._buy_payload())
        inv = self.world.get_component(self.eid, Inventory)
        equip = self.world.get_component(self.eid, Equipment)
        self.assertEqual(len(inv.items), 1, "fora da instância, item fica na bag normal")
        self.assertIsNone(equip.slots["mainhand"])

    def test_item_de_classe_errada_fica_na_bag_sem_crashar(self):
        """Fallback gracioso: arqueiro comprando um item de classe errada
        (ex.: catálogo futuro/engano) não deveria travar nem forçar o
        equip — _equip_item já rejeita e o item continua na bag."""
        char = self.world.get_component(self.eid, CharacterStats)
        char.class_id = "mago"
        self.client._handle_msg_buy_result(
            self._buy_payload(item_type="weapon", subtype="Bow"))
        inv = self.world.get_component(self.eid, Inventory)
        equip = self.world.get_component(self.eid, Equipment)
        self.assertEqual(len(inv.items), 1)
        self.assertIsNone(equip.slots["mainhand"])

    def test_auto_equip_preserva_fracao_de_hp_mesmo_quando_max_hp_sobe(self):
        """Bug real relatado pelo usuário (03/08/2026): "se o personagem
        está 100% de hp, quando ele equipa um item precisa continuar com
        100% de hp, ele não sofreu nenhum dano" — recalculate_combat_stats
        (stat_fns.py) só preserva o HP ABSOLUTO (comportamento padrão de
        buff em qualquer RPG: current_hp não muda, então a % cai se
        max_hp subir) — comprar item na loja de instância não é dano nem
        cura, então o auto-equip da compra precisa preservar a FRAÇÃO."""
        cs = self.world.get_component(self.eid, CombatStats)
        cs.base_stamina = 100
        cs._recalculate_effective_stats()
        cs.current_hp = cs.max_hp  # 100% antes de comprar
        hp_antes = cs.max_hp

        self.client._handle_msg_buy_result(self._buy_payload(
            modifiers=[{"attribute": "stamina", "value": 5, "type": "flat"}]))

        self.assertGreater(cs.max_hp, hp_antes, "item deveria ter aumentado o max_hp (stamina)")
        self.assertEqual(cs.current_hp, cs.max_hp,
                         "current_hp deveria ter acompanhado o max_hp novo (ainda 100%)")

    def test_auto_equip_preserva_fracao_parcial_nao_so_o_caso_100_por_cento(self):
        cs = self.world.get_component(self.eid, CombatStats)
        cs.base_stamina = 100
        cs._recalculate_effective_stats()
        cs.current_hp = cs.max_hp // 2  # 50% antes de comprar

        self.client._handle_msg_buy_result(self._buy_payload(
            modifiers=[{"attribute": "stamina", "value": 5, "type": "flat"}]))

        self.assertAlmostEqual(cs.current_hp / cs.max_hp, 0.5, delta=0.02,
                               msg="fração de HP (50%) deveria ter sido preservada, não só o absoluto")


class TestInstanceSkillsHotbarClientApply(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): a barra de ações
    continuava mostrando as skills REAIS dentro da instância. Fix:
    skills_hotbar/learned_skill_ids novos no payload de STATS_UPDATE,
    substituição COMPLETA da PlayerSkills local."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        ps = PlayerSkills()
        ps.learned_skill_ids = {"executar", "vitoria_iminente"}
        ps.skills[0] = PlayerSkills._make_skill("executar", __import__(
            "content.skill_config", fromlist=["SKILL_CATALOG"]).SKILL_CATALOG)
        self.world.add_component(self.eid, ps)
        self.client = FakeClient(self.world, self.eid)

    def test_skills_hotbar_substitui_a_barra_local_por_completo(self):
        payload = {
            "eid": self.eid,
            "skills_hotbar": ["golpe_poderoso"] + [None] * 9,
            "learned_skill_ids": ["golpe_poderoso"],
        }
        self.client._handle_msg_stats_update(payload)
        ps = self.world.get_component(self.eid, PlayerSkills)
        self.assertEqual(ps.learned_skill_ids, {"golpe_poderoso"})
        self.assertEqual(ps.skills[0].skill_id, "golpe_poderoso")
        self.assertIsNone(ps.skills[1])

    def test_sem_skills_hotbar_no_payload_nao_mexe_na_barra_local(self):
        payload = {"eid": self.eid, "hp": 10, "hp_max": 100}
        self.client._handle_msg_stats_update(payload)
        ps = self.world.get_component(self.eid, PlayerSkills)
        self.assertEqual(ps.learned_skill_ids, {"executar", "vitoria_iminente"})
        self.assertEqual(ps.skills[0].skill_id, "executar")

    def test_skill_no_mesmo_slot_preserva_cooldown_ao_vivo(self):
        """Bug real relatado pelo usuário (03/08/2026): "o cooldown das
        skills não aparece logo depois de usar, só depois de apertar o
        atalho de novo". Causa raiz: `skills_hotbar` chega a CADA level-up
        de instância (XP por proximidade de minion morto — muito frequente
        na BG), e a substituição completa do slot criava um `Skill` NOVO
        (current_cooldown=0.0) mesmo quando o `skill_id` do slot não
        mudou — apagando visualmente um cooldown real que o servidor
        acabou de mandar via SKILL_RESULT, segundos antes."""
        ps = self.world.get_component(self.eid, PlayerSkills)
        ps.skills[0].current_cooldown = 45.0
        ps.skills[0].charges = 1
        payload = {
            "eid": self.eid,
            "skills_hotbar": ["executar"] + [None] * 9,
            "learned_skill_ids": ["executar", "vitoria_iminente"],
        }
        self.client._handle_msg_stats_update(payload)
        ps2 = self.world.get_component(self.eid, PlayerSkills)
        self.assertEqual(ps2.skills[0].skill_id, "executar")
        self.assertEqual(ps2.skills[0].current_cooldown, 45.0,
                         "cooldown ao vivo não deveria ser resetado quando o "
                         "skill_id do slot não mudou")


class TestInstanceConsumableBarSwap(unittest.TestCase):
    """Bug real relatado pelo usuário (02/08/2026): a barra de
    consumíveis continuava mostrando os consumíveis REAIS dentro da
    instância. `ConsumableBar` é 100% client-local (nunca existiu no
    servidor) — swap/restauração feitos aqui mesmo, gatilhados pela
    transição de `in_instance` (mesmo trigger que já troca Inventory/
    Equipment/TalentTree/PlayerSkills, vindos do servidor)."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        cb = ConsumableBar()
        cb.slots[0] = "Poção de Vida Real"
        self.world.add_component(self.eid, cb)
        from ui.ui_components import InstanceInventoryUIState
        self.world.add_component(self.eid, InstanceInventoryUIState())
        self.client = FakeClient(self.world, self.eid)

    def test_entrar_na_instancia_limpa_a_barra_e_guarda_a_real(self):
        self.client._handle_msg_stats_update({"eid": self.eid, "in_instance": True})
        cb = self.world.get_component(self.eid, ConsumableBar)
        self.assertEqual(cb.slots, [None] * ConsumableBar.NUM_SLOTS)
        self.assertEqual(self.client._real_consumable_bar_slots[0], "Poção de Vida Real")

    def test_sair_da_instancia_restaura_a_barra_real(self):
        self.client._handle_msg_stats_update({"eid": self.eid, "in_instance": True})
        cb = self.world.get_component(self.eid, ConsumableBar)
        cb.slots[1] = "Poção de Mana da Instância"  # comprado lá dentro
        self.client._handle_msg_stats_update({"eid": self.eid, "in_instance": False})
        self.assertEqual(cb.slots[0], "Poção de Vida Real")
        self.assertIsNone(cb.slots[1], "consumível da instância não deveria sobreviver à saída")


class TestInstanceBuyConsumableAutoAddsToBar(unittest.TestCase):
    """Pedido do usuário (02/08/2026): "os consumíveis deveria aparecer
    só quando o player comprasse... deveria ir para a barra de ações de
    consumíveis automaticamente"."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, PlayerControlled())
        self.world.add_component(self.eid, Inventory(items=[], max_slots=6))
        self.world.add_component(self.eid, Equipment())
        self.world.add_component(self.eid, Wallet(gold=150))
        self.world.add_component(self.eid, ConsumableBar())
        from ui.ui_components import InstanceInventoryUIState
        iius = InstanceInventoryUIState()
        iius.active = True
        self.world.add_component(self.eid, iius)
        self.client = FakeClient(self.world, self.eid)

    def _buy_potion_payload(self):
        return {
            "success": True, "quantity": 1, "new_gold": 125, "price": 25,
            "item": {
                "name": "Poção de Vida", "item_type": "consumable", "slot": "",
                "rarity": "common", "value": 25, "consumable": {"heal_instant": 120},
                "max_stack": 20, "stack": 1,
            },
        }

    def test_comprar_pocao_vai_pro_primeiro_slot_livre(self):
        self.client._handle_msg_buy_result(self._buy_potion_payload())
        cb = self.world.get_component(self.eid, ConsumableBar)
        self.assertEqual(cb.slots[0], "Poção de Vida")

    def test_comprar_de_novo_nao_duplica_slot(self):
        self.client._handle_msg_buy_result(self._buy_potion_payload())
        self.client._handle_msg_buy_result(self._buy_potion_payload())
        cb = self.world.get_component(self.eid, ConsumableBar)
        self.assertEqual(cb.slots.count("Poção de Vida"), 1)

    def test_fora_da_instancia_nao_auto_adiciona(self):
        from ui.ui_components import InstanceInventoryUIState
        iius = self.world.get_component(self.eid, InstanceInventoryUIState)
        iius.active = False
        self.client._handle_msg_buy_result(self._buy_potion_payload())
        cb = self.world.get_component(self.eid, ConsumableBar)
        self.assertEqual(cb.slots, [None] * ConsumableBar.NUM_SLOTS)


if __name__ == "__main__":
    unittest.main()
