# combat_log.py
"""Log de combate persistente (dano, cura, proc, loot, etc.).

Antes desta versão era um popup flutuante (8 mensagens, fade em 8s) desenhado
perto do HUD. Virou histórico persistente (até 500 entradas) consumido pela
aba "Combate" da janela de chat (client/chat_handlers.py) — sem popup
próprio. `add(text, color)` mantém a MESMA assinatura de sempre: ~145
call-sites espalhados por skill_handlers/spell_system/world_systems/etc. só
chamam `.add()` e nunca leem estado interno, então nenhum deles precisa
mudar. Sem pygame — seguro pro servidor headless importar (via
world_systems.py e módulos de skill/spell compartilhados).
"""
from collections import deque

class CombatLog:
    MAX_ENTRIES = 500

    def __init__(self):
        # cada entrada: (text, color)
        self._entries: deque = deque(maxlen=self.MAX_ENTRIES)

    def add(self, text: str, color: tuple = (220, 220, 220)):
        self._entries.append((text, color))

    @property
    def entries(self) -> deque:
        """Histórico completo, mais recente por último — read-only pro
        consumidor (aba Combate em client/chat_handlers.py)."""
        return self._entries


LOG = CombatLog()
