"""
server/server_death_handler.py
Processa mortes de mobs no servidor (sem Pygame).

Responsabilidades neste módulo:
  - Detectar entidades com PendingDeath a cada tick
  - Calcular XP baseado no EnemyTier do mob
  - Determinar first-attacker via world_server.get_damage_log()
  - Rolar loot via roll_mob_loot e enfileirar pending_loot
  - Notificar SpawnZone (remove eid de active_entity_ids imediatamente)
  - Enfileirar despawns para o WorldServer enviar ENTITY_DESPAWN aos clientes
  - Remover a entidade do world

Intencional NÃO fazer aqui:
  - Criar entidade ECS de cadáver (WorldServer faz via dict simples)
  - Tocar sons (SOUNDS)
  - Qualquer import de Pygame
"""
from __future__ import annotations
import random
from server.log import log


_XP_BY_TIER: dict[str, int] = {
    "normal": 50,
    "elite":  150,
    "rare":   300,
    "boss":   1000,
}


def _serialize_item(item) -> dict:
    """Extrai apenas campos serializáveis de um Item ECS (sem objetos Pygame)."""
    return {
        "name":      getattr(item, "name",      ""),
        "icon_key":  getattr(item, "icon_key",  ""),
        "item_type": getattr(item, "item_type", ""),
        "rarity":    getattr(item, "rarity",    "common"),
        "value":     getattr(item, "value",     0),
        "slot":      getattr(item, "slot",      ""),
        "stack":     getattr(item, "stack",     1),
    }


class ServerDeathHandler:
    """Processa todas as entidades com PendingDeath a cada tick do servidor."""

    def __init__(self, world, world_server=None) -> None:
        self.world          = world
        self.world_server   = world_server
        self.pending_xp:       list[dict] = []
        # Cada entrada: {"eid": int, "tx": int, "ty": int}
        self.pending_despawns: list[dict] = []
        self.pending_loot:     list[dict] = []

    # ── API pública ──────────────────────────────────────────────────────────

    def update(self) -> None:
        """Processa todas as entidades com PendingDeath este tick."""
        from engine.components import PendingDeath, EnemyTier, SpawnZoneOwner, SpawnZone, TileMovement

        to_remove:    list[int] = []
        to_pd_only:   list[int] = []  # boneco de treino: só remove PendingDeath

        for eid, pd in self.world.get_entities_with(PendingDeath):
            # Player morreu (PvP): usa _handle_player_death, NÃO o fluxo de mob
            # (que criaria corpse com loot, daria XP de mob e despawnaria a entidade).
            if self.world_server and eid in self.world_server._player_eids.values():
                # CharStatsTracker.players_killed (Fase E) — só conta se o
                # killer também for player (mob que mata player não credita
                # "players_killed" pra ninguém).
                if pd.killer_entity_id in self.world_server._player_eids.values():
                    from engine.utils import incr_char_stat as _incr_cst_pk
                    _incr_cst_pk(self.world, pd.killer_entity_id, "players_killed")
                self.world_server._handle_player_death(eid)
                try:
                    self.world.remove_component(eid, PendingDeath)
                except Exception:
                    pass
                continue

            # Boneco de treino: reseta HP em vez de morrer
            from engine.components import TrainingDummy as _TDdh, CombatStats as _CSdh
            if self.world.get_component(eid, _TDdh) is not None:
                _td_cs = self.world.get_component(eid, _CSdh)
                if _td_cs:
                    _td_cs.current_hp = _td_cs.max_hp
                    if self.world_server:
                        self.world_server._combat_this_tick.append({
                            "attacker": -1, "target": eid,
                            "damage":   0,  "outcome": "regen",
                            "hp_after": _td_cs.max_hp, "source": "regen",
                        })
                if self.world_server:
                    self.world_server.get_damage_log(eid)  # descarta log acumulado
                to_pd_only.append(eid)
                continue

            killer_eid = pd.killer_entity_id

            # 1. Log — DEBUG, não INFO (05/08/2026, causa real de "travamento"
            # relatado pelo usuário numa troca de lane da BG): morte de
            # mob/minion é evento de ALTA frequência, e log.info() escreve
            # SÍNCRONO em 2 handlers (console + arquivo, server/log.py) —
            # várias mortes quase simultâneas (choque de lane) viravam
            # dezenas de ms de I/O bloqueando o tick real. Com
            # RPG_LOG_LEVEL default (INFO), debug() nem chega nos handlers —
            # ainda disponível ligando RPG_LOG_LEVEL=DEBUG.
            log.debug(f"[Death] mob {eid} morto por {killer_eid}")

            # Posição/mapa do mob — precisa vir ANTES do bloco de XP (usado
            # pelo split de XP compartilhado de grupo, passo 2c abaixo) e
            # também é usada mais adiante pro registro de corpse.
            mob_tx, mob_ty = 0, 0
            tm = self.world.get_component(eid, TileMovement)
            if tm:
                mob_tx, mob_ty = tm.current_tile_x, tm.current_tile_y
            mob_map = self.world_server.get_entity_map(eid) if self.world_server else None

            # Torre (29/07/2026, pedido do usuário): tabela própria
            # (content/tower_definitions.py::TOWER_TABLE), SEPARADA de
            # MOB_TABLE — XP/ouro vêm direto do componente `Tower`
            # (xp_reward/gold_min/gold_max), NUNCA do lookup por nome/
            # tier abaixo (passos 2 e 5). `_tower_dh` checado aqui (usado
            # já no passo 2, XP) — o registro de respawn em si acontece
            # mais abaixo, depois de `identity` ser lido (precisa do
            # level pra recriar a torre igual).
            from engine.components import Tower as _TowerDH
            _tower_dh = self.world.get_component(eid, _TowerDH)
            # Nexus (02/08/2026, pedido do usuário; generalizado 04/08/2026
            # pra fila real — server/bg_queue_processor.py): torre marcada
            # como is_nexus derrubada termina a partida — lê a Faction E o
            # mapa/instância da torre MORTA aqui (entidade ainda intacta;
            # só é removida no fim deste loop) e notifica os DOIS
            # sistemas que podem ter uma partida rodando nesse mapa
            # (debug_battleground.py — instância única de teste — e
            # bg_queue_processor.py — N partidas reais, cada uma na
            # própria instância); cada um decide sozinho, pelo map_file,
            # se a torre é DELE (no-op se não — Arena de verdade nunca
            # usa is_nexus, então nunca aciona nada aqui).
            if _tower_dh is not None and _tower_dh.is_nexus and self.world_server:
                from engine.components import Faction as _FactionDH
                _tower_fac_dh = self.world.get_component(eid, _FactionDH)
                if _tower_fac_dh is not None:
                    _tower_map_dh = self.world_server.get_entity_map(eid)
                    from server.debug_battleground import notify_nexus_destroyed
                    notify_nexus_destroyed(self.world_server, _tower_map_dh,
                                           _tower_fac_dh.faction_id, killer_eid)
                    self.world_server.notify_bg_queue_nexus_destroyed(
                        _tower_map_dh, _tower_fac_dh.faction_id, killer_eid)
            # Minion (30/07/2026, pedido do usuário) — mesmo motivo de
            # Tower: tabela própria (content/minion_definitions.py::
            # MINION_TABLE), XP direto do componente `Minion` × level
            # (nunca cai no lookup por nome/MOB_TABLE). Minion morto NÃO
            # agenda respawn individual (diferente de Tower) — só a
            # próxima wave programada (WorldServer._tick_minion_waves)
            # cria minions novos, então não há um passo 6-equivalente aqui.
            from engine.components import Minion as _MinionDH
            _minion_dh = self.world.get_component(eid, _MinionDH)

            # 2. XP proporcional por dano causado — base por level do mob ×
            # xp_given_by_lvl (mob_definitions.py), modificado pelo
            # multiplicador de tier. Mobs sem cadastro (ex: "Elemental")
            # caem no fallback flat por tier (_XP_BY_TIER).
            tier_comp = self.world.get_component(eid, EnemyTier)
            tier      = tier_comp.tier if tier_comp else "normal"

            from engine.components import EntityIdentity
            from content.mob_definitions import MOB_TABLE
            from engine.entity_factory import ENEMY_TIER_CONFIGS
            identity = self.world.get_component(eid, EntityIdentity)
            mob_def  = MOB_TABLE.get(identity.name) if identity else None
            if _tower_dh is not None:
                # Torre: XP flat da própria definição — nunca cai no
                # lookup por nome/tier (torre não está em MOB_TABLE).
                base_xp = _tower_dh.xp_reward
            elif _minion_dh is not None:
                # Minion: xp_reward da própria definição ESCALA com o
                # level do minion (pedido do usuário, 30/07/2026 — level
                # do minion virou a média do time, recalculada a cada
                # wave, então XP acompanha: minion mais forte também vale
                # mais). `identity.level` é o mesmo level gravado em
                # create_minion (WorldServer._compute_team_avg_level) —
                # nunca cai no fallback de MOB_TABLE/tier (minion não
                # está cadastrado lá).
                minion_level = identity.level if identity else 1
                base_xp = _minion_dh.xp_reward * minion_level
            elif mob_def and "xp_given_by_lvl" in mob_def:
                mob_level  = identity.level if identity else 1
                tier_mult  = ENEMY_TIER_CONFIGS.get(tier, ENEMY_TIER_CONFIGS["normal"])["xp"]
                base_xp    = int(mob_level * mob_def["xp_given_by_lvl"] * tier_mult)
            else:
                base_xp = _XP_BY_TIER.get(tier, _XP_BY_TIER["normal"])

            damage_log: dict = {}
            if self.world_server:
                damage_log = self.world_server.get_damage_log(eid)

            # Filtra pra só ATACANTES PLAYER contarem pro XP (pedido do
            # usuário, 01/08/2026: "a experiência deve ser compartilhada
            # somente entre players, os minions não devem receber xp").
            # Minion/torre já não ganhavam XP DE VERDADE (sem
            # CharacterStats, ver create_minion/create_tower — o
            # STATS_UPDATE resultante era descartado em silêncio por
            # falta de sessão) — mas ANTES deste filtro, o dano deles
            # ainda diluía proporcionalmente o que os players recebiam
            # (entrava no `total_damage` do denominador). Filtra ANTES de
            # somar, não depois — assim um mob 50% morto por minion +
            # 50% por player dá o XP CHEIO (não só metade) pro player.
            if self.world_server:
                _player_eids_now = set(self.world_server._player_eids.values())
                damage_log = {p: d for p, d in damage_log.items() if p in _player_eids_now}
            else:
                _player_eids_now = set()

            _xp_entries_start = len(self.pending_xp)
            # Minion: XP por PROXIMIDADE, não por dano (02/08/2026, pedido
            # do usuário — "a xp não é para ser necessário bater no mob,
            # se o mob morrer perto dos players, tem que ser dividido
            # entre os players, sem necessidade de dar um hit sequer nos
            # minions", estilo LoL: creep XP é compartilhado com QUALQUER
            # aliado próximo, mesmo que não tenha batido — só o GOLD exige
            # golpe final de verdade, ver bloco abaixo). Só se houver
            # alguém em progressão normalizada por perto — sem isso cai no
            # damage_log normal abaixo (minion fora de instância nunca
            # deveria existir de verdade, mas mantém o fallback seguro).
            _minion_xp_by_proximity = False
            if _minion_dh is not None and self.world_server:
                from shared.constants import PARTY_XP_SHARE_RADIUS_TILES as _MXPR
                from server.instance_progression import players_in_normalized_progression_near as _players_near
                _nearby = _players_near(self.world_server, mob_tx, mob_ty, mob_map, _MXPR)
                if _nearby:
                    _minion_xp_by_proximity = True
                    _share = max(1, base_xp // len(_nearby))
                    for _p_eid in _nearby:
                        self.pending_xp.append({
                            "player_eid": _p_eid,
                            "xp":         _share,
                            "mob_eid":    eid,
                        })
            if not _minion_xp_by_proximity:
                if damage_log:
                    total_damage = sum(damage_log.values())
                    for p_eid, dmg in damage_log.items():
                        proportion = dmg / total_damage
                        xp_earned  = max(1, int(base_xp * proportion))
                        self.pending_xp.append({
                            "player_eid": p_eid,
                            "xp":         xp_earned,
                            "mob_eid":    eid,
                        })
                else:
                    # Fallback: killer leva tudo — só se o killer for player
                    # de verdade (mesmo filtro acima; sem isso um minion/torre
                    # que dá o golpe final, sem NENHUM player ter batido no
                    # mob, virava "player_eid" da entrada de XP).
                    if killer_eid != -1 and killer_eid in _player_eids_now:
                        self.pending_xp.append({
                            "player_eid": killer_eid,
                            "xp":         base_xp,
                            "mob_eid":    eid,
                        })

            # Ouro de INSTÂNCIA por kill (01/08/2026, pedido do usuário:
            # "quando um oponente morre deve ir automaticamente pro
            # inventário do player que o matou... só quando dá o dano que
            # MATA"). SEMPRE `killer_eid` (golpe final) QUANDO ele for um
            # player de verdade — e só se estiver em progressão
            # normalizada (`is_in_normalized_progression`), senão não faz
            # nada (kills no mundo aberto/Arena continuam só no
            # coins/loot normal).
            # `_gold_recipient_eid` (03/08/2026, bug real relatado pelo
            # usuário — "jungo", torre morta pelo golpe final de um MINION
            # aliado): quando o `killer_eid` NÃO é um player (torre/mob
            # morto pelo golpe final de um minion — MinionSystem também
            # chama `deal_damage`/PendingDeath), o gate `killer_eid in
            # _player_eids_now` falhava por completo — nem creditava ouro
            # (killer não é player) NEM suprimia o coin físico do corpse
            # (`_instance_gold_ja_concedido` nunca virava True), fazendo a
            # torre voltar a dropar ouro físico dentro da instância.
            # Fallback: primeiro PLAYER que bateu (`damage_log` já
            # filtrado só-players acima, mesma fonte de `first_attacker_
            # eid` mais abaixo) — se NENHUM player bateu (minion mata
            # sozinho), `_gold_recipient_eid` fica -1 e o comportamento
            # antigo (coins físicos normais) se mantém, correto.
            # `_instance_gold_ja_concedido`: usado no passo 5 (loot/coins do
            # corpse) pra NÃO duplicar ouro de torre — torre sempre cai no
            # roll_mob_loot/coins normal (é "estrutura", não tem tabela
            # própria de loot condicional), então sem essa flag o killer
            # ganhava o gold de instância AQUI *e* de novo ao lootear o
            # corpse (coins normais de torre nunca eram suprimidos).
            _instance_gold_ja_concedido = False
            if self.world_server:
                _gold_recipient_eid = (killer_eid if killer_eid in _player_eids_now
                                       else (next(iter(damage_log)) if damage_log else -1))
                if _gold_recipient_eid != -1:
                    from server.instance_progression import (
                        is_in_normalized_progression as _is_norm_dh,
                        grant_instance_gold as _grant_gold_dh,
                        INSTANCE_PLAYER_KILL_GOLD as _PKG_dh,
                    )
                    if _is_norm_dh(self.world_server, _gold_recipient_eid):
                        if _tower_dh is not None:
                            _gold_amt = (random.randint(_tower_dh.gold_min, _tower_dh.gold_max)
                                        if _tower_dh.gold_max > 0 else 0)
                        elif _minion_dh is not None:
                            _gold_amt = (random.randint(_minion_dh.gold_min, _minion_dh.gold_max)
                                        if _minion_dh.gold_max > 0 else 0)
                        elif eid in _player_eids_now:
                            _gold_amt = _PKG_dh
                        else:
                            _gold_amt = 0
                        if _gold_amt > 0:
                            _grant_gold_dh(self.world_server, _gold_recipient_eid, _gold_amt)
                        _instance_gold_ja_concedido = True

            # 2c. Party: XP compartilhado (decisão do usuário 17/07/2026,
            # ver ARQUITETURA_ONLINE.md §34.19). As fatias proporcionais por
            # dano acima continuam valendo entre atacantes SEM grupo em
            # comum — mas entre membros do MESMO grupo, a soma das fatias
            # que esse grupo ganharia forma um "pool", redistribuído
            # IGUALMENTE entre todos os membros do grupo dentro do raio da
            # morte (PARTY_XP_SHARE_RADIUS_TILES), incluindo quem não bateu.
            # Quem está fora do raio não ganha nada dessa morte. Atacantes
            # de outro grupo (ou sem grupo) mantêm a fatia individual.
            if self.world_server:
                from shared.constants import PARTY_XP_SHARE_RADIUS_TILES as _PXPR
                _this_death_entries = self.pending_xp[_xp_entries_start:]
                _by_party: dict = {}
                _solo_entries = []
                for _entry in _this_death_entries:
                    _pid = self.world_server.get_party_id_of(_entry["player_eid"])
                    if _pid == -1:
                        _solo_entries.append(_entry)
                    else:
                        _by_party.setdefault(_pid, []).append(_entry)

                if _by_party:
                    _new_entries = list(_solo_entries)
                    for _pid, _party_entries in _by_party.items():
                        _pool = sum(_e["xp"] for _e in _party_entries)
                        _in_range = self.world_server._party_members_in_range(
                            _pid, mob_tx, mob_ty, mob_map, _PXPR)
                        if not _in_range:
                            # Ninguém do grupo por perto (raro) — mantém as
                            # fatias originais dos que bateram.
                            _new_entries.extend(_party_entries)
                            continue
                        _share = max(1, _pool // len(_in_range))
                        for _m_eid in _in_range:
                            _new_entries.append({
                                "player_eid": _m_eid,
                                "xp":         _share,
                                "mob_eid":    eid,
                            })
                    self.pending_xp[_xp_entries_start:] = _new_entries

            # 2b. Skills com on_kill=="charge" (Vitória Iminente): killer ganha
            # carga ao matar mob. Itera o CATÁLOGO (não ps.skills) com
            # lazy-create: skill comprada no treinador DURANTE a sessão só
            # atualiza learned_skill_ids no servidor (sync_player_skills) — o
            # objeto Skill nunca entrava em ps.skills até o relog e a carga
            # nunca era concedida (bug real; mesmo padrão do PnQ em
            # combat_processor).
            try:
                if killer_eid != -1:
                    from engine.components import PlayerSkills as _PSdh
                    from content.skill_config import SKILL_CATALOG as _SC
                    _ks = self.world.get_component(killer_eid, _PSdh)
                    if _ks:
                        for _sid_ok, _defn in _SC.items():
                            if _defn.get("on_kill") != "charge":
                                continue
                            _sk = _ks.skill_by_id(_sid_ok)
                            if _sk is None:
                                from engine.world_systems import is_skill_authorized as _auth_dh
                                if not _auth_dh(self.world, killer_eid, _sid_ok)[0]:
                                    continue
                                _sk = _PSdh._make_skill(_sid_ok, _SC)
                                if _sk is None:
                                    continue
                                try:
                                    _idx_ok = _ks.skills.index(None)
                                    _ks.skills[_idx_ok] = _sk
                                except ValueError:
                                    _ks.skills.append(_sk)
                            if _sk.max_charges <= 0:
                                continue
                            if _sk.charges < _sk.max_charges:
                                _sk.charges      = _sk.max_charges
                                _sk.charge_timer = _sk.charge_timeout
                            self.pending_xp.append({
                                "player_eid":   killer_eid,
                                "xp":           0,
                                "mob_eid":      eid,
                                "on_kill_skill": _sk.skill_id,
                            })
                            break
            except Exception:
                pass

            # 3. Determina first-attacker (primeiro a bater = dono do loot)
            first_attacker_eid = killer_eid
            if damage_log:
                # damage_log é dict preservado em ordem de inserção (Python 3.7+)
                first_attacker_eid = next(iter(damage_log))

            # 3a2. CharStatsTracker.mobs_killed (Fase E, modal de estatísticas)
            # — mesmo dono que loot/quest (first-attacker), incrementado uma
            # vez por mob morto, independente de quantos players participaram.
            if first_attacker_eid != -1:
                from engine.utils import incr_char_stat as _incr_cst_mob
                _incr_cst_mob(self.world, first_attacker_eid, "mobs_killed")

            # 3b. Evento de quest "kill" — first-attacker é o dono do
            # progresso (mesmo critério de dono do loot). Server-autoritativo
            # — ver quest_logic.py/PROBLEMAS_ARQUITETURA.md.
            if first_attacker_eid != -1 and identity:
                from engine.quest_events import fire as _qfire_kill
                _qfire_kill("kill", player_eid=first_attacker_eid,
                            name=identity.name, race=identity.race, tier=tier)

            # 4. Posição do mob para registrar corpse — já lida antes do
            # bloco de XP (mob_tx/mob_ty), reaproveitada aqui.

            # 5. Rola loot usando EntityIdentity.name (= race display, ex: "Aranha")
            # (identity já buscado no passo 2, pro cálculo de XP por level)
            mob_name = identity.name if identity else ""

            if _tower_dh is not None:
                # Torre: ouro flat da própria definição, sem item de
                # loot — pula roll_mob_loot/roll_mob_coins inteiramente
                # (torre não está em MOB_TABLE). Se o gold de INSTÂNCIA já
                # foi concedido de graça acima (passo 2b), NÃO duplica
                # aqui via coins do corpse (02/08/2026, bug real: killer
                # ganhava o mesmo ouro duas vezes — instantâneo + lootando
                # o corpse — o usuário só reparou a metade "tive que
                # lootear", sem perceber que também já tinha ganho na hora).
                loot_items = []
                coins = (0 if _instance_gold_ja_concedido else
                         (random.randint(_tower_dh.gold_min, _tower_dh.gold_max)
                          if _tower_dh.gold_max > 0 else 0))
            elif _minion_dh is not None:
                # Minion: moeda fora de escopo por enquanto (pedido
                # explícito do usuário, 30/07/2026 — "moeda pra outro
                # momento") — zero ouro/loot, sem lookup em MOB_TABLE.
                loot_items = []
                coins = 0
                # CharStatsTracker.minions_killed ("farm", 02/08/2026,
                # pedido do usuário) — golpe FINAL (killer_eid), convenção
                # MOBA de "CS" — diferente de mobs_killed acima, que usa
                # first_attacker_eid (dono do loot/quest, não
                # necessariamente quem bateu o golpe final).
                if killer_eid != -1 and killer_eid in _player_eids_now:
                    from engine.utils import incr_char_stat as _incr_farm
                    _incr_farm(self.world, killer_eid, "minions_killed")
            else:
                try:
                    from content.loot_tables import roll_mob_loot, roll_mob_coins as _roll_mob_coins
                    loot_items = roll_mob_loot(mob_name, tier) if mob_name else []
                    coins      = _roll_mob_coins(mob_name, tier) if (mob_name and tier) else 0
                except Exception:
                    loot_items = []
                    coins      = 0

            # 5a. Drop condicional de quest (collect_item, ex: Pelo de Urso) —
            # REMOVIDO daqui (25/07/2026, Fase L1, pedido do usuário): rolar
            # 1x na morte contra a QuestLog do first-attacker e gravar FIXO
            # no corpse deixava o item visível/pegável por QUALQUER membro
            # do MESMO GRUPO depois (request_loot permite "free-for-all
            # dentro do grupo"), mesmo sem a quest — bug real confirmado
            # (usuário perguntou, investigação achou). Resolvido agora POR
            # JOGADOR em server/loot_processor.py::_resolve_conditional_loot_for
            # (chamado na hora que a notificação LOOT_AVAILABLE é montada
            # pra cada destinatário, server/session.py, e de novo como
            # fallback em request_loot) — cacheado por (corpse, player_eid),
            # nunca re-sorteado pro MESMO jogador. mob_name/mob_race
            # (abaixo, no pending_loot.append) é o que essa função usa.

            # 5b. Reciclagem: flechas que acertaram este mob (contadas em
            # _server_apply_ranged_physical) voltam como loot pro matador, se ele
            # tiver o talento. Mesma fórmula do offline (systems.py): 50-100% das
            # flechas recebidas, mínimo 1.
            from engine.components import CombatStats as _CSdh, Equipment as _EqDh, Item as _ItemDh
            _dead_cs = self.world.get_component(eid, _CSdh)
            if _dead_cs and _dead_cs.arrows_received > 0 and first_attacker_eid != -1:
                _killer_cs = self.world.get_component(first_attacker_eid, _CSdh)
                if _killer_cs and getattr(_killer_cs, "arrow_recovery_enabled", False):
                    import random as _rand_dh
                    _pct       = _rand_dh.randint(50, 100) / 100.0
                    _recovered = max(1, int(_dead_cs.arrows_received * _pct))
                    _equip_r   = self.world.get_component(first_attacker_eid, _EqDh)
                    _quiver_r  = _equip_r.slots.get("offhand") if _equip_r else None
                    _atype     = getattr(_quiver_r, "subtype", "") or "Flecha"
                    _ret = _ItemDh(
                        name=_atype, item_type="ammo", slot="",
                        rarity="common", value=1,
                        damage_min=getattr(_quiver_r, "damage_min", 0),
                        damage_max=getattr(_quiver_r, "damage_max", 0),
                        max_stack=1000,
                    )
                    _ret.stack = _recovered
                    # Dentro da instância (03/08/2026, pedido do usuário):
                    # Reciclagem vai DIRETO pra bag do killer, sem precisar
                    # lootear o corpse — battleground de teste é rápido
                    # demais pra gerenciar loot manual de cada minion morto.
                    # Fora da instância, comportamento inalterado (cai no
                    # corpse como qualquer outro item).
                    _went_to_bag = False
                    if self.world_server:
                        from server.instance_progression import (
                            is_in_normalized_progression as _is_norm_recycle)
                        if _is_norm_recycle(self.world_server, first_attacker_eid):
                            from engine.components import Inventory as _InvDh
                            _inv_r = self.world.get_component(first_attacker_eid, _InvDh)
                            if _inv_r is not None:
                                _existing_ammo = next(
                                    (it for it in _inv_r.items
                                     if it is not None and it.name == _ret.name), None)
                                if _existing_ammo is not None:
                                    _existing_ammo.stack += _recovered
                                    _went_to_bag = True
                                elif len(_inv_r.items) < _inv_r.max_slots:
                                    _inv_r.items.append(_ret)
                                    _went_to_bag = True
                    if not _went_to_bag:
                        loot_items.append(_ret)

            # Sempre registra o corpse (visual) — só inclui itens se houve drop.
            # Sem essa entrada, o world_server nunca cria o body e o cliente
            # nunca recebe ENTITY_SPAWN(kind="corpse").
            if first_attacker_eid != -1:
                # "map": capturado ANTES do remove_entity — corpse/loot são
                # broadcast por loops diretos (não AOI_UPDATE) e precisam do
                # mapa pro filtro cross-map (ver session._sessions_in_aoi).
                from engine.components import MapLocation as _MLdh
                _ml_dh = self.world.get_component(eid, _MLdh)
                self.pending_loot.append({
                    "mob_eid":   eid,
                    "owner_eid": first_attacker_eid,
                    "items":     [_serialize_item(it) for it in loot_items],
                    "coins":     coins,
                    "tx":        mob_tx,
                    "ty":        mob_ty,
                    "map":       _ml_dh.map_file if _ml_dh else None,
                    # Pra resolução de loot condicional de quest POR JOGADOR
                    # (Fase L1) — ver server/loot_processor.py::
                    # _resolve_conditional_loot_for.
                    "mob_name":  mob_name,
                    "mob_race":  identity.race if identity else "",
                    # Minion nunca tem loot (items/coins sempre vazios, ver
                    # passo 5 abaixo) — corpse dele é só confirmação visual
                    # de morte, não um objeto pra lootear. Timer curto
                    # dedicado (server/loot_processor.py::_process_loot_drops)
                    # em vez do padrão de mob normal (120s) — pedido do
                    # usuário (03/08/2026): volume de mortes numa lane
                    # "floodava" o mapa de corpses vazios por 2 minutos.
                    "is_minion": _minion_dh is not None,
                })

            # 6. Notifica SpawnZone — remove de active_entity_ids e agenda respawn
            #    com o cooldown da zona (igual ao offline: SpawnZoneSystem adicionava
            #    zone.respawn_cooldown ao detectar deaths na cleanup sweep).
            #    Fazemos aqui para não perder o timer quando SpawnZoneSystem roda
            #    depois e vê before == alive (entidade já removida).
            szo = self.world.get_component(eid, SpawnZoneOwner)
            if szo is not None:
                zone = self.world.get_component(szo.zone_entity_id, SpawnZone)
                if zone is not None:
                    zone.active_entity_ids.discard(eid)
                    zone.respawn_timers.append(zone.respawn_cooldown)

            # 6b. Torre respawnável: agenda respawn exato no MESMO tile
            # (NUNCA via SpawnZone, que sorteia tile aleatório — ver
            # WorldServer.register_tower_respawn). Precisa de Faction/
            # EntityIdentity.level, só capturáveis AGORA (antes do
            # remove_entity mais abaixo).
            if _tower_dh is not None and self.world_server:
                from engine.components import Faction as _FactionDH
                _fac_dh = self.world.get_component(eid, _FactionDH)
                self.world_server.register_tower_respawn(
                    _tower_dh, _fac_dh.faction_id if _fac_dh else "monstros_hostis",
                    mob_map, identity.level if identity else 1)

            # 7. Agenda despawn para o WorldServer emitir ENTITY_DESPAWN
            if not any(d["eid"] == eid for d in self.pending_despawns):
                self.pending_despawns.append({"eid": eid, "tx": mob_tx, "ty": mob_ty})

            to_remove.append(eid)

        # Boneco de treino: só remove PendingDeath, mantém entidade
        for eid in to_pd_only:
            try:
                from engine.components import PendingDeath as _PD
                self.world.remove_component(eid, _PD)
            except Exception:
                pass

        # Remove PendingDeath ANTES de remove_entity (evita iteração inválida)
        for eid in to_remove:
            try:
                from engine.components import PendingDeath as _PD
                self.world.remove_component(eid, _PD)
            except Exception:
                pass
            try:
                self.world.remove_entity(eid)
            except Exception:
                pass

    def consume_despawns(self) -> list[dict]:
        """Retorna e limpa pending_despawns. Cada entry: {"eid": int, "tx": int, "ty": int}"""
        result = list(self.pending_despawns)
        self.pending_despawns.clear()
        return result

    def consume_xp(self) -> list[dict]:
        """Retorna e limpa pending_xp."""
        result = list(self.pending_xp)
        self.pending_xp.clear()
        return result

    def consume_loot(self) -> list[dict]:
        """Retorna e limpa pending_loot."""
        result = list(self.pending_loot)
        self.pending_loot.clear()
        return result
