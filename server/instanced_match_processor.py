"""
server/instanced_match_processor.py
Mixin base pra `MatchProcessorMixin` (Arena) e `BgQueueProcessorMixin`
(Battleground) — extrai só os pedaços do ciclo de vida de partida
instanciada que são de fato IDÊNTICOS entre os dois modos (débito B6,
PROBLEMAS_ARQUITETURA.md §16, 10/08/2026).

Escopo deliberadamente PEQUENO: mapeamento completo dos dois arquivos
mostrou duplicação real de FORMA em vários pontos (fila, propose, accept,
pending/countdown, leave), mas Arena e BG divergem de verdade em
SEMÂNTICA em quase todos eles — Arena é eliminação/1-vida com congelamento
dos sobreviventes ao decidir; BG é corrida-por-objetivo (Nexus) com
respawn contínuo até alguém sair, sem conceito de W.O./congelamento.
Forçar tudo isso numa base só com hooks pra cada divergência produziria
uma função "compartilhada" cheia de callbacks — menos legível que 2
funções separadas, não mais modular (mesmo critério já usado nesta sessão
pra decidir NÃO generalizar Chama Interna/timer do Fatiador: abstração
sem 2º caso de uso real de verdade não é modularidade, é ofuscação).
Referência: mesmo o `Battleground` do AzerothCore, que compartilha bem
mais entre modos diferentes de BG, mantém a lógica de spawn/setup
(`AddPlayer`) largamente NA subclasse concreta — só o bookkeeping
genérico de sessão/estado vai pra base.

3 pedaços aqui são cópia quase literal entre os dois arquivos (mesma
sequência de passos, mesmos nomes de variável) — esses SIM viram base
compartilhada:
  - `_im_sweep_accept_deadline` — detecta janela de aceite vencida +
    limpa convites pendentes (o que fazer com quem não apareceu
    continua no chamador — semântica diverge).
  - `_im_open_gate_if_ready` — abre portão físico + ativa minion lanes
    quando o countdown de preparo vence (só o conjunto de tiles e o
    evento de notificação mudam por modo).
  - `_im_results_timeout` — força saída de quem ficou parado na tela de
    resultado além do tempo limite (100% idêntico, só o dict de
    partidas/tempo limite/função de saída mudam).
"""
from __future__ import annotations


class InstancedMatchMixin:

    def _im_sweep_accept_deadline(self, match: dict, invite_dict: dict, now: float) -> bool:
        """Retorna True na primeira vez (só 1x por partida, via
        `match["accept_swept"]`) em que a janela de aceite vence — marca
        `accept_swept` e limpa os convites pendentes (`invite_dict`) de
        `invited_a`+`invited_b`. O chamador decide o que fazer com quem
        não apareceu (W.O./descarte/devolução — semântica difere por
        modo, ver `_tick_arena_pending`/`_tick_bg_pending`)."""
        if match["accept_swept"] or now < match["accept_deadline"]:
            return False
        match["accept_swept"] = True
        for eid in match["invited_a"] + match["invited_b"]:
            invite_dict.pop(eid, None)
        return True

    def _im_open_gate_if_ready(self, match: dict, now: float,
                               gate_tiles, emit_gate_open) -> None:
        """Abre o portão físico (pinta piso de pedra nos tiles) + ativa as
        minion lanes da instância quando o countdown de preparo vence —
        mesma mecânica em Arena/BG, só o conjunto de tiles e o evento de
        notificação mudam por modo. `gate_tiles`: lista de (gx, gy).
        `emit_gate_open(eid)`: dispara o evento de abertura pro cliente
        daquele eid (fila de eventos própria de cada modo)."""
        cd = match["countdown_deadline"]
        if cd is None or now < cd or match["fight_started"]:
            return
        match["fight_started"] = True
        self._activate_minion_lanes(match["instance_key"])
        from engine.components import Tilemap as _TMim
        from engine.tileset import STONE_FLOOR as _SFim
        bundle = self._map_bundles.get(match["instance_key"])
        if bundle is not None:
            tilemap = self.world.get_component(bundle.tilemap_entity, _TMim)
            if tilemap is not None:
                for gx, gy in gate_tiles:
                    tilemap.tile_matrix[gy][gx] = _SFim
        for eid in match["team_a"] + match["team_b"]:
            emit_gate_open(eid)

    def _im_results_timeout(self, matches: dict, auto_leave_s: float, leave_fn) -> None:
        """Partidas DECIDIDAS há mais de `auto_leave_s` segundos forçam a
        saída de quem ainda não saiu sozinho — mesmo padrão em Arena/BG,
        só o dict de partidas/tempo limite/função de saída mudam por
        modo. `leave_fn(match_id, eid)`."""
        import time as _time_imrt
        now = _time_imrt.time()
        for match_id, match in list(matches.items()):
            if not match.get("decided"):
                continue
            if now - match["decided_at"] < auto_leave_s:
                continue
            for eid in list(match["team_a"] + match["team_b"]):
                leave_fn(match_id, eid)
