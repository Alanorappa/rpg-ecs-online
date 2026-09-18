# icon_manager.py
"""
Carrega e cacheia ícones PNG para slots de habilidades, itens e inimigos.

Convenção de nomes em assets/icons/:
  Habilidades  : skill_1.png  skill_2.png  skill_3.png  skill_4.png
  Itens        : item_<item_id>.png  (ex: item_iron_sword.png — débito C2/
                 migração de ícone 11/08/2026, ver PROBLEMAS_ARQUITETURA.md
                 §25; era item_<nome_snake>.png antes, nome de exibição não
                 é identidade estável)
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
        """
        Retorna Surface de `size`×`size` com o ícone escalado por nearest-neighbor
        para preencher o slot completamente. Retorna None se não encontrado.
        """
        key = f"{name}@{size}"
        if key in self._cache:
            return self._cache[key]
        raw  = self._load_raw(name)
        surf = pygame.transform.scale(raw, (size, size)) if raw is not None else None
        self._cache[key] = surf
        return surf

    def _load_raw(self, name: str) -> "pygame.Surface | None":
        """Surface no tamanho NATURAL do ícone — atlas primeiro
        (`ui/icon_atlas.py`, 17/09/2026), arquivo individual como
        fallback pro que ainda não foi catalogado (ver
        PROBLEMAS_ARQUITETURA.md §181)."""
        from ui.icon_atlas import ICON_ATLAS_SHEETS
        raw = ICON_ATLAS_SHEETS.get_region(name)
        if raw is not None:
            return raw
        path = resource_path(os.path.join(self.ICON_DIR, f"{name}.png"))
        if os.path.isfile(path):
            try:
                return pygame.image.load(path).convert_alpha()
            except Exception:
                return None
        return None

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
        """`item_id` (débito C2, migração de ícone 11/08/2026) é a chave
        preferida — estável, nunca colide entre itens com nomes iguais
        (trava de integridade em WorldServer._check_item_name_collisions
        garante isso pro conteúdo real). Fallback por NOME só cobre item
        sem item_id — hoje isso só acontece por item reconstruído de um
        save local antigo corrompido/incompleto (engine/save_system.py
        já foi corrigido pra sempre salvar item_id, mas um arquivo de
        save já existente em disco pode ainda não ter passado por um
        save novo)."""
        item_id = getattr(item, "item_id", "")
        if item_id:
            return "item_" + item_id
        return IconManager.item_key_by_name(item.name)

    @staticmethod
    def item_key_by_name(name: str) -> str:
        """Fallback por nome — usado por `item_key` acima e por chamadores
        que só têm o NOME (string solta, sem objeto Item de verdade pra
        ler `.item_id`), ex: client/consumable_bar_handlers.py quando o
        item não resolveu em nenhum catálogo local. Ponto único do
        formato de fallback — nunca reimplementar `"item_" + nome...`
        inline num call site novo."""
        return "item_" + name.lower().replace(" ", "_")

    @staticmethod
    def enemy_key(tier: str, is_ranged: bool) -> str:
        kind = "ranged" if is_ranged else "melee"
        return f"enemy_{tier}_{kind}"


ICONS = IconManager()
