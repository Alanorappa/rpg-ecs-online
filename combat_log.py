# combat_log.py
from collections import deque

class CombatLog:
    MAX_MESSAGES     = 8
    MESSAGE_DURATION = 8.0
    FADE_DURATION    = 2.0

    def __init__(self):
        # cada entrada: [text, color, timer]
        self._messages: deque = deque()
        # cache: (text, color) → CÓPIA mutável da surface renderizada.
        # O render em si já é cacheado por fonts.CachedFont; a cópia local
        # existe porque o fade via set_alpha muta a surface — não pode mutar
        # a surface compartilhada do cache da fonte.
        self._surf_cache: dict = {}
        self._cache_font = None

    def add(self, text: str, color: tuple = (220, 220, 220)):
        self._messages.appendleft([text, color, self.MESSAGE_DURATION])
        while len(self._messages) > self.MAX_MESSAGES:
            self._messages.pop()

    def update(self, dt: float):
        updated = []
        for entry in self._messages:
            entry[2] -= dt
            if entry[2] > 0:
                updated.append(entry)
        self._messages = deque(updated)
        if not self._messages:
            self._surf_cache.clear()

    def draw(self, screen, font, x: int, bottom_y: int):
        if font is not self._cache_font:
            # fonte mudou (ex: resize) — invalida cache
            self._surf_cache.clear()
            self._cache_font = font
        line_h = 18
        for i, (text, color, timer) in enumerate(self._messages):
            key = (text, color)
            if key not in self._surf_cache:
                self._surf_cache[key] = font.render(text, True, color).copy()
            surf = self._surf_cache[key]
            alpha = min(255, int(255 * timer / self.FADE_DURATION))
            surf.set_alpha(alpha)
            screen.blit(surf, (x, bottom_y - (i + 1) * line_h))


LOG = CombatLog()
