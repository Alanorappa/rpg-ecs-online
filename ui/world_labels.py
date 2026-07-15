"""
world_labels.py — Overlay de mundo (nome de NPC/mob/player, HUD de
nível+barras, ícone de quest acima da cabeça, fila de ícones de efeito...)
ancorado em posição de MUNDO mas renderizado em ESPAÇO DE TELA, depois do
zoom_surf já ter sido escalado pro tamanho real da tela (game.py).

Por quê: o zoom da câmera (`self._zoom`) desenha tudo numa surface LÓGICA
menor (`world_surf`/`zoom_surf`) e escala o resultado inteiro pro tamanho
real da tela com `pygame.transform.scale()`. Isso é ótimo pra sprites/
tiles (pixel art desenhada num grid fixo, escala bem), mas texto de fonte
pixel-perfect e ícones pequenos saem BORRADOS/DISTORCIDOS quando
redimensionados por um fator de zoom não-inteiro (bug relatado
11/07/2026: nome dos NPCs ilegível na tela). PIOR: se só PARTE de um
elemento composto (ex: HUD) for screen-space e o resto ficar em espaço de
mundo, os dois desalinham entre si conforme o zoom muda (outro bug real,
mesma data — número do nível "flutuando" fora da HUD). Regra desde então:
tudo que precisa ficar visualmente junto de um elemento pixel-perfect
também vai por aqui, INTEIRO — nunca meio-mundo meio-tela.

Fix: elementos registrados aqui NUNCA passam pelo `pygame.transform.scale`
do mundo — nascem já no pixel final da tela, um único blit direto.

Três formas de posicionar:
  - add_text()/add_icon() — EMPILHADO por `stack_key` (ex: entity_id), de
    baixo pra cima a partir de `world_y` (topo da entidade, ou de algo já
    desenhado ali). `gap_before` (px de tela, default 4) controla o
    respiro ANTES deste elemento especificamente.
  - add_icon_offset() — posição projetada + deslocamento em PX DE TELA
    (não escala com zoom) + alinhamento de borda — uso: fila de ícones de
    efeito encostada na borda direita de um elemento de tamanho de tela
    já conhecido (ex: HUD de barras, ver ui/hud_bars.py).

game.py chama render() UMA VEZ por frame, depois do blit/scale final do
mundo pra tela.
"""
from __future__ import annotations

_STACK_GAP_PX = 4   # respiro padrão entre elementos empilhados, e entre o
                     # primeiro elemento e o topo do sprite — em PX DE
                     # TELA reais, não escala com zoom (de propósito).


class _WorldOverlayQueue:
    def __init__(self):
        self._pending: list[tuple[float, float, "object", int]] = []
        self._stack_offset: dict = {}       # stack_key -> altura acumulada (px de tela)
        self._pending_offset: list[tuple] = []

    def add_text(self, world_x: float, world_y: float, text: str, font, color: tuple,
                stack_key=None, gap_before: "int | None" = None) -> None:
        """world_y: topo da entidade (ex: pos.y - renderable.height/2), ou
        já deslocado pra cima de algo (ex: topo da HUD de barras) — a
        pilha soma a partir daí.

        render_tight (não font.render direto): corrige bearing
        desproporcional de glifos estreitos (ex: "i" da MEGAMAN10 tinha
        quase metade do avanço em espaço vazio, "Zumbi" virava "Zumb i" —
        reportado pelo usuário 11/07/2026). Ver ui/fonts.py::render_tight."""
        from ui.fonts import render_tight as _render_tight
        surf = _render_tight(font, text, color)
        self._queue(world_x, world_y, surf, stack_key, gap_before)

    def add_icon(self, world_x: float, world_y: float, surf, stack_key=None,
                gap_before: "int | None" = None) -> None:
        self._queue(world_x, world_y, surf, stack_key, gap_before)

    def add_icon_offset(self, world_x: float, world_y: float, surf,
                        x_offset: float = 0, y_offset: float = 0,
                        halign: str = "center", valign: str = "center") -> None:
        """Projeta (world_x, world_y) pra tela e soma um deslocamento em
        PX DE TELA fixo (não escala com zoom) — pra grudar um elemento na
        borda de outro que já tem tamanho de tela conhecido (ex: fila de
        ícones de efeito encostada na borda direita da HUD de barras,
        pedido do usuário 11/07/2026). halign/valign: qual borda do surf
        encosta no ponto final ("left"/"center"/"right",
        "top"/"center"/"bottom")."""
        self._pending_offset.append((world_x, world_y, surf, x_offset, y_offset, halign, valign))

    def _queue(self, world_x: float, world_y: float, surf, stack_key,
              gap_before: "int | None") -> None:
        key = stack_key if stack_key is not None else (world_x, world_y)
        default_gap = gap_before if gap_before is not None else _STACK_GAP_PX
        gap = self._stack_offset.get(key, default_gap)
        self._pending.append((world_x, world_y, surf, gap))
        self._stack_offset[key] = gap + surf.get_height() + _STACK_GAP_PX

    def render(self, screen, cam_x: float, cam_y: float, zoom: float) -> None:
        for world_x, world_y, surf, gap in self._pending:
            sx = int((world_x - cam_x) * zoom - surf.get_width() / 2)
            sy = int((world_y - cam_y) * zoom - gap - surf.get_height())
            screen.blit(surf, (sx, sy))
        for world_x, world_y, surf, xo, yo, halign, valign in self._pending_offset:
            px = (world_x - cam_x) * zoom + xo
            py = (world_y - cam_y) * zoom + yo
            if halign == "left":
                sx = px
            elif halign == "right":
                sx = px - surf.get_width()
            else:
                sx = px - surf.get_width() / 2
            if valign == "top":
                sy = py
            elif valign == "bottom":
                sy = py - surf.get_height()
            else:
                sy = py - surf.get_height() / 2
            screen.blit(surf, (int(sx), int(sy)))
        self._pending.clear()
        self._stack_offset.clear()
        self._pending_offset.clear()


WORLD_LABELS = _WorldOverlayQueue()
