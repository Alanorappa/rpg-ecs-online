# chat_bubble.py
"""Balão de fala exibido acima da cabeça de quem envia uma mensagem de chat.

Diferente de FloatingTextManager (floating_text.py): aquele grava a posição
mundo NO MOMENTO da criação (adequado pra números de dano, que só sobem);
este RASTREIA a posição viva da entidade a cada frame (Position component),
já que o balão precisa acompanhar o personagem andando enquanto a mensagem
ainda está visível.

Enfileirado em ui/world_labels.py::WORLD_LABELS (não blitado direto no
zoom_surf) — MESMO stack_key do nameplate (entity_id), pra empilhar
SEMPRE acima do que já foi desenhado ali (nome/nível/HP) na mesma frame.
Dois problemas reportados pelo usuário 17/07/2026 vêm da MESMA causa:
blitar direto no zoom_surf (mundo, pré-zoom) sujeita o texto ao
`pygame.transform.scale()` de zoom não-inteiro no fim do frame — nasce
nítido mas sai borrado ("fonte não pixel perfect, como no chat" — o
chat É pixel-perfect porque desenha em screen-space, nunca passa por
esse scale) — e não tinha NENHUMA noção da altura do nameplate (só um
gap fixo do topo do sprite), então sobrepunha nome/HP quando a pilha
era mais alta que o gap ("balão em cima do nameplate, ilegível"). Rotear
por WORLD_LABELS resolve os dois de graça: nasce em screen-space (nunca
mais borra) e herda a altura acumulada da pilha (nunca mais sobrepõe).
"""
import pygame
from ui.fonts import make as _font
from ui.ui_helpers import wrap_text


class _ChatBubbleEntry:
    __slots__ = ("lines", "timer", "duration", "bubble_surf")

    def __init__(self, lines: list, duration: float):
        self.lines       = lines
        self.timer        = duration
        self.duration     = duration
        self.bubble_surf: "pygame.Surface | None" = None  # lazy, construído 1x


class ChatBubbleManager:
    DURATION   = 5.0
    MAX_WIDTH  = 220
    # 22 (não 16) — fonts.make() aplica _SCALE=0.5 na Determination, então
    # o tamanho REAL renderizado é metade disso (~11px, igual font_sm da UI).
    # Em 16 dava 8px reais — ilegível nessa fonte (bug real reportado pelo
    # usuário, print do balão mostrando "aeudhia" em vez do texto digitado).
    # Só usado como FALLBACK — set_font() (chamado por
    # GameEngine._reload_ui_fonts) substitui por font_sm de verdade.
    FONT_SIZE  = 22
    PAD        = 8
    LINE_H     = 22
    # Respiro entre o TOPO da pilha de nameplate (nome/nível/HP já
    # empilhados por _draw_remote_players) e a base do balão — só importa
    # como fallback se por algum motivo a entidade não tiver nameplate
    # enfileirado ainda nesta frame (WORLD_LABELS._queue usa o offset já
    # acumulado, ignorando isto, sempre que já existe algo na pilha).
    GAP_ABOVE_STACK = 8

    def __init__(self):
        # 1 balão por entidade — mensagem nova substitui a anterior
        self._entries: dict[int, _ChatBubbleEntry] = {}
        self._font: "pygame.font.Font | None" = None
        self._external_font: "pygame.font.Font | None" = None

    def set_font(self, font: "pygame.font.Font") -> None:
        """GameEngine._reload_ui_fonts() chama isto com self.font_sm — o
        balão passa a usar o MESMO objeto de fonte da janela de chat
        (mesmo tamanho renderizado, mesmo _ui_scale) em vez da própria
        instância fixa em FONT_SIZE=22 (ignorava _ui_scale). Usuário
        reportou 17/07/2026 que o balão "parecia" a fonte do chat só que
        bold — eram o mesmo arquivo/tamanho NOMINAL, mas escalas
        diferentes (font_sm segue _ui_scale, o balão não) fazem uma fonte
        sem antialiasing renderizar com peso de traço visualmente
        diferente a cada tamanho inteiro distinto."""
        self._external_font = font

    def _get_font(self) -> "pygame.font.Font":
        if self._external_font is not None:
            return self._external_font
        if self._font is None:
            self._font = _font(self.FONT_SIZE)
        return self._font

    def add(self, entity_id: int, text: str) -> None:
        lines = wrap_text(text, self._get_font(), self.MAX_WIDTH - self.PAD * 2)
        self._entries[entity_id] = _ChatBubbleEntry(lines, self.DURATION)

    def update(self, dt: float) -> None:
        dead = []
        for eid, entry in self._entries.items():
            entry.timer -= dt
            if entry.timer <= 0:
                dead.append(eid)
        for eid in dead:
            del self._entries[eid]

    def _build_bubble_surf(self, entry: _ChatBubbleEntry) -> None:
        font = self._get_font()
        surfs = [font.render(l, False, (235, 235, 235)).copy() for l in entry.lines]
        w = max((s.get_width() for s in surfs), default=0) + self.PAD * 2
        h = len(surfs) * self.LINE_H + self.PAD * 2
        bubble = pygame.Surface((w, h), pygame.SRCALPHA)
        bubble.fill((20, 20, 26, 200))
        pygame.draw.rect(bubble, (150, 150, 165, 220), bubble.get_rect(),
                         width=1, border_radius=4)
        for i, s in enumerate(surfs):
            bubble.blit(s, (self.PAD, self.PAD + i * self.LINE_H))
        entry.bubble_surf = bubble

    def render(self, world) -> None:
        """Enfileira o balão de cada entidade em WORLD_LABELS — chamar
        DEPOIS de _draw_remote_players/_draw_mob_hp_bars (que já
        enfileiraram nome/nível/HP) e ANTES de WORLD_LABELS.render() —
        mesmo ponto onde este método já era chamado em game.py."""
        if not self._entries:
            return
        from engine.components import Position, Renderable
        from ui.world_labels import WORLD_LABELS
        for entity_id, entry in list(self._entries.items()):
            pos = world.get_component(entity_id, Position)
            if pos is None:
                # entidade sumiu (despawn/desconectou) — some junto
                del self._entries[entity_id]
                continue
            rend = world.get_component(entity_id, Renderable)
            top_h = rend.height / 2 if rend else 16
            world_y_top = pos.y - top_h

            if entry.bubble_surf is None:
                self._build_bubble_surf(entry)

            alpha = 255 if entry.timer > 1.0 else max(0, int(255 * entry.timer))
            entry.bubble_surf.set_alpha(alpha)

            WORLD_LABELS.add_icon(pos.x, world_y_top, entry.bubble_surf,
                                  stack_key=entity_id, gap_before=self.GAP_ABOVE_STACK)


CHAT_BUBBLE = ChatBubbleManager()
