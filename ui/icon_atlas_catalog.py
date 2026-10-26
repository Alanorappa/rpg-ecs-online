# icon_atlas_catalog.py
"""
Catálogo do atlas de ícones — GERADO/ATUALIZADO por
`tools/build_icon_atlas.py` (17/09/2026, ver PROBLEMAS_ARQUITETURA.md
§181 e PROJECT_WIDE_REVIEW.md item #10).

O script MESCLA — nunca apaga uma entrada existente ao rodar de novo.
Entrada adicionada à mão (ícone pintado direto numa célula em branco
do atlas, fora do script) é permitida e sobrevive a uma execução
futura do script — só siga o mesmo formato de tupla abaixo.

Formato: nome do ícone (mesma chave que `IconManager` já usa — sem
".png") → (arquivo do atlas em assets/icons/, coluna, linha). Célula
= 32×32px, grade 16×16 por atlas (ver ui/icon_atlas.py::CELL_SIZE/
GRID_COLS).
"""
from __future__ import annotations

ICON_ATLAS: "dict[str, tuple[str, int, int]]" = {
    'item_arrow': ('atlas_items.png', 0, 0),
    'item_artefato_misterioso': ('atlas_items.png', 1, 0),
    'item_basic_quiver': ('atlas_items.png', 2, 0),
    'item_hearty_stew': ('atlas_items.png', 3, 0),
    'item_hp_potion': ('atlas_items.png', 4, 0),
    'item_iron_sword': ('atlas_items.png', 5, 0),
    'item_lampiao': ('atlas_items.png', 6, 0),
    'item_large_hp_potion': ('atlas_items.png', 7, 0),
    'item_large_mana_potion': ('atlas_items.png', 8, 0),
    'item_large_waterskin': ('atlas_items.png', 9, 0),
    'item_mana_potion': ('atlas_items.png', 10, 0),
    'item_medium_waterskin': ('atlas_items.png', 11, 0),
    'item_pa': ('atlas_items.png', 12, 0),
    'item_presa_lobo': ('atlas_items.png', 13, 0),
    'item_roasted_meat': ('atlas_items.png', 14, 0),
    'item_short_bow': ('atlas_items.png', 15, 0),
    'item_simple_bread': ('atlas_items.png', 0, 1),
    'item_small_hp_potion': ('atlas_items.png', 1, 1),
    'item_small_mana_potion': ('atlas_items.png', 2, 1),
    'item_small_waterskin': ('atlas_items.png', 3, 1),
    'item_training_sword': ('atlas_items.png', 4, 1),
    'item_vomito_zumbi': ('atlas_items.png', 5, 1),
    'skill_bloco_de_gelo': ('atlas_skills.png', 0, 0),
    'skill_bola_de_fogo': ('atlas_skills.png', 1, 0),
    'skill_brado_provocativo': ('atlas_skills.png', 2, 0),
    'skill_calamidade_flamejante': ('atlas_skills.png', 3, 0),
    'skill_calcinar': ('atlas_skills.png', 4, 0),
    'skill_camuflagem': ('atlas_skills.png', 5, 0),
    'skill_cancao_inspiracao': ('atlas_skills.png', 6, 0),
    'skill_cancao_ninar': ('atlas_skills.png', 7, 0),
    'skill_escudo_fogo': ('atlas_skills.png', 8, 0),
    'skill_executar': ('atlas_skills.png', 9, 0),
    'skill_fatiador_de_corpos': ('atlas_skills.png', 10, 0),
    'skill_flecha_reiterada': ('atlas_skills.png', 11, 0),
    'skill_golpe_debilitante': ('atlas_skills.png', 12, 0),
    'skill_golpe_poderoso': ('atlas_skills.png', 13, 0),
    'skill_impacto': ('atlas_skills.png', 14, 0),
    'skill_interceptar': ('atlas_skills.png', 15, 0),
    'skill_nova_congelante': ('atlas_skills.png', 0, 1),
    'skill_picada_escorpiao': ('atlas_skills.png', 1, 1),
    'skill_pirofagia': ('atlas_skills.png', 2, 1),
    'skill_polimorfia': ('atlas_skills.png', 3, 1),
    'skill_punho_no_queixo': ('atlas_skills.png', 4, 1),
    'skill_recarregar': ('atlas_skills.png', 5, 1),
    'skill_sequencia_crew': ('atlas_skills.png', 6, 1),
    'skill_so_um_gole': ('atlas_skills.png', 7, 1),
    'skill_tiro_multiplo': ('atlas_skills.png', 8, 1),
    'skill_tiro_repulsivo': ('atlas_skills.png', 9, 1),
    'skill_vitoria_iminente': ('atlas_skills.png', 10, 1),
    # PROVISÓRIO (24/10/2026): reaproveita a célula da Canção de Inspiração
    # até existir arte própria — trocar pra uma célula livre quando pintar.
    'skill_sopro_da_coruja': ('atlas_skills.png', 6, 0),
    # PROVISÓRIOS (25/10/2026, skills da Raiz bloco C) — mesma regra acima.
    'skill_abraco_do_urso':       ('atlas_skills.png', 7, 1),   # célula do Só um Gole
    'skill_matilha_ancestral':    ('atlas_skills.png', 7, 0),   # célula da Canção de Ninar
    'skill_negociacao_ancestral': ('atlas_skills.png', 3, 1),   # célula da Polimorfia
}
