# floating_text.py
"""Números de dano flutuantes exibidos acima das entidades."""
import pygame
from ui.fonts import make as _font


class FloatingTextEntry:
    __slots__ = ("text", "wx", "wy", "color", "font_size",
                 "timer", "duration", "speed_y", "offset_y", "target_id",
                 "is_crit", "surf")

    def __init__(self, text: str, wx: float, wy: float,
                 color: tuple, font_size: int, duration: float,
                 speed_y: float, target_id: int, is_crit: bool = False):
        self.text      = text
        self.wx        = wx
        self.wy        = wy
        self.color     = color
        self.font_size = font_size
        self.timer     = duration
        self.duration  = duration
        self.speed_y   = speed_y    # pixels/s para cima após empilhar
        self.offset_y  = 0.0       # pixels subidos (cresce com speed_y * dt)
        self.target_id = target_id  # entidade dona — usado para empilhar
        self.is_crit   = is_crit    # animação de escala crescente + fade breve
        # Cópia própria da surface renderizada (lazy, no primeiro render).
        # font.render() é cacheado/compartilhado (fonts.CachedFont) — o fade
        # via set_alpha exige uma cópia mutável por entrada.
        self.surf: "pygame.Surface | None" = None


class FloatingTextManager:
    """Gerencia textos flutuantes de combate com empilhamento por alvo.

    Quando um novo texto chega para um alvo que já tem textos ativos:
      - Os textos existentes sobem imediatamente SLOT_HEIGHT pixels.
      - O novo texto aparece na posição base (pés do alvo) e sobe dali —
        mesma animação de sempre (deriva pra cima + empilhamento), só o
        ponto de partida mudou. Antes nascia ACIMA da cabeça (BASE_Y_OFFSET
        positivo), colidindo com a HUD de nível/barras nova (pedido do
        usuário 11/07/2026: nascer da base, "na frente do personagem",
        subindo através do sprite — mesmo efeito, sem brigar com a HUD).
    """

    SLOT_HEIGHT = 20   # px de separação entre textos empilhados
    BASE_Y_OFFSET = -14  # px abaixo do centro da entidade onde o 1º texto
                          # aparece (negativo = pra baixo, ver fórmula em add())

    _PRESETS = {
        #         (font_size, duration, speed_y_idle)
        "small":  (22, 1.0, 5),
        "normal": (26, 1.2, 5),
        "large":  (35, 1.5, 5),
        "crit":   (38, 1.35, 0),   # crítico: grow 0.25s + hold 1s + fade 0.1s
    }

    def __init__(self):
        self._entries: list[FloatingTextEntry] = []
        self._font_cache: dict[int, pygame.font.Font] = {}

    def _get_font(self, size: int) -> pygame.font.Font:
        if size not in self._font_cache:
            self._font_cache[size] = _font(size)
        return self._font_cache[size]

    def add(self, text: str, wx: float, wy: float,
            color: tuple, size: str = "normal", target_id: int = -1,
            is_crit: bool = False) -> None:
        if is_crit:
            size = "crit"
        font_size, duration, speed_y = self._PRESETS.get(size, self._PRESETS["normal"])

        # Empilha textos do mesmo alvo para cima
        for e in self._entries:
            if e.target_id == target_id and target_id != -1:
                e.offset_y += self.SLOT_HEIGHT

        self._entries.append(FloatingTextEntry(
            text, wx, wy - self.BASE_Y_OFFSET,
            color, font_size, duration, speed_y, target_id, is_crit,
        ))

    def update(self, dt: float) -> None:
        alive = []
        for e in self._entries:
            e.timer    -= dt
            e.offset_y += e.speed_y * dt   # deriva lentamente para cima
            if e.timer > 0:
                alive.append(e)
        self._entries = alive

    def render(self, screen: pygame.Surface,
               camera_offset_x: float, camera_offset_y: float) -> None:
        cam_x = int(camera_offset_x)
        cam_y = int(camera_offset_y)
        for e in self._entries:
            if e.surf is None:
                font = self._get_font(e.font_size)
                # .copy(): surface do CachedFont é compartilhada; esta entrada
                # muta alpha por frame, então precisa de cópia própria.
                e.surf = font.render(e.text, False, e.color).copy()
            base_surf = e.surf

            if e.is_crit:
                # Fases: grow rápido (0.25s) → hold (1.0s) → fade breve (0.1s)
                _GROW = 0.25
                _HOLD = 1.0
                _FADE = 0.1
                elapsed = e.duration - e.timer
                if elapsed < _GROW:
                    scale = 1.0 + (0.50 * elapsed / _GROW)  # 1.0 → 1.75 (metade de 2.5)
                    alpha = 255
                elif elapsed < _GROW + _HOLD:
                    scale = 1.50
                    alpha = 255
                else:
                    fade_t = elapsed - _GROW - _HOLD
                    scale  = 1.50
                    alpha  = max(0, int(255 * (1.0 - fade_t / _FADE)))
                new_w = max(1, int(base_surf.get_width()  * scale))
                new_h = max(1, int(base_surf.get_height() * scale))
                scaled = pygame.transform.scale(base_surf, (new_w, new_h))
                scaled.set_alpha(alpha)
                sx = int(e.wx - cam_x) - new_w // 2
                sy = int(e.wy - cam_y - e.offset_y) - new_h  # âncora bottom-center + empilhamento
                screen.blit(scaled, (sx, sy))
            else:
                # Fade nos últimos 35% do tempo de vida + deriva para cima
                fade_start = e.duration * 0.35
                alpha = 255 if e.timer >= fade_start else max(0, int(255 * e.timer / fade_start))
                base_surf.set_alpha(alpha)
                sx = int(e.wx - cam_x) - base_surf.get_width()  // 2
                sy = int(e.wy - cam_y - e.offset_y) - base_surf.get_height() // 2
                screen.blit(base_surf, (sx, sy))


FLT = FloatingTextManager()


# ---------------------------------------------------------------------------
# Dash trail — rastro vermelho do Interceptar
# ---------------------------------------------------------------------------

class _TrailSegment:
    __slots__ = ("wx", "wy", "timer", "duration", "width", "height")

    def __init__(self, wx: float, wy: float, duration: float, w: int, h: int):
        self.wx       = wx
        self.wy       = wy
        self.timer    = duration
        self.duration = duration
        self.width    = w
        self.height   = h


class DashTrailManager:
    """Rastro vermelho animado gerado ao usar Interceptar.

    Funciona por emissão contínua: a cada frame do dash, TileMovementSystem
    chama emit() com a posição atual do player. Cada ponto dura 0.25s e
    faz fade por alpha — o resultado é um rastro que segue o player.
    """

    COLOR    = (210, 30, 30)
    DURATION = 0.25   # segundos que cada ponto persiste

    def __init__(self):
        self._segments: list[_TrailSegment] = []

    def emit(self, wx: float, wy: float, w: int = 24, h: int = 24) -> None:
        """Emite um único ponto de rastro na posição atual do player."""
        self._segments.append(_TrailSegment(wx, wy, self.DURATION, w, h))

    def update(self, dt: float) -> None:
        alive = []
        for s in self._segments:
            s.timer -= dt
            if s.timer > 0:
                alive.append(s)
        self._segments = alive

    def render(self, screen: "pygame.Surface",
               camera_offset_x: float, camera_offset_y: float) -> None:
        if not self._segments:
            return
        from ui.ui_helpers import fill_surf
        for s in self._segments:
            # Alpha quantizado em passos de 16: visual idêntico, mas limita o
            # cache do fill_surf a ~14 entradas em vez de 221 (uma por alpha).
            alpha = (max(0, int(220 * s.timer / s.duration)) // 16) * 16
            sx = int(s.wx - camera_offset_x) - s.width  // 2
            sy = int(s.wy - camera_offset_y) - s.height // 2
            screen.blit(fill_surf((s.width, s.height), (*self.COLOR, alpha)), (sx, sy))


DASH_TRAIL = DashTrailManager()


# ---------------------------------------------------------------------------
# Warn text — mensagem de ação bloqueada em posição fixa na tela
# ---------------------------------------------------------------------------

class WarnTextManager:
    """Exibe mensagens de aviso em posição fixa (screen-space).

    Centralizado horizontalmente. Verticalmente posicionado na parte inferior
    da área de jogo, abaixo do personagem, longe dos floating texts de dano.
    Novas mensagens substituem a anterior imediatamente.
    """

    DURATION   = 1.6          # segundos visível
    FONT_SIZE  = 27
    COLOR      = (230, 170, 50)
    Y_RATIO    = 0.72         # 72% da altura da tela (abaixo do centro)

    def __init__(self):
        self._text:     str   = ""
        self._timer:    float = 0.0
        self._font: "pygame.font.Font | None" = None
        self._surf: "pygame.Surface | None" = None  # cópia mutável do texto atual

    def _get_font(self) -> "pygame.font.Font":
        if self._font is None:
            self._font = _font(self.FONT_SIZE)
        return self._font

    def add(self, text: str) -> None:
        """Exibe (ou substitui) a mensagem de aviso atual."""
        if text != self._text:
            self._surf = None  # texto mudou — re-renderiza no próximo frame
        self._text  = text
        self._timer = self.DURATION

    def update(self, dt: float) -> None:
        if self._timer > 0:
            self._timer = max(0.0, self._timer - dt)

    def render(self, screen: "pygame.Surface") -> None:
        if self._timer <= 0 or not self._text:
            return
        if self._surf is None:
            # .copy(): CachedFont compartilha a surface; set_alpha exige cópia.
            self._surf = self._get_font().render(self._text, False, self.COLOR).copy()
        surf   = self._surf
        sw, sh = screen.get_size()
        alpha  = int(255 * min(1.0, self._timer / (self.DURATION * 0.3)))
        surf.set_alpha(alpha)
        x = sw // 2 - surf.get_width() // 2
        y = int(sh * self.Y_RATIO)
        screen.blit(surf, (x, y))


WARN = WarnTextManager()


# ---------------------------------------------------------------------------
# Proc text — notificações de proc/habilidade em posição fixa abaixo do player
# ---------------------------------------------------------------------------

class _ProcEntry:
    __slots__ = ("text", "color", "timer", "duration", "offset_y", "surf")

    def __init__(self, text: str, color: tuple, duration: float):
        self.text     = text
        self.color    = color
        self.timer    = duration
        self.duration = duration
        self.offset_y = 0.0  # deslocamento acumulado para baixo ao empilhar
        self.surf: "pygame.Surface | None" = None  # cópia mutável (fade)


class ProcTextManager:
    """Exibe notificações de proc/habilidade em screen-space abaixo do jogador.

    Posicionado entre o sprite do jogador (~50% da tela) e os avisos de WARN
    (72% da tela). Empilha textos para baixo quando múltiplos chegam juntos.
    """

    DURATION   = 1.5
    FONT_SIZE  = 26
    Y_RATIO    = 0.58   # base: 58% da altura — abaixo do player, acima do WARN
    SLOT_HEIGHT = 22    # px entre textos empilhados

    def __init__(self):
        self._entries: list[_ProcEntry] = []
        self._font: "pygame.font.Font | None" = None

    def _get_font(self) -> "pygame.font.Font":
        if self._font is None:
            self._font = _font(self.FONT_SIZE)
        return self._font

    def add(self, text: str, color: tuple = (255, 255, 255),
            duration: "float | None" = None) -> None:
        """Exibe uma notificação de proc. Empilha para baixo se já houver ativas.
        duration=None usa o padrão (DURATION) — passar um valor sobrescreve só
        esta entrada, sem afetar outras notificações já na fila."""
        for e in self._entries:
            e.offset_y += self.SLOT_HEIGHT
        self._entries.append(_ProcEntry(text, color, duration if duration is not None else self.DURATION))

    def update(self, dt: float) -> None:
        alive = []
        for e in self._entries:
            e.timer -= dt
            if e.timer > 0:
                alive.append(e)
        self._entries = alive

    def render(self, screen: "pygame.Surface") -> None:
        if not self._entries:
            return
        font   = self._get_font()
        sw, sh = screen.get_size()
        y_base = int(sh * self.Y_RATIO)
        for e in self._entries:
            fade_start = e.duration * 0.35
            alpha = 255 if e.timer >= fade_start else max(0, int(255 * e.timer / fade_start))
            if e.surf is None:
                # .copy(): CachedFont compartilha a surface; set_alpha exige cópia.
                e.surf = font.render(e.text, False, e.color).copy()
            surf = e.surf
            surf.set_alpha(alpha)
            x = sw // 2 - surf.get_width() // 2
            y = y_base + int(e.offset_y)
            screen.blit(surf, (x, y))


PROC = ProcTextManager()
