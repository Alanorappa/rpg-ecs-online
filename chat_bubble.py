# chat_bubble.py
"""Balão de fala exibido acima da cabeça de quem envia uma mensagem de chat.

Diferente de FloatingTextManager (floating_text.py): aquele grava a posição
mundo NO MOMENTO da criação (adequado pra números de dano, que só sobem);
este RASTREIA a posição viva da entidade a cada frame (Position component),
já que o balão precisa acompanhar o personagem andando enquanto a mensagem
ainda está visível.
"""
import pygame
from fonts import make as _font
from ui_helpers import wrap_text


class _ChatBubbleEntry:
    __slots__ = ("lines", "timer", "duration", "surfs")

    def __init__(self, lines: list, duration: float):
        self.lines    = lines
        self.timer    = duration
        self.duration = duration
        self.surfs: "list | None" = None  # lazy render


class ChatBubbleManager:
    DURATION   = 5.0
    MAX_WIDTH  = 220
    # 22 (não 16) — fonts.make() aplica _SCALE=0.5 na Determination, então
    # o tamanho REAL renderizado é metade disso (~11px, igual font_sm da UI).
    # Em 16 dava 8px reais — ilegível nessa fonte (bug real reportado pelo
    # usuário, print do balão mostrando "aeudhia" em vez do texto digitado).
    FONT_SIZE  = 22
    PAD        = 8
    LINE_H     = 22
    GAP_ABOVE_HEAD = 16  # espaço entre o topo do sprite e a base do balão

    def __init__(self):
        # 1 balão por entidade — mensagem nova substitui a anterior
        self._entries: dict[int, _ChatBubbleEntry] = {}
        self._font: "pygame.font.Font | None" = None

    def _get_font(self) -> "pygame.font.Font":
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

    def render(self, screen: "pygame.Surface", world,
               camera_offset_x: float, camera_offset_y: float) -> None:
        if not self._entries:
            return
        from components import Position, Renderable
        font = self._get_font()
        cam_x, cam_y = int(camera_offset_x), int(camera_offset_y)
        for entity_id, entry in list(self._entries.items()):
            pos = world.get_component(entity_id, Position)
            if pos is None:
                # entidade sumiu (despawn/desconectou) — some junto
                del self._entries[entity_id]
                continue
            rend = world.get_component(entity_id, Renderable)
            top_h = rend.height / 2 if rend else 16

            if entry.surfs is None:
                entry.surfs = [font.render(l, False, (235, 235, 235)).copy()
                              for l in entry.lines]

            w = max((s.get_width() for s in entry.surfs), default=0) + self.PAD * 2
            h = len(entry.surfs) * self.LINE_H + self.PAD * 2

            bx = int(pos.x - cam_x) - w // 2
            by = int(pos.y - cam_y - top_h) - h - self.GAP_ABOVE_HEAD

            alpha = 255 if entry.timer > 1.0 else max(0, int(255 * entry.timer))

            bubble = pygame.Surface((w, h), pygame.SRCALPHA)
            bubble.fill((20, 20, 26, 200))
            pygame.draw.rect(bubble, (150, 150, 165, 220), bubble.get_rect(),
                             width=1, border_radius=4)
            for i, s in enumerate(entry.surfs):
                bubble.blit(s, (self.PAD, self.PAD + i * self.LINE_H))
            bubble.set_alpha(alpha)
            screen.blit(bubble, (bx, by))


CHAT_BUBBLE = ChatBubbleManager()
