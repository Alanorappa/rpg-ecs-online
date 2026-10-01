"""
server/portal_processor.py
Mixin para WorldServer: portais com seleção de destino (30/09/2026,
pedido do usuário) — diferente das transições de mapa existentes
(cavernas, ZONE_CHANGE_REQ, destino FIXO por tile), um portal tem
vários destinos possíveis e o jogador escolhe 1 no cliente; o
servidor então roda um cast de alguns segundos e só teleporta de
verdade quando o cast completa.

Espelha o padrão de DuelProcessorMixin/TradeProcessorMixin: estado
como bookkeeping simples no WorldServer (`_portal_casts`, criado em
WorldServer.__init__), API pública chamada pelos handlers de
server/session.py, tick próprio chamado de WorldServer._tick().

Achado de segurança que motivou este desenho (ver
PROBLEMAS_ARQUITETURA.md): `_handle_zone_change_req` (ZONE_CHANGE_REQ
existente) confia cegamente no to_map/target_x/y que o cliente manda —
seguro hoje porque toda transição existente tem exatamente 1 destino
fixo por tile (um cliente desonesto só chegaria no MESMO destino mais
cedo). Isso deixaria de ser verdade se um portal de destino-escolhido
reusasse ZONE_CHANGE_REQ diretamente. Por isso o destino é validado
AQUI, contra a lista de destinos do PRÓPRIO portal (`_portal_gates_by_map`,
carregado do mapa em `WorldServer._load_map_for`), no momento em que o
cast COMEÇA — nunca confiado de novo no fim. A teleportação final
reusa `transfer_player`/ZONE_CHANGE (mesmo par que ZONE_CHANGE_REQ já
usa), disparada pelo PRÓPRIO SERVIDOR quando o timer dele mesmo
completa, nunca por um pedido novo vindo do cliente.
"""
from __future__ import annotations


class PortalProcessorMixin:

    # ── Helpers internos ─────────────────────────────────────────────────────

    def _portal_gate_at(self, map_file: str, portal_id: str) -> "dict | None":
        """Acha o dict do portal `portal_id` na lista carregada pro
        mapa `map_file` (`_portal_gates_by_map`, populado em
        `_load_map_for` a partir de `spawn_points["portal_gates"]`).
        None se o mapa não tem esse portal (id errado, ou mapa sem
        nenhum portal cadastrado)."""
        for gate in self._portal_gates_by_map.get(map_file, []):
            if gate.get("id") == portal_id:
                return gate
        return None

    def _portal_gate_at_position(self, map_file: str, x: int, y: int) -> "dict | None":
        """Mesmo que `_portal_gate_at`, mas busca por POSIÇÃO em vez de
        id — usado por `_tick_portal_casts` pra saber se o tile de
        CHEGADA de um teleporte também é um portal cadastrado (gatilho
        da animação de "desativar", ver PORTAL_ANIM_ARRIVE). None se
        não há portal nenhum naquele tile específico (destino comum,
        sem portal — não deve animar nada)."""
        for gate in self._portal_gates_by_map.get(map_file, []):
            if gate.get("x") == x and gate.get("y") == y:
                return gate
        return None

    # ── API pública (handlers de server/session.py) ──────────────────────────

    def request_portal_enter(self, eid: int, portal_id: str, dest_index: int) -> "str | None":
        """None = cast iniciado (handler manda PORTAL_CAST_START pro
        próprio player). String = motivo de recusa (handler manda
        PORTAL_CAST_CANCEL{reason} de volta, sem iniciar nada).

        Validação em 3 passos, todos server-autoritativos — nunca
        confia em nada calculado no cliente além de QUAL portal e QUAL
        índice de destino o jogador clicou:
        1. `portal_id` existe no mapa ATUAL do player (nunca um portal
           de outro mapa, nem um id inventado).
        2. Player está DE VERDADE no tile do portal (mesmo padrão de
           validação de posição usado em outras interações do
           projeto — ex: distância de trade/duelo).
        3. `dest_index` é um índice válido da lista de destinos
           DAQUELE portal especificamente.
        """
        if eid in self._portal_casts:
            return "already_casting"
        map_file = self.get_entity_map(eid)
        if map_file is None:
            return "invalid"
        gate = self._portal_gate_at(map_file, portal_id)
        if gate is None:
            return "invalid"
        from engine.components import TileMovement as _PortalTM
        tm = self.world.get_component(eid, _PortalTM)
        if tm is None or tm.current_tile_x != gate["x"] or tm.current_tile_y != gate["y"]:
            return "not_on_portal"
        destinations = gate.get("destinations", [])
        if not isinstance(dest_index, int) or not (0 <= dest_index < len(destinations)):
            return "invalid_destination"

        self._portal_casts[eid] = {
            "portal_id":  portal_id,
            "dest_index": dest_index,
            "elapsed":    0.0,
            "duration":   self.PORTAL_CAST_DURATION_S,
            "map_file":   map_file,
        }

        # Animação de "ativando" no tile do portal (01/10/2026, visível
        # pra qualquer sessão por perto, não só pra quem está castando)
        # — broadcast SÓ no 1º caster deste portal específico (2º+
        # jogador castando no MESMO portal ao mesmo tempo não reinicia
        # a animação pra quem já estava vendo).
        anim_key = (map_file, portal_id)
        casters = self._portal_anim_casters.setdefault(anim_key, set())
        if not casters:
            self._portal_anim_start_events_this_tick.append({
                "map_file": map_file, "portal_id": portal_id,
                "x": gate["x"], "y": gate["y"],
                "duration": self.PORTAL_CAST_DURATION_S,
            })
        casters.add(eid)
        return None

    def cancel_portal_cast(self, eid: int, reason: str) -> bool:
        """Cancela o cast de `eid` se houver um ativo (movimento,
        disconnect, ou qualquer motivo futuro — ex: dano recebido).
        True se havia cast pra cancelar (handler manda
        PORTAL_CAST_CANCEL); False = no-op silencioso (não havia cast,
        chamador não precisa mandar nada)."""
        cast = self._portal_casts.pop(eid, None)
        if cast is None:
            return False
        self._portal_cast_cancel_events_this_tick.append({
            "eid": eid, "reason": reason,
        })

        # Última pessoa castando NESTE portal desistiu antes do tempo —
        # avisa quem estava vendo a animação de "ativando" pra ela
        # parar JÁ, sem esperar a duração original (que não vai mais
        # acontecer de verdade) terminar sozinha no cliente deles.
        anim_key = (cast["map_file"], cast["portal_id"])
        casters = self._portal_anim_casters.get(anim_key)
        if casters is not None:
            casters.discard(eid)
            if not casters:
                del self._portal_anim_casters[anim_key]
                gate = self._portal_gate_at(cast["map_file"], cast["portal_id"])
                if gate is not None:
                    self._portal_anim_stop_events_this_tick.append({
                        "map_file": cast["map_file"], "portal_id": cast["portal_id"],
                        "x": gate["x"], "y": gate["y"],
                    })
        return True

    # ── Tick: contagem do cast + teleporte ao completar ───────────────────────

    def _tick_portal_casts(self, dt: float) -> None:
        completed: list[tuple[int, dict]] = []
        for eid, cast in self._portal_casts.items():
            cast["elapsed"] += dt
            if cast["elapsed"] >= cast["duration"]:
                completed.append((eid, cast))
        for eid, cast in completed:
            del self._portal_casts[eid]

            # Conclusão NATURAL (chegou ao fim do tempo) — sai do set de
            # casters igual ao cancelamento, mas SEM broadcast de STOP:
            # quem estava vendo a animação já recebeu a duração certa em
            # PORTAL_ANIM_START e encerra sozinho no cliente, no mesmo
            # instante (ver engine/portal_anim_state.py::get_active).
            anim_key = (cast["map_file"], cast["portal_id"])
            casters = self._portal_anim_casters.get(anim_key)
            if casters is not None:
                casters.discard(eid)
                if not casters:
                    del self._portal_anim_casters[anim_key]

            gate = self._portal_gate_at(cast["map_file"], cast["portal_id"])
            if gate is None:
                continue  # mapa/portal sumiu entre o início do cast e agora (raríssimo)
            destinations = gate.get("destinations", [])
            if not (0 <= cast["dest_index"] < len(destinations)):
                continue
            dest = destinations[cast["dest_index"]]
            session_id = self.get_session_id_for_player(eid)
            if session_id is None:
                continue  # desconectou durante o cast
            # Teleporta de verdade AGORA, síncrono (mesma chamada que
            # _handle_zone_change_req já faz) — o evento abaixo só leva
            # o AVISO de rede (ZONE_CHANGE) pro loop assíncrono de
            # SessionManager mandar; o estado do servidor já mudou.
            self.transfer_player(session_id, eid, dest["target_map"],
                                 dest["target_x"], dest["target_y"],
                                 emit_moved_delta=True)
            self._portal_teleport_events_this_tick.append({
                "eid":        eid,
                "session_id": session_id,
                "to_map":     dest["target_map"],
                "target_x":   dest["target_x"],
                "target_y":   dest["target_y"],
            })

            # Chegada EM CIMA de outro portal cadastrado (ex: os 2
            # portais de teste se mirando) — toca "desativando" nele,
            # visível pra quem já estiver perto do destino. Destino sem
            # portal nenhum (ex: caverna comum) não dispara nada.
            dest_gate = self._portal_gate_at_position(
                dest["target_map"], dest["target_x"], dest["target_y"])
            if dest_gate is not None:
                self._portal_anim_arrive_events_this_tick.append({
                    "map_file": dest["target_map"], "portal_id": dest_gate["id"],
                    "x": dest["target_x"], "y": dest["target_y"],
                })

    def consume_portal_cast_cancel_events(self) -> list[dict]:
        result = list(self._portal_cast_cancel_events_this_tick)
        self._portal_cast_cancel_events_this_tick.clear()
        return result

    def consume_portal_teleport_events(self) -> list[dict]:
        result = list(self._portal_teleport_events_this_tick)
        self._portal_teleport_events_this_tick.clear()
        return result

    def consume_portal_anim_start_events(self) -> list[dict]:
        result = list(self._portal_anim_start_events_this_tick)
        self._portal_anim_start_events_this_tick.clear()
        return result

    def consume_portal_anim_stop_events(self) -> list[dict]:
        result = list(self._portal_anim_stop_events_this_tick)
        self._portal_anim_stop_events_this_tick.clear()
        return result

    def consume_portal_anim_arrive_events(self) -> list[dict]:
        result = list(self._portal_anim_arrive_events_this_tick)
        self._portal_anim_arrive_events_this_tick.clear()
        return result
