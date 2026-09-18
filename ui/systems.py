# systems.py
"""
Re-exportação de compatibilidade (18/09/2026, ver PROBLEMAS_ARQUITETURA.md
item #2 do backlog de 14, "ui/systems.py monolítico") — este arquivo era
4913 linhas com 10 classes `System` diferentes; foi dividido por domínio
em arquivos próprios:

    ui/player_input_system.py  → MouseTargetingSystem, PlayerInputSystem
    ui/render_system.py        → RenderSystem, CameraSystem
    ui/tile_render_system.py   → TileRenderSystem, FogSystem
    ui/shop_system.py          → ShopSystem
    ui/consumable_system.py    → ConsumableSystem
    ui/loot_system.py          → LootSystem
    ui/skill_system.py         → SkillSystem
    ui/click_rect_helpers.py   → _corpse_click_rect, _entity_click_rect,
                                  _live_target_at_world_pos (hit-test de
                                  clique compartilhado por
                                  MouseTargetingSystem e LootSystem)

Este arquivo continua existindo SÓ pra compatibilidade — todo código que
já fazia `from ui.systems import X` continua funcionando sem mudança
nenhuma, mesmo padrão já usado aqui pra re-exportar nomes de
`engine/world_systems.py` (sistemas headless do servidor). Código NOVO
deve importar direto do arquivo de domínio certo, nunca daqui.
"""
from __future__ import annotations

# Re-exporta apply_effect/compute_fov para compatibilidade com todo o
# código que já faz `from ui.systems import apply_effect` (ex:
# ui/spell_system.py) ou `ui.systems.compute_fov` (ex: testes) — igual já
# fazia o ui/systems.py original.
from engine.core_systems import apply_effect
from engine.fov import compute_fov

# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import (
    System, PathfindingSystem, TileValidationSystem, CombatSystem,
    DeathHandlerSystem, CombatStateSystem, ProjectileSystem, EnemyAISystem,
    TileMovementSystem, StatusEffectSystem, EnemyAbilitySystem, CorpseSystem,
    SpawnZoneSystem,
    _svc, register_services, deal_damage, find_path, get_tilemap,
    is_tile_walkable, get_mainhand_weapon,
)

# ---------------------------------------------------------------------------
# Re-export dos Systems de UI/render, agora vivendo em arquivos próprios
# ---------------------------------------------------------------------------
from ui.player_input_system import MouseTargetingSystem, PlayerInputSystem
from ui.render_system import (
    RenderSystem, CameraSystem,
    _build_effects_row, _filter_nameplate_effects, _get_ground_effect_sprite,
)
from ui.tile_render_system import (
    TileRenderSystem, FogSystem, _object_scan_margin_tiles,
)
from ui.shop_system import ShopSystem
from ui.consumable_system import ConsumableSystem
from ui.loot_system import LootSystem
from ui.skill_system import SkillSystem
from ui.click_rect_helpers import (
    _corpse_click_rect, _entity_click_rect, _live_target_at_world_pos,
)
