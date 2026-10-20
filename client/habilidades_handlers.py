"""
habilidades_handlers.py — Mixin com o painel de Habilidades (tecla H):
lista scrollável de skills aprendidas com drag-and-drop para a hotbar,
o ghost de drag exibido sobre os slots, e a tela de loading exibida
enquanto aguarda LOGIN_OK do servidor. Separado de game.py para manter
GameEngine conciso. Esta classe NÃO deve ser instanciada diretamente —
ela é herdada por GameEngine, que fornece self.world, self.player_entity,
self.screen, self.font_*, self._net e os demais atributos referenciados
aqui.
"""
import pygame
from ui.ui_helpers import fill_surf

from engine.components import PlayerSkills
from ui.icon_manager import ICONS
from ui.ui_sizes import UI

# Fundo da tela de loading (19/10/2026, pedido do usuário) — mesmo padrão
# de cache-por-tamanho-de-janela já usado em ui/login_screen.py (carrega
# 1x via ui/asset_cache.py, escala só quando o tamanho da janela muda,
# nunca todo frame). Sem o arquivo → cai pro fundo sólido de sempre.
#
# Variantes aleatórias (19/10/2026, pedido do usuário) — mesmo padrão de
# descoberta SEQUENCIAL já usado em `engine/entity_disguise.py::
# discover_sprite_variants` (probe `os.path.exists` um a um, para no
# primeiro buraco, nunca glob — ordem de glob não é garantida e poderia
# pegar arquivo não relacionado por engano). `loading_screen.png` (sem
# sufixo, já em uso desde a feature anterior) sempre entra no sorteio
# também, nunca é substituído pelo "_1" — são conceitos separados, pra
# quem só tinha o arquivo único de antes continuar funcionando sem
# precisar renomear nada. A escolha é feita 1 VEZ por processo (sorteia
# na 1ª vez que a tela de loading precisa desenhar, nunca recalcula depois
# — trocar de imagem no meio da mesma sessão seria mais estranho que
# "piscar" que repetitivo).
_LOADING_BG_BASE = "assets/ui/loading_screen"
_UNSET = object()  # sentinel — distingue "ainda não sorteou" de "sorteou None" (nenhum arquivo achado)
_loading_bg_chosen_path = _UNSET
_loading_bg_scaled_cache: "dict[tuple[int, int], pygame.Surface]" = {}


def _discover_loading_bg_variants() -> "list[str]":
    import os
    candidates = []
    base_path = f"{_LOADING_BG_BASE}.png"
    if os.path.exists(base_path):
        candidates.append(base_path)
    n = 1
    while True:
        p = f"{_LOADING_BG_BASE}_{n}.png"
        if not os.path.exists(p):
            break
        candidates.append(p)
        n += 1
    return candidates


def _choose_loading_bg_path() -> "str | None":
    global _loading_bg_chosen_path
    if _loading_bg_chosen_path is not _UNSET:
        return _loading_bg_chosen_path
    import random
    candidates = _discover_loading_bg_variants()
    _loading_bg_chosen_path = random.choice(candidates) if candidates else None
    return _loading_bg_chosen_path


def _get_loading_background(size: "tuple[int, int]") -> "pygame.Surface | None":
    if size in _loading_bg_scaled_cache:
        return _loading_bg_scaled_cache[size]
    path = _choose_loading_bg_path()
    if path is None:
        _loading_bg_scaled_cache[size] = None
        return None
    from ui.asset_cache import get_image
    raw = get_image(path)
    scaled = pygame.transform.scale(raw, size) if raw is not None else None
    _loading_bg_scaled_cache[size] = scaled
    return scaled


# ── Barra de progresso com sprite (19/10/2026, pedido do usuário) ──────────
# `assets/ui/loading_screen_bar.png` — moldura/trilho prontos no PNG;
# o marcador de cor #291000 (41,16,0) dentro da imagem marca tanto a
# POSIÇÃO/TAMANHO do retângulo de preenchimento (margem simétrica dos 2
# lados, mesma altura do marcador) quanto a COR do preenchimento (pedido
# explícito do usuário — a cor do marcador É a cor de quem "anda" na
# barra, não só uma guia de posição). Escala reaproveitada de `ui.
# hud_bars.SCALE` (nearest-neighbor fixo, 2x hoje) — "mesma escala que os
# nameplates têm no jogo", NUNCA um número novo/duplicado. Ver
# PROBLEMAS_ARQUITETURA.md (HISTORICO_FEATURES.md).
_LOADING_BAR_IMG_PATH = "assets/ui/loading_screen_bar.png"
_LOADING_BAR_MARKER_COLOR = (0x29, 0x10, 0x00)
_loading_bar_cache: "dict | None" = None  # {"img": Surface, "fill_rect": Rect} ou None (sem arquivo)


def _detect_bar_fill_rect(raw: "pygame.Surface") -> "pygame.Rect":
    """Acha o retângulo de preenchimento a partir do marcador de cor
    (#291000) — margem horizontal simétrica (distância do marcador até a
    borda esquerda = distância até a borda direita) e altura/topo EXATOS
    do marcador (não um cálculo de margem, a altura é intencional e não
    deriva de nada simétrico). Fallback (marcador não encontrado — PNG
    editado sem o marcador) → retângulo com 3px de margem em todos os
    lados do próprio PNG, pra nunca quebrar mesmo sem o marcador."""
    w, h = raw.get_size()
    xs, ys = [], []
    for x in range(w):
        for y in range(h):
            if raw.get_at((x, y))[:3] == _LOADING_BAR_MARKER_COLOR:
                xs.append(x)
                ys.append(y)
    if not xs:
        return pygame.Rect(3, 3, max(1, w - 6), max(1, h - 6))
    x0, y0 = min(xs), min(ys)
    y1 = max(ys)
    fill_w = w - 2 * x0  # margem simétrica: mesma distância da borda direita
    return pygame.Rect(x0, y0, max(1, fill_w), y1 - y0 + 1)


def _get_loading_bar() -> "dict | None":
    global _loading_bar_cache
    if _loading_bar_cache is not None:
        return _loading_bar_cache if _loading_bar_cache else None
    from ui.asset_cache import get_image
    from ui.hud_bars import SCALE as _HUD_SCALE
    raw = get_image(_LOADING_BAR_IMG_PATH)
    if raw is None:
        _loading_bar_cache = {}
        return None
    fill_native = _detect_bar_fill_rect(raw)
    w, h = raw.get_size()
    img_scaled = pygame.transform.scale(raw, (w * _HUD_SCALE, h * _HUD_SCALE))
    fill_scaled = pygame.Rect(fill_native.x * _HUD_SCALE, fill_native.y * _HUD_SCALE,
                              fill_native.w * _HUD_SCALE, fill_native.h * _HUD_SCALE)
    _loading_bar_cache = {"img": img_scaled, "fill_rect": fill_scaled}
    return _loading_bar_cache


class HabilidadesHandlers:

    # ── Painel de Habilidades (H) ─────────────────────────────────────────────

    def _draw_habilidades_panel(self, events: list) -> None:
        """Modal central de habilidades — lista scrollável com descrição completa + drag para hotbar."""
        from content.skill_config import SKILL_CATALOG, NUM_SLOTS
        from engine.components import TalentTree as _TT

        ps = self.world.get_component(self.player_entity, PlayerSkills)
        if not ps:
            return

        mx, my   = pygame.mouse.get_pos()
        clicked  = any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 for e in events)
        released = any(e.type == pygame.MOUSEBUTTONUP   and e.button == 1 for e in events)

        _drag = self._get_drag()
        _dragging_here = (_drag.kind == "skill" and _drag.source == "habilidades"
                          and _drag.payload is not None)

        for e in events:
            if e.type == pygame.MOUSEWHEEL:
                self._hab_scroll = max(0, self._hab_scroll - e.y)

        # Coleta skills aprendidas
        avail: list[str] = [sid for sid in SKILL_CATALOG if sid in ps.learned_skill_ids]
        for s in ps.skills:
            if s and getattr(s, "talent_id", None) and s.skill_id not in avail:
                avail.append(s.skill_id)
        _tt = self.world.get_component(self.player_entity, _TT)
        if _tt:
            from content.talent_data import TALENTS as _TAL
            for tid, pts in _tt.allocated.items():
                t = _TAL.get(tid)
                if not t or not t.get("unlocks_skill"):
                    continue
                if pts >= t.get("unlock_at", t["max_points"]) and t["unlocks_skill"] not in avail:
                    avail.append(t["unlocks_skill"])

        # ── Geometria — modal centralizado ────────────────────────────────
        ppx, ppy = self._safe_panel_origin(UI.HABILIDADES_W, UI.HABILIDADES_H)
        ppx, ppy = ppx + UI.HABILIDADES_OFFSET_X, ppy + UI.HABILIDADES_OFFSET_Y
        PW      = self._u(UI.HABILIDADES_W)
        PH      = self._u(UI.HABILIDADES_H)
        ICON_SZ = self._u(48)
        ROW_H   = self._u(80)          # altura de cada linha (ícone + nome + descrição completa)
        LIST_X  = ppx + self._u(12)
        LIST_W  = PW - self._u(24)
        CONTENT_Y = ppy + self._u(44)  # abaixo do header
        FOOTER_H  = self._u(28)

        max_vis    = max(1, (PH - (CONTENT_Y - ppy) - FOOTER_H - self._u(10)) // ROW_H)
        max_scroll = max(0, len(avail) - max_vis)
        self._hab_scroll = min(self._hab_scroll, max_scroll)

        if self._show_habilidades:
            # Overlay escurecido
            self.screen.blit(fill_surf((self.screen.get_width(), self.screen.get_height()), (0, 0, 0, 160)), (0, 0))

            # Fundo do modal
            pygame.draw.rect(self.screen, (26, 20, 10), (ppx, ppy, PW, PH), border_radius=8)
            pygame.draw.rect(self.screen, (90, 72, 44), (ppx, ppy, PW, PH), 2, border_radius=8)

            # Título
            ts = self.font_md.render("Habilidades", False, (225, 195, 110))
            self.screen.blit(ts, (ppx + PW // 2 - ts.get_width() // 2, ppy + self._u(10)))

            # Botão [X]
            cr = pygame.Rect(ppx + PW - self._u(30), ppy + self._u(8), self._u(24), self._u(24))
            pygame.draw.rect(self.screen, (80, 40, 30) if cr.collidepoint(mx, my) else (50, 30, 20),
                             cr, border_radius=4)
            pygame.draw.rect(self.screen, (180, 80, 60), cr, 1, border_radius=4)
            xs = self.font_md.render("X", False, (220, 120, 100))
            self.screen.blit(xs, xs.get_rect(center=cr.center))
            if clicked and cr.collidepoint(mx, my):
                self._show_habilidades = False
                return

            pygame.draw.line(self.screen, (70, 56, 32),
                             (ppx + self._u(12), ppy + self._u(38)), (ppx + PW - self._u(12), ppy + self._u(38)))

            # ── Lista de skills ──────────────────────────────────────────
            vis = avail[self._hab_scroll: self._hab_scroll + max_vis]
            for idx, sid in enumerate(vis):
                ry    = CONTENT_Y + idx * ROW_H
                r     = pygame.Rect(LIST_X, ry, LIST_W, ROW_H - 4)
                is_src = (_dragging_here and _drag.payload == sid)
                hov    = r.collidepoint(mx, my) and not _dragging_here
                alt    = idx % 2 == 1

                bg = (16, 12, 6) if is_src else ((46, 38, 22) if hov else (32, 26, 14) if alt else (26, 20, 10))
                pygame.draw.rect(self.screen, bg, r, border_radius=5)
                pygame.draw.rect(self.screen, (120, 98, 52) if hov else (62, 50, 30),
                                 r, 1, border_radius=5)

                # Ícone
                ic_key = ICONS.skill_key_by_name(f"skill_{sid}") or ICONS.skill_key(idx)
                ic     = ICONS.get(ic_key, ICON_SZ)
                if ic:
                    self.screen.blit(ic, (LIST_X + self._u(8), ry + (ROW_H - self._u(4) - ICON_SZ) // 2))

                entry    = SKILL_CATALOG.get(sid)
                name_txt = entry["name"] if isinstance(entry, dict) else sid
                desc_txt = entry.get("desc", "")   if isinstance(entry, dict) else ""
                cd       = entry.get("cooldown", 0) if isinstance(entry, dict) else 0
                cast_t   = entry.get("cast_time", 0) if isinstance(entry, dict) else 0

                tx = LIST_X + ICON_SZ + self._u(20)

                # Nome
                self.screen.blit(self.font_sm.render(name_txt, False, (230, 210, 148)),
                                 (tx, ry + self._u(6)))

                # Descrição completa — quebrada em duas linhas se necessário
                max_chars = (LIST_W - ICON_SZ - self._u(28)) // 7  # aprox chars por linha a font_xs
                if len(desc_txt) > max_chars:
                    cut = desc_txt.rfind(" ", 0, max_chars) or max_chars
                    line1, line2 = desc_txt[:cut], desc_txt[cut:].strip()
                else:
                    line1, line2 = desc_txt, ""
                self.screen.blit(self.font_xs.render(line1, False, (155, 140, 95)),
                                 (tx, ry + self._u(28)))
                if line2:
                    self.screen.blit(self.font_xs.render(line2, False, (155, 140, 95)),
                                     (tx, ry + self._u(44)))

                # Metadados (CD / cast) — canto direito da linha
                meta_parts = []
                if cd:
                    meta_parts.append(f"CD {cd:.0f}s")
                if cast_t:
                    meta_parts.append(f"Cast {cast_t:.1f}s")
                if meta_parts:
                    meta_s = self.font_xs.render("  ·  ".join(meta_parts), False, (110, 100, 65))
                    self.screen.blit(meta_s, (LIST_X + LIST_W - meta_s.get_width() - self._u(10),
                                              ry + ROW_H - meta_s.get_height() - self._u(8)))

                if clicked and r.collidepoint(mx, my):
                    _drag.kind    = "skill"
                    _drag.source  = "habilidades"
                    _drag.payload = sid
                    _drag.active  = True

            # Barra de scroll
            if len(avail) > max_vis:
                list_h = max_vis * ROW_H
                pct    = self._hab_scroll / max(1, max_scroll)
                bar_h  = max(self._u(20), list_h * max_vis // max(1, len(avail)))
                bar_y  = CONTENT_Y + int((list_h - bar_h) * pct)
                bar_x  = ppx + PW - self._u(8)
                pygame.draw.rect(self.screen, (44, 35, 18), (bar_x, CONTENT_Y, self._u(5), list_h), border_radius=2)
                pygame.draw.rect(self.screen, (125, 100, 55), (bar_x, bar_y, self._u(5), bar_h), border_radius=2)

            # Dica de rodapé
            hint = self.font_xs.render(
                "Clique e arraste uma habilidade para um slot da hotbar  |  H ou [X] para fechar", False, (90, 82, 55))
            self.screen.blit(hint, hint.get_rect(centerx=ppx + PW // 2, y=ppy + PH - self._u(20)))

        # Ghost de drag renderizado separadamente (veja _draw_hab_drag_ghost),
        # APÓS o redesenho da hotbar, para ficar sempre na frente dos slots.

        # ── Drop sobre a hotbar ────────────────────────────────────────────
        if (released and _drag.kind == "skill" and _drag.source == "habilidades"
                and _drag.payload):
            sid = _drag.payload
            _drag.reset()
            # Calcula posições de TODOS os 10 slots (incluindo vazios) para detecção
            # Precisa do clamp da LINHA da hotbar (não do painel de habilidades,
            # que setou outra escala acima) pra bater com o que _draw_hotbar desenhou.
            self._set_hotbar_row_scale()
            total_w = NUM_SLOTS * self._HB_W + (NUM_SLOTS - 1) * self._HB_PAD
            x0      = self.screen.get_width() // 2 - total_w // 2
            y0      = self.screen.get_height() - self._HB_H - self._u(10)
            for i in range(NUM_SLOTS):
                sx = x0 + i * (self._HB_W + self._HB_PAD)
                if pygame.Rect(sx, y0, self._HB_W, self._HB_H).collidepoint(mx, my):
                    # Verifica se a skill já está em outro slot (swap)
                    ex = next((k for k in range(NUM_SLOTS)
                               if ps.skills[k] and ps.skills[k].skill_id == sid), None)
                    if ex is not None and ex != i:
                        ps.skills[ex], ps.skills[i] = ps.skills[i], ps.skills[ex]
                    elif ex is None:
                        new_s = type(ps)._make_skill(sid, SKILL_CATALOG)
                        if new_s is None:
                            new_s = next((s for s in ps.skills if s and s.skill_id == sid), None)
                        if new_s:
                            ps.skills[i] = new_s
                    self._save_config()
                    break

    def _draw_loading_screen(self, dt: float) -> None:
        """Tela de loading exibida enquanto aguarda LOGIN_OK do servidor."""
        self._loading_anim_t += dt
        dots = "." * (int(self._loading_anim_t * 2) % 4)

        sw, sh = self.screen.get_width(), self.screen.get_height()
        _bg_img = _get_loading_background((sw, sh))
        if _bg_img is not None:
            self.screen.blit(_bg_img, (0, 0))
        else:
            self.screen.fill((8, 6, 4))

        # Gradiente sutil no centro
        _grad = pygame.Surface((sw, sh), pygame.SRCALPHA)
        _grad.fill((0, 0, 0, 0))
        for _r in range(min(sw, sh) // 2, 0, -20):
            _alpha = max(0, 40 - _r // 8)
            pygame.draw.circle(_grad, (30, 20, 10, _alpha), (sw // 2, sh // 2), _r)
        self.screen.blit(_grad, (0, 0))

        # Status — 2 fases (28/09/2026): "assets" (preload_all rodando,
        # ver __init__) tem prioridade de exibição sobre a fase de rede,
        # mesmo que a conexão já tenha sido aberta em paralelo.
        _phase = getattr(self, "_loading_phase", "server")
        if _phase == "assets":
            msg = f"Carregando recursos{dots}"
        elif self._net and self._net.connected:
            msg = f"Carregando personagem{dots}"
        elif self._net and getattr(self._net, "reconnecting", False):
            msg = f"Reconectando{dots}"
        else:
            msg = f"Conectando ao servidor{dots}"

        # Barra de progresso — sprite (19/10/2026, pedido do usuário):
        # moldura/trilho vêm do PNG, SCALE igual nameplate (ver
        # _get_loading_bar); centralizada no eixo horizontal, encostada
        # 20px acima do limite inferior da tela no eixo vertical. "Sem
        # arte ainda" cai pro desenho antigo (retângulos de cor), mesmo
        # padrão de fallback do resto do projeto.
        #
        # Progresso ÚNICO pras 2 fases (19/10/2026, pedido do usuário:
        # "não dá pra fazer o carregamento dos recursos e do personagem
        # na mesma barra?") — em vez de cada fase reiniciar um ciclo
        # 0%→100% próprio (a barra "resetava" visualmente na troca de
        # fase), "assets" ocupa a fração [0, _ASSETS_PHASE_WEIGHT] e
        # "server" ocupa o resto [_ASSETS_PHASE_WEIGHT, 1.0] — uma única
        # progressão contínua. 0.5 (metade pra cada fase) é um ponto de
        # partida razoável sem dado real de duração de cada fase — fácil
        # de recalibrar depois, é só esta 1 constante.
        _ASSETS_PHASE_WEIGHT = 0.5
        if _phase == "assets":
            _assets_pct = (self._preload_done / self._preload_total) if self._preload_total > 0 else 0.0
            _pct = max(0.0, min(1.0, _assets_pct)) * _ASSETS_PHASE_WEIGHT
        else:
            _server_pct = max(0.0, min(1.0, 1.0 - self._loading_min_t / 1.5))
            _pct = _ASSETS_PHASE_WEIGHT + _server_pct * (1.0 - _ASSETS_PHASE_WEIGHT)

        # Texto com CONTORNO (19/10/2026, pedido do usuário — "a letra é
        # clara assim como o fundo", ilegível em cima da imagem nova) —
        # reusa `ui/floating_text.py::_render_outlined`, o MESMO ponto
        # único de verdade de texto com contorno já usado no FLT de
        # dano, nunca uma 2ª implementação de contorno de texto.
        from ui.floating_text import _render_outlined as _outl

        _bar = _get_loading_bar()
        if _bar is not None:
            _bar_img  = _bar["img"]
            _fill_r   = _bar["fill_rect"]
            _bar_w, _bar_h = _bar_img.get_size()
            _bx = sw // 2 - _bar_w // 2
            _by = sh - 20 - _bar_h
            self.screen.blit(_bar_img, (_bx, _by))
            _fill_w = max(0, int(_fill_r.w * _pct))
            if _fill_w > 0:
                pygame.draw.rect(self.screen, _LOADING_BAR_MARKER_COLOR,
                                 (_bx + _fill_r.x, _by + _fill_r.y, _fill_w, _fill_r.h))
            _status = _outl(self.font_sm, msg, (150, 130, 70), thickness=2)
            self.screen.blit(_status, _status.get_rect(centerx=sw // 2, bottom=_by - 5))
            if self._loading_timeout < 5.0:
                _hint = _outl(self.font_xs,
                    f"Sem resposta do servidor — continuando em {self._loading_timeout:.0f}s...",
                    (120, 80, 60), thickness=2)
                self.screen.blit(_hint, _hint.get_rect(centerx=sw // 2, top=_by + _bar_h + 6))
        else:
            # Fallback sem o PNG — layout antigo (retângulos de cor),
            # centralizado na tela.
            _status = _outl(self.font_sm, msg, (150, 130, 70), thickness=2)
            self.screen.blit(_status, _status.get_rect(centerx=sw // 2, centery=sh // 2 + 10))
            _BAR_W, _BAR_H = 400, 10
            _bx = sw // 2 - _BAR_W // 2
            _by = sh // 2 + 32
            _fill = max(1, int(_BAR_W * _pct))
            pygame.draw.rect(self.screen, (70, 15, 15),  (_bx, _by, _BAR_W, _BAR_H), border_radius=3)
            pygame.draw.rect(self.screen, (25, 90, 25),  (_bx, _by, _fill,  _BAR_H), border_radius=3)
            pygame.draw.rect(self.screen, (50, 35, 20),  (_bx, _by, _BAR_W, _BAR_H), 1, border_radius=3)
            if self._loading_timeout < 5.0:
                _hint = _outl(self.font_xs,
                    f"Sem resposta do servidor — continuando em {self._loading_timeout:.0f}s...",
                    (120, 80, 60), thickness=2)
                self.screen.blit(_hint, _hint.get_rect(centerx=sw // 2, centery=sh // 2 + 55))

    def _draw_map_transition_screen(self, status: str) -> None:
        """Tela de transição (28/09/2026, pedido do usuário — ver
        PROBLEMAS_ARQUITETURA.md §215) — mostrada por 1 frame estático
        antes de `_do_transition()` rodar de verdade (troca de mapa OU
        entrada/saída de instância — BG, arena). Sem isso, o jogador
        via a tela CONGELADA na última cena do mapa antigo por até ~2s
        (bug real medido: carregamento de mapa/tilemap novo é síncrono,
        sem yield pro loop de render) — pesquisa de mercado confirmou
        que até MMOs AAA (WoW) mostram tela dedicada pra teletransporte/
        instância, nunca deixam a tela "congelada" sem feedback.

        Mesmo estilo visual de `_draw_loading_screen`, mas SEM barra de
        progresso (a troca de mapa não é um loop com contagem própria,
        diferente do preload de assets) — só título + status."""
        sw, sh = self.screen.get_width(), self.screen.get_height()
        self.screen.fill((8, 6, 4))
        _grad = pygame.Surface((sw, sh), pygame.SRCALPHA)
        _grad.fill((0, 0, 0, 0))
        for _r in range(min(sw, sh) // 2, 0, -20):
            _alpha = max(0, 40 - _r // 8)
            pygame.draw.circle(_grad, (30, 20, 10, _alpha), (sw // 2, sh // 2), _r)
        self.screen.blit(_grad, (0, 0))
        _title = self.font_lg.render("RPG Online", False, (220, 185, 80))
        self.screen.blit(_title, _title.get_rect(centerx=sw // 2, centery=sh // 2 - 60))
        pygame.draw.line(self.screen, (80, 62, 30),
                         (sw // 2 - 120, sh // 2 - 32),
                         (sw // 2 + 120, sh // 2 - 32), 1)
        _status = self.font_sm.render(status, False, (150, 130, 70))
        self.screen.blit(_status, _status.get_rect(centerx=sw // 2, centery=sh // 2 + 10))

    def _draw_hab_drag_ghost(self) -> None:
        """Renderiza o ícone fantasma que segue o mouse durante o drag do painel H.
        Chamado APÓS _draw_hotbar() para ficar sempre na frente dos slots."""
        _drag = self._get_drag()
        if not (_drag.kind == "skill" and _drag.source == "habilidades" and _drag.payload):
            return
        GSZ   = 48
        mx, my = pygame.mouse.get_pos()
        ghost = pygame.Surface((GSZ, GSZ), pygame.SRCALPHA)
        ghost.fill((30, 24, 12, 180))
        ic_k = ICONS.skill_key_by_name(f"skill_{_drag.payload}") or ICONS.skill_key(0)
        ic   = ICONS.get(ic_k, GSZ - 4)
        if ic:
            ghost.blit(ic, (2, 2))
        self.screen.blit(ghost, ghost.get_rect(center=(mx, my)))
