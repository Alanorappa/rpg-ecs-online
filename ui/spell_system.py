"""
spell_system.py — Sistemas de magia do jogador (classe Mago).

Sistemas incluídos:
  ManaSystem            — regen de mana ao longo do tempo
  SpellCastSystem       — processa barra de cast e dispara projéteis ao completar
  PlayerProjectileSystem — move projéteis do jogador e aplica dano/morte
  ChannelingSystem      — processa ticks de canalização (Calamidade Flamejante)
  IceBlockSystem        — processa Bloco de Gelo (imunidade + cura por segundo)
  AoeTargetingSystem    — intercepta clique esquerdo para posicionar magia AOE
"""
from __future__ import annotations
import math
import random

import pygame

from engine.world import World
from ui.systems import System
from engine.components import (
    Position, PlayerControlled, CombatState, CombatStats, CharacterStats,
    Equipment, Enemy, AIControlled, TileMovement, StatusEffects, Camera,
    SpellCast, Channeling, IceBlockEffect, FireShieldEffect, PirofagiaAiming,
    PlayerProjectile, AoeTargeting, PlayerSkills,
    PendingDeath, PlayerAutoMove, NpcSounds,
)
from engine.tileset import TILE_SIZE
from engine.utils import chebyshev
from ui.combat_log import LOG
from ui.floating_text import FLT, WARN
from ui.sound_manager import SOUNDS
from engine.stat_fns import enter_combat
from engine.damage_calculator import resolve_attack_outcome, CRITICAL_DAMAGE_MULTIPLIER, spell_damage as _spell_damage_fn


def _spell_damage(attacker_id: int, world: World,
                  dmg_weapon_pct: float, sp_coeff: float) -> int:
    """Wrapper para damage_calculator.spell_damage (fonte única)."""
    return _spell_damage_fn(world, attacker_id, dmg_weapon_pct, sp_coeff)


# _apply_magic_damage: extraído para engine/core_systems.py::apply_magic_damage_shared
# (débito B3/CRÍTICO B, 10/08/2026 — a função sempre foi pygame-free, só morava
# num módulo que importa pygame no topo, arrastando o servidor junto quando
# handlers server-side precisavam dela, ex: engine/skill_handlers.py::
# _skill_pirofagia). Alias mantido aqui pra não tocar os 3 call sites locais.
from engine.core_systems import apply_magic_damage_shared as _apply_magic_damage


# ---------------------------------------------------------------------------
# ManaSystem
# ---------------------------------------------------------------------------

class ManaSystem(System):
    """Timers de proc de fogo + indicador de Choque Térmico (nome histórico).

    Mana NÃO é regenerada aqui: só o servidor regenera (ver
    core_systems.BaseCombatStateSystem._tick_mana_regen), sincronizada via
    STATS_UPDATE — bug real documentado em PROBLEMAS_ARQUITETURA.md (cliente
    regenerava sozinho e divergia pra sempre). O branch offline de regen
    local foi REMOVIDO junto do modo offline (15/07/2026, item A2 §11 —
    offline vive só no master).
    """

    def __init__(self, world: World):
        self.world = world
        self._net = None  # injetado por game.py após _connect_online()

    def update(self, events=None, dt: float = 0) -> None:
        for entity_id, char_stats, cs, _, combat_stats in self.world.get_entities_with(
                CharacterStats, CombatState, PlayerControlled, CombatStats):
            if char_stats.max_mana <= 0:
                continue
            # Decrementa janela de crits de fogo para Lapso Elemental
            if combat_stats.fire_crit_timer > 0:
                combat_stats.fire_crit_timer -= dt
                if combat_stats.fire_crit_timer <= 0:
                    combat_stats.fire_crit_timer   = 0.0
                    combat_stats.fire_crit_counter = 0

            # Choque Térmico: atualiza indicador de proc a cada frame
            if getattr(combat_stats, "thermal_shock_enabled", False) and cs:
                target_id = cs.target_entity_id
                if target_id != -1:
                    _t_sfx = self.world.get_component(target_id, StatusEffects)
                    char_stats.thermal_shock_active = (
                        _t_sfx is not None and _t_sfx.has("root"))
                else:
                    char_stats.thermal_shock_active = False
            else:
                char_stats.thermal_shock_active = False


# ---------------------------------------------------------------------------
# SpellCastSystem
# ---------------------------------------------------------------------------

class SpellCastSystem(System):
    """Processa a barra de cast e dispara o efeito da magia ao completar.

    Para adicionar nova spell com cast_time: registrar em _CAST_HANDLERS abaixo.
    O método recebe (entity_id, target_id). Para AOE sem alvo, target_id = -1.
    """

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.world_surf = screen
        self.hud_surf   = screen
        # Sprite da mira de Tiro Múltiplo — lazy-loaded e escalado na primeira renderização
        self._tiro_aim_img: "pygame.Surface | None" = None
        # Dispatch de conclusão de cast — sem if/elif por spell_id.
        # Chave = spell_id do SKILL_CATALOG. Valor = nome do método nesta classe.
        # Adicionar nova spell com cast: inserir entrada aqui.
        self._current_spell_id: str = ""
        # Casts visual_only cancelados por movimento neste frame — game.py envia CANCEL_CAST
        self.interrupted_visual_casts: list[str] = []
        # Direção final de skills direcionais (ex: tiro_multiplo) capturada na conclusão
        # do cast local — game.py lê e envia CAST_DIR_UPDATE ao servidor.
        # Lista de (spell_id, dir_x, dir_y).
        self.pending_dir_updates: list[tuple[str, float, float]] = []
        # Callback opcional (injetado por game.py no online): notifica mudança de
        # inventário/equipamento que precisa ser persistida (ex: Recarregar).
        self._on_inventory_changed = None
        self._CAST_HANDLERS: dict[str, str] = {
            "bola_de_fogo":      "_launch_fireball",
            "nova_congelante":   "_apply_nova_congelante",
            "polimorfia":        "_apply_polymorph",
            "calcinar":          "_apply_calcinar",
            "recarregar":        "_apply_recarregar",
            "flecha_reiterada":    "_apply_flecha_reiterada",
            "picada_escorpiao":  "_apply_arrow_skill",
            "cancao_ninar":      "_apply_cancao_ninar_complete",
            "tiro_repulsivo":    "_apply_tiro_repulsivo",
            "tiro_multiplo":     "_apply_tiro_multiplo",
        }
        # Handlers chamados quando um cast INTERRUPTÍVEL é cancelado por movimento.
        self._CANCEL_HANDLERS: dict[str, str] = {
            "cancao_ninar": "_cancel_cancao_ninar",
        }

    def update(self, events=None, dt: float = 0) -> None:
        for entity_id, spell_cast, combat_state, _ in self.world.get_entities_with(
                SpellCast, CombatState, PlayerControlled):

            # Movimento cancela cast — exceto se spell_cast.interruptible == False
            tm = self.world.get_component(entity_id, TileMovement)
            if tm and tm.is_moving and spell_cast.interruptible:
                # Chama handler de cancelamento se definido
                cancel_handler_name = self._CANCEL_HANDLERS.get(spell_cast.spell_id)
                if cancel_handler_name:
                    cancel_fn = getattr(self, cancel_handler_name, None)
                    if cancel_fn:
                        cancel_fn(entity_id)
                # Notifica o servidor se era um cast visual (online) — evita dano sem cast
                if spell_cast.visual_only:
                    self.interrupted_visual_casts.append(spell_cast.spell_id)
                self.world.remove_component(entity_id, SpellCast)
                combat_state.is_casting = False
                SOUNDS.fadeout_skills(300)
                WARN.add("Cast interrompido!")
                return

            spell_cast.elapsed += dt
            if spell_cast.elapsed >= spell_cast.cast_time:
                self._complete_cast(entity_id, spell_cast, combat_state)
                self.world.remove_component(entity_id, SpellCast)
                combat_state.is_casting = False

    def _complete_cast(self, entity_id: int, spell_cast: SpellCast,
                       combat_state: CombatState) -> None:
        # Online: cast visual_only — barra preenche, servidor aplica dano.
        # BdF: projétil criado no SKILL_RESULT(is_completion) em game.py — não chama handler aqui.
        # Outras spells (nova_congelante, calcinar, polimorfia): chama handler para sons/visuais
        # locais. Handlers são seguros: verificam target_cs antes de causar dano (online = None).
        if spell_cast.visual_only:
            if spell_cast.spell_id == "tiro_multiplo":
                # Online: captura direção do mouse AGORA (conclusão do cast) e notifica servidor.
                # Não checa inimigos locais — mobs remotos não têm componente Enemy.
                # Projéteis visuais chegam via SKILL_RESULT do servidor.
                self._capture_tiro_multiplo_dir(entity_id)
                return
            _PROJ_SPELLS_LOCAL = {"bola_de_fogo", "flecha_reiterada", "picada_escorpiao", "tiro_repulsivo"}
            # "Handlers são seguros: verificam target_cs antes de causar dano"
            # (comentário acima) NÃO vale pra recarregar — é auto-alvo (sempre
            # tem CombatStats válido, nunca None), então o guard que protege
            # os outros handlers nunca dispara aqui. _apply_recarregar faz
            # mutação REAL (bag→aljava), não é cosmético — rodar aqui E de
            # novo quando a confirmação do servidor chega duplicava o
            # consumo de flechas da bag (bug real relatado pelo usuário
            # 19/07/2026: comprou 200, aljava de 75, consumiu 150 da bag —
            # exatamente o dobro). O resultado real vem só do servidor
            # (client/network_handlers.py, confirmação de queue_stats_update)
            # — aqui só toca som/cast-bar, igual os PROJ_SPELLS_LOCAL acima.
            _SELF_TARGET_SERVER_ONLY = {"recarregar"}
            if spell_cast.spell_id not in _PROJ_SPELLS_LOCAL and spell_cast.spell_id not in _SELF_TARGET_SERVER_ONLY:
                handler_name = self._CAST_HANDLERS.get(spell_cast.spell_id)
                if handler_name:
                    handler = getattr(self, handler_name, None)
                    if handler:
                        self._current_spell_id = spell_cast.spell_id
                        try:
                            handler(entity_id, spell_cast.target_id)
                        except Exception:
                            pass
                        self._current_spell_id = ""
            return

        # Deduz recursos aqui — cast completado com sucesso.
        # Interrupções removem SpellCast sem chegar aqui → recurso não é descontado.
        char_stats = self.world.get_component(entity_id, CharacterStats)
        if char_stats:
            if spell_cast.mana_cost > 0:
                char_stats.mana = max(0, char_stats.mana - spell_cast.mana_cost)
            if spell_cast.concentration_cost > 0:
                _cs_buff = self.world.get_component(entity_id, CombatStats)
                if not (_cs_buff and _cs_buff.concentration_free):
                    char_stats.concentration = max(0, char_stats.concentration - spell_cast.concentration_cost)

        # Aplica cooldown da skill ao completar o cast (não no início —
        # cast interrompido não consome cooldown)
        from engine.components import PlayerSkills
        _ps = self.world.get_component(entity_id, PlayerSkills)
        if _ps:
            _sk = _ps.skill_by_id(spell_cast.spell_id)
            if _sk and _sk.cooldown > 0:
                _sk.current_cooldown = _sk.cooldown

        # Skills ofensivas com cast: só persegue/aggra após o cast completar
        from content.skill_config import SKILL_CATALOG as _SC
        _skill_def = _SC.get(spell_cast.spell_id, {})
        if _skill_def.get("offensive", True) and spell_cast.spell_id in _SC:
            from engine.stat_fns import enter_combat as _ec
            _ec(combat_state)
            combat_state.is_pursuing = True
            combat_state.chase_suppressed = False   # reengajamento reativa a perseguição

        # Dispatch por spell_id — data-driven, sem if/elif
        handler_name = self._CAST_HANDLERS.get(spell_cast.spell_id)
        if handler_name:
            handler = getattr(self, handler_name, None)
            if handler:
                self._current_spell_id = spell_cast.spell_id  # disponível ao handler genérico
                handler(entity_id, spell_cast.target_id)
                self._current_spell_id = ""
            else:
                print(f"[WARN] SpellCastSystem: handler '{handler_name}' não encontrado")

    def _launch_fireball(self, attacker_id: int, target_id: int) -> None:
        pos = self.world.get_component(attacker_id, Position)
        target_pos = self.world.get_component(target_id, Position)
        if not pos or not target_pos:
            return
        target_cs = self.world.get_component(target_id, CombatStats)
        if target_cs and target_cs.current_hp <= 0:
            return
        from content.skill_config import SKILL_CATALOG as _SC_bdf
        _bdf = _SC_bdf.get("bola_de_fogo", {})
        proj = self.world.create_entity()
        self.world.add_component(proj, Position(pos.x, pos.y, pos.x, pos.y))
        self.world.add_component(proj, PlayerProjectile(
            spell_id="bola_de_fogo",
            attacker_id=attacker_id,
            target_id=target_id,
            speed=300.0,
            dmg_weapon_pct=_bdf.get("dmg_weapon_pct", 0.5),
            dmg_sp_coeff=_bdf.get("dmg_sp_coeff",   1.0),
            color=(255, 120, 20),
            target_last_x=target_pos.x,
            target_last_y=target_pos.y,
        ))
        LOG.add("Bola de Fogo!", (255, 160, 60))
        from engine.components import PlayerControlled as _PC_bdf
        _lpos_bdf = None
        for _, _, _lp_bdf in self.world.get_entities_with(_PC_bdf, Position):
            _lpos_bdf = (_lp_bdf.x, _lp_bdf.y)
            break
        if _lpos_bdf:
            SOUNDS.play_spell_at("bola_de_fogo", "launch", pos.x, pos.y, _lpos_bdf[0], _lpos_bdf[1])
        else:
            SOUNDS.play_spell("bola_de_fogo", "launch")

    def _apply_calcinar(self, attacker_id: int, target_id: int) -> None:
        """Calcinar — hit instantâneo: 50 + 25% SP. Escola fogo. Pode ser castado em movimento.

        Online visual_only: target não tem CombatStats no cliente (mob remoto ou player remoto).
        Dano é calculado no servidor; aqui apenas tocamos o som de impacto.
        """
        from ui.combat_log import LOG
        attacker_cs = self.world.get_component(attacker_id, CombatStats)
        sp = attacker_cs.spell_power if attacker_cs else 0
        target_cs = self.world.get_component(target_id, CombatStats)
        if target_cs and target_cs.current_hp > 0:
            # Offline: aplica dano localmente
            dmg = max(1, 50 + int(sp * 0.25))
            _apply_magic_damage(attacker_id, target_id, dmg, self.world)
            LOG.add(f"Calcinar! {dmg} de dano.", (255, 140, 40))
        # Som toca sempre: offline (dano local) e online visual_only (servidor aplica dano)
        SOUNDS.play_spell("calcinar", "impact")

    def _apply_nova_congelante(self, attacker_id: int, target_id: int = -1) -> None:
        """AOE: raiz 5s + dano 50% SP em todos os inimigos a 3 tiles."""
        from ui.systems import apply_effect
        from ui.floating_text import FLT
        from ui.combat_log import LOG
        from engine.components import Enemy, TileMovement as _TM, StatusEffects
        from engine.utils import chebyshev

        attacker_tm = self.world.get_component(attacker_id, _TM)
        attacker_cs = self.world.get_component(attacker_id, CombatStats)
        if not attacker_tm:
            return

        pl_x, pl_y = attacker_tm.current_tile_x, attacker_tm.current_tile_y
        sp = attacker_cs.spell_power if attacker_cs else 0

        hit = 0
        for eid, _, etm, ecs in self.world.get_entities_with(
                Enemy, _TM, CombatStats):
            if ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > 3:
                continue
            dmg = max(1, int(sp * 0.5))
            _apply_magic_damage(attacker_id, eid, dmg, self.world)
            apply_effect(self.world, eid, "root", 5.0)
            hit += 1

        # Som toca sempre ao completar o cast — AoE não depende de acertar alvo
        SOUNDS.play_spell("nova_congelante", "impact")
        if hit > 0:
            LOG.add(f"Nova Congelante — {hit} inimigo(s) enraizados.", (100, 180, 255))
        else:
            LOG.add("Nova Congelante — nenhum inimigo no raio.", (100, 180, 255))

    # ── Tiro Múltiplo ────────────────────────────────────────────────────────

    def _capture_tiro_multiplo_dir(self, attacker_id: int) -> None:
        """Online-only: captura direção atual do mouse e enfileira CAST_DIR_UPDATE.
        Chamado quando o cast visual_only completa — sem checar inimigos locais."""
        import math, pygame
        from engine.components import Position as _Pos, Camera as _Cam
        att_pos = self.world.get_component(attacker_id, _Pos)
        if not att_pos:
            return
        _sw, _sh = self.world_surf.get_size()
        _cam_x, _cam_y = 0.0, 0.0
        for _, _cp, _ in self.world.get_entities_with(_Pos, _Cam):
            _cam_x = _cp.x - _sw / 2
            _cam_y = _cp.y - _sh / 2
            break
        _surf_scale = (_sw / max(1, self.hud_surf.get_width())
                       if self.world_surf and self.hud_surf else 1.0)
        _sx, _sy = pygame.mouse.get_pos()
        _dx = _sx * _surf_scale - (att_pos.x - _cam_x)
        _dy = _sy * _surf_scale - (att_pos.y - _cam_y)
        _dlen = math.sqrt(_dx * _dx + _dy * _dy) or 1.0
        self.pending_dir_updates.append(("tiro_multiplo", _dx / _dlen, _dy / _dlen))

    def _apply_tiro_multiplo(self, attacker_id: int, target_id: int) -> None:
        """Dispara flechas em cone de 90° na direção do mouse."""
        import math, pygame
        from engine.components import (Position as _Pos, PlayerProjectile as _PP,
                                Equipment as _EQ, Enemy as _Emy, Camera as _Cam,
                                CombatStats as _CS2, Visible as _Vis)
        from engine.stat_fns import enter_combat as _ec2
        from engine.components import CombatState as _CS3
        from content.skill_config import SKILL_CATALOG as _SC

        att_pos = self.world.get_component(attacker_id, _Pos)
        att_cs  = self.world.get_component(attacker_id, CombatStats)
        equip   = self.world.get_component(attacker_id, _EQ)
        if not att_pos or not att_cs:
            return

        quiver  = equip.slots.get("offhand") if equip else None
        if not quiver or quiver.item_type != "quiver" or quiver.arrow_count < 1:
            return

        _params      = _SC.get("tiro_multiplo", {}).get("params", {})
        _ap_mult     = _params.get("damage_multiplier", 3.0)
        _half_angle  = _params.get("cone_half_angle", 45.0)
        _range_tiles = _params.get("range_tiles",     12)
        _range_px    = _range_tiles * TILE_SIZE
        _cos_thresh  = math.cos(math.radians(_half_angle))

        max_targets = getattr(att_cs, "tiro_multiplo_targets", 2)

        # Direção do cone: do arqueiro ao mouse (espaço da zoom_surf, como PirofagiaSystem)
        _sw, _sh = self.world_surf.get_size()
        _cam_x, _cam_y = 0.0, 0.0
        for _, _cp, _ in self.world.get_entities_with(_Pos, _Cam):
            _cam_x = _cp.x - _sw / 2
            _cam_y = _cp.y - _sh / 2
            break
        _surf_scale = (_sw / max(1, self.hud_surf.get_width())
                       if self.world_surf and self.hud_surf else 1.0)
        _sx, _sy = pygame.mouse.get_pos()
        _mx = _sx * _surf_scale   # mouse em coords de zoom_surf
        _my = _sy * _surf_scale
        _px = att_pos.x - _cam_x  # player em coords de zoom_surf
        _py = att_pos.y - _cam_y
        _dx = _mx - _px           # direção screen-space == direção world-space
        _dy = _my - _py
        _dlen = math.sqrt(_dx * _dx + _dy * _dy) or 1.0
        _dir_x, _dir_y = _dx / _dlen, _dy / _dlen

        # Coleta inimigos no cone (visíveis, dentro do range)
        _targets_in_cone = []
        for _eid, _epos, _, _, _ecs in self.world.get_entities_with(_Pos, _Emy, _Vis, CombatStats):
            if not _ecs or _ecs.current_hp <= 0:
                continue
            _ex = _epos.x - att_pos.x
            _ey = _epos.y - att_pos.y
            _edist = math.sqrt(_ex * _ex + _ey * _ey)
            if _edist > _range_px or _edist < 1:
                continue
            _dot = (_ex / _edist) * _dir_x + (_ey / _edist) * _dir_y
            if _dot >= _cos_thresh:
                _targets_in_cone.append((_edist, _eid, _epos))

        if not _targets_in_cone:
            LOG.add("Nenhum alvo no cone.", (180, 180, 180))
            return

        # Ordena por distância; limita ao máximo de alvos (99 = ilimitado prático)
        _targets_in_cone.sort(key=lambda t: t[0])
        if max_targets < 99:
            _targets_in_cone = _targets_in_cone[:max_targets]

        # Limita pelo número de flechas disponíveis
        _arrows_available = quiver.arrow_count
        _targets_in_cone  = _targets_in_cone[:_arrows_available]

        dmg_min  = getattr(quiver, "damage_min", 0)
        dmg_max  = getattr(quiver, "damage_max", 0)
        extra_ap = int(att_cs.attack_power * (_ap_mult - 1.0))

        for _delay_idx, (_edist, _eid, _epos) in enumerate(_targets_in_cone):
            proj_id = self.world.create_entity()
            self.world.add_component(proj_id, _Pos(
                x=att_pos.x, y=att_pos.y, prev_x=att_pos.x, prev_y=att_pos.y))
            self.world.add_component(proj_id, _PP(
                spell_id        = "arrow",
                attacker_id     = attacker_id,
                target_id       = _eid,
                speed           = 700.0,
                dmg_weapon_pct  = 1.0,
                dmg_sp_coeff    = 0.0,
                color           = (150, 210, 255),   # azul claro — tiro múltiplo
                damage_type     = "physical",
                arrow_dmg_min   = dmg_min,
                arrow_dmg_max   = dmg_max,
                launch_delay    = _delay_idx * 0.06, # leve escalonamento visual
                ap_multiplier   = _ap_mult,
                guaranteed_hit  = True,
            ))

        quiver.arrow_count -= len(_targets_in_cone)
        attacker_state = self.world.get_component(attacker_id, _CS3)
        if attacker_state:
            _ec2(attacker_state)

        n = len(_targets_in_cone)
        SOUNDS.play_random(["arrow_release_1", "arrow_release_2"], channel_group=(10, 11))
        LOG.add(f"Tiro Múltiplo! {n} flechas lançadas.", (150, 220, 255))

    # ── Tiro Repulsivo ────────────────────────────────────────────────────────

    def _apply_tiro_repulsivo(self, attacker_id: int, target_id: int) -> None:
        """Dispara a flecha de Tiro Repulsivo. O knockback acontece em _on_hit."""
        from engine.components import Position as _Pos, PlayerProjectile as _PP, Equipment as _EQ
        from content.skill_config import SKILL_CATALOG as _SC

        att_pos = self.world.get_component(attacker_id, _Pos)
        tgt_cs  = self.world.get_component(target_id, CombatStats)
        if not att_pos:
            return
        # Online: mob não tem CombatStats local — skip only if definitely dead
        if tgt_cs is not None and tgt_cs.current_hp <= 0:
            return

        # Verifica e consome 1 flecha da aljava
        equip  = self.world.get_component(attacker_id, _EQ)
        quiver = equip.slots.get("offhand") if equip else None
        if not quiver or quiver.item_type != "quiver" or quiver.arrow_count < 1:
            return
        quiver.arrow_count -= 1

        _params  = _SC.get("tiro_repulsivo", {}).get("params", {})
        _ap_mult = _params.get("damage_multiplier", 1.5)

        att_cs   = self.world.get_component(attacker_id, CombatStats)
        extra_ap = int(att_cs.attack_power * (_ap_mult - 1.0)) if att_cs else 0

        proj_id = self.world.create_entity()
        self.world.add_component(proj_id, _Pos(
            x=att_pos.x, y=att_pos.y, prev_x=att_pos.x, prev_y=att_pos.y))
        from engine.components import RemoteEntityMeta as _REM_tr
        _tr_meta   = self.world.get_component(target_id, _REM_tr)
        _tr_srv_id = _tr_meta.server_eid if _tr_meta else -1
        self.world.add_component(proj_id, _PP(
            spell_id         = "tiro_repulsivo",
            attacker_id      = attacker_id,
            target_id        = target_id,
            speed            = 800.0,
            dmg_weapon_pct   = 1.0,
            dmg_sp_coeff     = 0.0,
            color            = (80, 160, 255),
            damage_type      = "physical",
            ap_multiplier    = 1.5,
            guaranteed_hit   = True,
            target_server_id = _tr_srv_id,
        ))
        SOUNDS.play_random(["arrow_release_1", "arrow_release_2"], channel_group=(10, 11))

    # ── Canção de Ninar ───────────────────────────────────────────────────────

    def _apply_cancao_ninar_complete(self, attacker_id: int, target_id: int) -> None:
        """Cast completo: o sono continua normalmente (já aplicado no início do canal).
        Limpa lullaby_targets — slow será aplicado via on_expire_effect quando o sono acabar.
        """
        from engine.components import CharacterStats
        char_stats = self.world.get_component(attacker_id, CharacterStats)
        if char_stats:
            char_stats.lullaby_targets.clear()
        LOG.add("Canção de Ninar! Inimigos dormindo por 8s.", (160, 200, 255))

    def _cancel_cancao_ninar(self, attacker_id: int) -> None:
        """Canal cancelado: acorda todos os alvos adormecidos pela canção."""
        from engine.components import CharacterStats, StatusEffects
        char_stats = self.world.get_component(attacker_id, CharacterStats)
        if not char_stats:
            return
        for tid in char_stats.lullaby_targets:
            sfx = self.world.get_component(tid, StatusEffects)
            if sfx and sfx.has("sleep"):
                # Remove sleep SEM aplicar o slow (canal foi cancelado)
                eff = sfx.get("sleep")
                if eff:
                    eff.on_expire_effect = ""   # cancela o slow encadeado
                sfx.remove("sleep")
        char_stats.lullaby_targets.clear()
        LOG.add("Canção de Ninar interrompida — alvos acordaram.", (220, 180, 80))

    # ── Handler genérico de skill shot (flecha) ───────────────────────────────

    def _apply_arrow_skill(self, attacker_id: int, target_id: int) -> None:
        """Handler genérico para skills de flecha — lê params do SKILL_CATALOG.

        Campos suportados em params{}:
          damage_multiplier  float  — coeficiente do AP (padrão 1.0); dano final
                                      = arma + AP×(mult + 0.01×skill level do arco)
          guaranteed_hit     bool   — ignora miss/dodge/parry (padrão False)
          on_hit_effect      str    — efeito ao acertar (ex: "slow")
          on_hit_duration    float  — duração do efeito
          on_hit_magnitude   float  — magnitude do efeito
          arrow_count        int    — número de flechas (padrão 1)
          arrow_delay        float  — delay entre flechas (padrão 0)
        """
        from engine.components import Equipment, Position as _Pos, PlayerProjectile as _PP
        from engine.stat_fns import enter_combat
        from engine.components import CombatState
        from content.skill_config import SKILL_CATALOG as _SC

        equip   = self.world.get_component(attacker_id, Equipment)
        pos     = self.world.get_component(attacker_id, _Pos)
        tgt_pos = self.world.get_component(target_id,   _Pos)
        tgt_cs  = self.world.get_component(target_id,   CombatStats)
        if not equip or not pos or not tgt_pos:
            return
        # Offline: skip se alvo já morreu. Online: mob não tem CombatStats — projétil é visual.
        if tgt_cs is not None and tgt_cs.current_hp <= 0:
            return

        quiver = equip.slots.get("offhand")
        if not quiver or quiver.item_type != "quiver":
            return

        params   = _SC.get(self._current_spell_id, {}).get("params", {})
        ap_mult  = params.get("damage_multiplier", 1.0)
        g_hit    = params.get("guaranteed_hit",   False)
        effect   = params.get("on_hit_effect",    "")
        eff_dur  = params.get("on_hit_duration",  0.0)
        eff_mag  = params.get("on_hit_magnitude", 0.0)
        n_arrows = params.get("arrow_count",      1)
        delay    = params.get("arrow_delay",      0.0)

        if quiver.arrow_count < n_arrows:
            LOG.add("Flechas insuficientes.", (220, 80, 80))
            return

        dmg_min = getattr(quiver, "damage_min", 0)
        dmg_max = getattr(quiver, "damage_max", 0)

        from engine.components import RemoteEntityMeta as _REM_as
        _as_meta   = self.world.get_component(target_id, _REM_as)
        _as_srv_id = _as_meta.server_eid if _as_meta else -1
        for i in range(n_arrows):
            proj_id = self.world.create_entity()
            self.world.add_component(proj_id, _Pos(
                x=pos.x, y=pos.y, prev_x=pos.x, prev_y=pos.y))
            # Primeira flecha da skill: spell_id correto + target_server_id para PROJECTILE_HIT_CS
            _as_sid = self._current_spell_id if i == 0 else "arrow"
            _as_tsid = _as_srv_id if i == 0 else -1
            self.world.add_component(proj_id, _PP(
                spell_id         = _as_sid,
                attacker_id      = attacker_id,
                target_id        = target_id,
                speed            = 700.0,
                dmg_weapon_pct   = 1.0,
                dmg_sp_coeff     = 0.0,
                color            = (101, 67, 33),
                damage_type      = "physical",
                arrow_dmg_min    = dmg_min,
                arrow_dmg_max    = dmg_max,
                launch_delay     = i * delay,
                ap_multiplier    = ap_mult,
                guaranteed_hit   = g_hit,
                on_hit_effect    = effect,
                on_hit_duration  = eff_dur,
                on_hit_magnitude = eff_mag,
                target_server_id = _as_tsid,
            ))

        quiver.arrow_count -= n_arrows
        attacker_state = self.world.get_component(attacker_id, CombatState)
        if attacker_state:
            enter_combat(attacker_state)

        SOUNDS.play_random(["arrow_release_1", "arrow_release_2"], channel_group=(10, 11))

    def _apply_flecha_reiterada(self, attacker_id: int, target_id: int) -> None:
        """Dispara 2 flechas em sequência ao completar o cast."""
        from engine.components import Equipment, Position as _Pos, PlayerProjectile as _PP
        from engine.stat_fns import enter_combat
        from engine.components import CombatState

        equip  = self.world.get_component(attacker_id, Equipment)
        pos    = self.world.get_component(attacker_id, _Pos)
        tgt_cs = self.world.get_component(target_id, CombatStats)
        if not equip or not pos or not tgt_cs or tgt_cs.current_hp <= 0:
            return

        quiver = equip.slots.get("offhand")
        if not quiver or quiver.item_type != "quiver":
            LOG.add("Precisa de uma aljava equipada.", (220, 80, 80))
            return

        dmg_min = getattr(quiver, "damage_min", 0)
        dmg_max = getattr(quiver, "damage_max", 0)

        from content.skill_config import SKILL_CATALOG as _SC
        _params  = _SC.get("flecha_reiterada", {}).get("params", {})
        _ap_mult = _params.get("damage_multiplier", 2.0)
        _delay   = _params.get("arrow_delay",   0.25)

        # Talento Sequência Final: 3ª flecha se alvo abaixo do threshold de HP
        attacker_cs = self.world.get_component(attacker_id, CombatStats)
        _threshold  = getattr(attacker_cs, "flecha_reiterada_hp_threshold", 0.0) if attacker_cs else 0.0
        hp_ratio    = tgt_cs.current_hp / max(1, tgt_cs.max_hp)
        extra_arrow = _threshold > 0 and hp_ratio < _threshold

        n_arrows = 3 if extra_arrow else 2
        if quiver.arrow_count < n_arrows:
            n_arrows = quiver.arrow_count   # dispara com o que tiver (mínimo 1)
        if n_arrows == 0:
            LOG.add("Flechas insuficientes para Flecha Reiterada.", (220, 80, 80))
            return

        # Speeds e delays para cada flecha
        _schedule = [(700.0, 0.0), (640.0, _delay)]
        if extra_arrow:
            _schedule.append((580.0, _delay * 2))

        for i, (speed, launch_delay) in enumerate(_schedule[:n_arrows]):
            proj_id = self.world.create_entity()
            self.world.add_component(proj_id, _Pos(
                x=pos.x, y=pos.y, prev_x=pos.x, prev_y=pos.y))
            self.world.add_component(proj_id, _PP(
                spell_id="arrow",
                attacker_id=attacker_id,
                target_id=target_id,
                speed=speed,
                dmg_weapon_pct=1.0,
                dmg_sp_coeff=0.0,
                color=(101, 67, 33),
                damage_type="physical",
                arrow_dmg_min=dmg_min,
                arrow_dmg_max=dmg_max,
                launch_delay=launch_delay,
                ap_multiplier=_ap_mult,
                guaranteed_hit=True,
            ))

        quiver.arrow_count -= n_arrows
        attacker_state = self.world.get_component(attacker_id, CombatState)
        if attacker_state:
            enter_combat(attacker_state)

        SOUNDS.play_random(["arrow_release_1", "arrow_release_2"], channel_group=(10, 11))
        suffix = " (Sequência Final!)" if extra_arrow else ""
        LOG.add(f"Flecha Reiterada! {n_arrows} flechas lançadas.{suffix}", (180, 220, 255))

    def _apply_recarregar(self, attacker_id: int, target_id: int) -> None:
        """Recarrega a aljava com flechas do inventário."""
        from engine.components import Equipment, Inventory
        from ui.combat_log import LOG

        equip = self.world.get_component(attacker_id, Equipment)
        inv   = self.world.get_component(attacker_id, Inventory)
        if not equip or not inv:
            return

        quiver = equip.slots.get("offhand")
        if not quiver or quiver.item_type != "quiver":
            LOG.add("Precisa de uma aljava equipada para recarregar.", (220, 180, 80))
            return

        # Primeiro ammo disponível define o tipo a carregar
        _first = next(
            (it for it in inv.items if it is not None and it.item_type == "ammo" and it.stack > 0),
            None
        )
        if not _first:
            LOG.add("Não há flechas disponíveis para recarregar.", (220, 80, 80))
            return
        _selected = _first.item_id

        # Troca de tipo: devolve flechas antigas à bag antes de recarregar
        old_type = quiver.subtype
        if old_type and old_type != _selected and quiver.arrow_count > 0:
            returned = quiver.arrow_count
            for it in inv.items:
                if it is not None and it.item_id == old_type and it.stack < it.max_stack:
                    give = min(returned, it.max_stack - it.stack)
                    it.stack += give
                    returned -= give
                    if returned <= 0:
                        break
            if returned > 0 and len(inv.items) < inv.max_slots:
                import content.item_table as _ItemTableRec
                _old_factory = _ItemTableRec.ITEMS.get(old_type)
                if _old_factory:
                    _ret = _old_factory()
                else:
                    # old_type não bate com nenhum item_id conhecido
                    # (aljava de save antigo, pré-migração) — fallback
                    # inerte, mesmo padrão de server/world_server.py::
                    # _reconstruct_item pra item_id desconhecido.
                    from engine.components import Item as _Item
                    _ret = _Item(
                        name=old_type, item_type="ammo", slot="",
                        rarity="common", value=1,
                        damage_min=quiver.damage_min,
                        damage_max=quiver.damage_max,
                        max_stack=1000,
                    )
                _ret.stack = returned
                inv.items.append(_ret)
            quiver.arrow_count = 0
            # Troca de tipo já mutou bag/aljava — persiste mesmo que a recarga
            # em si não complete abaixo (ex: "Aljava já está cheia").
            if self._on_inventory_changed:
                self._on_inventory_changed()

        needed = quiver.max_arrows - quiver.arrow_count
        if needed <= 0:
            LOG.add("Aljava já está cheia.", (180, 200, 100))
            return

        arrow_stacks = [(i, it) for i, it in enumerate(inv.items)
                        if it is not None and it.item_type == "ammo" and it.item_id == _selected]
        total_avail = sum(it.stack for _, it in arrow_stacks)


        if total_avail == 0:
            LOG.add("Não há flechas disponíveis para recarregar.", (220, 80, 80))
            return

        to_transfer = min(needed, total_avail)
        remaining   = to_transfer

        # Subtrai das stacks da bag em ordem reversa (permite remoção segura por índice)
        indices_to_remove = []
        for idx, it in reversed(arrow_stacks):
            if remaining <= 0:
                break
            take = min(remaining, it.stack)
            it.stack  -= take
            remaining -= take
            if it.stack <= 0:
                indices_to_remove.append(idx)

        # Remove slots esgotados sem deixar None na lista
        for idx in sorted(indices_to_remove, reverse=True):
            del inv.items[idx]

        quiver.arrow_count += to_transfer
        # Copia o bônus de dano e memoriza o tipo carregado
        if arrow_stacks:
            loaded_arrow = arrow_stacks[0][1]
            quiver.damage_min = loaded_arrow.damage_min
            quiver.damage_max = loaded_arrow.damage_max
            quiver.subtype    = loaded_arrow.item_id   # identifica o tipo na aljava
            bonus_str = (f" (+{quiver.damage_min}–{quiver.damage_max} dmg)"
                     if quiver.damage_max > 0 else "")
        LOG.add(f"Aljava recarregada: {quiver.arrow_count}/{quiver.max_arrows}{bonus_str}", (180, 220, 100))
        if self._on_inventory_changed:
            self._on_inventory_changed()

    def _apply_polymorph(self, attacker_id: int, target_id: int) -> None:
        from ui.systems import apply_effect
        from ui.floating_text import FLT
        from ui.combat_log import LOG
        target_cs = self.world.get_component(target_id, CombatStats)
        if target_cs and target_cs.current_hp > 0:
            # Offline: aplica efeito localmente
            regen_per_tick = max(1, int(target_cs.max_hp * 0.10))
            apply_effect(self.world, target_id, "polymorph",
                         duration=6.0, magnitude=regen_per_tick)
        # Garante que o atacante NÃO retoma auto-ataque após o cast
        attacker_state = self.world.get_component(attacker_id, CombatState)
        if attacker_state:
            attacker_state.is_pursuing = False
        pos = self.world.get_component(target_id, Position)
        if pos:
            FLT.add("Polimorfizado!", pos.x, pos.y, (160, 80, 200),
                    size="normal", target_id=target_id)
        LOG.add("Polimorfia!", (160, 80, 200))
        SOUNDS.play_spell("polimorfia", "launch")

    # ── Render: mira de Tiro Múltiplo ────────────────────────────────────────

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for entity_id, spell_cast, _ in self.world.get_entities_with(
                SpellCast, PlayerControlled):
            if spell_cast.spell_id != "tiro_multiplo":
                continue
            pos = self.world.get_component(entity_id, Position)
            if pos is None or self.world_surf is None:
                continue

            # Lazy-load sem escala — tamanho real do arquivo
            if self._tiro_aim_img is None:
                try:
                    self._tiro_aim_img = pygame.image.load(
                        "assets/effects/skill_tiro_multiplo.png").convert_alpha()
                except Exception:
                    return

            img = self._tiro_aim_img
            iW, iH = img.get_size()

            # Mouse em coords de zoom_surf
            _surf_sc = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
            _sx, _sy = pygame.mouse.get_pos()
            _mx = _sx * _surf_sc
            _my = _sy * _surf_sc

            # Player em coords de tela
            px = pos.x - cam_x
            py = pos.y - cam_y

            # Ângulo player → mouse. Direção natural da imagem = 45° (TL → BR)
            angle_deg  = math.degrees(math.atan2(_my - py, _mx - px))
            pygame_rot = 45.0 - angle_deg   # rotação CCW que alinha BR com o mouse

            rotated = pygame.transform.rotate(img, pygame_rot)
            rW, rH  = rotated.get_size()

            # Calcula blit para manter o TL original em (px, py).
            # Matriz CCW em Y-down: x'= x·cos + y·sin, y'= -x·sin + y·cos
            # TL offset do centro original: (-iW/2, -iH/2)
            alpha   = math.radians(pygame_rot)
            ca, sa  = math.cos(alpha), math.sin(alpha)
            tl_rx   = (-iW / 2) * ca + (-iH / 2) * sa
            tl_ry   = -(-iW / 2) * sa + (-iH / 2) * ca
            blit_x  = int(px - (rW / 2 + tl_rx))
            blit_y  = int(py - (rH / 2 + tl_ry))

            self.world_surf.blit(rotated, (blit_x, blit_y))


# ---------------------------------------------------------------------------
# PlayerProjectileSystem
# ---------------------------------------------------------------------------

class PlayerProjectileSystem(System):
    """Move projéteis do jogador e aplica dano ao acertar o alvo."""

    HIT_THRESHOLD  = 12.0
    TRAIL_MAX_LEN  = 7    # posições guardadas no rastro de flecha
    ARROW_LINE_LEN = 20   # comprimento visual da linha da flecha (pixels)

    FIREBALL_FPS    = 12          # frames por segundo da animação
    FIREBALL_FRAMES = 10          # número de frames no sheet
    FIREBALL_SCALE  = 2           # multiplicador de tamanho do sprite

    def __init__(self, world: World, screen: pygame.Surface, player_entity: int = -1):
        self.world  = world
        self.world_surf = screen
        self.hud_surf   = screen
        self.player_entity = player_entity
        # rastro de flechas: proj_id → [(x, y), ...]
        self._arrow_trails: dict[int, list] = {}
        # Knockbacks pendentes: (attacker_id, target_id, timer_restante)
        self._pending_knockbacks: list[tuple[int, int, float]] = []
        # Hits de projéteis em mobs online — game.py envia PROJECTILE_HIT_CS ao servidor
        self.pending_proj_hits: list[dict] = []
        # Outcome/dano do auto-attack de flecha (mob/torre, player local ou
        # remoto) não fica mais numa fila por ALVO aqui — cada flecha carrega
        # o PRÓPRIO resultado em `PlayerProjectile.deferred_result` (engine/
        # components.py), prendido no momento em que ela nasce (client/
        # remote_entity_handlers.py::_spawn_archer_auto_arrow). Fila por alvo
        # entregava o resultado errado quando 2+ flechas convergiam pro mesmo
        # alvo fora da ordem em que foram disparadas (bug real, ver
        # PROBLEMAS_ARQUITETURA.md §44). HP em si NUNCA fica nesse resultado
        # diferido — é aplicado direto na confirmação do servidor, igual
        # magia/corpo-a-corpo (mesmo §44, correção seguinte: aplicar o HP só
        # no impacto visual da flecha podia sobrescrever com um valor
        # desatualizado um HP mais novo aplicado por OUTRO ataque enquanto
        # ela ainda voava).
        # Animação da Bola de Fogo
        self._fireball_frames: "list[pygame.Surface] | None" = None
        self._fireball_anim:   dict[int, float] = {}   # proj_id → elapsed

    def _load_fireball_frames(self) -> None:
        """Carrega e fatia o spritesheet da Bola de Fogo (lazy, uma vez)."""
        try:
            raw = pygame.image.load(
                "assets/effects/skill_bola_de_fogo.png").convert_alpha()
            n  = self.FIREBALL_FRAMES
            fw = raw.get_width() // n
            fh = raw.get_height()
            frames = []
            for i in range(n):
                frame = raw.subsurface((i * fw, 0, fw, fh))
                if self.FIREBALL_SCALE != 1:
                    frame = pygame.transform.scale(
                        frame, (fw * self.FIREBALL_SCALE, fh * self.FIREBALL_SCALE))
                frames.append(frame)
            self._fireball_frames = frames
        except Exception as e:
            print(f"[WARN] Fireball sheet: {e}")
            self._fireball_frames = []

    def _player_world_pos(self) -> "tuple[float, float] | None":
        from engine.components import TileMovement as _TM
        tm = self.world.get_component(self.player_entity, _TM)
        if tm:
            return tm.current_tile_x * TILE_SIZE + TILE_SIZE / 2, \
                   tm.current_tile_y * TILE_SIZE + TILE_SIZE / 2
        pos = self.world.get_component(self.player_entity, Position)
        if pos:
            return pos.x, pos.y
        return None

    def update(self, events=None, dt: float = 0) -> None:
        # Processa knockbacks com delay
        if self._pending_knockbacks:
            still = []
            for att_id, tgt_id, timer in self._pending_knockbacks:
                timer -= dt
                if timer <= 0:
                    self._apply_knockback(att_id, tgt_id)
                else:
                    still.append((att_id, tgt_id, timer))
            self._pending_knockbacks = still

        to_remove = []
        for proj_id, proj_pos, proj in self.world.get_entities_with(
                Position, PlayerProjectile):

            # Incrementa timer de animação para projéteis com spritesheet
            if proj.spell_id == "bola_de_fogo":
                self._fireball_anim[proj_id] = self._fireball_anim.get(proj_id, 0.0) + dt

            # ── Delay de lançamento (ex: segunda flecha de Flecha Reiterada) ──
            if proj.launch_delay > 0:
                proj.launch_delay -= dt
                continue   # ainda não começou a voar

            # ── Flecha em modo de erro: voa até o ponto desviado e some ────
            if proj.is_miss:
                dx   = proj.miss_end_x - proj_pos.x
                dy   = proj.miss_end_y - proj_pos.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist <= self.HIT_THRESHOLD:
                    to_remove.append(proj_id)
                else:
                    trail = self._arrow_trails.setdefault(proj_id, [])
                    trail.append((proj_pos.x, proj_pos.y))
                    if len(trail) > self.TRAIL_MAX_LEN:
                        trail.pop(0)
                    step = proj.speed * dt
                    proj_pos.x += dx / dist * step
                    proj_pos.y += dy / dist * step
                continue

            target_pos = self.world.get_component(proj.target_id, Position)
            target_cs  = self.world.get_component(proj.target_id, CombatStats)

            # Offline: remove projétil se alvo morreu
            if target_cs and target_cs.current_hp <= 0:
                to_remove.append(proj_id)
                continue

            # Online: alvo despawnou mas projétil continua voando até a última posição conhecida
            if target_pos is None:
                fx, fy = proj.target_last_x, proj.target_last_y
                if fx == 0.0 and fy == 0.0:
                    to_remove.append(proj_id)
                    continue
                dx   = fx - proj_pos.x
                dy   = fy - proj_pos.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist <= self.HIT_THRESHOLD:
                    self._on_hit(proj, proj_pos.x, proj_pos.y)
                    to_remove.append(proj_id)
                else:
                    step = proj.speed * dt
                    proj_pos.x += dx / dist * step
                    proj_pos.y += dy / dist * step
                continue

            # Atualiza última posição conhecida (usada se alvo despawnar durante o voo)
            proj.target_last_x = target_pos.x
            proj.target_last_y = target_pos.y

            # LOS check: bloqueia projétil se há parede entre ele e o alvo.
            # EXCLUI flecha de auto-attack (spell_id=="arrow"): desde que o
            # servidor passou a validar LOS em _server_apply_ranged_physical
            # (ver ARQUITETURA_ONLINE.md), um tiro bloqueado já chega como
            # outcome="miss" pelo canal normal — deixar cair no bloco de
            # resolução de outcome logo abaixo dá o redirecionamento visual
            # correto (flecha desvia, "Errou!", consome o deferred_result
            # certo, preso nesta MESMA flecha) em vez de destruir o projétil
            # aqui silenciosamente (bug real antigo: "some sem dano nem
            # projétil aparecer" — o destroy cedo demais nunca liberava o
            # evento pendente, então uma flecha seguinte no mesmo alvo
            # aplicava o outcome errado/velho — problema estrutural da fila
            # por alvo que existia antes, eliminado ao prender o resultado
            # na própria flecha em vez de numa fila compartilhada).
            # Mantido para skills com projétil de verdade (Bola de Fogo etc.)
            # — alvo pode se esconder atrás de parede DURANTE o voo, cenário
            # que o outcome já resolvido no lançamento não cobre.
            if proj.spell_id != "arrow":
                from ui.systems import get_tilemap as _get_tm, EnemyAISystem as _EAIS
                from engine.tileset import TILE_SIZE as _TS
                _tmap = _get_tm()
                if _tmap:
                    _ptx = int(proj_pos.x / _TS)
                    _pty = int(proj_pos.y / _TS)
                    _ttx = int(target_pos.x / _TS)
                    _tty = int(target_pos.y / _TS)
                    if not _EAIS._has_line_of_sight(_tmap, _ptx, _pty, _ttx, _tty):
                        to_remove.append(proj_id)
                        continue

            dx   = target_pos.x - proj_pos.x
            dy   = target_pos.y - proj_pos.y
            dist = math.sqrt(dx * dx + dy * dy)

            if dist <= self.HIT_THRESHOLD:
                if proj.damage_type == "physical" and not proj.pre_outcome:
                    attacker_cs = self.world.get_component(proj.attacker_id, CombatStats)
                    if proj.guaranteed_hit:
                        # Skill shot: ignora miss/dodge/parry — só rola crit
                        _crit_r = attacker_cs.crit_rating if attacker_cs else 0.05
                        proj.pre_outcome = "crit" if random.random() < _crit_r else "hit"
                    else:
                        # Auto-attack normal: rola outcome completo.
                        if target_cs is None or attacker_cs is None:
                            # Online: mob/player remoto sem CombatStats (ou atacante é
                            # player remoto sem CombatStats local) — usa outcome
                            # pré-computado pelo servidor, prendido nesta MESMA flecha
                            # (PlayerProjectile.deferred_result — nunca uma fila
                            # compartilhada por alvo, ver PROBLEMAS_ARQUITETURA.md §44).
                            # Se ainda não chegou, assume "hit" para não travar o projétil.
                            if proj.deferred_result:
                                outcome = proj.deferred_result.get("outcome", "hit")
                            else:
                                outcome = "hit"
                        else:
                            outcome, _ = resolve_attack_outcome(attacker_cs, target_cs, "physical")
                        if outcome in ('miss', 'dodge', 'parry'):
                            # Redireciona flecha para ponto desviado
                            att_pos = self.world.get_component(proj.attacker_id, Position)
                            if att_pos:
                                fx   = target_pos.x - att_pos.x
                                fy   = target_pos.y - att_pos.y
                                flen = math.sqrt(fx*fx + fy*fy) or 1.0
                                nx, ny = fx / flen, fy / flen
                                px, py = -ny, nx
                                side   = random.uniform(-1.8, 1.8) * TILE_SIZE
                                oversh = random.uniform(2, 3) * TILE_SIZE
                                proj.miss_end_x = target_pos.x + nx * oversh + px * side
                                proj.miss_end_y = target_pos.y + ny * oversh + py * side
                            else:
                                proj.miss_end_x = proj_pos.x
                                proj.miss_end_y = proj_pos.y
                            proj.is_miss = True
                            # deferred_result (se houver) fica preso na flecha — ela
                            # ainda vai passar por _on_hit ao chegar no ponto de
                            # desvio, que consome o resultado normalmente.
                            _avoid_txt = {"miss": "Errou!", "dodge": "Desviou!", "parry": "Aparou!"}
                            # Auto-attack: branco; skill: amarelo
                            _is_ability_miss = getattr(proj, "is_ability", False)
                            _avoid_col_miss  = (255, 220, 0) if _is_ability_miss else (220, 220, 220)
                            FLT.add(_avoid_txt[outcome], target_pos.x, target_pos.y,
                                    _avoid_col_miss, "small", target_id=proj.target_id)
                            continue  # não remove — flecha desvia
                        proj.pre_outcome = outcome  # hit/crit/block pré-rolado

                self._on_hit(proj, proj_pos.x, proj_pos.y)
                to_remove.append(proj_id)
            else:
                # Guarda posição ANTES de mover (forma o rastro)
                if proj.damage_type == "physical":
                    trail = self._arrow_trails.setdefault(proj_id, [])
                    trail.append((proj_pos.x, proj_pos.y))
                    if len(trail) > self.TRAIL_MAX_LEN:
                        trail.pop(0)
                step = proj.speed * dt
                proj_pos.x += dx / dist * step
                proj_pos.y += dy / dist * step

        for pid in to_remove:
            self.world.remove_entity(pid)
            self._arrow_trails.pop(pid, None)
            self._fireball_anim.pop(pid, None)

    def _apply_knockback(self, attacker_id: int, target_id: int) -> None:
        """Aplica knockback ao target na direção oposta ao attacker (Tiro Repulsivo)."""
        from ui.systems import apply_effect, is_tile_walkable
        from engine.components import (Position as _Pos, TileMovement as _TM,
                                AIControlled as _AI, Enemy as _Enemy, CombatState as _CS)
        from ui.floating_text import FLT as _FLT
        from content.skill_config import SKILL_CATALOG as _SC
        from engine.stat_fns import enter_combat as _ec

        att_pos = self.world.get_component(attacker_id, _Pos)
        tgt_pos = self.world.get_component(target_id,   _Pos)
        tgt_tm  = self.world.get_component(target_id,   _TM)
        tgt_cs  = self.world.get_component(target_id,   CombatStats)

        if not att_pos or not tgt_pos or not tgt_tm or not tgt_cs or tgt_cs.current_hp <= 0:
            return

        _params   = _SC.get("tiro_repulsivo", {}).get("params", {})
        _kb_tiles = _params.get("knockback_tiles", 5)
        _stun_dur = _params.get("stun_duration",   3.0)

        att_tx = int(att_pos.x / TILE_SIZE)
        att_ty = int(att_pos.y / TILE_SIZE)
        tgt_tx = tgt_tm.current_tile_x
        tgt_ty = tgt_tm.current_tile_y

        dx = tgt_tx - att_tx
        dy = tgt_ty - att_ty
        sx = (1 if dx > 0 else -1) if dx != 0 else 0
        sy = (1 if dy > 0 else -1) if dy != 0 else 0

        cur_x, cur_y = tgt_tx, tgt_ty
        final_x, final_y = cur_x, cur_y
        collision_type  = None
        collided_entity = -1

        for _ in range(_kb_tiles):
            nx, ny = cur_x + sx, cur_y + sy
            _hit_creature = -1
            for _eid, _etm, _ in self.world.get_entities_with(_TM, _Enemy):
                if _eid != target_id and _etm.current_tile_x == nx and _etm.current_tile_y == ny:
                    _hit_creature = _eid
                    break
            if _hit_creature != -1:
                collision_type  = "creature"
                collided_entity = _hit_creature
                break
            if not is_tile_walkable(target_id, nx, ny, cur_x, cur_y):
                collision_type = "wall"
                break
            cur_x, cur_y = nx, ny
            final_x, final_y = cur_x, cur_y

        tiles_moved = max(abs(final_x - tgt_tx), abs(final_y - tgt_ty))
        if tiles_moved > 0:
            tgt_tm.start_pixel_x  = tgt_pos.x
            tgt_tm.start_pixel_y  = tgt_pos.y
            tgt_tm.target_pixel_x = final_x * TILE_SIZE + TILE_SIZE / 2
            tgt_tm.target_pixel_y = final_y * TILE_SIZE + TILE_SIZE / 2
            tgt_tm.target_tile_x  = final_x
            tgt_tm.target_tile_y  = final_y
            tgt_tm.elapsed        = 0.0
            tgt_tm.move_duration  = tiles_moved * 0.06
            tgt_tm.is_moving      = True
            tgt_tm.current_tile_x = final_x
            tgt_tm.current_tile_y = final_y

        if collision_type == "wall":
            apply_effect(self.world, target_id, "stun", _stun_dur)
            _FLT.add("CRASH!", tgt_pos.x, tgt_pos.y, (255, 100, 50), "normal", target_id=target_id)
            LOG.add(f"Tiro Repulsivo: colisão com parede! Stun {_stun_dur:.0f}s.", (120, 200, 255))
        elif collision_type == "creature":
            apply_effect(self.world, target_id,      "stun", _stun_dur)
            apply_effect(self.world, collided_entity, "stun", _stun_dur)
            _col_pos = self.world.get_component(collided_entity, _Pos)
            if _col_pos:
                _FLT.add("CRASH!", _col_pos.x, _col_pos.y, (255, 100, 50), "normal", target_id=collided_entity)
            _col_ai = self.world.get_component(collided_entity, _AI)
            if _col_ai:
                _col_ai.state             = "CHASING"
                _col_ai.aggroed_by_damage = True
                _col_ai.path_recalc_timer = 0.0
            _col_cs = self.world.get_component(collided_entity, _CS)
            if _col_cs:
                _ec(_col_cs)
            LOG.add(f"Tiro Repulsivo: colisão! Ambos stunados {_stun_dur:.0f}s.", (120, 200, 255))
        else:
            LOG.add("Tiro Repulsivo! Alvo repelido.", (120, 200, 255))

    def _on_hit(self, proj: PlayerProjectile,
                impact_x: float = None, impact_y: float = None) -> None:
        attacker_cs = self.world.get_component(proj.attacker_id, CombatStats)
        target_cs   = self.world.get_component(proj.target_id,   CombatStats)

        # Som de impacto de flecha com falloff por distância até o jogador local.
        _lpos_oh = self._player_world_pos()
        def _play_arrow_impact_sound() -> None:
            if impact_x is not None and _lpos_oh is not None:
                SOUNDS.play_random_at(["arrow_impact_1", "arrow_impact_2"],
                                      impact_x, impact_y, _lpos_oh[0], _lpos_oh[1],
                                      base=1.0, channel_group=(12, 13))
            else:
                SOUNDS.play_random(["arrow_impact_1", "arrow_impact_2"], channel_group=(12, 13))

        # target_server_id == -3: projétil cosmético TOTALMENTE silencioso —
        # mob/NPC ranged (client/remote_entity_handlers.py::_spawn_mob_projectile,
        # 21/07/2026). Nenhum som aqui de propósito: os sons desses ataques já
        # são dirigidos por NpcSounds em outros pontos (disparo quando o
        # projétil nasce, impacto na chegada do COMBAT_RESULT via
        # _play_nonplayer_attack_impact) — tocar aqui também dobraria tudo.
        if proj.target_server_id == -3:
            return

        # target_server_id == -2: projétil cosmético (espectador) — apenas som, sem dano/HIT_CS
        if proj.target_server_id == -2:
            if proj.damage_type == "physical":
                _play_arrow_impact_sound()
            else:
                _tgt_pos_cs = self.world.get_component(proj.target_id, Position)
                _lpos_cs = self._player_world_pos()
                if _tgt_pos_cs and _lpos_cs:
                    SOUNDS.play_spell_at(proj.spell_id, "impact",
                                         _tgt_pos_cs.x, _tgt_pos_cs.y, _lpos_cs[0], _lpos_cs[1])
                else:
                    SOUNDS.play_spell(proj.spell_id, "impact")
            return

        # Online: mob/player sem CombatStats local — projétil colidiu, notifica servidor
        # (ou atacante é player remoto sem CombatStats local — alvo é o player local
        # sendo atingido por PvP; dano já foi calculado e confirmado pelo servidor,
        # client não deve recalcular via deal_damage).
        if target_cs is None or attacker_cs is None:
            if proj.damage_type == "physical":
                # Flecha de skill com PROJECTILE_HIT_CS: notifica servidor; FLT chega no is_proj_damage.
                if proj.target_server_id != -1:
                    _play_arrow_impact_sound()
                    self.pending_proj_hits.append({
                        "spell_id":         proj.spell_id,
                        "target_server_id": proj.target_server_id,
                        "attacker_id":      proj.attacker_id,
                    })
                    return

                # Online com guaranteed_hit: flecha cosmética de multi-hit (ex: flecha_reiterada arrow 2+).
                # Dano já foi tratado pelo PROJECTILE_HIT_CS da primeira flecha.
                # Nunca tem deferred_result (nasce sem ele) — só som.
                if target_cs is None and proj.guaranteed_hit:
                    _play_arrow_impact_sound()
                    return

                # Resultado pré-armazenado NESTA flecha pelo COMBAT_RESULT (dict com
                # outcome/damage/is_ability) — client/remote_entity_handlers.py grava
                # direto em PlayerProjectile.deferred_result no momento em que a
                # flecha nasce, nunca numa fila compartilhada por alvo (ver
                # PROBLEMAS_ARQUITETURA.md §44).
                _entry = proj.deferred_result

                # Online sem entry: auto-attack chegou antes do COMBAT_RESULT — só som.
                if _entry is None and target_cs is None:
                    _play_arrow_impact_sound()
                    return

                if _entry is not None:
                    _out_oh  = _entry["outcome"]  if isinstance(_entry, dict) else _entry
                    _dmg_oh  = _entry.get("damage",     0)     if isinstance(_entry, dict) else 0
                    _isab_oh = _entry.get("is_ability",  False) if isinstance(_entry, dict) else False
                    _isplr_oh = _entry.get("is_player_target", False) if isinstance(_entry, dict) else False

                    # FLT na posição atual do alvo; fallback para target_last quando já despawnado
                    _tpos_oh = self.world.get_component(proj.target_id, Position)
                    if _tpos_oh is None and (proj.target_last_x or proj.target_last_y):
                        class _FakePos:
                            x = proj.target_last_x
                            y = proj.target_last_y
                        _tpos_oh = _FakePos()
                    if _tpos_oh:
                        _AVOID_LABELS_OH = {
                            "miss":  "Errou!",
                            "dodge": "Desviou!",
                            "parry": "Aparou!",
                            "block": "Bloqueou!",
                        }
                        if _dmg_oh > 0:
                            if _isplr_oh:
                                # Dano em player (PvP): vermelho, igual ao padrão
                                # de "Player local/remoto foi atacado".
                                FLT.add(str(_dmg_oh), _tpos_oh.x, _tpos_oh.y,
                                        (220, 80, 80), target_id=proj.target_id,
                                        is_crit=(_out_oh == "crit"))
                            elif _out_oh == "crit":
                                _col_oh = (255, 220, 50) if _isab_oh else (255, 255, 255)
                                FLT.add(str(_dmg_oh), _tpos_oh.x, _tpos_oh.y,
                                        _col_oh, target_id=proj.target_id, is_crit=True)
                            elif _out_oh == "block":
                                FLT.add(str(_dmg_oh), _tpos_oh.x, _tpos_oh.y,
                                        (160, 160, 160), "normal", target_id=proj.target_id)
                            else:
                                _col_oh = (255, 220, 0) if _isab_oh else (220, 220, 220)
                                FLT.add(str(_dmg_oh), _tpos_oh.x, _tpos_oh.y,
                                        _col_oh, "normal", target_id=proj.target_id)
                        elif _out_oh in _AVOID_LABELS_OH:
                            _txt_oh  = _AVOID_LABELS_OH[_out_oh]
                            _col_oh  = (255, 220, 0) if _isab_oh else (220, 220, 220)
                            FLT.add(_txt_oh, _tpos_oh.x, _tpos_oh.y,
                                    _col_oh, "small", target_id=proj.target_id)

                    # HP não é mais diferido pro impacto visual (12-13/08/2026,
                    # ver PROBLEMAS_ARQUITETURA.md §44) — já foi aplicado direto
                    # em RemoteEntityMeta.hp assim que o servidor confirmou
                    # (client/remote_entity_handlers.py::_apply_combat_result),
                    # igual magia/corpo-a-corpo. Só FLT/som ficam presos ao
                    # momento em que a flecha chega.

                    # Som de impacto de flecha
                    if _out_oh in ("hit", "crit", "block"):
                        _play_arrow_impact_sound()
            else:
                _tgt_pos_on = self.world.get_component(proj.target_id, Position)
                _lpos_on = self._player_world_pos()
                if _tgt_pos_on and _lpos_on:
                    SOUNDS.play_spell_at(proj.spell_id, "impact",
                                         _tgt_pos_on.x, _tgt_pos_on.y, _lpos_on[0], _lpos_on[1])
                else:
                    SOUNDS.play_spell(proj.spell_id, "impact")
                # Registra hit para game.py enviar PROJECTILE_HIT_CS ao servidor
                if proj.target_server_id != -1:
                    self.pending_proj_hits.append({
                        "spell_id":         proj.spell_id,
                        "target_server_id": proj.target_server_id,
                        "attacker_id":      proj.attacker_id,
                    })
            return

        # Flechas usam o pipeline de dano físico (armor, crit, weapon damage)
        if proj.damage_type == "physical":
            from ui.systems import deal_damage
            from engine.stat_fns import enter_combat
            from engine.components import CombatState
            arrow_bonus = (random.randint(proj.arrow_dmg_min, proj.arrow_dmg_max)
                           if proj.arrow_dmg_max > 0 else 0)
            # ap_multiplier: coeficiente extra de AP somado ao 1× que já vem do
            # deal_damage("physical") — total = arco + AP×coef. Skills de arco
            # (spell_id setado) ganham +0.01×skill level do Arco no coeficiente
            # (fórmula única, espelha _server_apply_ranged_physical); flecha de
            # AUTO-attack (spell_id vazio) fica na fórmula clássica.
            _ap_coef = proj.ap_multiplier - 1.0
            if proj.spell_id:
                from engine.components import Equipment as _EqSkl
                from engine.stats_system import weapon_skill_level as _wsl_arrow
                _eq_skl = self.world.get_component(proj.attacker_id, _EqSkl)
                _bow_skl = _eq_skl.slots.get("mainhand") if _eq_skl else None
                _ap_coef += 0.01 * _wsl_arrow(self.world, proj.attacker_id, _bow_skl)
            if _ap_coef != 0.0:
                _att_cs = self.world.get_component(proj.attacker_id, CombatStats)
                extra_ap = int(_att_cs.attack_power * _ap_coef) if _att_cs else 0
            else:
                extra_ap = 0
            _is_proc = proj.damage_multiplier > 1.0
            _tgt_hp_before = 0
            if _is_proc:
                _tgt_cs_pre = self.world.get_component(proj.target_id, CombatStats)
                _tgt_hp_before = _tgt_cs_pre.current_hp if _tgt_cs_pre else 0

            # Na Mosca: próxima flecha após crit ganha +25% (consome o bônus aqui)
            _att_cs_nm  = self.world.get_component(proj.attacker_id, CombatStats)
            _na_mosca_m = 1.0
            if _att_cs_nm and _att_cs_nm.na_mosca_bonus_active:
                _na_mosca_m = 1.25
                _att_cs_nm.na_mosca_bonus_active = False
                from ui.floating_text import PROC as _PROC_NM
                _PROC_NM.add("Na Mosca! +25%", (255, 200, 50))

            deal_damage(proj.attacker_id, proj.target_id, "physical",
                        base_ability_damage=arrow_bonus + extra_ap,
                        multiplier=proj.damage_multiplier * _na_mosca_m,
                        pre_outcome=proj.pre_outcome)

            # Log de Flechas Despadronizadas: "Flecha: 45 + 22 (Despadronizada!)"
            if _is_proc:
                _tgt_cs_post = self.world.get_component(proj.target_id, CombatStats)
                _hp_now      = _tgt_cs_post.current_hp if _tgt_cs_post else 0
                _total_dmg   = int(_tgt_hp_before - _hp_now)
                _base_dmg    = int(_total_dmg / proj.damage_multiplier)
                _extra_dmg   = _total_dmg - _base_dmg
                if _total_dmg > 0:
                    LOG.add(
                        f"Flecha: {_base_dmg} + {_extra_dmg} (Despadronizada!)",
                        (220, 130, 20)
                    )

            # Na Mosca: crit ativa o bônus para a próxima flecha
            if proj.pre_outcome == "crit" and _att_cs_nm and _att_cs_nm.na_mosca_enabled:
                _att_cs_nm.na_mosca_bonus_active = True
                from ui.floating_text import PROC as _PROC_NM2
                _PROC_NM2.add("Na Mosca!", (255, 220, 50))

            attacker_state = self.world.get_component(proj.attacker_id, CombatState)
            if attacker_state:
                enter_combat(attacker_state)
            # Efeito on-hit configurável (slow, burn, stun, etc.)
            if proj.on_hit_effect:
                from ui.systems import apply_effect
                apply_effect(self.world, proj.target_id, proj.on_hit_effect,
                             proj.on_hit_duration, magnitude=proj.on_hit_magnitude)
            # Knockback do Tiro Repulsivo com delay de 150ms (efeito de impacto)
            if proj.spell_id == "tiro_repulsivo":
                self._pending_knockbacks.append((proj.attacker_id, proj.target_id, 0.10))

            # Reciclagem: conta flechas acertadas neste alvo
            target_cs_hit = self.world.get_component(proj.target_id, CombatStats)
            if target_cs_hit:
                target_cs_hit.arrows_received += 1
            _play_arrow_impact_sound()
            return

        # Resolve miss/crit usando a tabela de ataque mágica
        outcome, _ = resolve_attack_outcome(attacker_cs, target_cs, "magical")
        if outcome == "miss":
            pos = self.world.get_component(proj.target_id, Position)
            if pos:
                FLT.add("Resistiu!", pos.x, pos.y, (180, 100, 255), "small",
                        target_id=proj.target_id)
            return

        is_crit = (outcome == "crit")
        base_dmg = _spell_damage(proj.attacker_id, self.world,
                                 proj.dmg_weapon_pct, proj.dmg_sp_coeff)
        final_dmg = int(base_dmg * CRITICAL_DAMAGE_MULTIPLIER) if is_crit else base_dmg

        # Determina escola da spell pelo campo school do Skill (sem hardcode de nomes)
        _ps = self.world.get_component(proj.attacker_id, PlayerSkills)
        _sk = _ps.skill_by_id(proj.spell_id) if _ps else None
        _spell_school = _sk.school if _sk else ""

        # Piromaníaco: +X% dano em spells de fogo
        if _spell_school == "fogo" and attacker_cs:
            _pyr = getattr(attacker_cs, "pyromania_bonus", 0.0)
            if _pyr > 0:
                final_dmg = int(final_dmg * (1.0 + _pyr))

        # Crematória: +25% dano de fogo em alvos com menos de 20% de vida
        if _spell_school == "fogo" and attacker_cs:
            if getattr(attacker_cs, "crematoria_enabled", False) and target_cs:
                if target_cs.max_hp > 0 and target_cs.current_hp / target_cs.max_hp < 0.20:
                    final_dmg = int(final_dmg * 1.25)

        # Choque Térmico: dobra dano de fogo em alvos enraizados (Nova Congelante)
        if _spell_school == "fogo" and attacker_cs:
            if getattr(attacker_cs, "thermal_shock_enabled", False):
                _t_sfx = self.world.get_component(proj.target_id, StatusEffects)
                if _t_sfx and _t_sfx.has("root"):
                    final_dmg = int(final_dmg * 2.0)

        _apply_magic_damage(proj.attacker_id, proj.target_id, final_dmg, self.world,
                            is_crit=is_crit)
        # Quest "use_skill" (offline): só conta aqui, quando o dano do projétil já
        # foi efetivamente aplicado (outcome != "miss" retornou antes) — não na
        # ativação da skill, ver gating de has_cast em systems.py::_use_skill.
        from engine.quest_events import fire as _quest_fire_off
        from engine.components import TrainingDummy as _TDoff_hit
        _on_dummy_off_hit = self.world.get_component(proj.target_id, _TDoff_hit) is not None
        _quest_fire_off("use_skill", skill_id=proj.spell_id, on_dummy=_on_dummy_off_hit)
        _tgt_pos_off = self.world.get_component(proj.target_id, Position)
        _lpos_off = self._player_world_pos()
        if _tgt_pos_off and _lpos_off:
            SOUNDS.play_spell_at(proj.spell_id, "impact",
                                  _tgt_pos_off.x, _tgt_pos_off.y, _lpos_off[0], _lpos_off[1])
        else:
            SOUNDS.play_spell(proj.spell_id, "impact")

        # Queimaduras Profundas: crit de BdF aplica burn (duração escala com pontos)
        if is_crit and proj.spell_id == "bola_de_fogo" and attacker_cs:
            if getattr(attacker_cs, "fire_burns_on_crit", False):
                from ui.systems import apply_effect
                burn_dmg      = max(1, int(attacker_cs.spell_power * 0.3))
                burn_duration = getattr(attacker_cs, "fire_burn_duration", 3.0)
                apply_effect(self.world, proj.target_id, "burn",
                             duration=burn_duration, magnitude=burn_dmg)

        # Lapso Elemental: conta crits de fogo; 3 dentro de 6s → proc
        if is_crit and _spell_school == "fogo" and attacker_cs:
            attacker_cs.fire_crit_counter += 1
            attacker_cs.fire_crit_timer   = 6.0
            _lapse_bonus = getattr(attacker_cs, "elemental_lapse_crit_bonus", 0.0)
            if attacker_cs.fire_crit_counter >= 3 and _lapse_bonus > 0:
                attacker_cs.fire_crit_counter = 0
                attacker_cs.fire_crit_timer   = 0.0
                from ui.systems import apply_effect
                apply_effect(self.world, proj.attacker_id, "elemental_lapse",
                             duration=5.0, magnitude=0)   # auto-burn via tick
                # Aplica bônus de crit como modifier temporário
                from engine.components import Modifier
                from engine.stat_fns import add_timed_modifier
                _mod = Modifier("crit_rating", _lapse_bonus, "flat", source="buff")
                add_timed_modifier(attacker_cs, _mod, 5.0, "lapso_elemental")
                LOG.add("Lapso Elemental! +crit por 5s (auto-burn ativo).", (255, 100, 200))
                from ui.floating_text import PROC as _PROC2
                _PROC2.add("Lapso Elemental!", (255, 100, 200))

        # Exaustão: slow progressivo por Bola de Fogo consecutiva
        if proj.spell_id == "bola_de_fogo" and attacker_cs:
            if getattr(attacker_cs, "fire_exhaustion_enabled", False):
                from engine.components import ActiveEffect as _AEX
                # Mesma fonte que o servidor (server/spell_completion_processor.py)
                # usa pra essa duração — SKILL_CATALOG, não um literal hardcoded
                # que pode divergir se o catálogo for ajustado.
                from content.skill_config import SKILL_CATALOG as _SC_exh
                _exh_dur = (_SC_exh.get("bola_de_fogo", {})
                           .get("effect_durations", {}).get("exhaustion", 6.0))
                _t_sfx = self.world.get_component(proj.target_id, StatusEffects)
                if _t_sfx is None:
                    _t_sfx = StatusEffects()
                    self.world.add_component(proj.target_id, _t_sfx)
                # Incrementa stack (rastreado em magnitude do efeito "exhaustion")
                _exh = _t_sfx.get("exhaustion")
                if _exh:
                    _new_stacks = min(_exh.magnitude + 1, 5)
                    _exh.magnitude = _new_stacks
                    _exh.duration  = _exh_dur  # refresh
                else:
                    _new_stacks = 1
                    _t_sfx.effects["exhaustion"] = _AEX(
                        effect_type="exhaustion", duration=_exh_dur,
                        magnitude=1, tick_interval=0.0)
                # Slow: começa no 2º stack — 5% por stack acima do 1º
                _slow_pct = (_new_stacks - 1) * 0.05
                if _slow_pct > 0:
                    _slow_mult = 1.0 - _slow_pct
                    _slow = _t_sfx.get("slow")
                    if _slow:
                        _slow.magnitude = min(_slow.magnitude, _slow_mult)  # mantém o mais forte
                        _slow.duration  = _exh_dur
                    else:
                        _t_sfx.effects["slow"] = _AEX(
                            effect_type="slow", duration=_exh_dur,
                            magnitude=_slow_mult, tick_interval=0.0)

        # Chama Interna: rola proc após qualquer hit de spell de escola fogo
        if _spell_school == "fogo" and attacker_cs:
            _proc_chance = getattr(attacker_cs, "fire_instant_proc_chance", 0.0)
            if _proc_chance > 0 and random.random() < _proc_chance:
                _char = self.world.get_component(proj.attacker_id, CharacterStats)
                if _char and not _char.fire_instant_ready:
                    _char.fire_instant_ready = True
                    from ui.combat_log import LOG as _LOG
                    _LOG.add("Chama Interna! Próxima Bola de Fogo é instantânea.", (255, 160, 60))
                    from ui.floating_text import PROC as _PROC
                    _PROC.add("Chama Interna!", (255, 160, 60))

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for proj_id, pos, proj in self.world.get_entities_with(Position, PlayerProjectile):
            sx = pos.x - cam_x
            sy = pos.y - cam_y

            if proj.damage_type == "physical":
                # ── Flecha: rastro desbotado + linha fina ──────────────────
                trail = self._arrow_trails.get(proj_id, [])
                n = len(trail)

                # Rastro: pontos de tamanho e brilho decrescentes
                for i, (tx, ty) in enumerate(trail):
                    frac  = (i + 1) / (n + 1)          # 0..1 (mais velho = menor)
                    r = max(0, int(proj.color[0] * frac * 0.45))
                    g = max(0, int(proj.color[1] * frac * 0.45))
                    b = max(0, int(proj.color[2] * frac * 0.45))
                    rad = max(1, round(frac * 1.5))
                    pygame.draw.circle(self.world_surf, (r, g, b),
                                       (int(tx - cam_x), int(ty - cam_y)), rad)

                # Corpo da flecha: linha fina na direção do movimento
                if trail:
                    px, py = trail[-1]
                    ddx = sx - (px - cam_x)
                    ddy = sy - (py - cam_y)
                    dlen = math.sqrt(ddx * ddx + ddy * ddy) or 1.0
                    nx, ny = ddx / dlen, ddy / dlen
                    tail_x = sx - nx * self.ARROW_LINE_LEN
                    tail_y = sy - ny * self.ARROW_LINE_LEN
                    pygame.draw.line(self.world_surf, proj.color,
                                     (int(tail_x), int(tail_y)), (int(sx), int(sy)), 2)
                else:
                    pygame.draw.circle(self.world_surf, proj.color, (int(sx), int(sy)), 2)
            elif proj.spell_id == "bola_de_fogo":
                # ── Bola de Fogo: spritesheet animado e rotacionado ─────────
                if self._fireball_frames is None:
                    self._load_fireball_frames()
                frames = self._fireball_frames
                if frames:
                    elapsed = self._fireball_anim.get(proj_id, 0.0)
                    fi = int(elapsed * self.FIREBALL_FPS) % self.FIREBALL_FRAMES
                    frame = frames[fi]

                    # Direção: vetor prev→current (Position guarda prev_x/prev_y)
                    ddx = pos.x - pos.prev_x
                    ddy = pos.y - pos.prev_y
                    if abs(ddx) > 0.001 or abs(ddy) > 0.001:
                        angle_deg = -math.degrees(math.atan2(ddy, ddx))
                        rotated = pygame.transform.rotate(frame, angle_deg)
                    else:
                        rotated = frame

                    fw, fh = rotated.get_size()
                    self.world_surf.blit(rotated, (int(sx) - fw // 2, int(sy) - fh // 2))
                else:
                    pygame.draw.circle(self.world_surf, proj.color, (int(sx), int(sy)), 6)
            else:
                # ── Outros projéteis mágicos: círculo ───────────────────────
                pygame.draw.circle(self.world_surf, proj.color, (int(sx), int(sy)), 6)


# ---------------------------------------------------------------------------
# ChannelingSystem
# ---------------------------------------------------------------------------

class ChannelingSystem(System):
    """Cliente: acompanha uma canalização em andamento (Calamidade Flamejante)
    — prediz gasto de mana por tick (feedback de UI, servidor é quem
    realmente deduz), cancela por movimento/duração, desenha o círculo de
    área. NÃO aplica dano — isso é responsabilidade exclusiva do servidor
    (`server/spell_completion_processor.py::_process_player_channeling`).
    Um laço de dano local existia aqui antes (07/08/2026, removido) mas
    nunca executava de verdade: a query `Position/Enemy/CombatStats` não
    casa com mob online (representado só via `RemoteEntityMeta`/
    `RemoteControlled`, ver CLAUDE.md) e este branch não tem mais modo
    offline (`game.py::_connect_online()` é incondicional) — código morto
    sem nenhum caminho vivo, não uma predição intencional."""

    def __init__(self, world: World, screen: pygame.Surface):
        self.world  = world
        self.world_surf = screen
        self.hud_surf   = screen
        # Spell IDs interrompidos neste frame — game.py envia CANCEL_CAST ao servidor
        self.interrupted_channelings: list[str] = []

    def update(self, events=None, dt: float = 0) -> None:
        to_finish  = []
        interrupted = []
        for entity_id, channeling, combat_state, char_stats, _ in self.world.get_entities_with(
                Channeling, CombatState, CharacterStats, PlayerControlled):

            # Movimento cancela canalização — verifica intenção de mover (teclas ou clique direito)
            _keys = pygame.key.get_pressed()
            _move_intent = (
                _keys[pygame.K_LEFT] or _keys[pygame.K_a] or
                _keys[pygame.K_RIGHT] or _keys[pygame.K_d] or
                _keys[pygame.K_UP] or _keys[pygame.K_w] or
                _keys[pygame.K_DOWN] or _keys[pygame.K_s] or
                any(e.type == pygame.MOUSEBUTTONDOWN and e.button == 3
                    for e in (events or []))
            )
            if _move_intent:
                interrupted.append(entity_id)
                continue

            channeling.elapsed   += dt
            channeling.last_tick += dt

            if channeling.last_tick >= channeling.tick_interval:
                channeling.last_tick -= channeling.tick_interval
                # Piromaníaco: desconto no custo de mana por tick (escola fogo)
                _pyr_cs  = self.world.get_component(entity_id, CombatStats)
                _pyr_b   = getattr(_pyr_cs, "pyromania_bonus", 0.0) if _pyr_cs else 0.0
                tick_mana = max(0, int(channeling.mana_per_tick * (1.0 - _pyr_b)))
                # Verifica mana
                if char_stats.mana < tick_mana:
                    LOG.add("Mana insuficiente — canalização interrompida.", (180, 100, 255))
                    interrupted.append(entity_id)
                    continue
                char_stats.mana -= tick_mana

            if channeling.elapsed >= channeling.duration:
                to_finish.append(entity_id)

        for entity_id in interrupted:
            ch_int = self.world.get_component(entity_id, Channeling)
            if ch_int:
                self.interrupted_channelings.append(ch_int.spell_id)
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_casting = False
            if self.world.get_component(entity_id, Channeling):
                self.world.remove_component(entity_id, Channeling)
            SOUNDS.fadeout_skills(800)   # fadeout 0.8s
            WARN.add("Canalização interrompida!")

        for entity_id in to_finish:
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_casting = False
            if self.world.get_component(entity_id, Channeling):
                self.world.remove_component(entity_id, Channeling)
            SOUNDS.fadeout_skills(800)   # fadeout 0.8s ao finalizar naturalmente
            LOG.add("Calamidade Flamejante terminou.", (255, 160, 60))

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for entity_id, ch, _ in self.world.get_entities_with(Channeling, PlayerControlled):
            cx = int(ch.target_x - cam_x)
            cy = int(ch.target_y - cam_y)
            r  = int(ch.radius_tiles * TILE_SIZE)
            pygame.draw.circle(self.world_surf, (255, 160, 60), (cx, cy), r, 2)
            pygame.draw.circle(self.world_surf, (255, 200, 80), (cx, cy), 4)


# ---------------------------------------------------------------------------
# IceBlockSystem
# ---------------------------------------------------------------------------

class IceBlockSystem(System):
    """Processa Bloco de Gelo: imunidade + cura por segundo durante 5s."""

    def __init__(self, world: World):
        self.world = world

    def update(self, events=None, dt: float = 0) -> None:
        to_finish = []
        for entity_id, ice, combat_state, cs, _ in self.world.get_entities_with(
                IceBlockEffect, CombatState, CombatStats, PlayerControlled):

            ice.elapsed    += dt
            ice.last_heal  += dt

            if ice.last_heal >= ice.heal_interval:
                ice.last_heal -= ice.heal_interval
                heal = max(1, int(cs.max_hp * 0.10))
                cs.current_hp = min(cs.max_hp, cs.current_hp + heal)
                pos = self.world.get_component(entity_id, Position)
                if pos:
                    FLT.add(f"+{heal}", pos.x, pos.y, (80, 200, 255),
                            size="normal", target_id=entity_id)

            if ice.elapsed >= ice.duration:
                to_finish.append(entity_id)

        for entity_id in to_finish:
            cs = self.world.get_component(entity_id, CombatState)
            if cs:
                cs.is_stunned = False
                cs.is_immune  = False
            if self.world.get_component(entity_id, IceBlockEffect):
                self.world.remove_component(entity_id, IceBlockEffect)
            LOG.add("Bloco de Gelo terminou.", (100, 180, 255))


# ---------------------------------------------------------------------------
# FireShieldSystem
# ---------------------------------------------------------------------------

class FireShieldSystem(System):
    """Controla a duração do Escudo de Fogo. Retaliation aplicada em CombatSystem."""

    def __init__(self, world: World):
        self.world = world

    def update(self, events=None, dt: float = 0) -> None:
        to_finish = []
        for entity_id, shield, _ in self.world.get_entities_with(
                FireShieldEffect, PlayerControlled):
            shield.elapsed += dt
            if shield.elapsed >= shield.duration:
                to_finish.append(entity_id)

        for entity_id in to_finish:
            self.world.remove_component(entity_id, FireShieldEffect)
            LOG.add("Escudo de Fogo expirou.", (255, 120, 0))


# ---------------------------------------------------------------------------
# AoeTargetingSystem
# ---------------------------------------------------------------------------

class AoeTargetingSystem(System):
    """Intercepta clique esquerdo quando AOE targeting está ativo para posicionar a magia."""

    def __init__(self, world: World, player_entity: int, screen: pygame.Surface):
        self.world         = world
        self.player_entity = player_entity
        self.world_surf    = screen
        self.hud_surf      = screen
        self._net          = None   # injetado por game.py no modo online

    def _camera_offset(self) -> tuple[float, float]:
        sw, sh = self.world_surf.get_width(), self.world_surf.get_height()
        for _, _, cam_pos in self.world.get_entities_with(Camera, Position):
            return cam_pos.x - sw / 2, cam_pos.y - sh / 2
        return 0.0, 0.0

    @property
    def is_targeting(self) -> bool:
        return self.world.get_component(self.player_entity, AoeTargeting) is not None

    def _player_world_pos(self) -> "tuple[float, float] | None":
        from engine.components import TileMovement as _TM
        tm = self.world.get_component(self.player_entity, _TM)
        if tm:
            return tm.current_tile_x * TILE_SIZE + TILE_SIZE / 2, \
                   tm.current_tile_y * TILE_SIZE + TILE_SIZE / 2
        pos = self.world.get_component(self.player_entity, Position)
        if pos:
            return pos.x, pos.y
        return None

    def _in_cast_range(self, aoe: AoeTargeting, world_x: float, world_y: float) -> bool:
        if aoe.cast_range_tiles <= 0:
            return True
        pp = self._player_world_pos()
        if pp is None:
            return True
        dx, dy = world_x - pp[0], world_y - pp[1]
        return math.sqrt(dx * dx + dy * dy) <= aoe.cast_range_tiles * TILE_SIZE

    def _walk_toward_range(self, aoe: AoeTargeting) -> None:
        """Auto-move até o tile em que a distância ao alvo pendente entra no alcance."""
        import math as _math
        pp = self._player_world_pos()
        if pp is None:
            return
        tx, ty = aoe.pending_world_x, aoe.pending_world_y
        dx, dy = tx - pp[0], ty - pp[1]
        dist = _math.sqrt(dx * dx + dy * dy)
        if dist < 1:
            return
        stop_dist = (aoe.cast_range_tiles - 0.5) * TILE_SIZE
        ratio = max(0.0, (dist - stop_dist) / dist)
        stop_px = pp[0] + dx * ratio
        stop_py = pp[1] + dy * ratio
        tile_x = int(stop_px / TILE_SIZE)
        tile_y = int(stop_py / TILE_SIZE)
        auto = self.world.get_component(self.player_entity, PlayerAutoMove)
        if auto:
            auto.ground_target     = (tile_x, tile_y)
            auto.active            = True
            auto.path.clear()
            auto.path_recalc_timer = 0.0

    def update(self, events=None, dt: float = 0) -> None:
        aoe = self.world.get_component(self.player_entity, AoeTargeting)
        if not aoe:
            return

        # Remove componentes marcados como cancelados no frame anterior
        if aoe.cancel_pending:
            self.world.remove_component(self.player_entity, AoeTargeting)
            return

        # Checagem de chegada ao alcance: se estava esperando, verifica todo frame
        if aoe.waiting_for_range:
            if self._in_cast_range(aoe, aoe.pending_world_x, aoe.pending_world_y):
                self._start_channel(aoe, aoe.pending_world_x, aoe.pending_world_y)
                self.world.remove_component(self.player_entity, AoeTargeting)
                return

        if not events:
            return

        for ev in events:
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                self.world.remove_component(self.player_entity, AoeTargeting)
                LOG.add("Mira cancelada.", (180, 180, 180))
                return

            if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 3:
                aoe.cancel_pending = True
                LOG.add("Mira cancelada.", (180, 180, 180))
                return

            if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                cam_x, cam_y = self._camera_offset()
                scale   = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                world_x = ev.pos[0] * scale + cam_x
                world_y = ev.pos[1] * scale + cam_y
                if self._in_cast_range(aoe, world_x, world_y):
                    self._start_channel(aoe, world_x, world_y)
                    self.world.remove_component(self.player_entity, AoeTargeting)
                else:
                    aoe.pending_world_x   = world_x
                    aoe.pending_world_y   = world_y
                    aoe.waiting_for_range = True
                    self._walk_toward_range(aoe)
                    LOG.add("Fora do alcance — aproximando...", (220, 120, 60))
                return

    def _start_channel(self, aoe: AoeTargeting, world_x: float, world_y: float) -> None:
        combat_state = self.world.get_component(self.player_entity, CombatState)
        char_stats   = self.world.get_component(self.player_entity, CharacterStats)

        ps    = self.world.get_component(self.player_entity, PlayerSkills)
        skill = ps.skill_by_id(aoe.spell_id) if ps else None
        if skill is None or not skill.is_channeled:
            return

        if char_stats and char_stats.mana < skill.mana_cost:
            WARN.add("Mana insuficiente")
            return

        # Online: envia CAST_SKILL com coordenadas do alvo antes de criar Channeling local.
        # dir_x/dir_y são reaproveitados para as coordenadas world (servidor lê como aoe_x/y).
        if self._net:
            from shared.messages import MsgType as _MT_cf
            self._net.send(_MT_cf.CAST_SKILL, {
                "sid":   skill.skill_id,
                "tid":   -1,
                "dir_x": world_x,
                "dir_y": world_y,
                "rage":  0,
                "mana":  getattr(char_stats, "mana", 0) if char_stats else 0,
            })

        from engine.core_systems import build_channeling_from_skill
        self.world.add_component(self.player_entity,
            build_channeling_from_skill(skill, world_x, world_y))
        if combat_state:
            combat_state.is_casting = True
            enter_combat(combat_state)
        SOUNDS.play_skill(skill.sound_name)
        LOG.add(f"{skill.name} — canalizando!", (255, 160, 60))

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        """Desenha o círculo de mira AOE na posição do mouse."""
        aoe = self.world.get_component(self.player_entity, AoeTargeting)
        if not aoe:
            return

        # Converte posição do mouse de coordenadas de tela para coordenadas de world_surf.
        # Com zoom > 1, world_surf é menor que a tela — o mouse precisa ser escalonado.
        sx, sy = pygame.mouse.get_pos()
        scale  = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
        mx = int(sx * scale)
        my = int(sy * scale)

        world_x = mx + cam_x
        world_y = my + cam_y
        in_range = self._in_cast_range(aoe, world_x, world_y)
        ring_col   = (255, 200,  80) if in_range else (220,  60,  60)
        center_col = (255, 220, 100) if in_range else (255, 100, 100)
        r = int(aoe.radius_tiles * TILE_SIZE)
        pygame.draw.circle(self.world_surf, ring_col,   (mx, my), r, 2)
        pygame.draw.circle(self.world_surf, center_col, (mx, my), 4)
        # Círculo de alcance máximo ao redor do player (só quando targeting ativo)
        if aoe.cast_range_tiles > 0:
            pp = self._player_world_pos()
            if pp:
                scr_px = int(pp[0] - cam_x)
                scr_py = int(pp[1] - cam_y)
                range_r = int(aoe.cast_range_tiles * TILE_SIZE)
                range_surf = pygame.Surface((range_r * 2, range_r * 2), pygame.SRCALPHA)
                pygame.draw.circle(range_surf, (255, 255, 255, 30),
                                   (range_r, range_r), range_r)
                pygame.draw.circle(range_surf, (200, 200, 200, 80),
                                   (range_r, range_r), range_r, 1)
                self.world_surf.blit(range_surf, (scr_px - range_r, scr_py - range_r))


# ---------------------------------------------------------------------------
# PirofagiaSystem
# ---------------------------------------------------------------------------

# Cone base apontando para a direita (ângulo 0) em offsets de tile (dx, dy)
_PIRO_CONE = (
    (1,  0),
    (2,  0),
    (3, -1), (3,  0), (3,  1),
    (4, -2), (4, -1), (4,  0), (4,  1), (4,  2),
)


class PirofagiaSystem(System):
    """Pirofagia com mira: segura a tecla para apontar o cone, solta para disparar.

    No modo online (net != None) o sistema apenas:
      - renderiza o cone visual enquanto o jogador aponta
      - deduz mana e inicia cooldown localmente (feedback imediato)
      - envia CAST_SKILL {sid, dir_x, dir_y} ao servidor
    O servidor calcula quem foi atingido (com lag compensation) e devolve SKILL_RESULT.
    """

    def __init__(self, world: World, screen: pygame.Surface, net=None):
        self.world      = world
        self.world_surf = screen
        self.hud_surf   = screen
        self._net       = net   # NetworkClient ou None (modo offline)
        # Surface pré-alocada para o preenchimento do cone — reutilizada a cada frame
        _max = int(4.5 * TILE_SIZE + 2.5 * TILE_SIZE) + 10
        self._cone_surf = pygame.Surface((_max * 2, _max * 2), pygame.SRCALPHA)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _mouse_screen(self) -> tuple:
        """Coordenadas do mouse em world_surf space (igual AoeTargetingSystem)."""
        sx, sy = pygame.mouse.get_pos()
        scale  = (self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                  if self.world_surf and self.hud_surf else 1.0)
        return int(sx * scale), int(sy * scale)

    def _cone_tiles(self, tile_x: int, tile_y: int, angle: float) -> set:
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        tiles = set()
        for dx, dy in _PIRO_CONE:
            tiles.add((tile_x + round(dx * cos_a - dy * sin_a),
                       tile_y + round(dx * sin_a + dy * cos_a)))
        return tiles

    # ── Update ───────────────────────────────────────────────────────────────

    def update(self, events=None, dt: float = 0) -> None:
        to_fire  = []
        to_cancel = []
        for entity_id, aiming in self.world.get_entities_with(PirofagiaAiming):
            aiming.elapsed += dt
            for event in (events or []):
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:   # clique esquerdo → dispara
                        to_fire.append(entity_id)
                    elif event.button == 3: # clique direito → cancela sem disparar
                        to_cancel.append(entity_id)

        for entity_id in to_fire:
            if self.world.get_component(entity_id, PirofagiaAiming):
                self.world.remove_component(entity_id, PirofagiaAiming)
            self._fire_cone(entity_id)

        for entity_id in to_cancel:
            if self.world.get_component(entity_id, PirofagiaAiming):
                self.world.remove_component(entity_id, PirofagiaAiming)
            LOG.add("Pirofagia cancelada.", (180, 80, 30))

    def _fire_cone(self, entity_id: int) -> None:
        from engine.components import CharacterStats, PlayerSkills

        pos = self.world.get_component(entity_id, Position)
        tm  = self.world.get_component(entity_id, TileMovement)
        cs  = self.world.get_component(entity_id, CombatStats)
        if not pos or not tm or not cs:
            return

        # Verifica e deduz mana ao disparar (não na ativação)
        char_stats = self.world.get_component(entity_id, CharacterStats)
        ps         = self.world.get_component(entity_id, PlayerSkills)
        skill_obj  = ps.skill_by_id("pirofagia") if ps else None
        mana_cost  = skill_obj.mana_cost if skill_obj else 75
        if char_stats:
            if char_stats.mana < mana_cost:
                LOG.add("Mana insuficiente — Pirofagia cancelada.", (255, 100, 30))
                return
            char_stats.mana -= mana_cost
        # Inicia cooldown somente ao disparar
        if skill_obj:
            skill_obj.current_cooldown = skill_obj.cooldown

        # Ângulo do cone baseado na posição atual do mouse
        mx, my = self._mouse_screen()
        cam_x = cam_y = 0.0
        for _, cam, cam_pos in self.world.get_entities_with(Camera, Position):
            lw = self.world_surf.get_width()  if self.world_surf else 1280
            lh = self.world_surf.get_height() if self.world_surf else 720
            cam_x = cam_pos.x - lw / 2
            cam_y = cam_pos.y - lh / 2
            break

        px    = pos.x - cam_x
        py    = pos.y - cam_y
        angle = math.atan2(my - py, mx - px)
        dir_x = math.cos(angle)
        dir_y = math.sin(angle)

        # Delega ao servidor; aplica apenas feedback local.
        if not self._net:
            return
        from shared.messages import MsgType as _MT2
        from engine.components import CharacterStats as _CSfc
        _char_fc = self.world.get_component(entity_id, _CSfc)
        self._net.send(_MT2.CAST_SKILL, {
            "sid":   "pirofagia",
            "tid":   -1,
            "dir_x": dir_x,
            "dir_y": dir_y,
            "rage":  getattr(_char_fc, "rage", 0),
            "mana":  getattr(_char_fc, "mana", 0),
        })
        # Feedback visual/sonoro imediato — servidor confirma dano via SKILL_RESULT
        SOUNDS.play_skill("skill_pirofagia")

    # ── Render ───────────────────────────────────────────────────────────────

    def render(self, cam_x: float = 0, cam_y: float = 0) -> None:
        for entity_id, _ in self.world.get_entities_with(PirofagiaAiming):
            pos = self.world.get_component(entity_id, Position)
            if pos is None or self.world_surf is None:
                continue

            # Mouse em coordenadas de world_surf (mesmo padrão do AoeTargetingSystem)
            sx, sy = pygame.mouse.get_pos()
            scale  = self.world_surf.get_width() / max(1, self.hud_surf.get_width())
            mx = int(sx * scale)
            my = int(sy * scale)

            # Player em coordenadas de world_surf (eixo do cone)
            px = int(pos.x - cam_x)
            py = int(pos.y - cam_y)

            # Ângulo player → mouse
            angle = math.atan2(my - py, mx - px)
            ca    = math.cos(angle)
            sa    = math.sin(angle)

            L = 4.5 * TILE_SIZE   # comprimento do cone
            W = 2.5 * TILE_SIZE   # meia-largura na boca

            # Vértices: rotação de (L, ±W) em torno do player
            v_top = (int(px + L * ca - W * (-sa)), int(py + L * sa + W * (-ca)))
            v_bot = (int(px + L * ca - W *   sa ), int(py + L * sa + W *   ca ))
            pts   = [(px, py), v_top, v_bot]

            # Preenchimento semi-transparente
            all_x = [px, v_top[0], v_bot[0]]
            all_y = [py, v_top[1], v_bot[1]]
            bx = min(all_x) - 2;  by = min(all_y) - 2
            bw = max(all_x) - bx + 4;  bh = max(all_y) - by + 4
            if bw > 0 and bh > 0:
                # Reutiliza surface pré-alocada — evita alloc por frame
                cx = self._cone_surf.get_width()  // 2
                cy = self._cone_surf.get_height() // 2
                self._cone_surf.fill((0, 0, 0, 0))
                local  = [(px - bx, py - by),
                          (v_top[0] - bx, v_top[1] - by),
                          (v_bot[0] - bx, v_bot[1] - by)]
                pygame.draw.polygon(self._cone_surf, (220, 50, 0, 100), local)
                self.world_surf.blit(self._cone_surf, (bx, by))

            # Contorno opaco e ponto no vértice
            pygame.draw.polygon(self.world_surf, (255, 200, 60), pts, 2)
            pygame.draw.circle(self.world_surf,  (255, 240, 80), (px, py), 5)
