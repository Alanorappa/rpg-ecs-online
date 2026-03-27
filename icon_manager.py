# icon_manager.py
"""
Carrega e cacheia ícones PNG para slots de habilidades, itens e inimigos.

Convenção de nomes em assets/icons/:
  Habilidades  : skill_1.png  skill_2.png  skill_3.png  skill_4.png
  Itens        : item_<nome_snake>.png  (ex: item_rusty_sword.png)
  Inimigos     : enemy_<tier>_<melee|ranged>.png  (ex: enemy_elite_ranged.png)

Se o arquivo não existir, get() retorna None e o código usa um quadrado colorido.
"""
from __future__ import annotations
import os
import pygame
from paths import resource_path


class IconManager:
    ICON_DIR = "assets/icons"

    def __init__(self):
        self._cache: dict[str, pygame.Surface | None] = {}

    def get(self, name: str, size: int) -> pygame.Surface | None:
        """Retorna Surface escalada para `size`×`size`, ou None se não encontrado."""
        key = f"{name}@{size}"
        if key in self._cache:
            return self._cache[key]
        path = resource_path(os.path.join(self.ICON_DIR, f"{name}.png"))
        surf = None
        if os.path.isfile(path):
            try:
                raw  = pygame.image.load(path).convert_alpha()
                surf = pygame.transform.smoothscale(raw, (size, size))
            except Exception:
                surf = None
        self._cache[key] = surf
        return surf

    # ------------------------------------------------------------------
    # Helpers de nomenclatura
    # ------------------------------------------------------------------

    @staticmethod
    def skill_key(idx: int) -> str:
        return f"skill_{idx + 1}"

    @staticmethod
    def skill_key_by_name(icon_name: str) -> str:
        return icon_name

    @staticmethod
    def item_key(item) -> str:
        return "item_" + item.name.lower().replace(" ", "_")

    @staticmethod
    def enemy_key(tier: str, is_ranged: bool) -> str:
        kind = "ranged" if is_ranged else "melee"
        return f"enemy_{tier}_{kind}"


ICONS = IconManager()
