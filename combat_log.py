# combat_log.py
from collections import deque

class CombatLog:
    MAX_MESSAGES    = 8
    MESSAGE_DURATION = 8.0   # segundos antes de desaparecer
    FADE_DURATION   = 2.0    # últimos N segundos em que a mensagem faz fade

    def __init__(self):
        # cada entrada: [text, color, timer]
        self._messages: deque = deque()

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

    def draw(self, screen, font, x: int, bottom_y: int):
        line_h = 18
        for i, (text, color, timer) in enumerate(self._messages):
            alpha = min(1.0, timer / self.FADE_DURATION)
            r, g, b = color
            faded = (int(r * alpha), int(g * alpha), int(b * alpha))
            surf = font.render(text, True, faded)
            screen.blit(surf, (x, bottom_y - (i + 1) * line_h))


LOG = CombatLog()
