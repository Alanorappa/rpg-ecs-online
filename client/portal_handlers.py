"""
client/portal_handlers.py — Mixin do GameEngine: portais com seleção
de destino (30/09/2026, pedido do usuário).

Diferente das transições de mapa existentes (cavernas,
`self.transition_tiles`, `game.py::_build_transition_tiles`) — que
auto-disparam ZONE_CHANGE_REQ assim que o jogador PISA no tile, com
destino FIXO — um portal tem vários destinos possíveis: pisar no tile
só abre um modal de seleção (`self.portal_tiles`, dado 100% local,
mesmo princípio de `transition_tiles`, sem round-trip de rede pra
listar opções). Ao escolher um destino, manda PORTAL_ENTER_REQ; o
servidor valida o destino contra a lista DAQUELE portal (nunca confia
em to_map/target_x/y soltos, ver server/portal_processor.py) e inicia
um cast de alguns segundos — a teleportação de verdade só acontece
quando ESSE cast completa no servidor, via o ZONE_CHANGE normal que já
existe (`_handle_msg_zone_change`/`_do_transition`, zero código novo
de carregamento de mapa).

Sem lógica de gameplay aqui além da PREVISÃO local (barra de cast
otimista, animação disparada só na confirmação real do servidor,
cancelamento local imediato ao se mover) — mesmo espírito preditivo-
mas-servidor-manda de `SpellCast`/`ui/spell_system.py`. Esta classe NÃO
deve ser instanciada diretamente — é herdada por GameEngine, que
fornece self.world, self.player_entity, self.screen, self._net,
self.font_sm/font_md, self._u() (UIScaleMixin) e self._any_modal_open()
(ModalStackHandlers).
"""
import pygame

from ui.sound_manager import SOUNDS

_MODAL_W = 340
_ROW_H = 44
_PAD = 16

# Duração usada SÓ pra previsão otimista local (barra de cast aparece
# na hora do clique, antes do servidor confirmar) — o valor REAL vem
# de PORTAL_CAST_START logo em seguida e sobrescreve este palpite.
# Espelha server/world_server.py::WorldServer.PORTAL_CAST_DURATION_S —
# se um dia divergir, o servidor sempre vence (é só o "chute" inicial).
_DEFAULT_PORTAL_CAST_DURATION_S = 5.0

# Tempo parado no MESMO tile de portal antes do modal de seleção abrir
# (02/10/2026, pedido do usuário) — sem isso, andar DE PASSAGEM por
# cima do tile já abria o modal: o tween de um tile pra outro deixa
# `TileMovement.is_moving` False por 1 frame só entre dois passos, e
# isso bastava pra `_update_portal_state` abrir na hora. Ver
# `_portal_stand_key`/`_portal_stand_elapsed` — zera a cada frame em
# que o jogador se move ou troca de tile, só conta parado no mesmo.
_PORTAL_MODAL_STAND_DELAY_S = 0.5

# Duração NATURAL (não esticada) da animação de "desativar" ao chegar
# num portal (01/10/2026) — diferente de "activate" (sempre esticada
# pra caber a duração REAL do cast, mandada pelo servidor em
# PORTAL_ANIM_START), PORTAL_ANIM_ARRIVE não carrega duração nenhuma
# (é um evento pontual de chegada, não um cast com tempo) — o cliente
# decide sozinho quanto tempo o "desligar" deve durar. Ajustável aqui,
# 1 lugar só (ver _handle_msg_portal_anim_arrive).
_PORTAL_DEACTIVATE_DURATION_MS = 900.0

# Tremor de câmera no cast de portal (02/10/2026, pedido do usuário):
# sobe linear de 0% a 100% nos ÚLTIMOS `_PORTAL_SHAKE_RAMP_S` segundos
# do cast (ex: cast de 5s -> começa aos 2s decorridos) e, ao chegar no
# destino (mesmo instante que liga "portal_out", ver
# _trigger_portal_out_if_pending), decai de volta a 0 em
# `_PORTAL_SHAKE_FADE_S` -- reusa a MESMA duração da animação natural
# de "desativar" (_PORTAL_DEACTIVATE_DURATION_MS), não um número novo.
# Cancelamento por movimento para o tremor NA HORA (sem fade) -- só
# chegada de verdade decai suave, decisão confirmada com o usuário.
# Amplitude em pixels de MUNDO (antes do zoom) -- aplicado só no offset
# usado pra DESENHAR (self._cam_x/_cam_y em game.py), nunca dentro de
# engine.utils.get_camera_offset() (ponto único usado também por
# clique/mira/loot -- tremer ali deixaria clique impreciso).
_PORTAL_SHAKE_RAMP_S = 3.0
_PORTAL_SHAKE_FADE_S = _PORTAL_DEACTIVATE_DURATION_MS / 1000.0
_PORTAL_SHAKE_MAX_AMPLITUDE_PX = 6.0

# SFX de cast de portal (02/10/2026, pedido do usuário, timing exato
# calculado por ele a partir da forma de onda de `assets/sounds/sfx/
# portal.ogg`, 6.807s de duração total): toca a partir de
# `_PORTAL_SFX_START_S = 1.317` segundos de cast decorridos -- o ápice
# do ARQUIVO (contado a partir de quando a reprodução começa) cai aos
# 3.683s de reprodução, e 1.317 + 3.683 = 5.000s = exatamente a
# duração do cast (`_DEFAULT_PORTAL_CAST_DURATION_S`/`WorldServer.
# PORTAL_CAST_DURATION_S`) -- o estouro do som coincide com o instante
# da troca de tela. O restante do arquivo (~3.12s) toca naturalmente
# já no mapa de destino, sem interrupção (nunca corta no ZONE_CHANGE).
# Cancelamento por movimento PARA o som na hora (mesmo tratamento do
# tremor, `_PORTAL_SHAKE_*` acima) -- só chegada de verdade deixa tocar
# até o fim.
_PORTAL_SFX_START_S = 1.317

# Animação de PERSONAGEM "portal_in" (02/10/2026, pedido do usuário:
# arte foi refeita mais curta, deixou de fazer sentido esticar o ciclo
# pelos 5s inteiros do cast) -- liga só no ÚLTIMO
# `_PORTAL_CHAR_ANIM_START_S` segundo do cast, esticada (mesma técnica
# de sempre, `frame_ms = restante_ms / n_frames`) pra caber exatamente
# nesse intervalo final, terminando junto da troca de tela. Nos
# segundos anteriores o personagem fica no idle normal (nenhum estado
# de animação tocando). A luz azulada do personagem
# (`_get_portal_character_light_source`) é condicionada ao MESMO estado
# de animação -- acende só junto, decisão confirmada com o usuário
# (02/10/2026, não precisa aparecer mais cedo que a animação).
_PORTAL_CHAR_ANIM_START_S = 1.0

# Raio de busca (em tiles) pra achar o objeto VISUAL do portal perto do
# tile de interação do gate (01/10/2026) — ver _find_portal_anim_anchor.
# Achado real nos 2 portais de teste: o objeto fica consistentemente a
# (-1,+1) do gate (ancorado no canto inferior-esquerdo da própria arte,
# enquanto o gate marca onde o jogador INTERAGE) — raio 2 cobre essa
# distância com folga sem risco de pegar um objeto de outro portal
# vizinho (gates de teste estão a dezenas de tiles de distância entre si).
_PORTAL_ANIM_ANCHOR_SEARCH_RADIUS = 2

# Animação de PERSONAGEM entrando/saindo de um portal (02/10/2026,
# pedido do usuário) — arquivos SEM direção
# (`{class_id}_portal_in.png`/`{class_id}_portal_out.png`, ver fallback
# em `engine/character_animation.py::_load_sheet_frames`): "in" toca no
# jogador que está CASTANDO (ESTICADA pra caber a duração real do cast,
# via `anim_attack_frame_ms` calculado, NUNCA `anim_attack_loop_duration_ms`
# — esse usa módulo/loop, errado aqui, ver docstring de
# `_handle_msg_portal_cast_start`); "out" toca 1x, duração NATURAL, ao
# CHEGAR (mesmo padrão da animação "desativar" do tile). Intensidade da
# luz que fura a escuridão — mesma técnica do tile
# (`DayNightSystem._get_portal_frame_light_mask`), só que o PRODUTOR é
# `_get_portal_character_light_source()`, direto do frame REAL sendo
# desenhado no personagem, só pro jogador LOCAL (sem broadcast nesta
# rodada — decisão de escopo, diferente da luz do tile).
_PORTAL_CHAR_LIGHT_INTENSITY_PCT = 90


def _find_portal_anim_anchor(object_matrix: list, gate_x: int, gate_y: int) -> "tuple[int, int]":
    """Acha, perto de `(gate_x, gate_y)`, o tile ONDE o objeto de portal
    (catalogado com folha de ativar/desativar, ver `engine.tileset.
    PORTAL_ACTIVATE_SHEET_BY_TILE_ID`/`PORTAL_DEACTIVATE_SHEET_BY_TILE_ID`)
    está DE VERDADE desenhado — `ui/tile_render_system.py` indexa pelo
    tile onde o CARACTERE do objeto aparece no `object_matrix` (o
    "anchor"), que não precisa ser o mesmo tile que `portal_gates` marca
    pra interação (o gate marca "onde o jogador fica em pé pra usar o
    portal"; o objeto pode estar desenhado num canto diferente da
    própria arte). Bug real (01/10/2026): os 2 portais de teste têm o
    objeto plantado a (-1,+1) do gate — sem esta função, o estado de
    animação era escrito na posição do GATE mas o renderer só olhava a
    posição do OBJETO, então a animação nunca aparecia (chaves
    diferentes, nunca batiam).

    Retorna o MAIS PRÓXIMO (distância euclidiana ao quadrado) dentro de
    `_PORTAL_ANIM_ANCHOR_SEARCH_RADIUS` tiles; se nenhum for achado
    (arte ainda não cadastrada, ou erro de autoria no mapa), cai pro
    próprio `(gate_x, gate_y)` — mesmo fallback gracioso de qualquer
    outro mecanismo de animação ausente no projeto (resultado nesse
    caso: `portal_anim_state` nunca bate com nada no render, igual a
    hoje sem este fix, mas sem quebrar nada)."""
    from engine.tileset import (OBJECT_MAPPING, PORTAL_ACTIVATE_SHEET_BY_TILE_ID,
                                PORTAL_DEACTIVATE_SHEET_BY_TILE_ID)
    animatable_ids = set(PORTAL_ACTIVATE_SHEET_BY_TILE_ID) | set(PORTAL_DEACTIVATE_SHEET_BY_TILE_ID)
    if not animatable_ids:
        return (gate_x, gate_y)

    best = None
    best_dist = None
    r = _PORTAL_ANIM_ANCHOR_SEARCH_RADIUS
    for dy in range(-r, r + 1):
        row_idx = gate_y + dy
        if not (0 <= row_idx < len(object_matrix)):
            continue
        obj_row = object_matrix[row_idx]
        for dx in range(-r, r + 1):
            col_idx = gate_x + dx
            if not (0 <= col_idx < len(obj_row)):
                continue
            ch = obj_row[col_idx]
            if not ch or ch == ".":
                continue
            tile_type = OBJECT_MAPPING.get(ch)
            if tile_type is None:
                continue
            if getattr(tile_type, "sprite_name", "") not in animatable_ids:
                continue
            dist = dx * dx + dy * dy
            if best_dist is None or dist < best_dist:
                best_dist, best = dist, (col_idx, row_idx)
    return best if best is not None else (gate_x, gate_y)


def _begin_portal_in_anim(tm, class_id: "str | None", remaining_s: float,
                          hold_last_frame: bool = True) -> None:
    """Liga "portal_in" esticado pra caber em `remaining_s` segundos —
    PONTO ÚNICO compartilhado entre o caster local
    (`PortalHandlers._update_portal_char_anim`) e o espelho pra
    observadores remotos (`_update_remote_portal_casts`, 03/10/2026,
    bug real relatado pelo usuário: só o caster via a animação, nunca
    quem estava por perto) — nunca duplicar esta conta nos 2 lugares.

    `hold_last_frame=True` (padrão, usado pro caster local) segura na
    última frame até o ZONE_CHANGE real chegar (latência de rede/tick
    do servidor sempre deixa uma folga pequena — sem isso, 1 frame de
    idle aparece antes da troca de mapa). `False` (observador remoto)
    deixa o ciclo esticado terminar e voltar pro idle sozinho — a
    folga de 1 frame é imperceptível pra quem só está observando, e
    sem isso a pose ficaria PRESA pra sempre se o personagem continuar
    visível depois (ex: teleporte dentro do mesmo mapa)."""
    remaining_ms = max(1.0, remaining_s) * 1000.0
    frame_ms = 0
    if class_id:
        from engine.character_animation import _frames_for as _cframes_for
        n_frames = len(_cframes_for(class_id, "portal_in", tm.facing))
        if n_frames > 0:
            frame_ms = max(1, round(remaining_ms / n_frames))
    tm.anim_attack_state            = "portal_in"
    tm.anim_attack_started_ms       = pygame.time.get_ticks()
    tm.anim_attack_frame_ms         = frame_ms  # 0 = usa DEFAULT_FRAME_MS
    tm.anim_attack_loop_duration_ms = 0
    tm.anim_attack_hold_last_frame  = hold_last_frame


def _clear_portal_in_anim(tm) -> None:
    """Desliga "portal_in" se estiver ativo — PONTO ÚNICO compartilhado
    entre `_cancel_portal_cast_local` (caster) e o cancelamento
    observado remotamente (`_handle_msg_portal_cast_abort`)."""
    if tm is not None and tm.anim_attack_state == "portal_in":
        tm.anim_attack_state = ""
        # anim_attack_loop_duration_ms também precisa zerar aqui —
        # trigger_attack_anim's _attack_loop_still_active (engine/
        # character_animation.py) só olha esse campo + started_ms,
        # NUNCA anim_attack_state. Sem isso, cancelar o cast ANTES do
        # tempo deixava esse campo "vivo" até os 5s originais
        # passarem sozinhos, bloqueando golpe novo o tempo todo (bug
        # real: "estragou as animações de ataque", 01/10/2026).
        tm.anim_attack_loop_duration_ms = 0
        tm.anim_attack_hold_last_frame  = False


class PortalHandlers:

    # ── Estado (lazy — GameEngine não precisa de __init__ extra, mesmo
    # padrão de client/battleground_handlers.py::_bg_result) ────────────────

    @property
    def portal_tiles(self) -> dict:
        """(tile_x, tile_y) -> dict do portal (id, x, y, destinations) —
        populado por `_build_portal_tiles`, 100% local (mesmo dado que
        `self.transition_tiles`, carregado do mapa, nunca via rede)."""
        return getattr(self, "_portal_tiles_val", None) or {}

    @portal_tiles.setter
    def portal_tiles(self, value: dict) -> None:
        self._portal_tiles_val = value

    @property
    def _remote_portal_casts(self) -> dict:
        """eid (servidor) -> {"elapsed","duration","sfx_channel","char_anim_triggered"}
        — espelho, pra jogadores OBSERVANDO de fora, do que `_portal_cast_active`
        já faz pro próprio caster (03/10/2026, bug real relatado pelo
        usuário: ninguém além do caster ouvia o som/via a animação).
        Populado por `_handle_msg_portal_cast_begin`, atualizado todo
        frame em `_update_remote_portal_casts` (chamado de
        `_update_portal_state`). Nunca contém `self._my_eid` — o próprio
        caster já tem seu caminho privado via `_portal_cast_active`.

        NUNCA `getattr(...) or {}` aqui (padrão usado por `portal_tiles`
        acima) — aquele idioma só é seguro pra propriedade sempre
        REATRIBUÍDA por inteiro; esta é mutada em LUGAR (`self.
        _remote_portal_casts[eid] = ...`/`.pop(...)`), e dict vazio é
        "falsy" em Python — `or {}` devolveria um dict NOVO descartável
        a cada chamada assim que o dict guardado ficasse vazio, fazendo
        qualquer mutação em lugar desaparecer na hora (bug real pego
        pelo próprio teste novo, 03/10/2026, antes de ir pra produção)."""
        if getattr(self, "_remote_portal_casts_val", None) is None:
            self._remote_portal_casts_val = {}
        return self._remote_portal_casts_val

    @_remote_portal_casts.setter
    def _remote_portal_casts(self, value: dict) -> None:
        self._remote_portal_casts_val = value

    @property
    def _portal_modal_open(self):
        """None fora do modal de seleção; senão o dict do portal atual."""
        return getattr(self, "_portal_modal_open_val", None)

    @_portal_modal_open.setter
    def _portal_modal_open(self, value) -> None:
        self._portal_modal_open_val = value

    @property
    def _portal_modal_dismissed_key(self):
        """(x,y) do portal cujo modal foi fechado manualmente enquanto o
        jogador ainda estava em cima — evita reabrir sozinho todo frame
        até ele sair do tile e voltar."""
        return getattr(self, "_portal_modal_dismissed_key_val", None)

    @_portal_modal_dismissed_key.setter
    def _portal_modal_dismissed_key(self, value) -> None:
        self._portal_modal_dismissed_key_val = value

    @property
    def _portal_stand_key(self):
        """(x,y) do tile de portal onde o jogador está parado ACUMULANDO
        tempo agora (ver `_PORTAL_MODAL_STAND_DELAY_S`) — None quando não
        está parado em nenhum. Trocar de tile (ou se mover) zera o
        acumulado em `_portal_stand_elapsed`."""
        return getattr(self, "_portal_stand_key_val", None)

    @_portal_stand_key.setter
    def _portal_stand_key(self, value) -> None:
        self._portal_stand_key_val = value

    @property
    def _portal_stand_elapsed(self) -> float:
        """Segundos acumulados parado em `_portal_stand_key`."""
        return getattr(self, "_portal_stand_elapsed_val", 0.0)

    @_portal_stand_elapsed.setter
    def _portal_stand_elapsed(self, value: float) -> None:
        self._portal_stand_elapsed_val = value

    @property
    def _portal_shake_intensity(self) -> float:
        """0.0-1.0 — fração da amplitude máxima de tremor AGORA. Lida por
        `_portal_shake_render_offset()`, única consumidora (aplicada só
        no offset de DESENHO, nunca em `get_camera_offset()`)."""
        return getattr(self, "_portal_shake_intensity_val", 0.0)

    @_portal_shake_intensity.setter
    def _portal_shake_intensity(self, value: float) -> None:
        self._portal_shake_intensity_val = value

    @property
    def _portal_shake_fade_elapsed(self):
        """None fora de fade (tremor zerado, ou subindo junto do cast);
        senão segundos já decorridos desde a chegada, decaindo
        `_portal_shake_intensity` até 0 em `_PORTAL_SHAKE_FADE_S`."""
        return getattr(self, "_portal_shake_fade_elapsed_val", None)

    @_portal_shake_fade_elapsed.setter
    def _portal_shake_fade_elapsed(self, value) -> None:
        self._portal_shake_fade_elapsed_val = value

    @property
    def _portal_shake_fade_start(self) -> float:
        """Intensidade capturada no instante da chegada -- ponto de
        partida do decaimento linear em `_tick_portal_shake_fade`."""
        return getattr(self, "_portal_shake_fade_start_val", 0.0)

    @_portal_shake_fade_start.setter
    def _portal_shake_fade_start(self, value: float) -> None:
        self._portal_shake_fade_start_val = value

    @property
    def _portal_sfx_channel(self):
        """`pygame.mixer.Channel` do SFX de cast em andamento, ou None
        antes de `_PORTAL_SFX_START_S` ter decorrido (ainda não tocou)
        ou depois do cast terminar (tocado e já sem necessidade de
        controle -- deixado tocar até o fim por conta própria).
        `_cancel_portal_cast_local()` usa isso pra `.stop()` no
        cancelamento por movimento."""
        return getattr(self, "_portal_sfx_channel_val", None)

    @_portal_sfx_channel.setter
    def _portal_sfx_channel(self, value) -> None:
        self._portal_sfx_channel_val = value

    @property
    def _portal_char_anim_triggered(self) -> bool:
        """True depois que a animação "portal_in" já foi ligada pra
        este cast (ver `_update_portal_char_anim`) -- evita religar
        todo frame restante do último segundo."""
        return getattr(self, "_portal_char_anim_triggered_val", False)

    @_portal_char_anim_triggered.setter
    def _portal_char_anim_triggered(self, value: bool) -> None:
        self._portal_char_anim_triggered_val = value

    @property
    def _portal_cast_active(self):
        """None sem cast em andamento; senão {"elapsed","duration",
        "portal_id","dest_index"} — lido por client/hud_handlers.py::
        _draw_cast_bar."""
        return getattr(self, "_portal_cast_active_val", None)

    @_portal_cast_active.setter
    def _portal_cast_active(self, value) -> None:
        self._portal_cast_active_val = value

    @property
    def _portal_arrival_pending(self) -> bool:
        """True entre o momento em que `_handle_msg_zone_change` detecta
        que um `ZONE_CHANGE` encerrou um cast de portal (02/10/2026) e o
        momento em que `game.py::_do_transition()` termina de reposicionar
        o jogador — consumido por `_trigger_portal_out_if_pending()`
        (liga a animação "portal_out"). Não é a mesma coisa que
        `_portal_cast_active` (que já foi limpo nesse ponto) — existe só
        pra carregar esse "motivo" através da chamada de `_do_transition`,
        que não sabe nada sobre portais."""
        return getattr(self, "_portal_arrival_pending_val", False)

    @_portal_arrival_pending.setter
    def _portal_arrival_pending(self, value: bool) -> None:
        self._portal_arrival_pending_val = value

    @property
    def _portal_anim_anchor(self) -> dict:
        """(gate_x, gate_y) -> (anchor_x, anchor_y) — tile ONDE O OBJETO
        é desenhado de verdade (`ui/tile_render_system.py::
        render_static_objects()`/`get_world_objects()`, chave usada por
        `engine.portal_anim_state`), resolvido por `_build_portal_tiles`
        a partir do `object_matrix` real. NUNCA assuma que é igual ao
        tile do gate — ver docstring de `_find_portal_anim_anchor` pro
        porquê (bug real, 01/10/2026: eram tiles DIFERENTES, animação
        nunca aparecia porque o estado era escrito numa posição que o
        renderer nunca consultava)."""
        return getattr(self, "_portal_anim_anchor_val", None) or {}

    @_portal_anim_anchor.setter
    def _portal_anim_anchor(self, value: dict) -> None:
        self._portal_anim_anchor_val = value

    # ── Construção de dados do mapa (chamado de game.py, ao lado de
    # _build_transition_tiles) ────────────────────────────────────────────

    def _build_portal_tiles(self, spawn_points: dict, object_matrix: list) -> None:
        gates = spawn_points.get("portal_gates", [])
        self.portal_tiles = {(gate["x"], gate["y"]): gate for gate in gates}
        self._portal_anim_anchor = {
            (gate["x"], gate["y"]): _find_portal_anim_anchor(
                object_matrix, gate["x"], gate["y"])
            for gate in gates
        }
        self._portal_modal_open = None
        self._portal_modal_dismissed_key = None
        self._portal_stand_key = None
        self._portal_stand_elapsed = 0.0
        self._portal_cast_active = None
        self._portal_shake_intensity = 0.0
        self._portal_shake_fade_elapsed = None
        self._portal_sfx_channel = None
        self._portal_char_anim_triggered = False
        self._remote_portal_casts = {}

    # ── Update por frame (chamado de game.py, perto da detecção de
    # transição de cavernas) ──────────────────────────────────────────────

    def _update_portal_state(self, dt: float) -> None:
        from engine.components import TileMovement as _TMPortalUp
        tm = self.world.get_component(self.player_entity, _TMPortalUp)

        # Espelho pra quem está observando OUTRO jogador castar (03/10/2026)
        # — totalmente independente do cast do jogador LOCAL abaixo, por
        # isso roda incondicionalmente aqui, antes de qualquer `return`
        # do bloco local.
        self._update_remote_portal_casts(dt)

        if self._portal_cast_active is not None:
            # Movimento cancela o cast LOCALMENTE na hora (feedback
            # imediato) — o servidor manda PORTAL_CAST_CANCEL também
            # (mesmo espírito de SpellCast.interruptible), mas não
            # precisamos esperar a rede pra já parar de mostrar a barra.
            if tm is not None and tm.is_moving:
                self._cancel_portal_cast_local()
            else:
                self._portal_cast_active["elapsed"] += dt
                self._update_portal_shake_ramp()
                self._update_portal_sfx()
                self._update_portal_char_anim(tm)
            return

        # Fora de cast: só pode estar decaindo de uma chegada recente
        # (nunca subindo — subida só acontece dentro do bloco acima).
        self._tick_portal_shake_fade(dt)

        if not self.portal_tiles or tm is None or tm.is_moving:
            self._portal_stand_key = None
            self._portal_stand_elapsed = 0.0
            return
        key = (tm.current_tile_x, tm.current_tile_y)
        if key not in self.portal_tiles:
            self._portal_modal_dismissed_key = None
            self._portal_stand_key = None
            self._portal_stand_elapsed = 0.0
            return
        if key == self._portal_modal_dismissed_key:
            return
        if self._portal_modal_open is not None or self._any_modal_open():
            return

        if key != self._portal_stand_key:
            self._portal_stand_key = key
            self._portal_stand_elapsed = 0.0
        self._portal_stand_elapsed += dt
        if self._portal_stand_elapsed < _PORTAL_MODAL_STAND_DELAY_S:
            return

        self._portal_modal_open = self.portal_tiles[key]
        SOUNDS.play_ui("map_open")

    def _update_portal_shake_ramp(self) -> None:
        """Sobe `_portal_shake_intensity` linear de 0 a 1 nos últimos
        `_PORTAL_SHAKE_RAMP_S` segundos do cast ativo -- chamado todo
        frame enquanto `_portal_cast_active` existe e não está em
        movimento (ver `_update_portal_state`)."""
        cast = self._portal_cast_active
        remaining = cast["duration"] - cast["elapsed"]
        if remaining <= _PORTAL_SHAKE_RAMP_S:
            frac = 1.0 - max(0.0, remaining) / _PORTAL_SHAKE_RAMP_S
            self._portal_shake_intensity = min(1.0, max(0.0, frac))
        else:
            self._portal_shake_intensity = 0.0

    def _tick_portal_shake_fade(self, dt: float) -> None:
        """Decai `_portal_shake_intensity` até 0 em `_PORTAL_SHAKE_FADE_S`,
        a partir do valor capturado em `_portal_shake_fade_start`
        (`_trigger_portal_out_if_pending` é quem liga o fade, no
        instante exato da chegada). No-op se não há fade em andamento
        (`_portal_shake_fade_elapsed is None` -- tremor já zerado, ou
        ainda subindo dentro de um cast, caso que nunca chega aqui)."""
        if self._portal_shake_fade_elapsed is None:
            return
        self._portal_shake_fade_elapsed += dt
        t = self._portal_shake_fade_elapsed / _PORTAL_SHAKE_FADE_S
        if t >= 1.0:
            self._portal_shake_intensity = 0.0
            self._portal_shake_fade_elapsed = None
        else:
            self._portal_shake_intensity = self._portal_shake_fade_start * (1.0 - t)

    def _portal_shake_render_offset(self) -> tuple:
        """(dx, dy) em pixels de MUNDO pra somar no offset de câmera só
        de DESENHO (`game.py`'s `self._cam_x`/`_cam_y`) -- NUNCA dentro
        de `engine.utils.get_camera_offset()`, que clique/mira/loot
        também usam; tremer ali deixaria esses sistemas imprecisos
        durante o cast/chegada de portal. Recomputado do zero todo
        frame (ruído intencional, sem acúmulo -- diferente do resíduo
        de lerp documentado em CameraSystem, que é bug, não efeito)."""
        if self._portal_shake_intensity <= 0.0:
            return 0.0, 0.0
        import random
        amp = self._portal_shake_intensity * _PORTAL_SHAKE_MAX_AMPLITUDE_PX
        return random.uniform(-amp, amp), random.uniform(-amp, amp)

    def _update_portal_sfx(self) -> None:
        """Dispara o SFX (`assets/sounds/sfx/portal.ogg`, pré-carregado
        pelo nome `"portal"`) assim que o cast ativo atinge
        `_PORTAL_SFX_START_S` segundos decorridos -- chamado todo frame
        enquanto `_portal_cast_active` existe e não está em movimento
        (ver `_update_portal_state`), igual `_update_portal_shake_ramp`.
        `_portal_sfx_channel is not None` já cobre "já tocou pra este
        cast" -- nunca re-dispara no mesmo cast."""
        if self._portal_sfx_channel is not None:
            return
        if self._portal_cast_active["elapsed"] < _PORTAL_SFX_START_S:
            return
        self._portal_sfx_channel = SOUNDS.play("portal")

    def _update_remote_portal_casts(self, dt: float) -> None:
        """Espelho de `_update_portal_sfx`/`_update_portal_char_anim` pra
        jogadores OBSERVANDO outro player castar portal (03/10/2026, bug
        real relatado pelo usuário: som e animação só existiam pro
        próprio caster — `PORTAL_CAST_START` é privado de propósito).
        Itera `self._remote_portal_casts` (populado por
        `_handle_msg_portal_cast_begin`, nunca contém `self._my_eid`).

        Entrada remove sozinha quando: (a) a entidade saiu do AOI/mapa
        local (ENTITY_DESPAWN já limpa `_remote_players`, detectado aqui
        por `local_eid is None` — sem handler dedicado de despawn), ou
        (b) o cast local alcança a duração cheia (conclusão natural —
        não precisamos de nenhum evento do servidor pra saber disso,
        a duração já veio em PORTAL_CAST_BEGIN)."""
        if not self._remote_portal_casts:
            return
        from engine.components import TileMovement as _TMRemotePortal, RemoteControlled as _RCRemotePortal
        from engine.components import Position as _PosRemotePortal
        _lx, _ly = self._player_world_pos()
        _done = []
        for eid, cast in self._remote_portal_casts.items():
            local_eid = self._remote_players.get(eid)
            if local_eid is None:
                _done.append(eid)
                continue
            cast["elapsed"] += dt

            if not cast["sfx_played"] and cast["elapsed"] >= _PORTAL_SFX_START_S:
                cast["sfx_played"] = True
                pos = self.world.get_component(local_eid, _PosRemotePortal)
                if pos is not None:
                    SOUNDS.play_random_at(["portal"], pos.x, pos.y, _lx, _ly)

            if not cast["char_anim_triggered"]:
                remaining = cast["duration"] - cast["elapsed"]
                if remaining <= _PORTAL_CHAR_ANIM_START_S:
                    cast["char_anim_triggered"] = True
                    tm = self.world.get_component(local_eid, _TMRemotePortal)
                    if tm is not None:
                        rc = self.world.get_component(local_eid, _RCRemotePortal)
                        class_id = rc.class_id if rc else None
                        # hold_last_frame=False (diferente do caster local)
                        # — sem ZONE_CHANGE pra limpar explicitamente nesta
                        # ponta, deixar o ciclo esticado terminar sozinho é
                        # mais seguro que segurar a pose pra sempre.
                        _begin_portal_in_anim(tm, class_id, remaining,
                                             hold_last_frame=False)

            if cast["elapsed"] >= cast["duration"]:
                _done.append(eid)
        for eid in _done:
            self._remote_portal_casts.pop(eid, None)

    def _update_portal_char_anim(self, tm) -> None:
        """Liga a animação "portal_in" quando o cast ativo entra no
        último `_PORTAL_CHAR_ANIM_START_S` segundo (02/10/2026, pedido
        do usuário) -- chamado todo frame enquanto `_portal_cast_active`
        existe e não está em movimento, igual `_update_portal_shake_ramp`/
        `_update_portal_sfx`. `_portal_char_anim_triggered` cobre "já
        ligou pra este cast" -- nunca religa no mesmo cast.

        Estica o ciclo pra caber no `remaining` REAL no instante do
        disparo (não um valor fixo de 1000ms) -- mesma técnica de
        sempre (`frame_ms = restante_ms / n_frames`), só que a janela
        agora é o último segundo, não o cast inteiro."""
        if self._portal_char_anim_triggered or tm is None:
            return
        cast = self._portal_cast_active
        remaining = cast["duration"] - cast["elapsed"]
        if remaining > _PORTAL_CHAR_ANIM_START_S:
            return
        self._portal_char_anim_triggered = True

        # Estado "portal_in" (`{class_id}_portal_in.png`, sem direção)
        # -- engine/character_animation.py aceita QUALQUER string aqui,
        # cascata de fallback já cobre sprite ausente (cai pra idle
        # sozinho).
        #
        # pygame.time.get_ticks() — NUNCA time.time()*1000 aqui (bug
        # real já corrigido 01/10/2026, ver _trigger_portal_out_if_pending
        # pro histórico completo -- relógio de parede deixava
        # anim_attack_started_ms permanentemente "no passado" em
        # relação aos ticks do pygame, bloqueando golpe novo pra sempre).
        # `hold_last_frame=True` (padrão) — segura na ÚLTIMA frame até o
        # ZONE_CHANGE real chegar (latência de rede/tick do servidor
        # sempre deixa uma folga pequena). `_trigger_portal_out_if_pending`/
        # `_cancel_portal_cast_local` limpam isso quando o evento real chega.
        class_id = self._portal_local_class_id()
        _begin_portal_in_anim(tm, class_id, remaining)

    def _cancel_portal_cast_local(self, stop_shake: bool = True) -> None:
        """`stop_shake=True` (padrão, usado pelo cancelamento por
        movimento em `_update_portal_state`) zera o tremor NA HORA, sem
        fade -- decisão confirmada com o usuário (02/10/2026). `client/
        network_handlers.py::_handle_msg_zone_change` chama esta mesma
        função pra limpar `_portal_cast_active` em TODO ZONE_CHANGE,
        inclusive a conclusão NATURAL do cast (chegou ao destino) -- ali
        passa `stop_shake=False` pra preservar a intensidade atual
        (devia estar em ~1.0), que `_trigger_portal_out_if_pending()`
        captura logo depois pra iniciar o fade de chegada. Bug real
        (02/10/2026): sem esse parâmetro, o tremor sempre zerava ANTES
        do fade conseguir capturar nada -- o usuário via o tremor subir
        normal durante o cast e simplesmente desaparecer ao chegar, em
        vez de decair suave.

        Mesmo `stop_shake` também controla o SFX (`_portal_sfx_channel`)
        -- `True` para o som NA HORA (cancelamento por movimento, nunca
        ouve o resto do "estouro" de um teleporte que não aconteceu);
        `False` (conclusão natural) deixa o canal tocando por conta
        própria até o fim, mesmo já no mapa de destino."""
        self._portal_cast_active = None
        if stop_shake:
            self._portal_shake_intensity = 0.0
            self._portal_shake_fade_elapsed = None
            if self._portal_sfx_channel is not None:
                self._portal_sfx_channel.stop()
        self._portal_sfx_channel = None
        from engine.components import TileMovement as _TMPortalC
        tm = self.world.get_component(self.player_entity, _TMPortalC)
        _clear_portal_in_anim(tm)

    def _portal_local_class_id(self) -> "str | None":
        """`CharacterStats.class_id` do jogador LOCAL, ou None se
        ausente (nunca deve faltar em jogo de verdade — guard só pra
        fixture de teste incompleta)."""
        from engine.components import CharacterStats as _CSPortalLocal
        cs = self.world.get_component(self.player_entity, _CSPortalLocal)
        return cs.class_id if cs is not None else None

    def _suppress_portal_modal_here(self) -> None:
        """Marca o tile ONDE O JOGADOR ESTÁ AGORA como "modal já
        dispensado" (02/10/2026, pedido do usuário) — chamado por
        `game.py::_do_transition()` logo depois de reposicionar o
        jogador por TELEPORTE (morte, portal, qualquer `ZONE_CHANGE`),
        nunca por caminhada normal. Sem isso, chegar via portal numa
        posição que por acaso é o tile de OUTRO portal (ex: os 2
        portais de teste se mirando) abria o modal de seleção na hora,
        como se o jogador tivesse ANDADO até lá — bug real relatado
        pelo usuário.

        `_update_portal_state()` já reseta esse dismissed_key sozinho
        assim que o jogador SAI do tile (`key not in self.portal_tiles`
        não se aplica aqui — o reset é no `else` de "saiu de um tile que
        ERA portal"), então isso só suprime o momento exato da chegada —
        sair e voltar ANDANDO depois abre normal."""
        from engine.components import TileMovement as _TMPortalSup
        tm = self.world.get_component(self.player_entity, _TMPortalSup)
        if tm is not None:
            self._portal_modal_dismissed_key = (tm.current_tile_x, tm.current_tile_y)

    def _trigger_portal_out_if_pending(self) -> None:
        """Liga a animação "portal_out" se o `ZONE_CHANGE` que acabou de
        reposicionar o jogador (`game.py::_do_transition()`, chamado
        LOGO DEPOIS de `_reposition_player`) veio de um cast de portal
        completo (02/10/2026, pedido do usuário) — `_portal_arrival_pending`
        é setado por `client/network_handlers.py::_handle_msg_zone_change`
        ANTES de `_do_transition` rodar. No-op silencioso pra qualquer
        OUTRO motivo de `ZONE_CHANGE` (morte de verdade, caverna) — a
        animação de chegada é exclusiva de portal.

        Duração NATURAL (`anim_attack_loop_duration_ms = 0`, nunca
        esticada) — diferente de "portal_in", "portal_out" não tem
        relação com nenhum tempo de servidor, é só o flourish de
        materializar, toca 1 ciclo e volta pro idle sozinho (mesmo
        mecanismo de qualquer golpe normal)."""
        if not self._portal_arrival_pending:
            return
        self._portal_arrival_pending = False
        # Tremor: captura a intensidade de AGORA (deveria ser ~1.0 se o
        # cast rodou os 3s finais inteiros) e liga o decaimento suave em
        # _PORTAL_SHAKE_FADE_S (ver _tick_portal_shake_fade, chamado de
        # _update_portal_state enquanto _portal_cast_active já é None).
        self._portal_shake_fade_start = self._portal_shake_intensity
        self._portal_shake_fade_elapsed = 0.0
        from engine.components import TileMovement as _TMPortalOut
        tm = self.world.get_component(self.player_entity, _TMPortalOut)
        if tm is not None:
            tm.anim_attack_state            = "portal_out"
            tm.anim_attack_started_ms       = pygame.time.get_ticks()
            tm.anim_attack_frame_ms         = 0   # 0 = DEFAULT_FRAME_MS, velocidade natural
            tm.anim_attack_loop_duration_ms = 0
            # Nunca segura em "portal_out" — ao contrário de "portal_in",
            # não há nenhum evento de rede futuro que precise dar tempo;
            # termina o ciclo e volta pro idle sozinho, normal. Também
            # limpa um possível hold deixado por "portal_in" (não deveria
            # sobrar nenhum — _trigger_portal_out_if_pending só roda
            # DEPOIS do teleporte já ter acontecido de verdade — mas
            # reset explícito é mais seguro que depender de ordem).
            tm.anim_attack_hold_last_frame  = False

    def _get_portal_character_light_source(self, camera_offset_x: float,
                                           camera_offset_y: float) -> "dict | None":
        """Luz azulada (02/10/2026, pedido do usuário — "mesma ideia" da
        luz do tile, ver `ui/tile_render_system.py::
        get_portal_light_sources`) derivada do frame REAL desenhado
        agora no personagem, enquanto `anim_attack_state` for
        "portal_in"/"portal_out" — None fora disso. Só o jogador LOCAL
        (sem broadcast nesta rodada, diferente da luz do tile — decisão
        de escopo, não uma limitação técnica).

        Mesmo formato de dict que `get_portal_light_sources()` devolve
        (`"screen_x"/"screen_y"/"portal_light_frame"/"intensity_pct"`) —
        `ui/day_night_system.py::DayNightSystem.render()` trata os dois
        no MESMO loop, sem saber se a luz veio de um tile ou de um
        personagem."""
        from engine.components import TileMovement as _TMPortalCharLight, Position as _PosPortalCharLight
        from engine.character_animation import (resolve_display_state, get_character_frame,
                                                 character_anchor_offset)
        tm = self.world.get_component(self.player_entity, _TMPortalCharLight)
        if tm is None or tm.anim_attack_state not in ("portal_in", "portal_out"):
            return None
        class_id = self._portal_local_class_id()
        if not class_id:
            return None
        pos = self.world.get_component(self.player_entity, _PosPortalCharLight)
        if pos is None:
            return None
        state, anim_t, frame_ms = resolve_display_state(tm, class_id, pygame.time.get_ticks())
        if state not in ("portal_in", "portal_out"):
            return None   # já expirou neste exato frame (resolve_display_state já resetou)
        frame = get_character_frame(class_id, state, tm.facing, anim_t, frame_ms)
        if frame is None:
            return None
        anchor_dx, anchor_dy = character_anchor_offset(frame.get_height(), frame.get_width())
        scr_x = int(pos.x - camera_offset_x - anchor_dx)
        scr_y = int(pos.y - camera_offset_y - anchor_dy)
        return {
            "screen_x": scr_x, "screen_y": scr_y,
            "portal_light_frame": frame,
            "intensity_pct": _PORTAL_CHAR_LIGHT_INTENSITY_PCT,
        }

    # ── Handlers de rede (dispatch em client/network_handlers.py) ────────────

    def _handle_msg_portal_cast_start(self, payload: dict) -> None:
        """Servidor confirmou o cast (request_portal_enter aceito) —
        substitui a previsão otimista (_DEFAULT_PORTAL_CAST_DURATION_S)
        pela duração REAL. A animação do personagem NÃO liga aqui
        (nunca client-side otimista puro, mesmo princípio de
        trigger_attack_anim só rodar em confirmação real) -- liga só
        no último `_PORTAL_CHAR_ANIM_START_S` segundo, via
        `_update_portal_char_anim` (chamado de `_update_portal_state`
        todo frame enquanto o cast está ativo; 02/10/2026, pedido do
        usuário -- arte foi refeita mais curta, deixou de fazer sentido
        esticar pelos 5s inteiros)."""
        duration = payload.get("duration", _DEFAULT_PORTAL_CAST_DURATION_S)
        self._portal_cast_active = {
            "elapsed":    0.0,
            "duration":   duration,
            "portal_id":  payload.get("portal_id", ""),
            "dest_index": payload.get("dest_index", -1),
        }
        self._portal_char_anim_triggered = False

    def _handle_msg_portal_cast_cancel(self, payload: dict) -> None:
        """Servidor cancelou o cast (moveu, ou qualquer motivo futuro) —
        idempotente: se o cliente já tinha cancelado localmente (caso
        comum, o cancelamento local roda 1 frame antes da confirmação
        de rede chegar), isto só confirma o estado que era."""
        self._cancel_portal_cast_local()

    # ── Handlers de rede — espelho pra OBSERVADORES (03/10/2026) ────────────
    # Diferente dos 2 acima (sobre O PRÓPRIO cast do jogador local,
    # privado), estes 2 são broadcast por AOI — chegam pra qualquer sessão
    # perto do CASTER (nunca pro caster em si, ver skip de eid==_my_eid).

    def _handle_msg_portal_cast_begin(self, payload: dict) -> None:
        """Outro jogador começou um cast de portal e está dentro do meu
        AOI — registra em `self._remote_portal_casts` pra
        `_update_remote_portal_casts` espelhar som+animação a cada
        frame. `eid == self._my_eid` nunca deveria acontecer na prática
        (`_sessions_in_aoi` inclui a origem, mas eu já tenho meu próprio
        caminho via `PORTAL_CAST_START`) — guard defensivo, mesmo
        princípio de `skip_local_caster` já usado por SKILL_EFFECT."""
        eid = payload.get("eid", -1)
        if eid == self._my_eid:
            return
        self._remote_portal_casts[eid] = {
            "elapsed":            0.0,
            "duration":           float(payload.get("duration", _DEFAULT_PORTAL_CAST_DURATION_S)),
            "sfx_played":         False,
            "char_anim_triggered": False,
        }

    def _handle_msg_portal_cast_abort(self, payload: dict) -> None:
        """Cast observado foi cancelado antes do tempo (o outro jogador
        se moveu) — remove do tracking e desliga a animação "portal_in"
        se ela já tinha ligado (só possível se o cancelamento aconteceu
        bem no último segundo do cast)."""
        eid = payload.get("eid", -1)
        self._remote_portal_casts.pop(eid, None)
        local_eid = self._remote_players.get(eid)
        if local_eid is not None:
            from engine.components import TileMovement as _TMPortalAbort
            tm = self.world.get_component(local_eid, _TMPortalAbort)
            _clear_portal_in_anim(tm)

    # ── Handlers de rede — animação do TILE do portal (01/10/2026) ──────────
    # Diferente dos 2 acima (sobre O PRÓPRIO cast do jogador local, privado),
    # estes 3 são broadcast por AOI — chegam pra QUALQUER sessão perto do
    # portal, incluindo quem nem está usando ele. Só escrevem em
    # engine/portal_anim_state.py (lido por ui/tile_render_system.py); zero
    # relação com a barra de cast ou com a animação do PERSONAGEM acima.

    def _portal_anim_render_pos(self, payload: dict) -> "tuple[int, int]":
        """Traduz (x,y) do GATE, como o servidor manda (payload cru, ele
        não sabe nada de sprite — ver shared/messages.py), pro tile onde
        o OBJETO é desenhado de verdade (`_portal_anim_anchor`, resolvido
        em `_build_portal_tiles`) — NUNCA usar payload["x"]/["y"] direto
        como chave de `engine.portal_anim_state`, ou a animação nunca
        bate com o que o renderer consulta (bug real, 01/10/2026)."""
        key = (payload.get("x", 0), payload.get("y", 0))
        return self._portal_anim_anchor.get(key, key)

    def _handle_msg_portal_anim_start(self, payload: dict) -> None:
        """Portal de ORIGEM começou a animar "ativando" — alguém (local
        ou não) iniciou um cast nele. Duração vem do servidor (mesma
        duração real do cast) pra esticar certo os N frames do sheet."""
        from engine import portal_anim_state as _pas_start
        import time as _t_pas_start
        now_ms = int(_t_pas_start.time() * 1000)
        duration_ms = float(payload.get("duration", _DEFAULT_PORTAL_CAST_DURATION_S)) * 1000.0
        ax, ay = self._portal_anim_render_pos(payload)
        _pas_start.start_activate(ax, ay, now_ms, duration_ms)

    def _handle_msg_portal_anim_stop(self, payload: dict) -> None:
        """Cast cancelado antes do tempo (último caster daquele portal
        desistiu) — encerra a animação de "ativando" já, sem esperar a
        duração original passar pros outros que estavam vendo."""
        from engine import portal_anim_state as _pas_stop
        ax, ay = self._portal_anim_render_pos(payload)
        _pas_stop.stop_activate(ax, ay)

    def _handle_msg_portal_anim_arrive(self, payload: dict) -> None:
        """Alguém chegou DE um portal nesse tile (ele é, ele mesmo, um
        portal de destino cadastrado) — toca "desativando" 1x, duração
        NATURAL do sheet (nenhuma duração vem do servidor aqui, é um
        evento pontual, não um cast)."""
        from engine import portal_anim_state as _pas_arrive
        import time as _t_pas_arrive
        now_ms = int(_t_pas_arrive.time() * 1000)
        ax, ay = self._portal_anim_render_pos(payload)
        _pas_arrive.start_deactivate(ax, ay, now_ms, _PORTAL_DEACTIVATE_DURATION_MS)

    # ── Envio ao servidor ─────────────────────────────────────────────────

    def _send_portal_enter(self, gate: dict, dest_index: int) -> None:
        if not self._net:
            return
        from shared.messages import MsgType as _MTPortal
        self._net.send(_MTPortal.PORTAL_ENTER_REQ, {
            "portal_id":  gate["id"],
            "dest_index": dest_index,
        })
        # Previsão otimista da barra — CONFIRMADA (duração real) por
        # PORTAL_CAST_START logo em seguida.
        self._portal_cast_active = {
            "elapsed":    0.0,
            "duration":   _DEFAULT_PORTAL_CAST_DURATION_S,
            "portal_id":  gate["id"],
            "dest_index": dest_index,
        }
        self._portal_modal_dismissed_key = (gate["x"], gate["y"])
        self._portal_modal_open = None
        SOUNDS.play_ui("button_click")

    # ── Modal de seleção de destino ───────────────────────────────────────

    def _close_portal_modal(self) -> None:
        gate = self._portal_modal_open
        if gate is not None:
            self._portal_modal_dismissed_key = (gate["x"], gate["y"])
        self._portal_modal_open = None

    def _portal_modal_rects(self, gate: dict) -> "tuple[pygame.Rect, list[pygame.Rect]]":
        """(panel_rect, [rect por destino]) — geometria compartilhada
        entre desenho e clique (mesmo princípio de outros modais do
        projeto, ex: hotbar/_trade_layout)."""
        destinations = gate.get("destinations", [])
        modal_w = self._u(_MODAL_W)
        row_h   = self._u(_ROW_H)
        pad     = self._u(_PAD)
        title_h = self._u(40)
        modal_h = title_h + pad + len(destinations) * row_h + pad
        sw, sh  = self.screen.get_size()
        panel = pygame.Rect(sw // 2 - modal_w // 2, sh // 2 - modal_h // 2, modal_w, modal_h)
        rects = []
        for i in range(len(destinations)):
            rects.append(pygame.Rect(
                panel.x + pad, panel.y + title_h + pad + i * row_h,
                modal_w - pad * 2, row_h - self._u(6),
            ))
        return panel, rects

    def _draw_portal_modal(self) -> None:
        gate = self._portal_modal_open
        if gate is None:
            return
        destinations = gate.get("destinations", [])
        panel, rects = self._portal_modal_rects(gate)

        overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        self.screen.blit(overlay, (0, 0))
        pygame.draw.rect(self.screen, (18, 22, 34), panel, border_radius=8)
        pygame.draw.rect(self.screen, (90, 130, 180), panel, 2, border_radius=8)

        title_s = self.font_md.render("Selecione o destino", False, (210, 225, 245))
        self.screen.blit(title_s, (panel.centerx - title_s.get_width() // 2,
                                   panel.y + self._u(12)))

        mouse_pos = pygame.mouse.get_pos()
        for dest, rect in zip(destinations, rects):
            hov = rect.collidepoint(mouse_pos)
            pygame.draw.rect(self.screen, (55, 75, 105) if hov else (35, 45, 65),
                             rect, border_radius=5)
            pygame.draw.rect(self.screen, (120, 160, 210), rect, 1, border_radius=5)
            label_s = self.font_sm.render(dest.get("label", "?"), False, (225, 235, 250))
            self.screen.blit(label_s, (rect.x + self._u(10),
                                       rect.centery - label_s.get_height() // 2))

    def _handle_portal_click(self, event) -> bool:
        """True sempre que o modal está aberto (bloqueante — consome
        qualquer clique, mesmo padrão de _handle_bg_result_click)."""
        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return False
        gate = self._portal_modal_open
        if gate is None:
            return False
        _panel, rects = self._portal_modal_rects(gate)
        for i, rect in enumerate(rects):
            if rect.collidepoint(event.pos):
                self._send_portal_enter(gate, i)
                return True
        return True
