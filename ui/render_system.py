# render_system.py
"""
RenderSystem — desenho de entidades (sprites, HP/nameplate, efeitos)
por Y-sort. CameraSystem — segue o jogador, fica no mesmo arquivo
porque o offset que ela calcula é o que RenderSystem consome todo
frame. Extraído de ui/systems.py (18/09/2026, ver
PROBLEMAS_ARQUITETURA.md §185, item #2 do backlog de 14).
"""
from __future__ import annotations
import pygame
import math
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui.ui_sizes import UI
from ui.world_labels import WORLD_LABELS
from ui.hud_bars import (build_player_hud, build_mob_hud, build_npc_badge, build_simple_hp_bar,
                       RESOURCE_COLORS, HUD_GAP_PX, effects_row_offset)

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from engine.core_systems import apply_effect
try:
    from debug.mob_combat_debug import MCL as _MCL
except ImportError:
    _MCL = None  # não disponível no ambiente do cliente

from engine.components import Position, Renderable, PlayerControlled, Camera, TileMovement, CombatStats, CombatState, CharacterStats, FogOfWar, StatusEffects, EntityIdentity, NPC, RemoteControlled, GhostState
from engine.world import World
from engine.tileset import TILE_SIZE
# Hoisted pro topo (14/09/2026, revisão de performance — ver
# arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 2): estavam sendo
# reimportados dentro de RenderSystem.render(), no loop de desenho mais
# quente do cliente (uma vez por ENTIDADE visível, todo frame). Módulos
# puros (`from __future__ import annotations` só), sem ciclo de import
# com ui/systems.py — seguro no topo.
from engine.entity_disguise import get_active_appearance_override, get_animated_disguise_frame
from engine.character_animation import (get_character_frame, resolve_display_state,
                                        character_anchor_offset)
from ui.ui_helpers import fill_surf
from ui.tile_sprite_manager import TILE_SPRITES
from content.status_effects_data import EFFECT_DEFS

# Lookup reverso skill_id → (talent_id, min_points) — fonte única em
# world_systems (_TALENT_SKILL_REQS, usado também por is_skill_authorized
# no gate autoritativo do servidor). Alias mantido pro gate de UI local.



# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import System, EnemyAISystem


# ── Cache de sprites de efeito de chão (lazy-loaded) ─────────────────────────
_GROUND_EFFECT_SPRITES: dict[str, "pygame.Surface | None"] = {}

_GROUND_EFFECT_TYPES_ICONS = {"root"}  # efeitos com sprite de chão: sem ícone na barra


def _build_effects_row(active_effects: list, font: "pygame.font.Font",
                       icon_size: "int | None" = None
                       ) -> "tuple[pygame.Surface | None, list[tuple[str, pygame.Rect]]]":
    """Monta a fila de ícones de efeito ativos (bleed/stun/sleep/...) numa
    ÚNICA Surface — vai inteira pro WORLD_LABELS (screen-space, via
    add_icon_offset, encostada na borda direita da HUD de barras) em vez
    de desenhar direto no world_surf. Motivo: a HUD (ui/hud_bars.py) já é
    screen-space desde a correção do bug "número flutuando fora da caixa"
    (ARQUITETURA_ONLINE.md 23.9) — desenhar os ícones de efeito em espaço
    de MUNDO de novo reintroduziria o mesmo desalinhamento (a fila
    desgrudaria da HUD conforme o zoom da câmera muda). Ícones 16×16 por
    padrão (nameplate); `icon_size` (31/08/2026, buff bar) escala pra
    outro tamanho — o PNG de 16×16 é reescalado com `pygame.transform.
    scale` (SEM smoothscale, de propósito: mantém a nitidez pixel-art no
    upscale, smoothscale borraria). Sobreposição do tempo restante em
    vermelho.

    Fila "estilo WoW": a ordem é a própria ordem de inserção no dict de
    StatusEffects (Python preserva ordem de inserção) — recalculada do
    zero a cada frame, então não são posições fixas por efeito. O mais
    antigo fica mais perto da HUD (esquerda da fila), o mais novo entra
    sempre na ponta direita; quando um efeito expira, os seguintes
    "puxam" pra esquerda sozinhos (é só a lista atual desenhada em
    sequência, ninguém guarda slot fixo) — efeito novo ocupa a posição
    livre automaticamente, sem pular a fila. `ui/buff_bar.py` ancora essa
    Surface pela borda DIREITA (`bar_right_x - row.get_width()`), então
    o mais novo (desenhado na ponta direita DESTA Surface) fica sempre na
    posição fixa junto do minimapa, e os mais antigos vão "empurrados"
    pra esquerda conforme a fila cresce — mesmo efeito visual do WoW.

    Retorna `(surface, rects)` — `rects` (01/09/2026, tooltip da buff
    bar) é `[(effect_type, Rect_local_à_Surface), ...]`, o MESMO `ix`/
    `ICON` já usados pra desenhar, só que também guardados em vez de
    descartados — ponto único de geometria, nunca duplicar esse cálculo
    num hit-test separado (mesmo princípio já usado pela hotbar,
    client/hotbar_handlers.py). Quem não precisa (nameplate/mob/player
    remoto) só ignora o 2º valor.
    """
    from math import ceil as _ceil
    from ui.effect_animator import get_frame as _get_frame, FRAME_W, FRAME_H
    from content.status_effects_data import EFFECT_DEFS as _EDEFS

    visible = [e for e in active_effects
               if e.effect_type not in _GROUND_EFFECT_TYPES_ICONS]
    if not visible:
        return None, []

    ICON = icon_size if icon_size is not None else FRAME_W   # 16 por padrão
    GAP  = 3
    n = len(visible)
    row = pygame.Surface((n * ICON + (n - 1) * GAP, ICON), pygame.SRCALPHA)
    ix = 0
    rects: list = []

    for eff in visible:
        frame = _get_frame(eff.effect_type)
        if frame is not None:
            if frame.get_width() != ICON or frame.get_height() != ICON:
                frame = pygame.transform.scale(frame, (ICON, ICON))
            row.blit(frame, (ix, 0))
        else:
            defn = _EDEFS.get(eff.effect_type)
            col  = defn.color if defn else (150, 150, 150)
            pygame.draw.rect(row, col, (ix, 0, ICON, ICON))

        rects.append((eff.effect_type, pygame.Rect(ix, 0, ICON, ICON)))

        dur = getattr(eff, 'duration', 0.0)
        if 0 < dur <= 99:
            txt   = str(max(1, _ceil(dur)))
            shad  = font.render(txt, False, (0, 0, 0))
            label = font.render(txt, False, (255, 60, 60))
            tx = ix + (ICON - label.get_width())  // 2
            ty = (ICON - label.get_height()) // 2
            row.blit(shad,  (tx + 1, ty + 1))
            row.blit(label, (tx,     ty))

        ix += ICON + GAP

    return row, rects

def _filter_nameplate_effects(sfx: "StatusEffects | None") -> list:
    """Efeitos ativos pra exibir no NAMEPLATE — exclui buff (`is_buff=True`
    em EFFECT_DEFS), 31/08/2026: buff agora tem barra própria
    (ui/buff_bar.py, só o jogador local), nameplate mostra só debuff, pra
    não duplicar o mesmo ícone nos dois lugares. Extraído (não inline) pra
    dar pra testar sem montar a pipeline de render inteira. Tipo
    desconhecido/sem entrada em EFFECT_DEFS fica visível por padrão
    (mesmo fail-open de `_build_effects_row` abaixo, `defn = _EDEFS.get(...)`)."""
    if sfx is None:
        return []
    from content.status_effects_data import EFFECT_DEFS as _EDEFS_np
    return [e for e in sfx.effects.values()
            if not (_EDEFS_np.get(e.effect_type) and _EDEFS_np[e.effect_type].is_buff)]


def _get_ground_effect_sprite(effect_type: str) -> "pygame.Surface | None":
    """Retorna sprite de efeito de chão para o tipo dado; carrega na primeira vez.

    Usa o mesmo padrão lazy-load do effect_animator — falha silenciosa se
    o arquivo não existir ou pygame ainda não tiver modo de vídeo ativo.
    """
    if effect_type in _GROUND_EFFECT_SPRITES:
        return _GROUND_EFFECT_SPRITES[effect_type]
    _PATH_MAP = {
        "root": "assets/effects/frozen_root.png",
    }
    rel_path = _PATH_MAP.get(effect_type)
    surf = None
    if rel_path:
        try:
            from paths import resource_path as _rp
            surf = pygame.image.load(_rp(rel_path)).convert_alpha()
        except Exception:
            surf = None
    _GROUND_EFFECT_SPRITES[effect_type] = surf
    return surf














# ─────────────────────────────────────────────────────────────────────────────


# apply_effect é re-exportado de core_systems (importado no topo do arquivo).
# Mantido aqui para backward compatibility com todos os importadores.


















class RenderSystem(System):
    def __init__(self, world: World, screen: pygame.Surface):
        self.world = world
        self.world_surf = screen
        self.hud_surf   = screen
        # CachedFont: timers de efeito re-renderizam todo frame (texto "3.4s"
        # por entidade) — cache na fonte evita o custo do TTF render por frame.
        from ui.fonts import CachedFont as _CF
        self._effect_dur_font = _CF(None, 18)
        # Nome flutuante acima de NPCs (e, no cliente online, mobs — ver
        # client/remote_entity_handlers.py::_draw_mob_hp_bars, que usa
        # self.font_sm de lá porque roda numa classe diferente/mixin de
        # GameEngine). Determination (fonte do projeto) — usava a pixel
        # font MEGAMAN10 até o usuário reportar o "g" parecendo "9"
        # (17/07/2026), depois "muito pequena" com o size mal corrigido, e
        # por fim "ainda parece diferente do chat" — pedido explícito:
        # "mesmo estilo do nick que está no chat". self._name_font aqui é
        # só um FALLBACK até set_name_font() ser chamado (GameEngine
        # injeta o próprio self.font_sm logo após construir este objeto e
        # de novo a cada _reload_ui_fonts(), ver game.py) — garante o
        # MESMO tamanho/objeto da janela de chat, nunca um `make()`
        # próprio e potencialmente dessincronizado de _ui_scale.
        from ui.fonts import make as _make_name
        self._name_font = _make_name(22)
        # Fonte do número de nível — tamanho escolhido testando visualmente
        # (ver ui/hud_bars.py::LEVEL_FONT_SIZE), não é o mesmo tamanho do
        # nome (a caixinha da HUD tem proporção diferente).
        from ui.hud_bars import LEVEL_FONT_SIZE as _LFS
        self._level_font = _make_name(_LFS * 2)

    def set_name_font(self, font: "pygame.font.Font") -> None:
        """GameEngine injeta self.font_sm aqui (logo após construir este
        objeto, e de novo a cada _reload_ui_fonts()) — nameplate de NPC/
        mob local passa a usar o MESMO objeto de fonte da janela de chat.
        Ver comentário em __init__."""
        self._name_font = font

    def _resolve_character_class_id(self, entity_id: int) -> "str | None":
        """class_id resolvível pra animação de personagem (guerreiro/
        mago/arqueiro) — local via `CharacterStats`, remoto via
        `RemoteControlled`. None = mob/NPC/harvestable, sem sprite de
        personagem nenhum. Ponto único de verdade — usado tanto pro
        Y-sort (10/09/2026, precisa saber ANTES de desenhar se esta
        entidade usa o tile de colisão de 32×32 do sprite, não o
        `renderable.height` antigo) quanto pro desenho de fato."""
        if self.world.get_component(entity_id, PlayerControlled) is not None:
            cs = self.world.get_component(entity_id, CharacterStats)
            return cs.class_id if cs else None
        rc = self.world.get_component(entity_id, RemoteControlled)
        return rc.class_id if rc else None

    def render(self, camera_offset_x: float = 0, camera_offset_y: float = 0,
               world_objects: list = None, nameplate_mode: int = 0) -> None:
        """
        Renderiza entidades e tile-objetos (árvores, pedras, etc.) em Y-sort.

        world_objects — lista retornada por TileRenderSystem.get_world_objects().
        Objetos com sort_y menor são desenhados primeiro (ficam atrás de quem está
        mais ao sul na tela), criando o efeito de profundidade.

        nameplate_mode — ciclo Shift+V (game.py::self._nameplate_mode,
        pedido do usuário 29/07/2026): 0=completo (badge/barra PNG+nome+
        efeitos, padrão), 1=só nome, 2=nome+barra de HP simples
        (build_simple_hp_bar, sem PNG/badge/efeitos). Vale pro HUD do
        próprio player, de mob local e de badge de NPC — mob/player
        remotos leem o mesmo campo direto em
        client/remote_entity_handlers.py.
        """
        # Descobre qual entidade o jogador tem como alvo
        target_id = -1
        _cs_first = self.world.get_first(CombatState, PlayerControlled)
        if _cs_first is not None:
            target_id = _cs_first[1].target_entity_id

        # ── Fog of War: conjunto de tiles visíveis neste frame ────────────────
        _fog_visible:   set | None = None
        _fog_explored:  set | None = None
        _fow_render_first = self.world.get_first(FogOfWar)
        if _fow_render_first is not None:
            _fog_visible  = _fow_render_first[1].visible
            _fog_explored = _fow_render_first[1].explored

        # ── Coleta drawables: (sort_y_world, tipo, dados) ─────────────────────
        drawables = []

        # Culling de viewport (15/09/2026, item 8 da revisão de performance
        # — arquitetura/CLIENT_PERFORMANCE_REVIEW.md): antes disso, TODA
        # entidade com Position+Renderable entrava no Y-sort/desenho, mesmo
        # fora da tela mas dentro do raio de AOI do servidor (AOI_RADIUS=15
        # tiles — bem maior que a tela). AOI existe pra replicação de rede,
        # nunca foi pensado como filtro de render. Mesmo espírito de
        # `_object_visible_tile_range()` (que já faz isso pra tile-objetos),
        # com margem FIXA generosa (não dinâmica por catálogo, como lá) —
        # cobre o maior sprite de personagem conhecido (CHAR_TILE_SIZE=96px)
        # + a pilha de nameplate/HUD/ícones acima da entidade
        # (ui/world_labels.py), sem precisar calcular o maior sprite de
        # mob/NPC do jogo inteiro.
        _CULL_MARGIN_PX = 200.0
        _cull_l = camera_offset_x - _CULL_MARGIN_PX
        _cull_t = camera_offset_y - _CULL_MARGIN_PX
        _cull_r = camera_offset_x + self.world_surf.get_width()  + _CULL_MARGIN_PX
        _cull_b = camera_offset_y + self.world_surf.get_height() + _CULL_MARGIN_PX

        # Entidades vivas
        for entity_id, position, renderable in self.world.get_entities_with(Position, Renderable):
            if not (_cull_l <= position.x <= _cull_r and _cull_t <= position.y <= _cull_b):
                continue
            combat_stats = self.world.get_component(entity_id, CombatStats)
            _gst_rnd = self.world.get_component(entity_id, GhostState)
            # Corpo (morto, espírito ainda não liberado): permanece visível no
            # local da morte. Ghost (espírito liberado): renderiza semi-transparente.
            # GhostState só existe no player LOCAL (client/network_handlers.py
            # só anexa em self.player_entity) — player/bot REMOTO morto nunca
            # bate aqui, só em RemoteControlled.hp<=0 (mesmo sinal já usado
            # embaixo no branch de desenho/Y-sort). Sem esse fallback, o corpo
            # remoto nunca era reconhecido como "corpo" nesta 1ª passagem —
            # 3ª vez que esse mesmo gap aparece nesta sessão (animação de
            # morte e colisão tiveram o mesmo bug, ver PROBLEMAS_ARQUITETURA.md
            # §186), desta vez quebrando o Y-sort (corpo remoto continuava
            # ordenado como personagem em pé, na frente de quem andava por
            # cima dele).
            _rc_rnd = self.world.get_component(entity_id, RemoteControlled)
            _is_remote_dead_rnd = _rc_rnd is not None and _rc_rnd.hp <= 0
            _is_corpse_rnd = ((_gst_rnd is not None and _gst_rnd.is_dead and not _gst_rnd.is_ghost)
                              or _is_remote_dead_rnd)
            _is_ghost_rnd  = _gst_rnd is not None and _gst_rnd.is_ghost
            if combat_stats and combat_stats.current_hp <= 0 and not _is_corpse_rnd and not _is_ghost_rnd:
                continue
            # Oculta entidades fora do campo de visão (jogador local e players
            # remotos nunca são ocultos por fog — LOS/exploração é mecânica de
            # "neblina sobre mobs/mapa", não deve esconder outro jogador real
            # dentro do AOI; invisibilidade de player é regra do servidor
            # (CombatState.is_visible/_can_see), não de fog of war).
            if _fog_visible is not None:
                is_player = (self.world.get_component(entity_id, PlayerControlled) is not None
                             or self.world.get_component(entity_id, RemoteControlled) is not None)
                if not is_player:
                    etx = int(position.x / TILE_SIZE)
                    ety = int(position.y / TILE_SIZE)
                    if (etx, ety) not in _fog_visible:
                        continue
            # Harvestable (Renderable.sprite_id setado, Fase M1) usa Position.y
            # já igual ao CENTRO do tile (create_harvestable_entity) e ocupa a
            # altura inteira do tile (32px) — o foot_y genérico (position.y +
            # height/2) então cai na BASE do tile, mais "pra frente" que o
            # sort_y de objeto estático de mapa (ry*tile_size + tile_size//2,
            # TileRenderSystem, também centro do tile) e que o foot_y de
            # qualquer personagem/mob no MESMO tile (sprite menor que o tile —
            # PLAYER_SIZE=24). Resultado: harvestable desenhava sempre por
            # cima do personagem parado em cima dele (bug real relatado pelo
            # usuário 28/07/2026 — "o item fica sobre o personagem, deveria
            # ser o contrário"). Fix: harvestable ordena pelo CENTRO do tile
            # (position.y puro, já é isso por construção), igual objeto
            # estático — só o visual (branch de sprite_id logo abaixo, já
            # ancorado na base do tile) continua ocupando o tile inteiro.
            # Personagem com animação (guerreiro/mago/arqueiro, local ou
            # remoto — 10/09/2026): Y-sort usa o tile de colisão REAL de
            # 32×32 dentro do sprite (`CHAR_COLLISION_SIZE`, ver
            # engine/character_animation.py), nunca `renderable.height`
            # (24, convenção antiga do retângulo) — vale mesmo quando o
            # frame específico ainda cai no retângulo de fallback (ex:
            # direção sem arte ainda), pra o Y-sort não pular dependendo
            # de qual arte já existe.
            # Corpo de player/bot (is_dead, não-ghost) — plano no chão, não
            # compete por Y-sort com quem anda por cima: precisa SEMPRE
            # desenhar atrás, não só "às vezes" por ter um foot_y um pouco
            # menor que o de personagem vivo (`renderable.height/2=12` vs
            # `CHAR_COLLISION_SIZE/2=16` — margem de só 4px, MUITO menor que
            # o passo de 32px de um tile: durante a travessia a pé por cima
            # do corpo, o `position.y` real do personagem vivo cruza essa
            # margem de 4px e a ordem inverte por um instante — bug real
            # relatado pelo usuário, 19/09/2026, "anda pra cima, ele fica
            # por baixo por um breve momento"). Pedido explícito do
            # usuário: corpo é chão, não personagem — "sem Y-sort, ele
            # sempre precisa estar atrás". Fix: em vez de SOMAR uma âncora
            # (competindo com `position.y` de quem está por cima), SUBTRAI
            # meio tile — garante `foot_y` do corpo sempre menor que o de
            # QUALQUER personagem vivo parado em qualquer ponto do MESMO
            # tile (a pior posição do personagem, na borda norte do tile,
            # ainda tem `foot_y` = `position.y_do_corpo`, ainda maior que
            # `position.y_do_corpo - TILE_SIZE/2`). Escopo de 1 tile só —
            # não afeta ordenação contra árvore/objeto 2+ tiles de
            # distância. Corpo de MOB não passa por aqui (não resolve
            # `class_id`) — comportamento dele intocado.
            _is_corpse_rnd_sort = _is_corpse_rnd
            _char_class_id_sort = self._resolve_character_class_id(entity_id)
            if _is_corpse_rnd_sort:
                foot_y = position.y - TILE_SIZE / 2
            elif _char_class_id_sort:
                # Personagem com animação (guerreiro/mago/arqueiro, local ou
                # remoto — 10/09/2026): Y-sort usa o tile de colisão REAL de
                # 32×32 dentro do sprite (`CHAR_COLLISION_SIZE`, ver
                # engine/character_animation.py), nunca `renderable.height`
                # (24, convenção antiga do retângulo) — vale mesmo quando o
                # frame específico ainda cai no retângulo de fallback (ex:
                # direção sem arte ainda), pra o Y-sort não pular dependendo
                # de qual arte já existe.
                from engine.character_animation import CHAR_COLLISION_SIZE as _CCS_sort
                foot_y = position.y + _CCS_sort / 2
            elif renderable.sprite_id:
                foot_y = position.y
            else:
                foot_y = position.y + renderable.height / 2
            drawables.append((foot_y, "entity", entity_id, position, renderable, combat_stats, _gst_rnd))

        # Tile-objetos (árvores, arbustos, pedras — objetos estáticos do mapa)
        if world_objects:
            for obj in world_objects:
                # Objetos estáticos aparecem tanto em tiles visíveis quanto
                # explorados (o fog overlay escurece os explorados automaticamente).
                if _fog_visible is not None and _fog_explored is not None:
                    tx, ty = obj.get("tile_x", -1), obj.get("tile_y", -1)
                    if (tx, ty) not in _fog_visible and (tx, ty) not in _fog_explored:
                        continue
                drawables.append((obj["sort_y"], "object", obj))

        # ── Ordena por Y do pé (sul = frente) ─────────────────────────────────
        drawables.sort(key=lambda d: d[0])

        # ── Desenha na ordem ──────────────────────────────────────────────────
        # Retângulo de tela do jogador local, capturado quando ele é
        # desenhado (mais abaixo neste loop) — usado pra decidir a
        # transparência de objeto "alto" (occludes_player, 20/09/2026,
        # pedido do usuário). `None` até o jogador ser desenhado nesta
        # passada: como a lista está em ORDEM CRESCENTE de sort_y e um
        # objeto só deve ficar transparente quando o jogador está ATRÁS
        # dele (sort_y do jogador < sort_y do objeto → jogador desenhado
        # ANTES na mesma passada), continuar `None` aqui é exatamente
        # "objeto está na frente/abaixo do jogador no Y-sort" — nenhuma
        # comparação de sort_y explícita é necessária.
        _local_player_screen_rect: "pygame.Rect | None" = None
        for item in drawables:
            if item[1] == "object":
                obj = item[2]
                sx, sy, w, h = obj["screen_x"], obj["screen_y"], obj["width"], obj["height"]
                _occlude_fade = (obj.get("occludes_player", False)
                                and _local_player_screen_rect is not None
                                and pygame.Rect(sx, sy, w, h).colliderect(_local_player_screen_rect))
                if obj["sprite"] is not None:
                    if _occlude_fade:
                        # Sprite CACHEADA (TILE_SPRITES), compartilhada por
                        # todo tile do mesmo tipo — copy() antes do
                        # set_alpha(), nunca mutar o objeto original (mesma
                        # lição do bush stealth/corpo, ver PROBLEMAS_ARQUITETURA.md).
                        _faded_obj = obj["sprite"].copy()
                        _faded_obj.set_alpha(77)   # 70% transparente = 30% opaco
                        self.world_surf.blit(_faded_obj, (sx, sy))
                    else:
                        self.world_surf.blit(obj["sprite"], (sx, sy))
                elif _occlude_fade:
                    self.world_surf.blit(fill_surf((w, h), (*obj["color"][:3], 77)), (sx, sy))
                else:
                    pygame.draw.rect(self.world_surf, obj["color"], (sx, sy, w, h))
                continue

            # ── Entidade ──────────────────────────────────────────────────────
            _, _, entity_id, position, renderable, combat_stats, _gst_draw = item
            # GhostState só existe no PRÓPRIO player local (ver
            # client/network_handlers.py) — player/bot REMOTO nunca tem
            # esse componente; "morto" pra eles é `RemoteControlled.hp<=0`
            # (mesmo sinal que remote_entity_handlers.py já usa pra
            # suprimir a barra de HP do nameplate). Sem isso, um remoto
            # morto caía direto no branch de animação abaixo (nenhum dos
            # dois `elif` de corpo/espírito reconhecia ele como morto) e
            # ficava preso pra sempre em "idle" — bug real relatado pelo
            # usuário, 19/09/2026, ver PROBLEMAS_ARQUITETURA.md.
            _rc_dead_draw = self.world.get_component(entity_id, RemoteControlled)
            _is_remote_dead_draw = _rc_dead_draw is not None and _rc_dead_draw.hp <= 0
            _is_corpse_draw = ((_gst_draw is not None and _gst_draw.is_dead and not _gst_draw.is_ghost)
                               or _is_remote_dead_draw)
            _is_ghost_draw  = _gst_draw is not None and _gst_draw.is_ghost
            # Feedback visual de bush stealth (01/09/2026, pedido do usuário)
            # — calculado 1x aqui, reusado tanto no branch de opacidade
            # abaixo quanto no badge de HUD mais adiante (evita duplicar a
            # checagem de PlayerControlled). Só busca CombatState quando é
            # de fato o jogador local — barato (1 entidade por cliente).
            _is_local_player = self.world.get_component(entity_id, PlayerControlled) is not None
            _combat_state_draw = self.world.get_component(entity_id, CombatState) if _is_local_player else None
            # Altura real do sprite de personagem desenhado (None = nenhum,
            # caiu no retângulo) — setado no branch de desenho abaixo,
            # consumido pelo cálculo de topo do HUD (nameplate/HP) mais
            # adiante NA MESMA iteração. Precisa ser resetado aqui (não só
            # no branch que a seta) — senão uma entidade que cai no
            # retângulo herdaria por acidente a altura do sprite da
            # entidade anterior no loop (bug real pego revisando antes de
            # existir em produção). Guarda o `anchor_dy` (distância do
            # topo do FRAME até o centro do tile de colisão, ver
            # `character_anchor_offset`), não mais a altura crua do
            # sprite (10/09/2026 — a âncora não é mais "base do sprite",
            # é o tile de colisão de 32×32 dentro dele).
            _char_anchor_dy_for_hud = None
            # Resetado pelo MESMO motivo do campo acima (evita herdar o
            # frame da entidade anterior do loop) — consumido logo abaixo
            # pra decidir o retângulo de tela REAL do jogador local, usado
            # pela transparência de objeto "alto" (occludes_player, ver
            # branch de objeto mais acima neste método).
            _char_frame = None

            draw_x = position.x - camera_offset_x
            draw_y = position.y - camera_offset_y

            _sfx_rnd = self.world.get_component(entity_id, StatusEffects)

            # ── Override de aparência (Camuflagem/Polimorfia/futuro) ───────────
            # Resolução genérica — ver engine/entity_disguise.py
            # (get_active_appearance_override + tabela _APPEARANCE_SOURCES).
            # Nenhuma skill é hardcoded aqui; adicionar uma fonte nova de troca
            # de aparência não toca nesta função.
            _appearance = get_active_appearance_override(self.world, entity_id)
            if _appearance is not None:
                _sprite_base, _ap_variant = _appearance
                _ap_tm     = self.world.get_component(entity_id, TileMovement)
                _ap_moving = bool(_ap_tm and _ap_tm.is_moving)
                _ap_frame  = get_animated_disguise_frame(
                    _sprite_base, _ap_variant, _ap_moving, pygame.time.get_ticks())
                if _ap_frame:
                    _sw = _ap_frame.get_width()
                    _sh = _ap_frame.get_height()
                    # Alinha o fundo do sprite ao pé da entidade
                    _blit_x = int(draw_x - _sw / 2)
                    _blit_y = int(draw_y + 12 - _sh)  # +12 = offset do pé do jogador
                    self.world_surf.blit(_ap_frame, (_blit_x, _blit_y))
                elif _sprite_base == "polimorfia":
                    # Placeholder TEMPORÁRIO enquanto não existe
                    # polimorfia_idle.png/polimorfia_run.png de verdade — some
                    # sozinho assim que o asset existir (get_animated_disguise_frame
                    # passa a retornar o frame real em vez de None). Não é um
                    # branch por skill permanente, é cobertura de asset faltando.
                    _cx = int(draw_x)
                    _cy = int(draw_y)
                    pygame.draw.circle(self.world_surf, (160, 80, 200), (_cx, _cy), 14)
                    pygame.draw.circle(self.world_surf, (220, 180, 255), (_cx, _cy), 14, 2)
                rect = pygame.Rect(int(draw_x - 16), int(draw_y - 16), 32, 32)
            else:
                rect = pygame.Rect(
                    int(draw_x - renderable.width / 2),
                    int(draw_y - renderable.height / 2),
                    renderable.width,
                    renderable.height
                )
                # Sprite do catálogo de objeto de mapa (Fase M1, revisão 2,
                # 25/07/2026 — harvestable) tem prioridade sobre o retângulo
                # colorido de sempre. Nunca coincide com corpo/espírito
                # (harvestable não tem GhostState). Ancorado igual a
                # qualquer objeto de mapa estático (base do sprite = base
                # do tile) — mesma fórmula de TileRenderSystem.
                _sprite_rnd = (TILE_SPRITES.get_raw_sprite(renderable.sprite_id)
                              if renderable.sprite_id else None)
                if _sprite_rnd:
                    _sw_rnd, _sh_rnd = _sprite_rnd.get_size()
                    _blit_x_rnd = draw_x - TILE_SIZE / 2
                    _blit_y_rnd = draw_y + TILE_SIZE / 2 - _sh_rnd
                    self.world_surf.blit(_sprite_rnd, (int(_blit_x_rnd), int(_blit_y_rnd)))
                    # Traçado de seleção (mais abaixo) precisa envolver o
                    # sprite de VERDADE, não o retângulo antigo — sem isso
                    # o contorno amarelo ficava pequeno/deslocado num
                    # sprite grande (12/08/2026, bug real relatado pelo
                    # usuário, torre).
                    rect = pygame.Rect(int(_blit_x_rnd), int(_blit_y_rnd), _sw_rnd, _sh_rnd)
                elif _is_corpse_draw:
                    # Corpo morto: dessatura pra cinza (pose "morto", sem barra de HP)
                    _gray = sum(renderable.color[:3]) // 3
                    pygame.draw.rect(self.world_surf, (_gray, _gray, _gray), rect)
                elif _is_ghost_draw:
                    # Espírito: semi-transparente
                    self.world_surf.blit(fill_surf((rect.width, rect.height), (*renderable.color[:3], 127)), rect.topleft)
                else:
                    # Animação base de personagem (09/09/2026, pedido do
                    # usuário — implementação incremental, ver
                    # engine/character_animation.py). Só players (local
                    # via CharacterStats, remoto via RemoteControlled) —
                    # mob/NPC continuam 100% retângulo, sem class_id pra
                    # resolver sprite nenhum. Corpo/espírito (branches
                    # acima) continuam com o tratamento visual de
                    # retângulo que já tinham — este branch cobre "vivo",
                    # incluindo bush stealth (opacidade aplicada NA sprite
                    # animada, não só no retângulo — 19/09/2026, bug real
                    # relatado pelo usuário: bush stealth era um `elif`
                    # separado, escrito em 01/09 ANTES da animação existir
                    # (09/09) — nunca chamava get_character_frame, então
                    # parado/andando sobre bush sempre perdia a animação
                    # e caía no retângulo, mesmo com sprite disponível).
                    _bush_hidden_draw = (_is_local_player and _combat_state_draw is not None
                                        and _combat_state_draw.is_bush_hidden)
                    _char_id_rnd = self._resolve_character_class_id(entity_id)

                    _char_frame = None
                    if _char_id_rnd:
                        _tm_rnd = self.world.get_component(entity_id, TileMovement)
                        if _tm_rnd is not None:
                            _state_rnd, _anim_t_rnd, _frame_ms_rnd = resolve_display_state(
                                _tm_rnd, _char_id_rnd, pygame.time.get_ticks())
                            _char_frame = get_character_frame(
                                _char_id_rnd, _state_rnd, _tm_rnd.facing,
                                _anim_t_rnd, _frame_ms_rnd)

                    if _char_frame is not None:
                        if _bush_hidden_draw:
                            # get_character_frame devolve Surface CACHEADA
                            # (compartilhada por qualquer entidade/frame
                            # igual) — set_alpha muta o objeto direto, nunca
                            # sem copy() antes, ou a opacidade vaza pra
                            # próxima entidade/frame que reusar o cache.
                            _char_frame = _char_frame.copy()
                            _char_frame.set_alpha(127)
                        # Âncora pelo tile de colisão REAL (32×32) dentro
                        # do frame, não pelo centro/base do frame inteiro
                        # (10/09/2026, decisão do usuário — ver
                        # engine/character_animation.py::
                        # character_anchor_offset). Alinha o CENTRO desse
                        # tile com (draw_x, draw_y) — mesmo ponto que o
                        # ECS já usa como posição lógica da entidade.
                        _anchor_dx, _anchor_dy = character_anchor_offset()
                        _cblit_x = int(draw_x - _anchor_dx)
                        _cblit_y = int(draw_y - _anchor_dy)
                        self.world_surf.blit(_char_frame, (_cblit_x, _cblit_y))
                        _char_anchor_dy_for_hud = _anchor_dy
                    elif _bush_hidden_draw:
                        # Sem sprite animada disponível (classe sem asset
                        # ainda) — mesmo fallback translúcido de sempre.
                        self.world_surf.blit(fill_surf((rect.width, rect.height), (*renderable.color[:3], 127)), rect.topleft)
                    else:
                        pygame.draw.rect(self.world_surf, renderable.color, rect)

            # Retângulo de tela REAL do jogador local (20/09/2026, pedido do
            # usuário — transparência de objeto "alto" atrás do qual o
            # jogador anda). `rect` não reflete o frame animado de verdade
            # (fica com o tamanho genérico antigo, 24×24) — usa o frame só
            # quando ele foi de fato desenhado acima; senão `rect` já é
            # preciso (corpo/espírito/bush/disfarce/retângulo simples).
            # Só o PRÓPRIO jogador participa (decisão do usuário) — nunca
            # sobrescrito por outra entidade do loop, por isso o reset
            # (`_char_frame = None`) no topo desta iteração é obrigatório.
            if _is_local_player:
                if _char_frame is not None:
                    _local_player_screen_rect = pygame.Rect(
                        _cblit_x, _cblit_y, _char_frame.get_width(), _char_frame.get_height())
                else:
                    _local_player_screen_rect = rect.copy()

            # ── Efeitos de chão: desenhados NA FRENTE do retângulo da entidade ──
            # Verificação direta em StatusEffects (sem exigir CombatStats) para
            # funcionar com mobs remotos, player local e futuros players PvP.
            if _sfx_rnd is not None:
                # Root (Nova Congelante e similares): frozen_root.png na base
                if _sfx_rnd.has("root"):
                    _root_surf = _get_ground_effect_sprite("root")
                    if _root_surf is not None:
                        _rw = _root_surf.get_width()
                        _rh = _root_surf.get_height()
                        # Centralizado no mesmo tile da entidade (ambos 32×32)
                        _rx = int(draw_x - _rw / 2)
                        _ry = int(draw_y - _rh / 2)
                        self.world_surf.blit(_root_surf, (_rx, _ry))

            if entity_id == target_id:
                pygame.draw.rect(self.world_surf, (255, 220, 0), rect, 2)

            # ── HUD de barras: mobs/player LOCAIS (CombatStats) ──────────────
            # Player remoto (PvP) tem seu próprio passe completo em
            # client/remote_entity_handlers.py::_draw_remote_players (não
            # tem dado de XP/recurso local pra mostrar aqui, e desenhar 2x
            # seria bug) — por isso NÃO entra mais nesta condição.
            #
            # TUDO (fundo+barras+número do nível) sai como UMA ÚNICA
            # Surface via ui/hud_bars.py, enfileirada inteira em
            # WORLD_LABELS (screen-space) — nunca mais misturado (fundo em
            # espaço de mundo + número em espaço de tela causava um bug
            # real: "99" de 2 dígitos não cabia na caixinha nativa de
            # 12px e ficava flutuando fora dela, ver ARQUITETURA_ONLINE.md
            # 23.9). O nome empilha por cima com gap_before=2 (pedido do
            # usuário); efeitos ativos encostam na borda direita.
            _draw_hp_bar = (combat_stats and combat_stats.max_hp > 0
                           and not _is_corpse_draw and not _is_ghost_draw)
            if _draw_hp_bar:
                ratio = max(0.0, min(1.0, combat_stats.current_hp / combat_stats.max_hp))
                # _is_local_player já calculado acima (bush stealth) — reusado aqui.
                # Sprite real (torre, 12/08/2026, bug real relatado pelo
                # usuário) é bem mais alto que o renderable.height antigo
                # (retângulo/tier) — ancorar pela altura antiga deixava a
                # barra de HP no meio/base do sprite em vez do topo. Mesma
                # âncora de base que o RenderSystem já usa pra desenhar o
                # sprite (`position.y + TILE_SIZE/2 - sprite_height`, ver
                # branch `_sprite_rnd` do loop de desenho abaixo) — só sobe
                # até o TOPO do sprite em vez do rodapé.
                if renderable.sprite_id:
                    _spr_hud = TILE_SPRITES.get_raw_sprite(renderable.sprite_id)
                    _spr_h_hud = _spr_hud.get_height() if _spr_hud else renderable.height
                    _hud_top_world_y = position.y + TILE_SIZE / 2 - _spr_h_hud
                elif _char_anchor_dy_for_hud is not None:
                    # Animação de personagem (09-10/09/2026) — topo do
                    # sprite em coordenadas de MUNDO é `position.y -
                    # anchor_dy` (mesma âncora usada pra desenhar o
                    # sprite, ver `character_anchor_offset` — centro do
                    # tile de colisão de 32×32 alinhado com `position.y`,
                    # não a base/centro do frame inteiro de 96×H). Sem
                    # isso, o nameplate cobria parte do sprite (achado
                    # real: primeiro com a altura crua do frame, depois
                    # de novo quando a âncora do tile de colisão mudou).
                    _hud_top_world_y = position.y - _char_anchor_dy_for_hud
                else:
                    _hud_top_world_y = position.y - renderable.height / 2
                _hud_surf = None

                if _is_local_player:
                    _char_id = self.world.get_component(entity_id, CharacterStats)
                    if _char_id is not None:
                        if nameplate_mode == 0:
                            _xp_ratio = _char_id.current_xp / max(1, _char_id.xp_to_next_level)
                            if _char_id.class_id == "mago":
                                _res_ratio = _char_id.mana / max(1, _char_id.max_mana)
                            elif _char_id.class_id == "arqueiro":
                                _res_ratio = _char_id.concentration / max(1, _char_id.max_concentration)
                            else:
                                _res_ratio = _char_id.rage / max(1, _char_id.max_rage)
                            _res_color = RESOURCE_COLORS.get(_char_id.class_id, (150, 150, 150))
                            _hud_surf = build_player_hud(ratio, _xp_ratio, _res_ratio, _res_color,
                                                         _char_id.level, self._level_font)
                            WORLD_LABELS.add_icon(position.x, _hud_top_world_y, _hud_surf,
                                                  stack_key=entity_id, gap_before=HUD_GAP_PX)
                            WORLD_LABELS.add_text(
                                position.x, _hud_top_world_y,
                                _char_id.name, self._name_font, (255, 255, 200),
                                stack_key=entity_id, gap_before=2)
                        elif nameplate_mode == 2:
                            _hud_surf = build_simple_hp_bar(ratio)
                            WORLD_LABELS.add_icon(position.x, _hud_top_world_y, _hud_surf,
                                                  stack_key=entity_id, gap_before=HUD_GAP_PX)
                            WORLD_LABELS.add_text(
                                position.x, _hud_top_world_y,
                                _char_id.name, self._name_font, (255, 255, 200),
                                stack_key=entity_id, gap_before=2)
                        else:
                            WORLD_LABELS.add_text(
                                position.x, _hud_top_world_y,
                                _char_id.name, self._name_font, (255, 255, 200),
                                stack_key=entity_id)
                else:
                    # Mob local/offline — mob remoto usa
                    # client/remote_entity_handlers.py::_draw_mob_hp_bars.
                    _mob_id = self.world.get_component(entity_id, EntityIdentity)
                    if _mob_id is not None:
                        if nameplate_mode == 0:
                            _hud_surf = build_mob_hud(ratio, _mob_id.level, self._level_font)
                            WORLD_LABELS.add_icon(position.x, _hud_top_world_y, _hud_surf,
                                                  stack_key=entity_id, gap_before=HUD_GAP_PX)
                            WORLD_LABELS.add_text(
                                position.x, _hud_top_world_y,
                                _mob_id.name, self._name_font, (220, 200, 180),
                                stack_key=entity_id, gap_before=2)
                        elif nameplate_mode == 2:
                            _hud_surf = build_simple_hp_bar(ratio)
                            WORLD_LABELS.add_icon(position.x, _hud_top_world_y, _hud_surf,
                                                  stack_key=entity_id, gap_before=HUD_GAP_PX)
                            WORLD_LABELS.add_text(
                                position.x, _hud_top_world_y,
                                _mob_id.name, self._name_font, (220, 200, 180),
                                stack_key=entity_id, gap_before=2)
                        else:
                            WORLD_LABELS.add_text(
                                position.x, _hud_top_world_y,
                                _mob_id.name, self._name_font, (220, 200, 180),
                                stack_key=entity_id)

                # Ícones de efeito (stun/etc): só no modo completo — modos
                # 1/2 mostram só o que o usuário pediu explicitamente
                # ("a única coisa que permanecerá será o nome" / "somente
                # o nome e a barra de vida").
                if nameplate_mode == 0 and _hud_surf is not None:
                    _sfx = self.world.get_component(entity_id, StatusEffects)
                    _cst = self.world.get_component(entity_id, CombatState)

                    # Reúne efeitos ativos (só debuff — buff tem barra própria)
                    _active_effects = _filter_nameplate_effects(_sfx)
                    if _cst and _cst.is_stunned and _cst.stun_timer > 0:
                        if not (_sfx and _sfx.has("stun")):
                            _stun_timer_val = _cst.stun_timer
                            class _FakeEff:
                                effect_type = "stun"
                                duration    = _stun_timer_val
                            _active_effects.append(_FakeEff())

                    if _active_effects:
                        _row, _ = _build_effects_row(_active_effects, self._effect_dur_font)
                        if _row is not None:
                            _xo, _yo = effects_row_offset(_hud_surf)
                            WORLD_LABELS.add_icon_offset(
                                position.x, _hud_top_world_y, _row,
                                x_offset=_xo, y_offset=_yo, halign="left", valign="center")

            # ── Nameplate de NPC (mercador/treinador/quest giver/ferreiro...) ──
            # Badge de nível + nome — mesma linguagem visual do nameplate
            # de mob (build_mob_hud), só sem barra de HP (NPC não-combatente
            # não tem CombatStats). Pedido do usuário 15/07/2026: "NPCs
            # também quero que tenham nameplates igual aos mobs". Ícone de
            # quest (QuestDialogSystem.render_world, chamado depois deste
            # passe em game.py) empilha ACIMA via o mesmo stack_key=eid em
            # WORLD_LABELS — nunca desenhado direto no world_surf (mesmo
            # motivo do nome do mob acima).
            #
            # Exclui quem tem RemoteEntityMeta (21/07/2026 — Fase 1 de
            # combate genérico pra NPC de serviço): esses NPCs agora
            # sincronizam pelo mesmo pipeline de mob e já ganham
            # badge+nome+barra de HP via _draw_mob_hp_bars
            # (client/remote_entity_handlers.py) — sem este filtro, o
            # badge duplicava (um aqui sem HP, outro lá com HP), já que
            # perderam CombatStats (removido na reconstrução remota) mas
            # continuam com NPC.
            from engine.components import RemoteEntityMeta as _REM_npc_badge
            _npc_id = self.world.get_component(entity_id, NPC)
            _is_remote_synced = self.world.get_component(entity_id, _REM_npc_badge) is not None
            if _npc_id is not None and not _draw_hp_bar and not _is_remote_synced:
                _npc_top_world_y = position.y - renderable.height / 2
                # Modos 1 e 2 colapsam no mesmo resultado pra NPC: sem
                # CombatStats não há barra de HP pra desenhar no modo 2
                # (a barra do modo 2 é justamente a de vida) — só nome.
                if nameplate_mode == 0:
                    _npc_badge = build_npc_badge(_npc_id.level, self._level_font)
                    WORLD_LABELS.add_icon(position.x, _npc_top_world_y, _npc_badge,
                                          stack_key=entity_id, gap_before=HUD_GAP_PX)
                    WORLD_LABELS.add_text(
                        position.x, _npc_top_world_y,
                        _npc_id.name, self._name_font, (220, 220, 180),
                        stack_key=entity_id, gap_before=2)
                else:
                    WORLD_LABELS.add_text(
                        position.x, _npc_top_world_y,
                        _npc_id.name, self._name_font, (220, 220, 180),
                        stack_key=entity_id)


class CameraSystem(System):
    def __init__(self, world: World):
        self.world = world

    LERP_SPEED = 8.0  # maior = mais rápido; ~8 é suave mas responsivo

    # Decaimento exponencial (lerp) matematicamente NUNCA chega no alvo —
    # só se aproxima cada vez menos a cada frame, pra sempre. Enquanto o
    # personagem se move, isso não se nota (o alvo também está andando, a
    # câmera persegue com uma defasagem ~constante). Mas quando o
    # personagem PARA, a câmera continua fazendo ajustes MINÚSCULOS —
    # decrescentes, nunca exatamente zero — frame após frame; combinado
    # com o transform.scale() do zoom (1.5x-2.5x) amostrando essa posição
    # ligeiramente diferente a cada frame, esse resíduo aparece como
    # "tremida" bem no instante de parar (bug real relatado pelo usuário
    # 19/07/2026 — ver ARQUITETURA_ONLINE.md; SNAP_EPSILON é o fix padrão
    # de indústria pra câmera com lerp: ao chegar perto o bastante, trava
    # EXATO no alvo em vez de continuar se aproximando pra sempre).
    SNAP_EPSILON = 0.05  # px — abaixo disso o resto nunca seria visível de qualquer forma

    # Desligado — decisão final, não mais diagnóstico (19/07/2026).
    # SNAP_EPSILON sozinho não eliminou a "tremida" ao parar; desligar a
    # suavização inteira (câmera gruda direto na posição do alvo todo
    # frame) confirmou que a causa era mesmo o lerp e resolveu de vez —
    # usuário validou em jogo (sem suavização, sem tremida nenhuma) e
    # preferiu manter assim (já era a preferência original dele, antes
    # de tentarmos consertar o lerp). Código do lerp/SNAP_EPSILON fica
    # como referência/caso algo precise dele no futuro, mas não é mais
    # usado — ver ARQUITETURA_ONLINE.md.
    SMOOTHING_ENABLED = False

    def update(self, events: list = None, dt: float = 0) -> None:
        _cam_upd_first = self.world.get_first(Camera, Position)
        if _cam_upd_first is not None:
            entity_id, camera_component, camera_position = _cam_upd_first
            target_entity_id = camera_component.target_entity_id

            if target_entity_id != -1:
                target_position = self.world.get_component(target_entity_id, Position)
                if target_position:
                    if not self.SMOOTHING_ENABLED:
                        camera_position.x = target_position.x
                        camera_position.y = target_position.y
                    else:
                        dx = target_position.x - camera_position.x
                        dy = target_position.y - camera_position.y
                        if abs(dx) < self.SNAP_EPSILON and abs(dy) < self.SNAP_EPSILON:
                            camera_position.x = target_position.x
                            camera_position.y = target_position.y
                        else:
                            t = min(1.0, self.LERP_SPEED * dt)
                            camera_position.x += dx * t
                            camera_position.y += dy * t

