"""
skill_handlers.py — Mixin com todos os handlers de habilidades do jogador.

Separado de systems.py para manter SkillSystem conciso. Esta classe NÃO deve
ser instanciada diretamente — ela é herdada por SkillSystem, que fornece
self.world e self.player_entity_id.

Para adicionar uma nova skill base:
  def _skill_<skill_id>(self, skill, combat_stats, combat_state, tile_move): ...

Para adicionar uma nova skill de talento:
  def _talent_<handler_name>(self, skill, combat_stats, combat_state, tile_move): ...
"""
from __future__ import annotations
import math
import random

from engine.components import (
    Position, Enemy, AIControlled, TileMovement, CombatStats, CombatState,
    CharacterStats, Tilemap, PlayerAutoMove, StatusEffects,
    SpellCast, AoeTargeting, IceBlockEffect,
)
from engine.tileset import TILE_SIZE
from engine.utils import chebyshev
from ui.combat_log import LOG
# fx: façade headless — no-op no servidor, gerenciadores reais no cliente
# (fx.bind_client_fx no GameEngine). skill_handlers roda nos dois lados.
from engine.fx import SOUNDS, FLT, WARN, PROC
from engine.core_systems import apply_effect   # sem dep circular (systems re-exporta daqui)
from engine.stat_fns import enter_combat

# Lazy wrappers para deal_damage / is_tile_walkable / get_mainhand_weapon.
# Evitam "from world_systems import ..." no topo do módulo, quebrando o ciclo:
#   skill_handlers → world_systems → (entity_factory → ...) → skill_handlers
# Python cacheia o import — overhead apenas no primeiro call.
def deal_damage(*a, **kw):
    from engine.world_systems import deal_damage as _f; return _f(*a, **kw)

def is_tile_walkable(*a, **kw):
    from engine.world_systems import is_tile_walkable as _f; return _f(*a, **kw)

def ability_physical_damage(*a, **kw):
    from engine.damage_calculator import ability_physical_damage as _f; return _f(*a, **kw)

def get_mainhand_weapon(*a, **kw):
    from engine.world_systems import get_mainhand_weapon as _f; return _f(*a, **kw)


class SkillHandlers:
    """Mixin com implementações de _skill_* e _talent_* para SkillSystem."""

    # Constantes usadas por Interceptar
    INTERCEPT_MIN_RANGE = 2     # tiles mínimos para usar Interceptar
    INTERCEPT_MAX_RANGE = 6     # tiles máximos para usar Interceptar
    INTERCEPT_DURATION  = 0.18  # segundos do dash

    AoE_RADIUS = 3  # raio do Impacto em tiles

    # ── Alcance em pixels — padrão universal ────────────────────────────────
    # Todas as skills usam pixel-based para checagem de range.
    # Derivação: range_px = tiles * TILE_SIZE ± RANGE_TOLERANCE
    # Tolerância cobre sub-tile lag (mob animando entre tiles).
    # ECS note: constantes de regra de negócio do sistema, não do componente.

    # Tolerância em pixels para compensar latência de rede + animação.
    # MELEE_RANGE_PX = 2.25 tiles:
    #   - cobre adjacência cardinal (32px) e diagonal (45px)
    #   - cobre mob movendo 1 tile kiting durante o lag (~40ms = 6px) + snap = 32px extra
    #   - margem total: 72 - 45 = 27px para acomodar variação de posição
    RANGE_TOLERANCE_PX: float = 40.0   # tolerância usada na fórmula do cliente

    INTERCEPT_MIN_RANGE_PX: float = 2 * 32 + 10   # 74px  (2 tiles + margem) — exclui
                                                    # TODA adjacência (cardinal 32px,
                                                    # diagonal ~45px); skill_config min_range=2
                                                    # agora é respeitado de verdade
    INTERCEPT_MAX_RANGE_PX: float = 6 * 32 + 40   # 232px (6 tiles + tolerance)

    MELEE_RANGE_PX: float = 72.0   # 2.25 tiles — cobre kiting + lag

    def _target_alive(self, target_id: int) -> bool:
        from engine.utils import is_target_alive
        return is_target_alive(self.world, target_id)

    def _range_ok(self, player_pos, target_pos,
                  max_px: float, min_px: float = 0.0) -> bool:
        """Verifica alcance em pixels (hitbox circular, contínua).

        Usa Position.x/y (posição interpolada) para precisão sub-tile.
        Padrão universal: mesma fórmula para melee, dash e ranged.
        """
        if player_pos is None or target_pos is None:
            return False
        dx = player_pos.x - target_pos.x
        dy = player_pos.y - target_pos.y
        dist_sq = dx * dx + dy * dy
        if dist_sq > max_px * max_px:
            return False
        if min_px > 0 and dist_sq < min_px * min_px:
            return False
        return True

    # Alias melee para backward compat
    def _melee_ok(self, player_pos, target_pos) -> bool:
        return self._range_ok(player_pos, target_pos, self.MELEE_RANGE_PX)

    # ==================================================================
    # Utilitários internos
    # ==================================================================

    def _warn(self, text: str) -> None:
        """Exibe aviso de ação bloqueada em posição fixa na tela (não vai para o log)."""
        WARN.add(text)
        self._last_warn = text  # capturado pelo skill_processor para feedback online

    _last_warn: str = ""  # último motivo de rejeição — enviado em failed SKILL_RESULT

    # Setado pelo SkillProcessor (servidor) antes de chamar o handler; lista para
    # onde projéteis/spells diferidos são empurrados. None = modo offline (sem servidor).
    _server_pending_spells: list | None = None

    # ==================================================================
    # Habilidades base (skill_config.py)
    # ==================================================================

    def _skill_golpe_poderoso(self, skill, _combat_stats, combat_state, tile_move):
        """arma + AP×(damage_multiplier + skill level da arma) em alvo adjacente.
        Custa 15 de Raiva (talento Veterano reduz até 10)."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        rage_cost = _combat_stats.golpe_poderoso_rage_cost if _combat_stats else 15
        if not char_stats or char_stats.rage < rage_cost:
            self._warn(f"Raiva insuficiente ({rage_cost})")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm or not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return
        _pl_pos  = self.world.get_component(self.player_entity_id, Position)
        _tgt_pos = self.world.get_component(target_id, Position)
        if not self._melee_ok(_pl_pos, _tgt_pos):
            self._warn("Fora de alcance")
            return
        char_stats.rage -= rage_cost

        # Embalo: consome carga e aumenta o multiplicador de dano
        embalo_bonus = 0.0
        if _combat_stats and _combat_stats.embalo_on_crit and char_stats.embalo_charges > 0:
            embalo_bonus = _combat_stats.embalo_bonus_per_charge
            char_stats.embalo_charges -= 1

        # Fórmula única do catálogo (dmg = arma + AP×(mult + 0.01×skill_level))
        # — antes era multiplier=3.0 HARDCODED em (AP+arma)×3, ignorando o
        # damage_multiplier do SKILL_CATALOG. Embalo entra como extra_mult.
        _dano_gp = ability_physical_damage(
            self.world, self.player_entity_id, skill.params, extra_mult=embalo_bonus)
        deal_damage(
            self.player_entity_id, target_id, "physical_fixed",
            base_ability_damage=_dano_gp, is_ability=True)
        if combat_state:
            enter_combat(combat_state)
        return True

    # ------------------------------------------------------------------
    def _skill_vitoria_iminente(self, skill, combat_stats, combat_state, tile_move):
        """2× dano físico em alvo adjacente + cura 30% do HP máximo. Consome a carga."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm or not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return
        _pl_pos  = self.world.get_component(self.player_entity_id, Position)
        _tgt_pos = self.world.get_component(target_id, Position)
        if not self._melee_ok(_pl_pos, _tgt_pos):
            self._warn("Fora de alcance")
            return
        skill.charges -= 1
        skill.charge_timer = 0.0
        deal_damage(self.player_entity_id, target_id, "physical_fixed",
                    base_ability_damage=ability_physical_damage(
                        self.world, self.player_entity_id, skill.params),
                    is_ability=True)
        heal = int(combat_stats.max_hp * 0.30)
        heal_actual = min(heal, combat_stats.max_hp - combat_stats.current_hp)
        combat_stats.current_hp = min(combat_stats.max_hp, combat_stats.current_hp + heal)
        if heal_actual > 0:
            LOG.add(f"Vitória Iminente: +{heal_actual} HP recuperados!", (80, 220, 80))
        else:
            LOG.add("Vitória Iminente: HP já está cheio!", (180, 180, 100))
        if combat_state:
            enter_combat(combat_state)
        return True

    # ------------------------------------------------------------------
    def _skill_impacto(self, skill, _combat_stats, combat_state, tile_move):
        """50% dano em todos os inimigos dentro de AoE_RADIUS tiles.
        Máquina de Matar: +15% por inimigo no raio (checado antes do dano)."""
        px, py = tile_move.current_tile_x, tile_move.current_tile_y

        # Itera CombatStats (não Enemy) — inclui players em PvP no servidor
        targets = []
        for enemy_id, enemy_tm, enemy_cs in self.world.get_entities_with(
                TileMovement, CombatStats):
            if enemy_id == self.player_entity_id:
                continue  # não ataca a si mesmo
            if enemy_cs.current_hp <= 0:
                continue
            dist = max(abs(px - enemy_tm.current_tile_x),
                       abs(py - enemy_tm.current_tile_y))
            if dist <= self.AoE_RADIUS:
                targets.append(enemy_id)

        if not targets:
            self._warn("Nenhum inimigo no alcance")
            return False

        player_cs = self.world.get_component(self.player_entity_id, CombatStats)
        # Fórmula única (já era arma + AP×mult aqui, só que com 0.5 hardcoded;
        # agora lê damage_multiplier do catálogo + bônus de skill level da arma)
        dano_base = ability_physical_damage(
            self.world, self.player_entity_id, skill.params if skill else {})

        bonus = 0.15 * len(targets) if (player_cs and player_cs.impacto_maquina_matar) else 0.0
        dano_final = dano_base * (1.0 + bonus)

        for enemy_id in targets:
            deal_damage(
                self.player_entity_id, enemy_id, "physical_fixed",
                base_ability_damage=dano_final, is_ability=True)

        msg = f"Impacto! Atingiu {len(targets)} inimigo(s)."
        if bonus > 0:
            msg += f" [Máquina de Matar +{bonus*100:.0f}%]"
        LOG.add(msg, (255, 180, 0))

        # Proc Assassino: 5% por alvo acertado → 1 carga livre de Executar
        _assassino_flag = player_cs and player_cs.impacto_assassino
        _roll = random.random()
        _proc_chance = 0.05 * len(targets) if _assassino_flag else 0.0
        if _assassino_flag:
            if _roll < _proc_chance:
                char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
                if char_stats:
                    char_stats.free_executar_charges = 1
                    LOG.add("Assassino: Executar disponivel! (sem custo, sem restricao de HP)",
                            (255, 80, 80))
                    PROC.add("Assassino!", (255, 80, 80))
        if combat_state:
            enter_combat(combat_state)
        skill.current_cooldown = skill.cooldown
        return True

    # ------------------------------------------------------------------
    def _skill_executar(self, _skill, _combat_stats, combat_state, tile_move):
        """5x dano em alvo com menos de 30% HP — custa 10 de Raiva.
        Com carga livre (proc Assassino): ignora HP e custo de Raiva."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        free_charge = char_stats and char_stats.free_executar_charges > 0

        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        if not free_charge:
            if not char_stats or char_stats.rage < 10:
                self._warn("Raiva insuficiente (10)")
                return
        target_cs = self.world.get_component(target_id, CombatStats)
        if target_cs is None:
            from engine.components import RemoteControlled as _RC
            _rc_tgt = self.world.get_component(target_id, _RC)
            if _rc_tgt is None or _rc_tgt.hp <= 0:
                self._warn("Alvo inválido")
                return
            _hp_ratio = _rc_tgt.hp / max(1, _rc_tgt.hp_max)
        elif target_cs.current_hp <= 0:
            self._warn("Alvo inválido")
            return
        else:
            _hp_ratio = target_cs.current_hp / max(1, target_cs.max_hp)
        if not free_charge and _hp_ratio >= 0.30:
            self._warn("Alvo precisa ter <30% HP")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm:
            return
        _pl_pos  = self.world.get_component(self.player_entity_id, Position)
        _tgt_pos = self.world.get_component(target_id, Position)
        if not self._melee_ok(_pl_pos, _tgt_pos):
            self._warn("Fora de alcance")
            return

        if free_charge:
            char_stats.free_executar_charges -= 1
            LOG.add("Executar [Assassino]!", (255, 80, 80))
        else:
            char_stats.rage -= 10
        deal_damage(
            self.player_entity_id, target_id, "physical_fixed",
            base_ability_damage=ability_physical_damage(
                self.world, self.player_entity_id, _skill.params if _skill else {}),
            is_ability=True)

        # Talento "Horrorizante": se alvo sobreviveu, aplica medo por 1s
        # (usa _target_alive — alvo PvP só tem RemoteControlled, sem CombatStats)
        _cs_exec = self.world.get_component(self.player_entity_id, CombatStats)
        if _cs_exec and _cs_exec.executar_horrorizante and self._target_alive(target_id):
            apply_effect(self.world, target_id, "fear", 1.0)
            _tpos = self.world.get_component(target_id, Position)
            if _tpos:
                FLT.add("Medo!", _tpos.x, _tpos.y,
                        (255, 140, 0), size="normal", target_id=target_id)

        if combat_state:
            enter_combat(combat_state)
        return True

    # ------------------------------------------------------------------
    def _has_los(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """Bresenham — retorna True se não há tile sólido entre (x0,y0) e (x1,y1)."""
        tilemap_comp = None
        for _, tc in self.world.get_entities_with(Tilemap):
            tilemap_comp = tc
            break
        if not tilemap_comp:
            return True

        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x1 > x0 else -1
        sy = 1 if y1 > y0 else -1
        err = dx - dy
        cx, cy = x0, y0

        while True:
            if cx == x1 and cy == y1:
                break
            if not (cx == x0 and cy == y0):
                rows = tilemap_comp.tile_matrix
                if (0 <= cy < len(rows) and 0 <= cx < len(rows[cy])
                        and rows[cy][cx].is_solid):
                    return False
            e2 = err * 2
            if e2 > -dy:
                err -= dy
                cx  += sx
            if e2 < dx:
                err += dx
                cy  += sy
        return True

    def _dash_path_clear(self, x0: int, y0: int, x1: int, y1: int) -> bool:
        """Verifica cada passo Bresenham usando is_tile_walkable com from_x/from_y.

        Diferente de _has_los (só checa is_solid), também bloqueia cortes diagonais
        entre dois tiles sólidos — replicando exatamente a validação de move_player
        do servidor.
        """
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x1 > x0 else -1
        sy = 1 if y1 > y0 else -1
        err = dx - dy
        cx, cy = x0, y0

        while True:
            if cx == x1 and cy == y1:
                return True
            prev_x, prev_y = cx, cy
            e2 = err * 2
            if e2 > -dy:
                err -= dy
                cx  += sx
            if e2 < dx:
                err += dx
                cy  += sy
            if not is_tile_walkable(self.player_entity_id, cx, cy, prev_x, prev_y):
                return False

    # ------------------------------------------------------------------
    def _skill_interceptar(self, skill, _combat_stats, combat_state, tile_move):
        """Dash até o tile adjacente ao alvo (animado, alcance 2–6 tiles)."""
        # _max_range=8 (não 1): Interceptar é um dash de longo alcance, não melee — o
        # "1" antigo nunca representou o range real da skill, só nunca importava porque
        # _resolve_target() não validava range de um alvo JÁ selecionado (correção de
        # segurança posterior, ver Tier E / PROBLEMAS_ARQUITETURA.md). Depois dessa
        # correção, um alvo de Interceptar a mais de 1 tile passou a ser descartado
        # aqui antes mesmo de chegar no _range_ok() abaixo (que É a checagem real,
        # 2–6 tiles + tolerância) — bug real reportado pelo usuário ("BLOQUEADO" mesmo
        # sem obstáculo). 8 cobre o alcance máximo real (INTERCEPT_MAX_RANGE_PX ≈ 7.25
        # tiles) com margem; _range_ok() abaixo continua sendo o gate preciso.
        target_id = self._resolve_target(combat_state, tile_move, 8)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm or not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return

        tx, ty = target_tm.current_tile_x, target_tm.current_tile_y
        px, py = tile_move.current_tile_x, tile_move.current_tile_y

        # Range check em pixels (padrão universal)
        _pl_pos  = self.world.get_component(self.player_entity_id, Position)
        _tgt_pos = self.world.get_component(target_id, Position)
        if not self._range_ok(_pl_pos, _tgt_pos,
                               self.INTERCEPT_MAX_RANGE_PX,
                               self.INTERCEPT_MIN_RANGE_PX):
            _dx_i = (_pl_pos.x - _tgt_pos.x) if (_pl_pos and _tgt_pos) else 0
            _dy_i = (_pl_pos.y - _tgt_pos.y) if (_pl_pos and _tgt_pos) else 0
            _d_i  = (_dx_i*_dx_i + _dy_i*_dy_i)**0.5 if (_pl_pos and _tgt_pos) else 0
            if _d_i < self.INTERCEPT_MIN_RANGE_PX:
                self._warn("Alvo muito próximo")
            else:
                self._warn("Alvo muito longe")
            return

        adj = [(tx + dx, ty + dy) for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        walkable = [t for t in adj
                    if is_tile_walkable(self.player_entity_id, t[0], t[1])]
        if not walkable:
            self._warn("Sem espaço ao redor do alvo")
            return
        dest_x, dest_y = min(walkable, key=lambda t: abs(t[0] - px) + abs(t[1] - py))

        if not self._dash_path_clear(px, py, dest_x, dest_y):
            self._warn("Caminho bloqueado")
            return

        player_pos = self.world.get_component(self.player_entity_id, Position)
        new_px = dest_x * TILE_SIZE + TILE_SIZE / 2
        new_py = dest_y * TILE_SIZE + TILE_SIZE / 2

        if isinstance(player_pos, Position):
            tile_move.start_pixel_x = player_pos.x
            tile_move.start_pixel_y = player_pos.y
        tile_move.target_pixel_x  = new_px
        tile_move.target_pixel_y  = new_py
        tile_move.target_tile_x   = dest_x
        tile_move.target_tile_y   = dest_y
        tile_move.progress        = 0.0
        tile_move.move_duration   = self.INTERCEPT_DURATION
        tile_move.is_moving       = True
        tile_move.is_dash         = True

        auto = self.world.get_component(self.player_entity_id, PlayerAutoMove)
        if auto:
            auto.active        = False
            auto.path          = []
            auto.ground_target = None

        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        _cs_int = self.world.get_component(self.player_entity_id, CombatStats)
        if char_stats and _cs_int and _cs_int.interceptar_rage_bonus > 0:
            char_stats.rage = min(char_stats.max_rage, char_stats.rage + _cs_int.interceptar_rage_bonus)
        skill.current_cooldown = max(0.0, skill.cooldown - (_cs_int.interceptar_cooldown_reduction if _cs_int else 0.0))
        if _cs_int and _cs_int.interceptar_stun_duration > 0:
            apply_effect(self.world, target_id, "stun", _cs_int.interceptar_stun_duration)
            _tpos = self.world.get_component(target_id, Position)
            if _tpos:
                FLT.add("Atordoado!", _tpos.x, _tpos.y,
                        (180, 180, 255), size="normal", target_id=target_id)

        if combat_state:
            enter_combat(combat_state)
        LOG.add("Interceptar: dash!", (100, 200, 255))
        return True

    # ==================================================================
    # Handlers de habilidades de TALENTO
    # ==================================================================

    def _skill_golpe_debilitante(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Golpe Debilitante: 50% dano + -50% velocidade por 5s. Custa 5 Raiva."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not char_stats or char_stats.rage < 5:
            self._warn("Raiva insuficiente (5)")
            return False
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm or not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return False
        _pl_pos  = self.world.get_component(self.player_entity_id, Position)
        _tgt_pos = self.world.get_component(target_id, Position)
        if not self._melee_ok(_pl_pos, _tgt_pos):
            self._warn("Fora de alcance")
            return False
        char_stats.rage -= 5
        deal_damage(self.player_entity_id, target_id, "physical_fixed",
                    base_ability_damage=ability_physical_damage(
                        self.world, self.player_entity_id, skill.params),
                    is_ability=True)
        apply_effect(self.world, target_id, "slow", 5.0, magnitude=0.5)
        _tpos = self.world.get_component(target_id, Position)
        if _tpos:
            FLT.add("Lento!", _tpos.x, _tpos.y,
                    (100, 220, 80), size="normal", target_id=target_id)
        skill.current_cooldown = skill.cooldown
        if combat_state:
            enter_combat(combat_state)
        LOG.add("Golpe Debilitante: alvo com -50% velocidade por 5s!", (180, 220, 80))
        return True

    def _skill_punho_no_queixo(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Punho no Queixo: 45% AP + stun escalonável (1/2/3s por ponto). Consome 1 carga."""
        target_id = self._resolve_target(combat_state, tile_move, 1)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False
        if skill.charges <= 0:
            self._warn("Sem cargas")
            return False
        target_tm = self.world.get_component(target_id, TileMovement)
        if not target_tm or not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return False
        _pl_pos  = self.world.get_component(self.player_entity_id, Position)
        _tgt_pos = self.world.get_component(target_id, Position)
        if not self._melee_ok(_pl_pos, _tgt_pos):
            self._warn("Fora de alcance")
            return False

        # Duração do stun vem do flag pnq_stun_duration (calculado por apply_talent_effects)
        _cs_pnq = self.world.get_component(self.player_entity_id, CombatStats)
        stun_duration = _cs_pnq.pnq_stun_duration if _cs_pnq else 1.0

        skill.charges -= 1
        target_cs = self.world.get_component(target_id, CombatStats)
        hp_before = target_cs.current_hp if target_cs else 0
        # dmg_weapon_pct=0.0 no catálogo: soco não usa a arma — o dano é SÓ
        # AP×multiplier (a docstring "45% AP" agora é verdade; antes o caminho
        # "physical" somava a arma e multiplicava junto).
        killed, _ = deal_damage(self.player_entity_id, target_id, "physical_fixed",
                                base_ability_damage=ability_physical_damage(
                                    self.world, self.player_entity_id, skill.params),
                                is_ability=True)
        hit = killed or (target_cs is not None and target_cs.current_hp < hp_before)
        if hit:
            apply_effect(self.world, target_id, "stun", stun_duration)
            LOG.add(f"Punho no Queixo: alvo atordoado por {stun_duration:.0f}s!", (255, 180, 80))
        skill.current_cooldown = skill.cooldown
        if combat_state:
            enter_combat(combat_state)
        return True

    def _skill_fatiador_de_corpos(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Fatiador de Corpos: spin AoE com parâmetros vindos de skill.params."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not char_stats:
            return False
        p = skill.params
        char_stats.fatiador_timer = p.get("duration",       5.0)
        char_stats.fatiador_tick  = p.get("tick_interval",  1.0)
        self._fatiador_aoe_tick(skill, tile_move)
        skill.current_cooldown = skill.cooldown
        if combat_state:
            enter_combat(combat_state)
        LOG.add("Fatiador de Corpos: girando!", (255, 120, 60))
        return True

    def _fatiador_aoe_tick(self, skill, tile_move) -> None:
        """Aplica dano AoE a inimigos no raio. Parâmetros vindos de skill.params.

        Fórmula única do catálogo (arma + AP×mult + skill level) — o antigo
        flag include_weapon_dmg era código MORTO: os dois branches do if eram
        idênticos ("physical" com multiplier, que multiplicava a arma junto)."""
        p      = skill.params if skill else {}
        radius = p.get("radius_tiles", 2)

        pl_x, pl_y = tile_move.current_tile_x, tile_move.current_tile_y
        hit = 0

        for eid, etm, ecs in self.world.get_entities_with(TileMovement, CombatStats):
            if eid == self.player_entity_id: continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) <= radius and ecs.current_hp > 0:
                deal_damage(self.player_entity_id, eid, "physical_fixed",
                            base_ability_damage=ability_physical_damage(
                                self.world, self.player_entity_id, p),
                            is_ability=True)
                hit += 1

        if hit:
            LOG.add(f"Fatiador de Corpos: {hit} atingidos!", (255, 120, 60))

    def _skill_brado_provocativo(self, skill, _combat_stats, combat_state, tile_move):
        """Cavaleiro — Brado Provocativo: taunt de verdade (Fase D,
        23/07/2026, referência trazida pelo usuário — hard-CC de LoL,
        Rammus/Galio/Shen). Alvos em raio 3 são forçados a andar até o
        guerreiro e autoatacá-lo por 3s, sem poder usar habilidades.

        PLAYERS: movimento forçado é pilotado por `TauntSystem`
        (engine/world_systems.py, roda por bundle de mapa) — aqui só
        aplica o efeito `"taunted"`, que carrega o eid do taunter em
        `magnitude`. MOBS: força `AIControlled.state="CHASING"` na hora —
        `TauntSystem` não pilota mobs (não precisa: a RETENÇÃO de alvo já
        existente em `EnemyAISystem` mantém o mob preso no taunter
        enquanto ele for válido, sem precisar de nenhum guard novo)."""
        from engine.components import AIControlled as _AIC_BP
        from engine.faction_system import can_engage as _can_engage_bp
        _radius_bp   = skill.params.get("radius_tiles", 3)
        _duration_bp = skill.params.get("duration", 3.0)
        pl_x, pl_y = tile_move.current_tile_x, tile_move.current_tile_y
        taunted = 0
        for eid, etm, ecs in self.world.get_entities_with(TileMovement, CombatStats):
            if eid == self.player_entity_id: continue
            if ecs.current_hp <= 0:
                continue
            if chebyshev(pl_x, pl_y, etm.current_tile_x, etm.current_tile_y) > _radius_bp:
                continue
            # can_engage bloqueia alvo amigável — mesma classe de bug já
            # corrigida em Pirofagia (20/07/2026) e Canção de Ninar
            # (22/07/2026): sem este check, o taunt seria aplicado
            # incondicionalmente pra QUALQUER um no raio, amigável ou não.
            if not _can_engage_bp(self.world, self.player_entity_id, eid):
                continue
            apply_effect(self.world, eid, "taunted", _duration_bp,
                        magnitude=float(self.player_entity_id))
            _ai_bp = self.world.get_component(eid, _AIC_BP)
            if _ai_bp:
                _ai_bp.state      = "CHASING"
                _ai_bp.target_eid = self.player_entity_id
            taunted += 1
        PROC.add("Brado!", (255, 100, 50))
        skill.current_cooldown = skill.cooldown
        if combat_state:
            enter_combat(combat_state)
        if not taunted:
            self._warn("Nenhum inimigo no raio")
        return True

    # ==================================================================
    # Habilidades do Mago
    # ==================================================================

    def _check_mana(self, char_stats: CharacterStats, cost: int) -> bool:
        if not char_stats or char_stats.mana < cost:
            self._warn(f"Mana insuficiente ({cost})")
            return False
        return True

    def _skill_bola_de_fogo(self, skill, combat_stats, combat_state, tile_move):
        """1.5s cast (reduzido por Bola de Fogo Aperfeiçoada) — 50% dano + 100% SP."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)

        # Chama Interna: proc ativo → cast instantâneo e grátis
        proc_active = getattr(char_stats, "fire_instant_ready", False) if char_stats else False
        if proc_active:
            effective_cost = 0
            effective_cast = 0.0
            if char_stats:
                char_stats.fire_instant_ready = False   # consome o proc
        else:
            discount       = getattr(combat_stats, "fire_mana_discount", 0)
            pyr_discount   = int(skill.mana_cost * getattr(combat_stats, "pyromania_bonus", 0.0))
            effective_cost = max(0, skill.mana_cost - discount - pyr_discount)
            effective_cast = max(0.0, skill.cast_time
                                 - getattr(combat_stats, "fire_cast_time_reduction", 0.0))

        if not self._check_mana(char_stats, effective_cost):
            return False

        target_id = self._resolve_target(combat_state, tile_move, 30)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False

        if not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return False

        # Modo servidor: agenda conclusão diferida em vez de criar SpellCast
        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid": self.player_entity_id,
                "spell_id":   "bola_de_fogo",
                "target_id":  target_id,
                "timer":      effective_cast,
                "mana_cost":  effective_cost,
                "cooldown":   skill.cooldown,
            })
            # Não seta is_pursuing no servidor durante o cast — evita auto-attack
            # prematuro e falso agro. O agro ocorre ao completar o cast.
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id  = "bola_de_fogo",
            cast_time = effective_cast,
            elapsed   = 0.0,
            target_id = target_id,
            mana_cost = effective_cost,  # deduzido ao completar, não aqui
        ))
        if combat_state:
            combat_state.is_casting = True
            enter_combat(combat_state)
            combat_state.is_pursuing = True
            combat_state.chase_suppressed = False   # reengajamento reativa a perseguição

        SOUNDS.play_spell("bola_de_fogo", "cast")
        LOG.add("Lançando Bola de Fogo...", (255, 160, 60))
        return True

    def _cancel_pursuit_for_targeting(self) -> None:
        """
        Cancela perseguição de alvo e interrompe qualquer movimento em curso.
        Chamado por skills de mira/área antes de entrar no modo de targeting.
        Reutilizável para futuras skills com needs_aoe_target=True.
        """
        cs = self.world.get_component(self.player_entity_id, CombatState)
        if cs:
            cs.is_pursuing = False

        am = self.world.get_component(self.player_entity_id, PlayerAutoMove)
        if am:
            am.active         = False
            am.path.clear()
            am.ground_target  = None

        # Interrompe o tile movement em curso — snap para o tile atual
        tm = self.world.get_component(self.player_entity_id, TileMovement)
        if tm and tm.is_moving:
            tm.is_moving = False
            tm.progress  = 0.0
            pos = self.world.get_component(self.player_entity_id, Position)
            if pos:
                pos.x = tm.current_tile_x * TILE_SIZE + TILE_SIZE / 2
                pos.y = tm.current_tile_y * TILE_SIZE + TILE_SIZE / 2

    def _skill_calamidade_flamejante(self, skill, combat_stats, combat_state, tile_move):
        """Ativa o modo de mira AOE; clique esquerdo inicia a canalização."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not self._check_mana(char_stats, skill.mana_cost):
            return False

        # Modo servidor: recebe coordenadas AOE via _server_aoe_x/y (enviadas pelo cliente ao clicar)
        _server_mode = self._server_pending_spells is not None
        if _server_mode:
            aoe_x = getattr(tile_move, "_server_aoe_x", 0.0)
            aoe_y = getattr(tile_move, "_server_aoe_y", 0.0)
            if aoe_x == 0.0 and aoe_y == 0.0:
                return False  # coordenadas não enviadas
            from engine.core_systems import build_channeling_from_skill
            self.world.add_component(self.player_entity_id,
                build_channeling_from_skill(skill, aoe_x, aoe_y))
            if combat_state:
                from engine.stat_fns import enter_combat as _ec_cf
                _ec_cf(combat_state)
            skill.current_cooldown = skill.cooldown
            return True

        # Modo cliente (offline / aiming online): adiciona componente de mira AOE
        if self.world.get_component(self.player_entity_id, AoeTargeting):
            return False  # já em modo de mira

        self._cancel_pursuit_for_targeting()
        self.world.add_component(self.player_entity_id, AoeTargeting(
            spell_id         = skill.skill_id,
            radius_tiles     = skill.params.get("radius_tiles", 1.0),
            cast_range_tiles = float(skill.cast_range),
        ))
        LOG.add("Clique para posicionar Calamidade Flamejante.", (255, 200, 80))
        return True

    def _skill_nova_congelante(self, skill, combat_stats, combat_state, tile_move):
        """1s cast (reduzido por Precisão Elemental) — raiz 5s a 3 tiles + 50% SP."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not self._check_mana(char_stats, skill.mana_cost):
            return False

        # Precisão Elemental reduz o cast time (pir_precisao_elemental)
        effective_cast = max(0.0, skill.cast_time
                             - getattr(combat_stats, "ice_cast_time_reduction", 0.0))

        # Modo servidor: agenda conclusão diferida
        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid": self.player_entity_id,
                "spell_id":   "nova_congelante",
                "target_id":  -1,
                "timer":      effective_cast,
                "mana_cost":  skill.mana_cost,
                "cooldown":   skill.cooldown,
            })
            if combat_state:
                enter_combat(combat_state)
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id  = "nova_congelante",
            cast_time = effective_cast,
            elapsed   = 0.0,
            target_id = -1,            # AOE — sem alvo único
            mana_cost = skill.mana_cost,
        ))
        if combat_state:
            combat_state.is_casting = True
            enter_combat(combat_state)

        LOG.add("Lançando Nova Congelante...", (100, 180, 255))
        return True

    def _skill_polimorfia(self, skill, combat_stats, combat_state, tile_move):
        """1.5s cast — Transforma o alvo: desorientado + regen 10% HP/s. Custo: 10% mana."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        mana_cost  = max(1, int(char_stats.max_mana * skill.mana_cost_pct)) if char_stats else 1
        if not self._check_mana(char_stats, mana_cost):
            return False

        target_id = self._resolve_target(combat_state, tile_move, skill.cast_range)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False

        if not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return False

        # Modo servidor: agenda conclusão diferida
        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid": self.player_entity_id,
                "spell_id":   "polimorfia",
                "target_id":  target_id,
                "timer":      skill.cast_time,
                "mana_cost":  mana_cost,
                "cooldown":   skill.cooldown,
            })
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id  = "polimorfia",
            cast_time = skill.cast_time,
            elapsed   = 0.0,
            target_id = target_id,
            mana_cost = mana_cost,  # deduzido ao completar, não aqui
        ))
        if combat_state:
            combat_state.is_casting = True
            # is_pursuing não é setado — skill marcada como offensive=False no catálogo

        SOUNDS.play_spell("polimorfia", "cast")
        LOG.add("Lançando Polimorfia...", (160, 80, 200))
        return True

    def _skill_bloco_de_gelo(self, skill, combat_stats, combat_state, tile_move):
        """Imunidade + cura 10% HP/s durante 5s. Imóvel durante efeito.

        is_immune só bloqueia dano/tick NOVO enquanto ativo (ver
        core_systems.StatusEffectSystem._apply_tick) — não remove efeitos
        negativos já ativos (slow/root/fear/etc. continuavam agindo
        normalmente, e DOTs só "pausavam" o tick, retomando ao expirar o
        bloco). A descrição da skill promete "imune a todo dano e efeito
        negativo" — dispela tudo que já está ativo, igual ao padrão já usado
        por Camuflagem (lá só DOTs; aqui TODO efeito com is_buff=False, pois
        a skill promete isso de forma explícita). StatusEffectSystem
        resincroniza slow_mult/is_rooted/is_crowd_controlled automaticamente
        no próximo tick — não precisa replicar isso aqui."""
        if self.world.get_component(self.player_entity_id, IceBlockEffect):
            self._warn("Bloco de Gelo já ativo")
            return False

        from engine.components import StatusEffects as _SFX_ib
        from content.status_effects_data import EFFECT_DEFS as _ED_ib
        sfx = self.world.get_component(self.player_entity_id, _SFX_ib)
        if sfx:
            for _eff_name in list(sfx.effects.keys()):
                _def = _ED_ib.get(_eff_name)
                if _def and not _def.is_buff:
                    sfx.remove(_eff_name)

        self.world.add_component(self.player_entity_id, IceBlockEffect(
            duration=5.0, elapsed=0.0, heal_interval=1.0, last_heal=0.0,
        ))
        if self._server_pending_spells is None:
            SOUNDS.play_spell("bloco_de_gelo", "cast")
        if combat_state:
            combat_state.is_stunned = True
            combat_state.is_immune  = True

        skill.current_cooldown = skill.cooldown
        LOG.add("Bloco de Gelo ativado! Imune por 5s.", (100, 180, 255))
        return True

    def _skill_escudo_fogo(self, skill, combat_stats, combat_state, tile_move):
        """Escudo de Fogo — retaliation de fogo em atacantes por 15s. 25 mana / 20s CD."""
        from engine.components import FireShieldEffect
        _server_mode = self._server_pending_spells is not None
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if not self._check_mana(char_stats, 25):
            return False
        if self.world.get_component(self.player_entity_id, FireShieldEffect):
            self._warn("Escudo de Fogo já ativo")
            return False

        char_stats.mana -= 25
        # Sync CombatStats.mana para STATS_UPDATE ficar consistente
        from engine.components import CombatStats as _CombatEF
        _cs_ef = self.world.get_component(self.player_entity_id, _CombatEF)
        if _cs_ef:
            _cs_ef.mana = char_stats.mana
        self.world.add_component(self.player_entity_id, FireShieldEffect(duration=15.0))
        skill.current_cooldown = skill.cooldown
        if not _server_mode:
            LOG.add("Escudo de Fogo ativado! (15s)", (255, 120, 0))
            SOUNDS.play_skill("skill_fire_shield")
        return True

    # Cone base de Pirofagia (mesmo _PIRO_CONE de spell_system.py) — cópia para
    # evitar import circular: skill_handlers → spell_system → systems → skill_handlers
    _PIRO_CONE = (
        (1,  0),
        (2,  0),
        (3, -1), (3,  0), (3,  1),
        (4, -2), (4, -1), (4,  0), (4,  1), (4,  2),
    )

    def _piro_cone_tiles(self, tile_x: int, tile_y: int, dir_x: float, dir_y: float) -> set:
        """Retorna conjunto de tiles no cone de Pirofagia apontado para (dir_x, dir_y)."""
        import math as _math
        angle  = _math.atan2(dir_y, dir_x)
        cos_a, sin_a = _math.cos(angle), _math.sin(angle)
        tiles  = set()
        for dx, dy in self._PIRO_CONE:
            tiles.add((tile_x + round(dx * cos_a - dy * sin_a),
                       tile_y + round(dx * sin_a + dy * cos_a)))
        return tiles

    def _skill_pirofagia(self, skill, combat_stats, combat_state, tile_move):
        """Pirofagia — segura a tecla para mirar o cone, solte para disparar. 75 mana / 90s CD.

        Modo servidor: dir_x/dir_y injetados por world_server._process_skill_requests →
          executa cone diretamente (sem state machine de mira).
        Modo cliente (offline): adiciona PirofagiaAiming → PirofagiaSystem gerencia click.
        """
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)

        # ── Modo servidor: CAST_SKILL chegou com direção → dispara cone imediatamente ──
        dir_x = getattr(tile_move, "_server_dir_x", 0.0)
        dir_y = getattr(tile_move, "_server_dir_y", 0.0)
        if dir_x != 0.0 or dir_y != 0.0:
            if not self._check_mana(char_stats, skill.mana_cost):
                return False
            if not char_stats:
                return False
            char_stats.mana -= skill.mana_cost
            skill.current_cooldown = skill.cooldown

            from ui.spell_system import _apply_magic_damage
            pos_p = self.world.get_component(self.player_entity_id, Position)
            tm_p  = tile_move
            if not pos_p or not tm_p or not combat_stats:
                return False
            cone  = self._piro_cone_tiles(tm_p.current_tile_x, tm_p.current_tile_y,
                                          dir_x, dir_y)
            hit   = 0
            from engine.faction_system import can_engage as _can_engage_piro
            for eid, etm, ecs in self.world.get_entities_with(TileMovement, CombatStats):
                if eid == self.player_entity_id:
                    continue  # não afeta o próprio caster
                if ecs.current_hp <= 0:
                    continue
                # can_engage bloqueia alvo amigável — sem este check, o dano já
                # saía zerado (apply_damage_core::blocked_friendly), mas o
                # "disoriented" abaixo era aplicado incondicionalmente pra
                # QUALQUER um no cone, amigável ou não (bug real relatado pelo
                # usuário 20/07/2026). Mesmo gate que outras AoE já usam
                # (server/world_server.py::_combat_targets, usado por
                # _server_nova_congelante).
                if not _can_engage_piro(self.world, self.player_entity_id, eid):
                    continue
                if (etm.current_tile_x, etm.current_tile_y) in cone:
                    from content.skill_config import SKILL_CATALOG as _SC_piro
                    _piro_d = _SC_piro.get("pirofagia", {})
                    _piro_p = _piro_d.get("params", {})
                    _base = _piro_p.get("base_dmg", 150)
                    _coef = _piro_p.get("sp_coeff", 1.50)
                    _dis_dur = _piro_d.get("effect_durations", {}).get("disoriented", 3.0)
                    dmg = max(1, _base + int(combat_stats.spell_power * _coef))
                    # Em modo servidor: crit calculado via _server_apply_magic_damage (roll_crit).
                    # Em modo cliente (offline): _apply_magic_damage não tem crit — usa-se o
                    # PlayerProjectileSystem para skills com projétil, mas Pirofagia é cone direto.
                    # TODO: adicionar crit offline para Pirofagia via resolve_attack_outcome.
                    _apply_magic_damage(self.player_entity_id, eid, dmg, self.world)
                    apply_effect(self.world, eid, "disoriented", _dis_dur)
                    hit += 1
            if hit > 0:
                LOG.add(f"Pirofagia! {hit} alvo(s) atingido(s).", (255, 100, 30))
            if combat_state:
                from engine.stat_fns import enter_combat as _ec_piro
                _ec_piro(combat_state)
            return True

        # ── Modo cliente (offline): state machine de mira ────────────────────
        from engine.components import PirofagiaAiming
        if not self._check_mana(char_stats, skill.mana_cost):
            return False
        if self.world.get_component(self.player_entity_id, PirofagiaAiming):
            return False   # já está mirando

        # Mana e cooldown só são aplicados ao disparar (ver PirofagiaSystem._fire_cone)
        self.world.add_component(self.player_entity_id, PirofagiaAiming())
        LOG.add("Pirofagia — aponte e clique para disparar. (Dir. cancela)", (255, 100, 30))
        return True

    def _skill_calcinar(self, skill, combat_stats, combat_state, tile_move):
        """0.6s cast em movimento — 50 + 25% SP. Escola fogo. 15 mana. Sem cooldown."""
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        pyr_discount = int(skill.mana_cost * getattr(combat_stats, "pyromania_bonus", 0.0))
        effective_cost = max(0, skill.mana_cost - pyr_discount)
        if not self._check_mana(char_stats, effective_cost):
            return False

        target_id = self._resolve_target(combat_state, tile_move, skill.cast_range)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False
        if not self._target_alive(target_id):
            self._warn("Alvo inválido")
            return False

        # Modo servidor: agenda conclusão diferida
        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid": self.player_entity_id,
                "spell_id":   "calcinar",
                "target_id":  target_id,
                "timer":      skill.cast_time,
                "mana_cost":  effective_cost,
                "cooldown":   skill.cooldown,
            })
            if combat_state:
                enter_combat(combat_state)
            return True

        # interruptible=False — cast não é cancelado por movimento
        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id      = "calcinar",
            cast_time     = skill.cast_time,
            elapsed       = 0.0,
            target_id     = target_id,
            mana_cost     = effective_cost,
            interruptible = False,
        ))
        if combat_state:
            # Não seta is_casting — jogador pode continuar movendo normalmente
            enter_combat(combat_state)
        LOG.add("Calcinando...", (255, 140, 40))
        return True

    # ------------------------------------------------------------------
    def _is_concentration_free(self) -> bool:
        """Retorna True se o buff 'Só um Gole' está ativo (Concentração grátis)."""
        cs = self.world.get_component(self.player_entity_id, __import__("engine.components", fromlist=["CombatStats"]).CombatStats)
        return bool(cs and cs.concentration_free)

    def _check_concentration(self, char_stats, cost: int) -> bool:
        """Verifica se o player tem Concentração suficiente (respeita buff grátis).
        Retorna True se pode usar, False (e emite aviso) se não pode."""
        if self._is_concentration_free():
            return True
        if not char_stats or char_stats.concentration < cost:
            cur = int(char_stats.concentration) if char_stats else 0
            self._warn(f"Concentração insuficiente ({cur}/{cost})")
            return False
        return True

    # ------------------------------------------------------------------
    def _skill_tiro_multiplo(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — cone de 90° na direção do mouse. 60 Conc. CD 90s."""
        from engine.components import CharacterStats, Equipment, CombatStats as _CS
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        equip      = self.world.get_component(self.player_entity_id, Equipment)
        cs         = self.world.get_component(self.player_entity_id, _CS)

        cost = skill.params.get("concentration_cost", 60)
        if not self._check_concentration(char_stats, cost):
            return False

        max_targets = getattr(cs, "tiro_multiplo_targets", 0)
        if max_targets == 0:
            self._warn("Talento insuficiente para Tiro Múltiplo.")
            return False

        # Validação de equipamento — roda em ambos os lados (cliente E servidor).
        # Servidor valida tudo: sem isso, o handler server-side pulava a checagem
        # e enfileirava o cast mesmo sem arco/aljava/flechas.
        bow    = equip.slots.get("mainhand") if equip else None
        quiver = equip.slots.get("offhand")  if equip else None
        if not bow or getattr(bow, "subtype", "") != "Bow":
            self._warn("Precisa de um arco equipado.")
            return False
        if not quiver or getattr(quiver, "item_type", "") != "quiver" or quiver.arrow_count < 1:
            self._warn("Aljava vazia! Use Recarregar.")
            return False

        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid":         self.player_entity_id,
                "spell_id":           "tiro_multiplo",
                "target_id":          -1,
                "timer":              skill.cast_time,
                "concentration_cost": cost,
                "cooldown":           skill.cooldown,
                "dir_x":              getattr(tile_move, "_server_dir_x", 0.0),
                "dir_y":              getattr(tile_move, "_server_dir_y", 0.0),
            })
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id           = "tiro_multiplo",
            cast_time          = skill.cast_time,
            elapsed            = 0.0,
            target_id          = -1,
            mana_cost          = 0,
            concentration_cost = cost,
            interruptible      = True,
        ))
        LOG.add("Tiro Múltiplo...", (150, 220, 255))
        return True

    # ------------------------------------------------------------------
    def _skill_camuflagem(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — disfarça-se com a capa (sprite animado idle/run); inalvejável
        em PvP/PvE (imune a dano, DOTs dispelados), velocidade 75%, inimigos
        perdem o alvo."""
        from engine.components import CharacterStats, CombatStats as _CS, TileMovement as _TM
        from engine.components import AIControlled, Enemy, CombatState as _CSt, StatusEffects as _SFX_cam
        from engine.entity_disguise import discover_sprite_variants
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        cs         = self.world.get_component(self.player_entity_id, _CS)

        cost = skill.params.get("concentration_cost", 50)
        if not self._check_concentration(char_stats, cost):
            return False

        if cs and cs.camouflage_timer > 0:
            self._warn("Já está camuflado.")
            return False

        duration  = skill.params.get("duration",   5.0)
        speed_pct = skill.params.get("speed_pct",  0.60)

        # Escolhe uma variante de disfarce (sufixo "", "_2", "_3"... conforme
        # os pares camuflagem_idle{suf}/camuflagem_run{suf} existentes em
        # assets/sprites/ — descoberta dinâmica, sem precisar editar código
        # ao adicionar uma nova variante).
        _variants     = discover_sprite_variants("camuflagem")
        chosen_object = random.choice(_variants) if _variants else ""

        # Reduz velocidade de movimento
        tm = self.world.get_component(self.player_entity_id, _TM)
        if tm:
            tm.speed = 110.0 * speed_pct

        # Ativa timer e armazena a variante do disfarce (RenderSystem desenha
        # o sprite animado idle/run enquanto camouflage_timer > 0)
        if cs:
            cs.camouflage_timer  = duration
            cs.camouflage_object = chosen_object

        player_cst = self.world.get_component(self.player_entity_id, _CSt)
        if player_cst:
            player_cst.is_visible    = False
            player_cst.is_immune     = True   # inalvejável: nenhum dano (PvE/PvP/DOT) passa
            player_cst.is_camouflaged = True  # bloqueia can_act(): nao pode atacar/usar skill
            # No servidor, self é o SkillSystem (não o WorldServer) — registra a
            # mudança na lista bridge (ver skill_processor.py) pra _build_update_for_session
            # reavaliar _can_see() mesmo sem o player ter se movido neste tick.
            _vis_list = getattr(self, "_server_visibility_changed", None)
            if _vis_list is not None:
                _vis_list.append(self.player_entity_id)

        # DOTs ativos são dispelados ao camuflar (não ficam só pausados pela
        # imunidade — o usuário quer o efeito removido de fato).
        sfx = self.world.get_component(self.player_entity_id, _SFX_cam)
        if sfx:
            for _dot in ("poison", "bleed", "burn"):
                sfx.remove(_dot)

        # Inimigos perdem o alvo e retornam ao respawn
        for _eid, _ai, _ in self.world.get_entities_with(AIControlled, Enemy):
            if _ai.state in ("CHASING", "ATTACKING", "AGGRO_DELAY"):
                _ai.state             = "RETURNING"
                _ai.aggroed_by_damage = False
                _ai.path_recalc_timer = 0.0

        # PvP: outros players que já tinham o arqueiro selecionado perdem o
        # alvo — "inalvejável" inclui quem já travou antes de camuflar, não só
        # bloqueia seleção de alvo novo (que is_visible=False já impede).
        for _other_eid, _other_cst in self.world.get_entities_with(_CSt):
            if _other_eid != self.player_entity_id and _other_cst.target_entity_id == self.player_entity_id:
                _other_cst.target_entity_id = -1

        skill.current_cooldown = skill.cooldown
        LOG.add("Camuflagem!", (160, 220, 160))
        SOUNDS.play_skill("skill_camuflagem")
        return True

    # ------------------------------------------------------------------
    def _skill_tiro_repulsivo(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — repele o alvo 5 tiles; colisão = stun 3s. 100 Concentração."""
        from engine.components import CharacterStats, Equipment
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        equip      = self.world.get_component(self.player_entity_id, Equipment)

        cost = skill.params.get("concentration_cost", 100)
        if not self._check_concentration(char_stats, cost):
            return False

        target_id = self._resolve_target(combat_state, tile_move, skill.cast_range)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False

        # Validação de equipamento — roda em ambos os lados (cliente E servidor).
        # Servidor valida tudo: sem isso, o handler server-side pulava a checagem
        # e enfileirava o cast mesmo sem arco/aljava/flechas.
        bow    = equip.slots.get("mainhand") if equip else None
        quiver = equip.slots.get("offhand")  if equip else None
        if not bow or getattr(bow, "subtype", "") != "Bow":
            self._warn("Precisa de um arco equipado.")
            return False
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            self._warn("Precisa de uma aljava equipada.")
            return False
        if quiver.arrow_count < 1:
            self._warn("Aljava vazia! Use Recarregar.")
            return False

        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid":         self.player_entity_id,
                "spell_id":           "tiro_repulsivo",
                "target_id":          target_id,
                "timer":              skill.cast_time,
                "concentration_cost": cost,
                "cooldown":           skill.cooldown,
            })
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id           = "tiro_repulsivo",
            cast_time          = skill.cast_time,
            elapsed            = 0.0,
            target_id          = target_id,
            mana_cost          = 0,
            concentration_cost = cost,
            interruptible      = True,
        ))
        LOG.add("Tiro Repulsivo...", (120, 200, 255))
        return True

    # ------------------------------------------------------------------
    def _skill_cancao_inspiracao(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — Buff: +30% ataque por 20s. CD: 360s."""
        from engine.stat_fns import add_timed_modifier
        from engine.components import Modifier

        ap_pct   = skill.params.get("ap_bonus_pct", 0.30)
        duration = skill.params.get("duration",     20.0)

        _mod = Modifier("attack_power", ap_pct, "percentage", source="buff")
        add_timed_modifier(combat_stats, _mod, duration, label="cancao_inspiracao")

        skill.current_cooldown = skill.cooldown
        LOG.add(f"Canção da Inspiração! +{int(ap_pct*100)}% ataque por {int(duration)}s.", (220, 200, 120))
        SOUNDS.play_skill("skill_cancao_inspiracao")
        return True

    # ------------------------------------------------------------------
    def _skill_so_um_gole(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — Buff: Concentração grátis + acerto 100% por 10s."""
        from engine.components import CharacterStats, CombatStats
        from engine.stat_fns import add_timed_modifier
        from engine.components import Modifier
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        cs         = self.world.get_component(self.player_entity_id, CombatStats)
        if not cs:
            return False

        duration   = skill.params.get("duration",    10.0)
        acerto_bns = skill.params.get("acerto_flat", 100.0)

        # Ativa o buff
        cs.concentration_free       = True
        cs.concentration_free_timer = duration

        # Timed modifier que garante acerto = 100 (bônus flat grande)
        _mod = Modifier("acerto", acerto_bns, "flat", source="buff")
        add_timed_modifier(cs, _mod, duration, label="so_um_gole")

        skill.current_cooldown = skill.cooldown   # aplica CD de 120s
        LOG.add(f"Só um Gole! Acerto 100% + Concentração grátis por {int(duration)}s.", (180, 220, 255))
        SOUNDS.play_skill("skill_so_um_gole")
        return True

    # ------------------------------------------------------------------
    def _skill_cancao_ninar(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — AoE sleep 8s em raio 5 tiles. Canal de 2s. 25 Concentração."""
        from engine.components import CharacterStats, StatusEffects, TileMovement as _TM
        from engine.utils import chebyshev
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)

        cost = skill.params.get("concentration_cost", 25)
        if not self._check_concentration(char_stats, cost):
            return False

        radius  = skill.params.get("radius", 5)
        sleep_d = skill.params.get("sleep_duration",    8.0)
        slow_d  = skill.params.get("slow_duration",     5.0)
        slow_m  = skill.params.get("slow_magnitude",    0.30)

        player_tm = self.world.get_component(self.player_entity_id, _TM)
        if not player_tm:
            return False
        px, py = player_tm.current_tile_x, player_tm.current_tile_y

        # Aplica sono imediatamente a todos os inimigos no raio — itera
        # CombatStats (não Enemy/AIControlled) para incluir players em PvP,
        # igual ao padrão de _skill_impacto.
        from engine.faction_system import can_engage as _can_engage_ninar
        targets = []
        for eid, etm, tgt_cs in self.world.get_entities_with(_TM, CombatStats):
            if eid == self.player_entity_id:
                continue
            if tgt_cs.current_hp <= 0:
                continue
            # can_engage bloqueia alvo amigável — sem este check, "sleep"
            # era aplicado incondicionalmente pra QUALQUER um no raio,
            # amigável ou não (bug real relatado pelo usuário 22/07/2026,
            # mesma classe já corrigida em Pirofagia 20/07/2026).
            if not _can_engage_ninar(self.world, self.player_entity_id, eid):
                continue
            if chebyshev(px, py, etm.current_tile_x, etm.current_tile_y) <= radius:
                # Efeito de sono com on_expire_effect → slow
                apply_effect(self.world, eid, "sleep", sleep_d,
                             on_expire_effect=("slow" if slow_d > 0 else ""),
                             on_expire_duration=slow_d,
                             on_expire_magnitude=slow_m)
                targets.append(eid)

        char_stats.lullaby_targets = list(targets)

        # Para o arqueiro de atacar durante o canal (não-ofensiva)
        if combat_state:
            combat_state.is_pursuing = False

        # Toca o som de início do canal manualmente (cast_time > 0 impede play automático)
        SOUNDS.play_skill("skill_cancao_ninar")

        # Canal de 2s — se cancelado, _cancel_cancao_ninar acorda os alvos
        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid":         self.player_entity_id,
                "spell_id":           "cancao_ninar",
                "target_id":          -1,
                "timer":              skill.cast_time,
                "concentration_cost": cost,
                "cooldown":           skill.cooldown,
            })
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id           = "cancao_ninar",
            cast_time          = skill.cast_time,
            elapsed            = 0.0,
            target_id          = -1,
            mana_cost          = 0,
            concentration_cost = cost,
            interruptible      = True,
            on_cancel          = "_cancel_cancao_ninar",
        ))
        LOG.add("Canção de Ninar... (mantenha posição!)", (160, 200, 255))
        return True

    # ------------------------------------------------------------------
    def _skill_picada_escorpiao(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — flecha precisa com slow. Custa 20 de Concentração."""
        from engine.components import Equipment, CharacterStats
        equip      = self.world.get_component(self.player_entity_id, Equipment)
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)

        cost = skill.params.get("concentration_cost", 20)
        if not self._check_concentration(char_stats, cost):
            return False

        target_id = self._resolve_target(combat_state, tile_move, skill.cast_range)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False

        # Validação de equipamento — roda em ambos os lados (cliente E servidor).
        # Servidor valida tudo: sem isso, o handler server-side pulava a checagem
        # e enfileirava o cast mesmo sem arco/aljava/flechas.
        bow    = equip.slots.get("mainhand") if equip else None
        quiver = equip.slots.get("offhand")  if equip else None
        if not bow or getattr(bow, "subtype", "") != "Bow":
            self._warn("Precisa de um arco equipado.")
            return False
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            self._warn("Precisa de uma aljava equipada.")
            return False
        if quiver.arrow_count < 1:
            self._warn("Aljava vazia! Use Recarregar.")
            return False

        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid":         self.player_entity_id,
                "spell_id":           "picada_escorpiao",
                "target_id":          target_id,
                "timer":              skill.cast_time,
                "concentration_cost": cost,
                "cooldown":           skill.cooldown,
            })
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id           = "picada_escorpiao",
            cast_time          = skill.cast_time,
            elapsed            = 0.0,
            target_id          = target_id,
            mana_cost          = 0,
            concentration_cost = cost,
            interruptible      = True,
        ))
        LOG.add("Picada de Escorpião...", (160, 220, 100))
        return True

    # ------------------------------------------------------------------
    def _skill_flecha_reiterada(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — 2 flechas em sequência. Custa 80 de Concentração."""
        from engine.components import Equipment, CharacterStats
        equip      = self.world.get_component(self.player_entity_id, Equipment)
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)

        cost = skill.params.get("concentration_cost", 80)
        if not self._check_concentration(char_stats, cost):
            return False

        target_id = self._resolve_target(combat_state, tile_move, skill.cast_range)
        if target_id == -1:
            self._warn("Nenhum alvo")
            return False

        # Validação de equipamento — roda em ambos os lados (cliente E servidor).
        # Servidor valida tudo: sem isso, o handler server-side pulava a checagem
        # e enfileirava o cast mesmo sem arco/aljava/flechas.
        bow    = equip.slots.get("mainhand") if equip else None
        quiver = equip.slots.get("offhand")  if equip else None
        if not bow or getattr(bow, "subtype", "") != "Bow":
            self._warn("Precisa de um arco equipado.")
            return False
        if not quiver or getattr(quiver, "item_type", "") != "quiver":
            self._warn("Precisa de uma aljava equipada.")
            return False
        arrows_needed = skill.params.get("arrow_count", 2)
        if quiver.arrow_count < arrows_needed:
            self._warn(f"Flechas insuficientes na aljava ({quiver.arrow_count}/{arrows_needed})")
            return False

        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid":         self.player_entity_id,
                "spell_id":           "flecha_reiterada",
                "target_id":          target_id,
                "timer":              skill.cast_time,
                "concentration_cost": cost,
                "cooldown":           skill.cooldown,
            })
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id            = "flecha_reiterada",
            cast_time           = skill.cast_time,
            elapsed             = 0.0,
            target_id           = target_id,
            mana_cost           = 0,
            concentration_cost  = cost,
            interruptible       = True,
        ))
        LOG.add("Flecha Reiterada...", (180, 220, 255))
        return True

    # ------------------------------------------------------------------
    def _skill_recarregar(self, skill, combat_stats, combat_state, tile_move):
        """Arqueiro — 1.8s cast que reabastece a aljava com o primeiro ammo da bag."""
        from engine.components import Equipment, Inventory
        equip = self.world.get_component(self.player_entity_id, Equipment)
        inv   = self.world.get_component(self.player_entity_id, Inventory)

        quiver = equip.slots.get("offhand") if equip else None
        if not quiver or quiver.item_type != "quiver":
            self._warn("Precisa de uma aljava equipada para recarregar.")
            return False

        if quiver.max_arrows == 0:
            quiver.max_arrows = 100

        # Primeiro ammo disponível na bag (ordem dos slots)
        first_arrow = next(
            (it for it in (inv.items if inv else [])
             if it is not None and it.item_type == "ammo" and it.stack > 0),
            None
        )
        if not first_arrow:
            self._warn("Não há flechas disponíveis para recarregar.")
            return False

        # Se aljava está cheia do mesmo tipo, não há nada a fazer
        same_type = (quiver.subtype == first_arrow.name and
                     quiver.arrow_count >= quiver.max_arrows)
        if same_type:
            self._warn("Aljava já está cheia.")
            return False

        # Talento Prático: permite recarga em movimento
        _in_motion = getattr(combat_stats, "recarregar_in_motion", False)

        _server_pending = self._server_pending_spells
        if _server_pending is not None:
            _server_pending.append({
                "player_eid":         self.player_entity_id,
                "spell_id":           "recarregar",
                "target_id":          -1,
                "timer":              skill.cast_time,
                "concentration_cost": 0,
                "cooldown":           skill.cooldown,
            })
            return True

        self.world.add_component(self.player_entity_id, SpellCast(
            spell_id      = "recarregar",
            cast_time     = skill.cast_time,
            elapsed       = 0.0,
            target_id     = -1,
            mana_cost     = 0,
            interruptible = not _in_motion,
        ))
        LOG.add(f"Recarregando: {first_arrow.name}...", (200, 160, 80))
        return True
