"""
content/dungeon_definitions.py
Catálogo de dungeons instanciadas (07/10/2026, ver CLAUDE.md) — mesmo padrão
de MOB_TABLE/JUNGLE_BOSS_TABLE: fonte única de onde um `dungeon_key` (vindo
do campo opcional "dungeon_key" de uma transição em qualquer <mapa>_entities.json)
resolve pro arquivo de mapa real a instanciar. Novo dungeon_key futuro → só
uma entrada nova aqui, nenhum outro código muda (server/dungeon_processor.py::
DungeonProcessorMixin.request_dungeon_enter é o único consumidor).
"""

DUNGEON_TABLE: dict = {
    "aracnopolis": {
        "template_file": "maps/aracnopolis_dungeon.csv",
    },
}
