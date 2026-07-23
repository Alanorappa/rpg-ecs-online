"""
server/match_processor.py
Mixin para WorldServer: Arena 2x2 (Fase G, leva 1) — fila FIFO de grupos
pré-formados, criação/fim de partida numa instância privada.

Espelha o padrão de DuelProcessorMixin (estado como bookkeeping simples
no WorldServer, API pública chamada pelos handlers de server/session.py,
eventos por tick consumidos pelo broadcast loop) e reusa
PartyProcessorMixin pro conceito de "grupo pré-formado" — um "time" nesta
leva É o grupo (Party) que entrou na fila junto, sem estado de time
próprio (ver ARQUITETURA_ONLINE.md, decisão da leva).

"Instância" é uma cópia privada de ARENA_TEMPLATE_2V2 (maps/arena_poco_negro.csv)
carregada via
WorldServer._load_instance/_unload_instance (server/world_server.py) —
UM WorldServer só, chave sintética por partida, nunca outro processo
(ver docstring de _load_map_for pro racional — evita colisão dos globais
module-level de engine/world_systems.py etc.).

Hostilidade entre times é resolvida por FACÇÃO (não pelo contexto PvP
plugável como duelo/zona) — cada player ganha um componente Faction
("arena_time_a"/"arena_time_b", ver content/faction_data.py) enquanto a
partida dura, sobrescrevendo a facção default "jogadores". can_engage()
já libera dano nesse caso sem nenhuma mudança (relação != "amigavel").

Ciclo de vida de uma partida (revisado 21/07/2026, feedback do usuário —
aceite de partida + preparo):
0. PROPOSTA — fila pareia 2 grupos (_propose_match) mas NINGUÉM é
   teleportado ainda: os 4 recebem ARENA_MATCH_FOUND e têm
   ARENA_ACCEPT_WINDOW_S segundos pra mandar ARENA_MATCH_ACCEPT
   (request_arena_accept). Quem aceita entra IMEDIATAMENTE (sozinho, não
   espera o resto), livre pra se mover/agir dentro da própria sala de
   espera — vira PREPARO: contenção é FÍSICA, não freeze de ação (revisado
   22/07/2026, pedido do usuário — "em vez de bloquear as ações dos
   personagens, criar um local no mapa que fique fechado", modelo WoW). O
   portão (ARENA_GATE_TILES, shared/constants.py) fica sólido até
   ARENA_COUNTDOWN_S depois do fim de ARENA_ACCEPT_WINDOW_S (contagem é da
   PARTIDA, não por-jogador — quem entra depois já vê o portão abrir mais
   cedo/já aberto). Quem não aceita a tempo simplesmente não entra —
   _tick_arena_pending encerra a janela e, se um time inteiro nunca
   apareceu, o outro vence por W.O.; times desbalanceados (só 1 aceitou de
   um lado) são esperados e aceitos.
1. ATIVA — golpe que mataria elimina (CombatState.is_immune=True +
   is_stunned=True, não pode mais ser ferido NEM agir/mover — ver
   _eliminate_player). Time com todos os membros eliminados: a partida é
   DECIDIDA (_finish_match), NÃO restaurada ainda.
2. DECIDIDA — os 4 ficam congelados (is_immune+is_stunned, vencedores
   inclusive) parados na arena vendo o placar (nome/dano/vitória de cada
   um, mandado uma vez via ARENA_MATCH_RESULT) — igual ao modal de fim
   de partida do WoW. Cada player sai quando quiser clicando "Sair da
   Arena" (manda ARENA_FORFEIT, mesmo comando de desistir no meio da
   partida — nesta fase não muda o resultado, só teleporta de volta) ou
   é forçado a sair após ARENA_RESULT_AUTO_LEAVE_S segundos
   (_tick_arena_results_timeout, evita instância presa pra sempre se
   ninguém clicar nem desconectar).
3. ENCERRADA — quando o roster dos dois times esvazia (todo mundo já
   saiu), a instância é descarregada de vez (_arena_leave_now).
"""
from __future__ import annotations

from shared.constants import ARENA_ACCEPT_WINDOW_S, ARENA_COUNTDOWN_S, ARENA_GATE_TILES

ARENA_TEMPLATE_2V2 = "maps/arena_poco_negro.csv"
# Salas de espera opostas (topo/base) do mapa arena_poco_negro.csv — ligadas à
# arena circular central por 2 portões físicos (ARENA_GATE_TILES,
# shared/constants.py) que só abrem quando o preparo termina (22/07/2026,
# revisão "portão físico" — ver docstring da classe).
_SPAWN_TEAM_A = [(12, 2), (14, 2)]
_SPAWN_TEAM_B = [(12, 32), (14, 32)]

# Tempo máximo parado na tela de resultado antes de ser teleportado de
# volta à força — evita que a instância fique presa na memória pra
# sempre se um player não clicar "Sair da Arena" nem desconectar.
ARENA_RESULT_AUTO_LEAVE_S = 15.0


class MatchProcessorMixin:

    # ── Fila (grupos de exatamente 2) ────────────────────────────────────────

    def request_arena_queue_join(self, requester_eid: int) -> "str | None":
        """Só o líder do grupo pode enfileirar (mesma regra de
        kick_from_party). None = entrou na fila. Reason str = recusado
        (handler manda ARENA_QUEUE_STATE{in_queue:False, reason} só pro
        requester)."""
        party_id = self.get_party_id_of(requester_eid)
        party = self._parties.get(party_id)
        if party is None:
            return "no_party"
        if party["leader_eid"] != requester_eid:
            return "not_leader"
        if len(party["members"]) != 2:
            return "wrong_size"
        if party_id in self._arena_queue_2v2:
            return "already_queued"
        for m_eid in party["members"]:
            if m_eid in self._player_match_id:
                return "in_match"
        self._arena_queue_2v2.append(party_id)
        return None

    def request_arena_queue_leave(self, requester_eid: int) -> bool:
        """True = o grupo do requester estava na fila e saiu."""
        party_id = self.get_party_id_of(requester_eid)
        if party_id in self._arena_queue_2v2:
            self._arena_queue_2v2.remove(party_id)
            return True
        return False

    def _tick_arena_queue(self) -> None:
        """Pareamento FIFO puro — sem balanceamento/rank nesta leva. Roda
        1x por tick (mesmo padrão de _tick_duel_distance_check)."""
        while len(self._arena_queue_2v2) >= 2:
            pid_a = self._arena_queue_2v2.pop(0)
            pid_b = self._arena_queue_2v2.pop(0)
            party_a = self._parties.get(pid_a)
            party_b = self._parties.get(pid_b)
            if party_a is None or party_b is None:
                continue  # grupo se desfez enquanto esperava — descarta, segue tentando o resto
            self._propose_match(list(party_a["members"]), list(party_b["members"]))

    def _tick_arena_results_timeout(self) -> None:
        """Partidas DECIDIDAS há mais de ARENA_RESULT_AUTO_LEAVE_S segundos
        forçam a saída de quem ainda não clicou "Sair da Arena"."""
        import time as _time_to
        now = _time_to.time()
        for match_id, match in list(self._active_matches.items()):
            if not match.get("decided"):
                continue
            if now - match["decided_at"] < ARENA_RESULT_AUTO_LEAVE_S:
                continue
            for eid in list(match["team_a"] + match["team_b"]):
                self._arena_leave_now(match_id, eid)

    # ── Ciclo de vida de partida ─────────────────────────────────────────────

    def _propose_match(self, team_a_eids: list[int], team_b_eids: list[int]) -> None:
        """Fila pareou 2 grupos — NINGUÉM é teleportado ainda (revisado
        21/07/2026, pedido do usuário). Cria o match_id já em
        `_active_matches` com os rosters de verdade (`team_a`/`team_b`)
        VAZIOS — eles só crescem conforme cada um aceita, ver
        `request_arena_accept`. `invited_a`/`invited_b` guardam quem foi
        chamado (bookkeeping imutável, usado só pra montar
        teammates/opponents e pra decidir W.O. se um lado nunca aparecer).
        Instância só é carregada no primeiro aceite (`instance_key=None`
        aqui) — evita alocar mapa pra uma partida que ninguém topa jogar.

        `accept_deadline`/`countdown_deadline` são ANCORADOS aqui, no
        momento em que a fila pareou — NUNCA recalculados a partir de
        quando alguém aceita (revisado 21/07/2026, pedido do usuário:
        "unindo os 2 tempos, será 30 segundos pra iniciar a arena a
        partir do momento que a arena chamou"). `countdown_deadline` já
        nasce FIXO em propose_time + ACCEPT_WINDOW + COUNTDOWN — um
        aceite tardio (ainda dentro do accept_deadline) só recebe um
        `countdown_remaining` menor, nunca reinicia a contagem."""
        match_id = f"arena2v2_{self._next_match_id}"
        self._next_match_id += 1
        import time as _time_pm
        _propose_now = _time_pm.time()

        self._active_matches[match_id] = {
            "instance_key":      None,
            "team_a":            [],
            "team_b":            [],
            "invited_a":         list(team_a_eids),
            "invited_b":         list(team_b_eids),
            "eliminated":        set(),
            "return_pos":        {},
            "damage_by_eid":     {},
            "arena_locked":      set(),   # eids com is_immune/is_stunned setados POR _finish_match
            "decided":           False,
            "decided_at":        0.0,
            "winner_members":    set(),
            "accept_deadline":    _propose_now + ARENA_ACCEPT_WINDOW_S,
            "countdown_deadline": _propose_now + ARENA_ACCEPT_WINDOW_S + ARENA_COUNTDOWN_S,
            "fight_started":     False,
            "accept_swept":      False,
        }
        for eid in team_a_eids + team_b_eids:
            self._pending_arena_invite[eid] = match_id
        for eid in team_a_eids + team_b_eids:
            self._arena_match_found_events_this_tick.append({
                "eid":       eid,
                "teammates": [e for e in (team_a_eids if eid in team_a_eids else team_b_eids) if e != eid],
                "opponents": team_b_eids if eid in team_a_eids else team_a_eids,
            })

    def request_arena_accept(self, eid: int) -> "str | None":
        """Aceita a partida encontrada (ARENA_MATCH_ACCEPT) — entra
        IMEDIATAMENTE, sozinho, sem esperar o resto (mesmo estilo de
        `request_arena_queue_join`/`request_arena_forfeit`: None = sucesso,
        string = recusado). Primeiro aceite de QUALQUER um dos 4 carrega a
        instância (lazy) — a contagem de preparo (`countdown_deadline`) já
        nasceu FIXA em `_propose_match` (ancorada no momento em que a fila
        pareou, não em quando alguém aceita); aqui só calcula quanto falta
        pra ESTE entrante, nunca reinicia a contagem pra ninguém."""
        import time as _time_ac
        match_id = self._pending_arena_invite.get(eid)
        if match_id is None:
            return "no_pending_invite"
        match = self._active_matches.get(match_id)
        if match is None or match["decided"]:
            self._pending_arena_invite.pop(eid, None)
            return "expired"
        if _time_ac.time() >= match["accept_deadline"]:
            self._pending_arena_invite.pop(eid, None)
            return "expired"
        del self._pending_arena_invite[eid]

        from engine.components import Faction as _FactionM
        side       = "a" if eid in match["invited_a"] else "b"
        other_side = "b" if side == "a" else "a"

        if match["instance_key"] is None:
            match["instance_key"] = f"{ARENA_TEMPLATE_2V2}::{match_id}"
            self._load_instance(ARENA_TEMPLATE_2V2, match["instance_key"])

        sid = self.get_session_id_for_player(eid)
        if sid is None:
            return "disconnected"
        tx, ty = self.get_tile_pos(sid)
        match["return_pos"][eid] = (self.get_player_map(sid), tx, ty)

        spawn_list = _SPAWN_TEAM_A if side == "a" else _SPAWN_TEAM_B
        idx        = len(match[f"team_{side}"])
        sx, sy     = spawn_list[idx % len(spawn_list)]
        self.transfer_player(sid, eid, match["instance_key"], sx, sy)
        self.world.add_component(eid, _FactionM(faction_id=f"arena_time_{side}"))
        self._reset_combat_resources(eid)
        match[f"team_{side}"].append(eid)
        match["damage_by_eid"][eid] = 0
        self._player_match_id[eid] = match_id

        remaining = max(0.0, match["countdown_deadline"] - _time_ac.time())

        _map_file = self._template_file_of(match["instance_key"])
        self._arena_match_start_events_this_tick.append({
            "eid":                 eid,
            "map_file":            _map_file,
            "target_x":            sx, "target_y": sy,
            "teammates":           [e for e in match[f"invited_{side}"] if e != eid],
            "opponents":           match[f"invited_{other_side}"],
            "countdown_remaining": remaining,
        })
        # Entrada tardia (depois do portão já ter aberto pra esta instância,
        # ver _tick_arena_pending): o cliente acabou de carregar o CSV do
        # disco (portão sempre nasce fechado) — sem isso ele nunca receberia
        # o aviso pra abrir, já que o evento de abertura só é emitido UMA VEZ,
        # no tick em que fight_started vira True.
        if match["fight_started"]:
            self._arena_gate_open_events_this_tick.append({"eid": eid})
        return None

    def _tick_arena_pending(self) -> None:
        """Roda 1x por tick (mesmo padrão de `_tick_arena_queue`). Duas
        varreduras independentes por partida ainda não decidida/em luta:
        (a) janela de aceite vencida — quem não aceitou é descartado; se
        um time inteiro nunca apareceu, o outro vence por W.O. (mesma
        `_finish_match` de sempre); se nenhum dos 4 apareceu, a partida é
        só descartada (instância nunca chegou a carregar). (b) contagem de
        preparo vencida — abre o portão físico (`ARENA_GATE_TILES`) da
        instância (revisado 22/07/2026 — antes liberava `is_stunned`; agora
        a contenção é o portão, não freeze de ação/movimento) e avisa os 4
        players da partida."""
        import time as _time_tp
        now = _time_tp.time()
        from engine.components import Tilemap as _TMtp
        from engine.tileset import STONE_FLOOR as _SFtp

        for match_id, match in list(self._active_matches.items()):
            if match["decided"] or match["fight_started"]:
                continue

            if not match["accept_swept"] and now >= match["accept_deadline"]:
                match["accept_swept"] = True
                for eid in match["invited_a"] + match["invited_b"]:
                    self._pending_arena_invite.pop(eid, None)
                if not match["team_a"] and not match["team_b"]:
                    self._active_matches.pop(match_id, None)
                    continue
                elif not match["team_a"]:
                    self._finish_match(match_id, "team_b")
                    continue
                elif not match["team_b"]:
                    self._finish_match(match_id, "team_a")
                    continue
                # os dois times têm gente (só desbalanceado) — segue pro combate normalmente

            cd = match["countdown_deadline"]
            if cd is not None and now >= cd and not match["fight_started"]:
                match["fight_started"] = True
                bundle = self._map_bundles.get(match["instance_key"])
                if bundle is not None:
                    tilemap = self.world.get_component(bundle.tilemap_entity, _TMtp)
                    if tilemap is not None:
                        for gx, gy in ARENA_GATE_TILES:
                            tilemap.tile_matrix[gy][gx] = _SFtp
                for eid in match["team_a"] + match["team_b"]:
                    self._arena_gate_open_events_this_tick.append({"eid": eid})

    def _reset_combat_resources(self, eid: int) -> None:
        """Restaura HP/Mana/Concentração cheios e limpa cooldown de TODAS
        as skills — chamado ao ENTRAR na arena (request_arena_accept) e ao SAIR
        de vez (_arena_leave_now), pedido explícito do usuário
        20/07/2026: "os personagens que entram na arena, precisam ter
        todos os recursos restaurados... e quando saem da arena é a
        mesma coisa" — ninguém começa a partida em desvantagem por ter
        gastado recurso antes de entrar, e ninguém sai "quebrado" de
        volta ao mundo aberto.

        `_skill_last_used` (cooldown AUTORITATIVO, checado em
        skill_processor.py) é limpo aqui; o valor de EXIBIÇÃO
        (`Skill.current_cooldown`, ticado localmente pelo cliente,
        nunca sincronizado por rede) é responsabilidade do cliente —
        ver client/arena_handlers.py, mesmos gatilhos
        (ARENA_MATCH_START/ARENA_MATCH_END)."""
        from engine.components import CombatStats as _CSTres, CharacterStats as _CharRes
        cs = self.world.get_component(eid, _CSTres)
        if cs:
            cs.current_hp = cs.max_hp
        char = self.world.get_component(eid, _CharRes)
        if char:
            char.mana         = char.max_mana
            char.concentration = char.max_concentration
            char.reset_volatile()
        for key in [k for k in self._skill_last_used if k[0] == eid]:
            del self._skill_last_used[key]

    def _track_arena_damage(self, killer_eid: int, target_id: int, dmg: int) -> None:
        """Registrado via engine.core_systems.register_damage_tracker —
        acumula dano causado por `killer_eid` na partida ativa (placar de
        fim de partida). No-op fora de qualquer partida ou se o alvo não
        for da mesma partida (nunca deveria acontecer — instância isolada
        — mas defende contra o caso de dano vazando por engano)."""
        match_id = self._player_match_id.get(killer_eid)
        if match_id is None:
            return
        match = self._active_matches.get(match_id)
        if match is None or target_id not in (match["team_a"] + match["team_b"]):
            return
        match["damage_by_eid"][killer_eid] = match["damage_by_eid"].get(killer_eid, 0) + dmg

    def _eliminate_player(self, match_id: str, eid: int) -> None:
        """Marca `eid` eliminado — não faz mais nada com HP/is_immune/
        is_stunned aqui (revisado 20/07/2026): o golpe letal agora morre
        DE VERDADE (ver _arena_lethal_interceptor, que só notifica e
        deixa a morte acontecer) — o player vira fantasma de verdade
        (mesmo fluxo de PvE: GhostState, corpo, "Liberar espírito"), e
        current_hp<=0 já bloqueia ele como alvo/atacante em todo
        combat_processor/spell_completion_processor (mesma regra de
        qualquer morte). Isso resolve os dois problemas reportados pelo
        usuário de uma vez: (1) "continuam controlando o personagem mesmo
        após perder" — morto de verdade não pode agir; (2) "ainda são
        alvos atacáveis" — current_hp<=0 já bloqueia isso em qualquer
        lugar do jogo, sem precisar de lógica nova. Encerra a partida
        (decide, não restaura ainda — ver _finish_match) se o time
        inteiro dele já estiver eliminado."""
        match = self._active_matches.get(match_id)
        if match is None or eid in match["eliminated"]:
            return
        match["eliminated"].add(eid)

        team_key  = "team_a" if eid in match["team_a"] else "team_b"
        other_key = "team_b" if team_key == "team_a" else "team_a"
        if all(m in match["eliminated"] for m in match[team_key]):
            self._finish_match(match_id, other_key)

    def _finish_match(self, match_id: str, winner_team_key: "str | None") -> None:
        """Partida DECIDIDA (time inteiro eliminado, ou esvaziado por
        forfeit/desconexão em _arena_leave_now) — NÃO restaura ninguém
        ainda, diferente do antigo `_end_match` monolítico. Congela só
        quem ainda está VIVO (is_immune+is_stunned — normalmente o time
        vencedor; quem já morreu de verdade em combate já está
        naturalmente "congelado" via GhostState/current_hp<=0, não
        precisa de nada aqui) e manda o placar (nome+dano+vitória de cada
        um dos 4) pra cada cliente montar o modal de fim de partida
        (estilo WoW, pedido do usuário 20/07/2026). Restauração de
        verdade (e revive de quem morreu) só acontece quando cada player
        clica "Sair da Arena" (ARENA_FORFEIT — mesmo comando do desistir
        no meio da partida) ou pelo timeout automático
        (_tick_arena_results_timeout) — ver _arena_leave_now.

        `winner_team_key` = "team_a"/"team_b" (elimination normal) ou
        None (ninguém vence — ex: os dois times ficaram vazios)."""
        match = self._active_matches.get(match_id)
        if match is None or match["decided"]:
            return
        import time as _time_fm
        match["decided"]    = True
        match["decided_at"] = _time_fm.time()
        from engine.components import (CombatState as _CSF, CharacterStats as _CharF,
                                       CombatStats as _CSTF)
        winner_members = set(match[winner_team_key]) if winner_team_key else set()
        match["winner_members"] = winner_members
        all_members = match["team_a"] + match["team_b"]
        results = []
        for eid in all_members:
            cstats = self.world.get_component(eid, _CSTF)
            if cstats and cstats.current_hp > 0:
                cst = self.world.get_component(eid, _CSF)
                if cst:
                    cst.is_immune  = True
                    cst.is_stunned = True
                match["arena_locked"].add(eid)
            char = self.world.get_component(eid, _CharF)
            results.append({
                "eid":    eid,
                "name":   char.name if char else "?",
                "damage": match["damage_by_eid"].get(eid, 0),
                "won":    eid in winner_members,
            })
        for eid in all_members:
            self._arena_match_result_events_this_tick.append({
                "eid": eid, "results": results,
            })

        # CharStatsTracker.arena_wins/arena_losses (Fase E) — por modo.
        # Hardcoded "2v2" por enquanto (único modo existente até a Fase H
        # generalizar match_id/team_size por ARENA_MODES); ninguém ganha/
        # perde se a partida terminou sem vencedor (winner_team_key=None,
        # ex: os dois times esvaziaram por forfeit simultâneo).
        if winner_team_key is not None:
            from engine.utils import incr_char_stat_mode as _incr_cst_arena
            for eid in all_members:
                field = "arena_wins" if eid in winner_members else "arena_losses"
                _incr_cst_arena(self.world, eid, field, "2v2")

    def _arena_leave_now(self, match_id: str, eid: int) -> None:
        """`eid` sai da partida IMEDIATAMENTE (desconexão, /forfeit, ou
        clique em "Sair da Arena" depois de decidida) — restaura mapa/
        tile/Facção na hora e remove `eid` do roster do time (não só do
        set `eliminated`), porque ele está saindo de verdade — não faz
        sentido continuar contando como presente na instância.

        Precisa rodar ANTES do save do disconnect
        (server/session.py::on_disconnect) — sem isso, `get_player_save_
        data` capturava o `map_id` sintético da instância da arena (só
        existe em memória, nunca em disco) + o tile relativo ao spawn da
        arena, persistindo os dois no banco. No próximo login,
        `spawn_player` não reconhece mais aquele map_id (instância já
        descarregada) e cai no mapa principal, mas MANTÉM o tile da
        arena — o jogador aparecia num tile essencialmente aleatório do
        mapa principal, e como só existem 4 tiles de spawn de arena,
        vários jogadores que passaram pela mesma partida colidiam no
        mesmo lugar (bug real relatado pelo usuário 20/07/2026: "reloguei
        em algum lugar que não era a arena, com outros players em
        volta").

        Remover do roster (não só do set `eliminated`) também evita que
        este método, quando chamado de novo pra outro membro, tente
        restaurar/notificar este `eid` de novo — o que poderia
        teleportá-lo pra fora de onde quer que ele esteja àquela altura
        (já numa fila nova, ou dentro de outra partida)."""
        match = self._active_matches.get(match_id)
        if match is None:
            return
        from engine.components import Faction as _FactionL, CombatState as _CSL
        r_map, r_x, r_y = match["return_pos"].get(eid, (self._map_file, 115, 389))
        sid = self.get_session_id_for_player(eid)
        if sid is not None:
            self.transfer_player(sid, eid, r_map, r_x, r_y)

        # Quem morreu de verdade na arena (ver _eliminate_player, 20/07/2026)
        # sai como fantasma pra sempre se não for revivido — não pode
        # reviver DENTRO da arena (server/session.py::_handle_revive_request
        # bloqueia), então revive aqui, na hora de sair de vez, já na
        # posição restaurada acima (full HP — mesmo critério de
        # _auto_revive_on_disconnect: nunca revive "in place" perigoso, e
        # aqui "in place" já é o mapa/tile de origem, seguro por definição).
        from engine.components import GhostState as _GSL
        gst = self.world.get_component(eid, _GSL)
        if gst and gst.is_dead:
            self._revive_player(eid, hp_frac=1.0, at_corpse=False)

        # Recursos cheios + cooldowns limpos ao sair, igual ao entrar (pedido
        # do usuário 20/07/2026) — roda depois do revive acima (se aplicável)
        # pra não ter conflito: _revive_player já restaura HP/mana/reset_
        # volatile pra quem morreu, mas não mexe em concentração/cooldown.
        self._reset_combat_resources(eid)

        try:
            self.world.remove_component(eid, _FactionL)
        except Exception:
            pass
        cst = self.world.get_component(eid, _CSL)
        if cst:
            cst.is_immune = False
            # Só limpa is_stunned de quem ESTE mixin travou (arena_locked =
            # fim de partida) — um forfeit voluntário ANTES de qualquer
            # eliminação, ou o vencedor congelado em _finish_match, nunca
            # tiveram is_stunned setado por golpe letal; um player pode
            # legitimamente estar stunado por um efeito de combate real e
            # não-relacionado nesse instante — limpar sem checar apagaria
            # esse stun. Preparo (portão físico, revisado 22/07/2026) não
            # seta is_stunned mais, então não precisa de checagem própria
            # aqui.
            if eid in match["arena_locked"]:
                cst.is_stunned = False
        match["arena_locked"].discard(eid)
        self._player_match_id.pop(eid, None)
        self._arena_match_end_events_this_tick.append({
            "eid": eid, "won": eid in match["winner_members"],
            "map_file": r_map, "target_x": r_x, "target_y": r_y,
        })

        team_key  = "team_a" if eid in match["team_a"] else "team_b"
        other_key = "team_b" if team_key == "team_a" else "team_a"
        match[team_key]     = [m for m in match[team_key] if m != eid]
        match["eliminated"].discard(eid)

        if not match["team_a"] and not match["team_b"]:
            self._active_matches.pop(match_id, None)
            self._unload_instance(match["instance_key"])
            return

        if not match["decided"] and not match[team_key]:
            winner = other_key if match[other_key] else None
            self._finish_match(match_id, winner)

    def end_matches_of(self, eid: int) -> None:
        """Desconexão em partida ativa OU na tela de resultado = sair na
        hora (chamado ANTES de despawn_player E antes do save — mesmo
        ponto de end_duels_of/end_parties_of, ver
        server/session.py::on_disconnect)."""
        match_id = self._player_match_id.get(eid)
        if match_id is None:
            return
        self._arena_leave_now(match_id, eid)

    def request_arena_forfeit(self, eid: int) -> "str | None":
        """Comando de chat /forfeit ou /ff — também usado pelo botão
        "Sair da Arena" do modal de fim de partida (client/
        arena_handlers.py), já que os dois fazem exatamente a mesma
        coisa do lado servidor: sair na hora. No meio da partida conta
        como desistência (o time perde se isso esvaziar o roster); depois
        de decidida, só teleporta de volta (o resultado já está fechado).
        Retorna None em sucesso, motivo em string se recusado (não está
        numa partida)."""
        match_id = self._player_match_id.get(eid)
        if match_id is None:
            return "not_in_match"
        self._arena_leave_now(match_id, eid)
        return None

    # ── Interceptor de golpe letal ────────────────────────────────────────────

    def _arena_lethal_interceptor(self, world, killer_eid: int, target_id: int) -> bool:
        """Registrado via WorldServer._lethal_interceptor_composite (um
        registro só, composto com o de duelo — ver register_lethal_
        interceptor). SEMPRE retorna False (nunca intercepta) — diferente
        do duelo (que deixa o perdedor em 1 HP), a arena deixa o golpe
        MATAR de verdade (revisado 20/07/2026, pedido do usuário: morte
        real + fantasma, igual PvE, em vez de "1 HP + imune" congelado).
        Só usa este hook como PONTO DE NOTIFICAÇÃO — roda exatamente no
        instante do golpe que mataria, then apply_damage_core segue seu
        fluxo normal (PendingDeath → ServerDeathHandler →
        _handle_player_death)."""
        match_id = self._player_match_id.get(target_id)
        if match_id is not None:
            self._eliminate_player(match_id, target_id)
        return False

    # ── Eventos por tick (broadcast loop do SessionManager) ─────────────────

    def consume_arena_match_found_events(self) -> list[dict]:
        result = list(self._arena_match_found_events_this_tick)
        self._arena_match_found_events_this_tick.clear()
        return result

    def consume_arena_match_start_events(self) -> list[dict]:
        result = list(self._arena_match_start_events_this_tick)
        self._arena_match_start_events_this_tick.clear()
        return result

    def consume_arena_gate_open_events(self) -> list[dict]:
        result = list(self._arena_gate_open_events_this_tick)
        self._arena_gate_open_events_this_tick.clear()
        return result

    def consume_arena_match_end_events(self) -> list[dict]:
        result = list(self._arena_match_end_events_this_tick)
        self._arena_match_end_events_this_tick.clear()
        return result

    def consume_arena_match_result_events(self) -> list[dict]:
        result = list(self._arena_match_result_events_this_tick)
        self._arena_match_result_events_this_tick.clear()
        return result
