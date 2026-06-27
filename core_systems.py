"""
core_systems.py — Sistemas ECS compartilhados entre cliente e servidor.

Regra: NENHUMA dependência de Pygame aqui.
Sistemas visuais (FLT, LOG, PROC, sons) ficam nas subclasses de cada lado.

Exporta:
  apply_effect()              — aplica/atualiza status effect numa entidade
  StatusEffectSystem          — processa ciclo de vida de status effects (ticks, expiração)
  BaseCombatStateSystem       — núcleo headless: timers de combate, rage, HP5, concentração
  ServerCombatStateSystem     — herda Base; adiciona hp5_events para o servidor
"""
from __future__ import annotations


# ── apply_effect ──────────────────────────────────────────────────────────────

def apply_effect(
    world,
    entity_id: int,
    effect_type: str,
    duration: float,
    magnitude: float = 0.0,
    tick_interval: float | None = None,
    on_expire_effect: str = "",
    on_expire_duration: float = 0.0,
    on_expire_magnitude: float = 0.0,
) -> None:
    """
    Aplica ou atualiza um efeito de estado em uma entidade.

    - Se o efeito não existir: cria e adiciona.
    - Se já existir: refresh pelo maior valor (duração e magnitude).
    - Cria o componente StatusEffects automaticamente se necessário.
    - on_expire_effect: efeito encadeado aplicado quando este expira.
    """
    from components import StatusEffects, ActiveEffect
    from status_effects_data import EFFECT_DEFS

    defn = EFFECT_DEFS.get(effect_type)
    if defn is None:
        return

    # Boneco de treino: recebe DoT e slow para testes de dano/talentos,
    # mas não recebe polymorph (transformação não faz sentido num manequim).
    if effect_type == "polymorph":
        from components import TrainingDummy as _TDcheck
        if world.get_component(entity_id, _TDcheck) is not None:
            return

    sfx = world.get_component(entity_id, StatusEffects)
    if sfx is None:
        sfx = StatusEffects()
        world.add_component(entity_id, sfx)

    existing = sfx.get(effect_type)
    if existing is not None:
        existing.duration  = max(existing.duration, duration)
        existing.magnitude = max(existing.magnitude, magnitude)
        return

    resolved_tick = tick_interval if tick_interval is not None else defn.tick_interval
    sfx.effects[effect_type] = ActiveEffect(
        effect_type=effect_type,
        duration=duration,
        magnitude=magnitude,
        tick_interval=resolved_tick,
        on_expire_effect=on_expire_effect,
        on_expire_duration=on_expire_duration,
        on_expire_magnitude=on_expire_magnitude,
    )


# ── StatusEffectSystem ────────────────────────────────────────────────────────

class StatusEffectSystem:
    """
    Gerencia o ciclo de vida de todos os efeitos de estado (buffs/debuffs).

    Responsabilidades:
      - Decrementar duração de cada ActiveEffect por dt.
      - Disparar dano/cura periódica (poison, bleed, burn, regen).
      - Remover efeitos expirados e encadear on_expire_effect.
      - Sincronizar TileMovement.slow_mult a cada frame.
      - Sincronizar CombatState.is_rooted e is_crowd_controlled.

    Hooks virtuais (sobrescrever em subclasses):
      _emit_damage(eid, amount, effect_type, pos, color)
      _emit_heal  (eid, amount, effect_type, pos, color)

    Servidor: sobrescreve para emitir COMBAT_RESULT.
    Cliente : sobrescreve para chamar FLT.add().
    """

    def __init__(self, world) -> None:
        self.world = world

    # ── Loop principal ─────────────────────────────────────────────────────

    def update(self, events=None, dt: float = 0) -> None:
        from components import (StatusEffects, TileMovement, CombatState,
                                 CombatStats, Position, PlayerControlled,
                                 PendingDeath)
        from status_effects_data import EFFECT_DEFS

        for eid, sfx in self.world.get_entities_with(StatusEffects):
            if not sfx.effects:
                continue

            to_remove = []
            for effect in list(sfx.effects.values()):
                effect.duration -= dt

                if effect.tick_interval > 0:
                    effect.tick_timer -= dt
                    if effect.tick_timer <= 0:
                        effect.tick_timer += effect.tick_interval
                        self._apply_tick(eid, effect)

                if effect.duration <= 0:
                    to_remove.append((
                        effect.effect_type,
                        effect.on_expire_effect,
                        effect.on_expire_duration,
                        effect.on_expire_magnitude,
                    ))

            for key, expire_eff, expire_dur, expire_mag in to_remove:
                sfx.effects.pop(key, None)
                if expire_eff:
                    apply_effect(self.world, eid, expire_eff, expire_dur,
                                 magnitude=expire_mag)

            # Sincroniza slow_mult no TileMovement
            tm = self.world.get_component(eid, TileMovement)
            if tm:
                slow = sfx.get("slow")
                if slow:
                    tm.slow_mult = slow.magnitude if 0.0 < slow.magnitude < 1.0 else 0.5
                elif sfx.has("disoriented") or sfx.has("polymorph"):
                    # Disoriented/polymorph: 50% da velocidade normal.
                    # Sem guard de CombatStats — aplica a qualquer entidade com
                    # TileMovement+StatusEffects (local, remota, com ou sem CombatStats).
                    tm.slow_mult = 0.5
                else:
                    # Sem slow/disoriented/polymorph ativos: reseta para velocidade normal.
                    # Aplica a TODAS as entidades com TileMovement+StatusEffects — incluindo
                    # mobs remotos online. O slow agora é aplicado localmente via apply_effect
                    # (com duração real do servidor) e expira naturalmente por aqui.
                    tm.slow_mult          = 1.0
                    tm.debilitate_elapsed = 0.0

            # Sincroniza is_rooted
            cst = self.world.get_component(eid, CombatState)
            if cst:
                cst.is_rooted = sfx.has("root")

            # Sincroniza is_crowd_controlled (lido por damage_calculator)
            cs_cc = self.world.get_component(eid, CombatStats)
            if cs_cc:
                _CC = ("stun", "sleep", "fear", "polymorph", "slow",
                       "disoriented", "root")
                cs_cc.is_crowd_controlled = any(sfx.has(e) for e in _CC)

    # ── Tick de dano/cura ──────────────────────────────────────────────────

    def _apply_tick(self, eid: int, effect) -> None:
        from components import (CombatStats, CombatState, Position,
                                 PlayerControlled, PendingDeath)
        from status_effects_data import EFFECT_DEFS

        cs  = self.world.get_component(eid, CombatStats)
        pos = self.world.get_component(eid, Position)
        if not cs or cs.current_hp <= 0:
            return

        # DoT não pode ser esquivado, aparado ou reduzido por armadura.
        # Entidades imunes (Bloco de Gelo) são a única exceção.
        cst = self.world.get_component(eid, CombatState)
        if cst and cst.is_immune:
            return

        defn  = EFFECT_DEFS.get(effect.effect_type)
        color = defn.color if defn else (255, 255, 255)
        # Dano mínimo garantido de 1 — magnitude nunca resulta em 0
        dmg   = max(1, round(effect.magnitude))

        if effect.effect_type == "regen":
            healed = min(dmg, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + dmg)
            if healed > 0:
                self._emit_heal(eid, healed, effect.effect_type, pos, color)

        elif effect.effect_type == "polymorph":
            healed = min(dmg, cs.max_hp - cs.current_hp)
            cs.current_hp = min(cs.max_hp, cs.current_hp + dmg)
            if healed > 0:
                self._emit_heal(eid, healed, effect.effect_type, pos, color)

        elif effect.effect_type == "elemental_lapse":
            burn = max(1, int(cs.max_hp * 0.01))
            cs.current_hp = max(1, cs.current_hp - burn)
            self._emit_damage(eid, burn, effect.effect_type, pos, color)

        else:
            # DoT genérico (poison, bleed, burn, ...)
            cs.current_hp = max(0, cs.current_hp - dmg)
            self._emit_damage(eid, dmg, effect.effect_type, pos, color)

            if cs.current_hp <= 0:
                is_player = self.world.get_component(eid, PlayerControlled) is not None
                if not is_player and not self.world.get_component(eid, PendingDeath):
                    # Tenta atribuir o kill ao player que causou o efeito
                    killer_id = -1
                    for pid, _ in self.world.get_entities_with(PlayerControlled):
                        killer_id = pid
                        break
                    self.world.add_component(eid, PendingDeath(killer_entity_id=killer_id))

    # ── Hooks visuais (sem implementação base) ────────────────────────────

    def _emit_damage(self, eid: int, amount: int, effect_type: str,
                     pos, color: tuple) -> None:
        """Sobrescrever: cliente → FLT; servidor → COMBAT_RESULT."""
        pass

    def _emit_heal(self, eid: int, amount: int, effect_type: str,
                   pos, color: tuple) -> None:
        """Sobrescrever: cliente → FLT; servidor → COMBAT_RESULT."""
        pass


# ── BaseCombatStateSystem ─────────────────────────────────────────────────────

class BaseCombatStateSystem:
    """Núcleo headless de CombatStateSystem — compartilhado entre cliente e servidor.

    Regra: NENHUMA dependência de Pygame/FLT/LOG/SOUNDS aqui.

    Subclasses:
      systems.CombatStateSystem        — cliente; adiciona wander de disoriented/polymorph,
                                         camuflagem, timed_modifiers, procs.
      core_systems.ServerCombatStateSystem — servidor; adiciona hp5_events.

    Para adicionar nova lógica de combate (ex.: novo decay de recurso), editar
    apenas aqui — as duas implementações herdam automaticamente.
    """

    RAGE_DECAY_AMOUNT   = 5
    RAGE_DECAY_INTERVAL = 3.0   # segundos entre cada decaimento de Rage

    def __init__(self, world) -> None:
        self.world = world

    # ── Helpers estáticos por componente ──────────────────────────────────

    @staticmethod
    def _tick_combat_timer(cs, dt: float) -> None:
        if cs.in_combat and cs.combat_timer > 0:
            cs.combat_timer -= dt
            if cs.combat_timer <= 0:
                cs.in_combat    = False
                cs.is_pursuing  = False
                cs.combat_timer = 0.0

    @staticmethod
    def _tick_stun_timer(cs, dt: float) -> None:
        if cs.is_stunned and cs.stun_timer > 0:
            cs.stun_timer -= dt
            if cs.stun_timer <= 0:
                cs.is_stunned = False
                cs.stun_timer = 0.0

    @classmethod
    def _tick_rage_decay(cls, cs, char, dt: float) -> None:
        if not char or char.rage <= 0:
            return
        if not cs.in_combat:
            char.rage_decay_timer += dt
            if char.rage_decay_timer >= cls.RAGE_DECAY_INTERVAL:
                char.rage_decay_timer -= cls.RAGE_DECAY_INTERVAL
                char.rage = max(0, char.rage - cls.RAGE_DECAY_AMOUNT)
        else:
            char.rage_decay_timer = 0.0

    @staticmethod
    def _tick_hp5(cs, cst, dt: float):
        """Processa HP5 regen fora de combate.

        Retorna (old_hp, new_hp) se houve cura, None caso contrário.
        O servidor usa o retorno para emitir hp5_events; o cliente ignora.
        """
        if cs.in_combat or cst.current_hp <= 0:
            return None
        if cst.current_hp < cst.max_hp:
            cst.hp5_timer = getattr(cst, 'hp5_timer', 0.0) + dt
            if cst.hp5_timer >= 5.0:
                cst.hp5_timer -= 5.0
                regen  = max(1, int(cst.max_hp * cst.hp5))
                old_hp = cst.current_hp
                cst.current_hp = min(cst.max_hp, cst.current_hp + regen)
                if cst.current_hp != old_hp:
                    return (old_hp, cst.current_hp)
        else:
            cst.hp5_timer = 0.0
        return None

    @staticmethod
    def _tick_concentration_regen(cs, char, cst, tm, dt: float) -> None:
        """Regen de Concentração (Arqueiro). Taxa varia por movimento."""
        if char.max_concentration <= 0 or char.concentration >= char.max_concentration:
            return
        moving = tm.is_moving if tm else False
        rate   = (cst.concentration_regen_moving if moving
                  else cst.concentration_regen_idle)
        if rate > 0:
            char.concentration = min(
                char.max_concentration,
                char.concentration + rate * dt,
            )

    @staticmethod
    def _tick_concentration_free_timer(cst, dt: float) -> None:
        if cst.concentration_free_timer > 0:
            cst.concentration_free_timer -= dt
            if cst.concentration_free_timer <= 0:
                cst.concentration_free       = False
                cst.concentration_free_timer = 0.0

    @staticmethod
    def _tick_standing_seconds(cst, tm, dt: float) -> None:
        """Acumula segundos parado para o talento 'Calmo e Certeiro'."""
        if cst.acerto_per_standing_second <= 0:
            return
        if tm and tm.is_moving:
            cst.standing_seconds = 0.0
        else:
            cst.standing_seconds += dt


# ── ServerCombatStateSystem ───────────────────────────────────────────────────

class ServerCombatStateSystem(BaseCombatStateSystem):
    """Gerencia timers de combate e recursos para players no servidor.

    Herda BaseCombatStateSystem — constantes e lógica core ficam em um só lugar.
    Adiciona hp5_events (curas de regen) e proc_events (procs de item rolados
    autoritativamente, ver _roll_procs) para o servidor reportar ao cliente.

    Uso:
        sys = ServerCombatStateSystem(world)
        sys.update(world_server._player_eids, dt)
        for ev in sys.hp5_events:
            # ev = {player_eid, old_hp, new_hp, hp_max}
            ...
        for ev in sys.proc_events:
            # ev = {player_eid, item_name, label, attribute, value, duration}
            ...
    """

    def __init__(self, world) -> None:
        super().__init__(world)
        # Populado a cada update(); limpo no início do próximo update().
        self.hp5_events: list[dict] = []
        # Procs de item rolados autoritativamente pelo servidor neste tick.
        self.proc_events: list[dict] = []

    def update(self, player_eids: dict, dt: float) -> None:
        """Processa todos os players em player_eids (session_id → eid)."""
        self.hp5_events.clear()
        self.proc_events.clear()
        from components import CombatState, CombatStats, CharacterStats, TileMovement

        for _sid, peid in list(player_eids.items()):
            cs   = self.world.get_component(peid, CombatState)
            cst  = self.world.get_component(peid, CombatStats)
            char = self.world.get_component(peid, CharacterStats)
            if not cs or not cst or cst.current_hp <= 0:
                continue

            tm = self.world.get_component(peid, TileMovement)

            self._tick_combat_timer(cs, dt)
            self._tick_stun_timer(cs, dt)
            self._tick_rage_decay(cs, char, dt)

            hp5_result = self._tick_hp5(cs, cst, dt)
            if hp5_result:
                old_hp, new_hp = hp5_result
                self.hp5_events.append({
                    "player_eid": peid,
                    "old_hp":     old_hp,
                    "new_hp":     new_hp,
                    "hp_max":     cst.max_hp,
                })

            if char:
                self._tick_concentration_regen(cs, char, cst, tm, dt)
                self._tick_concentration_free_timer(cst, dt)
            self._tick_standing_seconds(cst, tm, dt)

            self._tick_timed_modifiers(cst, dt)

            # Procs de item: rolados aqui (autoritativo) em vez de confiar no
            # cliente reportar hp/max_hp via PLAYER_HP_SYNC depois de "rolar"
            # localmente — ver arquitetura/PROBLEMAS_ARQUITETURA.md (vulnerabilidade
            # de HP/proc). O cliente ainda rola sua própria cópia cosmética (LOG/
            # feedback visual); o resultado real e persistido é sempre este.
            if cs._just_entered_combat:
                cs._just_entered_combat = False
                self._roll_procs(peid, cst)

    @staticmethod
    def _tick_timed_modifiers(cst, dt: float) -> None:
        """Expira timed_modifiers (buffs/procs) — equivalente headless de
        systems.py::CombatStateSystem.update() (sem LOG, servidor é headless).

        Sem isso, um modificador aplicado por _roll_procs nunca expirava no
        servidor (autoritativo) — o buff ficaria permanente nos stats reais
        mesmo depois do cliente mostrar o efeito como expirado."""
        if not cst.timed_modifiers:
            return
        from stat_fns import remove_modifier
        for entry in cst.timed_modifiers:
            entry["timer"] -= dt
        expired = [e for e in cst.timed_modifiers if e["timer"] <= 0]
        for entry in expired:
            cst.timed_modifiers.remove(entry)
            _max_before_exp = cst.max_hp
            _pct_hp = cst.current_hp / _max_before_exp if _max_before_exp > 0 else 1.0
            remove_modifier(cst, entry["modifier"])
            if cst.max_hp < _max_before_exp:
                cst.current_hp = max(1, int(_pct_hp * cst.max_hp))

    def _roll_procs(self, entity_id: int, cst) -> None:
        """Rola procs de itens equipados ao entrar em combate.

        Versão server-autoritativa de systems.py::CombatStateSystem._trigger_procs
        — usa o Equipment do próprio servidor (validado contra catálogos, Tier B)
        em vez de confiar no cliente. Resultado entra em self.proc_events para o
        SessionManager decidir o que (se algo) notificar aos clientes."""
        import random
        from components import Equipment as _Equip, Modifier as _Mod
        from stat_fns import add_timed_modifier

        equip = self.world.get_component(entity_id, _Equip)
        if not equip:
            return
        for item in equip.slots.values():
            if item and item.proc:
                p = item.proc
                if random.random() < p["chance"]:
                    _max_before  = cst.max_hp
                    _pct_before  = cst.current_hp / _max_before if _max_before > 0 else 1.0
                    mod = _Mod(p["attribute"], p["value"], source="buff")
                    add_timed_modifier(cst, mod, p["duration"], p["label"])
                    if cst.max_hp > _max_before:
                        cst.current_hp = max(1, int(_pct_before * cst.max_hp))
                    self.proc_events.append({
                        "player_eid": entity_id,
                        "item_name":  item.name,
                        "label":      p["label"],
                        "attribute":  p["attribute"],
                        "value":      p["value"],
                        "duration":   p["duration"],
                    })
