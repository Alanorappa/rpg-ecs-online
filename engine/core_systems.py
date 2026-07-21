"""
core_systems.py — Sistemas ECS compartilhados entre cliente e servidor.

Regra: NENHUMA dependência de Pygame aqui.
Sistemas visuais (FLT, LOG, PROC, sons) ficam nas subclasses de cada lado.

Exporta:
  apply_effect()              — aplica/atualiza status effect numa entidade
  apply_damage_core()         — núcleo ÚNICO de aplicação final de dano em HP
  register_lethal_interceptor() — hook de golpe letal (duelo estilo WoW)
  StatusEffectSystem          — processa ciclo de vida de status effects (ticks, expiração)
  BaseCombatStateSystem       — núcleo headless: timers de combate, rage, HP5, concentração
  ServerCombatStateSystem     — herda Base; adiciona hp5_events para o servidor
"""
from __future__ import annotations

# ── Interceptor de golpe letal (plugável) ────────────────────────────────────
# Duelo estilo WoW (decisão do usuário 16/07/2026): o golpe que MATARIA um
# player em duelo encerra o duelo — o perdedor fica com 1 HP, ninguém morre.
# Mesmo padrão dos outros hooks engine↔server (register_pvp_context em
# faction_system, register_service_resolver em world_systems): o engine fica
# puro, o WorldServer registra a implementação no boot.
# `fn(world, killer_eid, target_id) -> bool` — True = intercepta (HP vira 1
# em vez de morte); False/None = morte segue normal.
_lethal_interceptor = None


def register_lethal_interceptor(fn) -> None:
    """Registra o interceptor de golpe letal (server: no boot). `None`
    desregistra (morte volta a ser sempre final — default seguro)."""
    global _lethal_interceptor
    _lethal_interceptor = fn


# ── Rastreador de dano (plugável) ────────────────────────────────────────────
# Mesmo padrão do interceptor acima — o WorldServer registra no boot pra
# acumular dano causado por player em partida de Arena (placar de fim de
# partida, estilo WoW, ver server/match_processor.py::_track_arena_damage).
# `fn(killer_eid, target_id, dmg) -> None` — chamado toda vez que dmg > 0 é
# efetivamente aplicado (mesmo ponto de on_damage_dealt, mas SEM precisar que
# cada call site de apply_damage_core passe o callback manualmente).
_damage_tracker = None


def register_damage_tracker(fn) -> None:
    """Registra o rastreador de dano (server: no boot). `None` desregistra."""
    global _damage_tracker
    _damage_tracker = fn


# ── apply_damage_core ─────────────────────────────────────────────────────────

def apply_damage_core(world, target_id: int, dmg: int, *,
                      killer_eid: int = -1,
                      add_pending_death: bool = True,
                      on_cc_break=None,
                      on_damage_dealt=None) -> str:
    """Núcleo ÚNICO da aplicação FINAL de dano em current_hp.

    Consolida os invariantes que antes viviam duplicados em 3+ lugares
    (problemas B/H, PROBLEMAS_ARQUITETURA.md): CombatSystem.deal_damage
    (melee/físico compartilhado), server _apply_final_damage (mágico/ranged
    server-side) e spell_system._apply_magic_damage (cliente offline).
    Regra nova de mitigação/imunidade/resistência entra AQUI, uma vez, e
    vale para os 3 caminhos.

    Invariantes:
      - alvo com current_hp <= 0, is_immune ou em modo evasão (AIControlled.
        state == "RETURNING", ver EnemyAISystem): dano bloqueado
      - atacante e alvo de facção "amigavel" entre si (content/
        faction_data.py, só checado se killer_eid != -1): dano bloqueado
      - overkill preservado (current_hp pode ficar negativo; nunca clampar)
      - dano > 0 quebra polymorph e sleep (sleep: on_expire_effect cancelado
        para não aplicar o slow encadeado ao acordar)
      - morte: adiciona PendingDeath(killer_eid) se add_pending_death

    on_cc_break: callback opcional `fn(kind: str)` chamado com "polymorph"/
    "sleep" quando o dano quebra o CC — hook para feedback visual do cliente
    (FLT); servidor não passa nada.

    on_damage_dealt: callback opcional `fn(attacker_eid: int, target_id: int,
    dmg: int)` chamado quando dano > 0 é efetivamente aplicado, com
    `killer_eid` como identidade do atacante (mesmo campo, propósito duplo:
    quem credita a morte E quem fez o dano — só dispara se killer_eid != -1).
    Usado pelo SERVIDOR pra popular o log de dano por mob (dono do
    loot/quest kill = quem ataca PRIMEIRO, ver PROBLEMAS_ARQUITETURA.md) sem
    duplicar esse hook em cada handler de skill — client offline não passa
    nada (no-op).

    Retorna: "blocked_dead" | "blocked_immune" | "blocked_evade" |
    "blocked_friendly" | "applied" | "killed".
    O chamador mantém a responsabilidade pelo que NÃO é invariante:
    cálculo do dano, outcome (crit/block/...), aggro, enter_combat,
    feedback visual, broadcast de rede.
    """
    from engine.components import CombatStats, CombatState, StatusEffects, PendingDeath, AIControlled
    cs = world.get_component(target_id, CombatStats)
    if not cs or cs.current_hp <= 0:
        return "blocked_dead"
    cst = world.get_component(target_id, CombatState)
    if cst and cst.is_immune:
        return "blocked_immune"
    ai = world.get_component(target_id, AIControlled)
    if ai and ai.state == "RETURNING":
        # Modo evasão (estilo WoW): mob voltando pro spawn é imune a
        # dano/aggro até chegar — rede de segurança final aqui; o feedback
        # visual ("Evadiu!") e o bloqueio de re-aggro ficam por conta de
        # cada chamador (ver EnemyAISystem/CombatSystem.deal_damage).
        return "blocked_evade"
    if killer_eid != -1:
        # Facção "amigavel" nunca pode ser alvo de dano — rede de segurança
        # final (mesmo padrão do blocked_evade acima), trazida da Fase 5 do
        # Sistema de Facções pra já valer na Fase 4 (NPC de combate "amigável"
        # sem isso seria livremente matável, contradizendo a própria palavra).
        # killer_eid == -1 (DoT/ambiente sem atacante identificado) não checa
        # — não dá pra resolver facção de "ninguém". Feedback visual/bloqueio
        # de efeitos secundários (knockback/DoT) por chamador fica pra Fase 5.
        from engine.faction_system import can_engage
        if not can_engage(world, killer_eid, target_id):
            return "blocked_friendly"

    cs.current_hp -= dmg  # overkill preservado por contrato

    if dmg > 0:
        if on_damage_dealt is not None and killer_eid != -1:
            on_damage_dealt(killer_eid, target_id, dmg)
        if _damage_tracker is not None and killer_eid != -1:
            _damage_tracker(killer_eid, target_id, dmg)
        sfx = world.get_component(target_id, StatusEffects)
        if sfx:
            if sfx.remove("polymorph") and on_cc_break:
                on_cc_break("polymorph")
            sleep_eff = sfx.get("sleep")
            if sleep_eff:
                sleep_eff.on_expire_effect = ""  # cancela slow pós-sono
                sfx.remove("sleep")
                if on_cc_break:
                    on_cc_break("sleep")

    if cs.current_hp <= 0:
        # Golpe letal interceptável (duelo estilo WoW): o interceptor
        # decide se esta morte vira "perdedor com 1 HP" (True) — só o
        # servidor registra um, e só intercepta pares em duelo. killer -1
        # (DoT/ambiente sem atacante) não intercepta — sem identidade não
        # há duelo a resolver.
        if (killer_eid != -1 and _lethal_interceptor is not None
                and _lethal_interceptor(world, killer_eid, target_id)):
            cs.current_hp = 1
            return "applied"
        if add_pending_death and not world.get_component(target_id, PendingDeath):
            world.add_component(target_id, PendingDeath(killer_entity_id=killer_eid))
        return "killed"
    return "applied"


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
    from engine.components import StatusEffects, ActiveEffect
    from content.status_effects_data import EFFECT_DEFS

    defn = EFFECT_DEFS.get(effect_type)
    if defn is None:
        return

    # Boneco de treino: recebe DoT e slow para testes de dano/talentos,
    # mas não recebe polymorph (transformação não faz sentido num manequim).
    if effect_type == "polymorph":
        from engine.components import TrainingDummy as _TDcheck
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

# DoT (dano por tick) cuja escola conta para resist_<escola> do skill level
# (ver stats_system.py). Sangramento ("bleed") NÃO entra aqui — é físico,
# ignora toda resistência mágica, como hoje.
DOT_SCHOOL = {"poison": "natureza", "burn": "fogo"}


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
        from engine.components import (StatusEffects, TileMovement, CombatState,
                                 CombatStats, Position, PlayerControlled,
                                 PendingDeath)
        from content.status_effects_data import EFFECT_DEFS

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
        from engine.components import (CombatStats, CombatState, Position,
                                 PlayerControlled, PendingDeath, AIControlled)
        from content.status_effects_data import EFFECT_DEFS

        cs  = self.world.get_component(eid, CombatStats)
        pos = self.world.get_component(eid, Position)
        if not cs or cs.current_hp <= 0:
            return

        # DoT não pode ser esquivado, aparado ou reduzido por armadura.
        # Entidades imunes (Bloco de Gelo) são a única exceção.
        cst = self.world.get_component(eid, CombatState)
        if cst and cst.is_immune:
            return
        # Modo evasão: mob em RETURNING não sofre (nem se cura por) tick de
        # status effect — mesmo guard de is_immune acima, ver apply_damage_core.
        ai = self.world.get_component(eid, AIControlled)
        if ai and ai.state == "RETURNING":
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
            _school = DOT_SCHOOL.get(effect.effect_type, "")
            if _school:
                from engine.damage_calculator import apply_resistance_reduction
                _resist = getattr(cs, f"resist_{_school}", 0.0)
                dmg = max(1, int(apply_resistance_reduction(dmg, _resist)))
                self._on_resisted_dot(eid, _school)
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

    def _on_resisted_dot(self, eid: int, school: str) -> None:
        """Sobrescrever no servidor: concede 1 xp de resist_<school> ao alvo
        (skill level, ver stats_system.py). Base no-op (cliente nunca concede)."""
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

    MANA_REGEN_INTERVAL = 5.0   # segundos entre cada tick de regen de mana
    # Taxas (% de max_mana por tick) NÃO são mais constantes fixas — vêm de
    # CombatStats.mp5 (fora de combate) / .mp5_ic (em combate), derivadas por
    # classe em stats_system.CLASS_BASE_REGEN + Spirit (só mp5, nunca mp5_ic —
    # mana em combate é controlada por talento). Ver _tick_mana_regen.

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
    def _tick_rage_decay(cls, cs, char, dt: float):
        """Decay de Raiva fora de combate. Único produtor autoritativo é o
        SERVIDOR (mesmo modelo do regen de mana) — o cliente online NÃO chama
        este método (ver systems.CombatStateSystem); recebe o valor via
        STATS_UPDATE (rage_events em ServerCombatStateSystem.update).

        Retorna (old_rage, new_rage) se houve decay, None caso contrário.
        """
        if not char or char.rage <= 0:
            return None
        if not cs.in_combat:
            char.rage_decay_timer += dt
            if char.rage_decay_timer >= cls.RAGE_DECAY_INTERVAL:
                char.rage_decay_timer -= cls.RAGE_DECAY_INTERVAL
                old_rage  = char.rage
                char.rage = max(0, char.rage - cls.RAGE_DECAY_AMOUNT)
                if char.rage != old_rage:
                    return (old_rage, char.rage)
        else:
            char.rage_decay_timer = 0.0
        return None

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

    @classmethod
    def _tick_mana_regen(cls, cs, char, cst, dt: float):
        """Regen de mana (Mago) — % de max_mana a cada MANA_REGEN_INTERVAL,
        taxa vinda de CombatStats.mp5 (fora de combate, Spirit entra aqui) ou
        .mp5_ic (em combate, SÓ talento — Spirit nunca afeta mana em combate,
        decisão do usuário 09/07/2026). Único produtor autoritativo é o
        SERVIDOR — ver ServerCombatStateSystem.update() em core_systems.py e
        spell_system.ManaSystem (cliente só prediz offline, sem servidor pra
        confirmar; online esperava o STATS_UPDATE, evitando o desync onde o
        cliente regenerava mana sozinho e o servidor nunca sabia — ver
        PROBLEMAS_ARQUITETURA.md).

        Retorna (old_mana, new_mana) se houve regen, None caso contrário.
        """
        if not char or char.max_mana <= 0 or char.mana >= char.max_mana:
            return None
        char.mana_regen_timer = getattr(char, "mana_regen_timer", 0.0) + dt
        if char.mana_regen_timer < cls.MANA_REGEN_INTERVAL:
            return None
        char.mana_regen_timer -= cls.MANA_REGEN_INTERVAL
        in_combat = bool(cs and cs.in_combat)
        rate      = (cst.mp5_ic if in_combat else cst.mp5) if cst else 0.0
        if rate <= 0:
            return None
        regen     = max(1, int(char.max_mana * rate))
        old_mana  = char.mana
        char.mana = min(char.max_mana, char.mana + regen)
        if char.mana != old_mana:
            return (old_mana, char.mana)
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
    Adiciona hp5_events (curas de regen), mana_events (regen de mana do
    Mago), rage_events (decay de Raiva do Guerreiro) e proc_events (procs de
    item rolados autoritativamente, ver _roll_procs) para o servidor reportar
    ao cliente.

    Uso:
        sys = ServerCombatStateSystem(world)
        sys.update(world_server._player_eids, dt)
        for ev in sys.hp5_events:
            # ev = {player_eid, old_hp, new_hp, hp_max}
            ...
        for ev in sys.mana_events:
            # ev = {player_eid, old_mana, new_mana}
            ...
        for ev in sys.rage_events:
            # ev = {player_eid, new_rage}
            ...
        for ev in sys.proc_events:
            # ev = {player_eid, item_name, label, attribute, value, duration}
            ...
    """

    def __init__(self, world) -> None:
        super().__init__(world)
        # Populado a cada update(); limpo no início do próximo update().
        self.hp5_events: list[dict] = []
        self.mana_events: list[dict] = []
        self.rage_events: list[dict] = []
        # Procs de item rolados autoritativamente pelo servidor neste tick.
        self.proc_events: list[dict] = []

    @staticmethod
    def _tick_player_move_grace(tm, dt: float) -> None:
        """Decai a janela 'ainda em movimento' usada só pra inferir is_moving
        de PLAYERS no servidor — server/world_server.py::move_player() faz
        snap instantâneo de tile (sem tween real, diferente de mob/cliente),
        então sem essa janela TileMovement.is_moving nunca fica True pra
        players no servidor, quebrando qualquer mecânica server-side que
        dependa de "parado vs andando" (Calmo e Certeiro — standing_seconds
        nunca resetava ao mover; regen de Concentração — sempre usava a taxa
        idle, nunca a de movimento). move_player() seta is_moving=True +
        _server_move_grace=PLAYER_MOVE_GRACE_S a cada move aceito; esta
        função só conta a janela pra baixo e desliga is_moving quando expira."""
        if tm is None or tm._server_move_grace <= 0:
            return
        tm._server_move_grace -= dt
        if tm._server_move_grace <= 0:
            tm._server_move_grace = 0.0
            tm.is_moving = False

    def update(self, player_eids: dict, dt: float) -> None:
        """Processa todos os players em player_eids (session_id → eid)."""
        self.hp5_events.clear()
        self.mana_events.clear()
        self.rage_events.clear()
        self.proc_events.clear()
        from engine.components import CombatState, CombatStats, CharacterStats, TileMovement

        for _sid, peid in list(player_eids.items()):
            cs   = self.world.get_component(peid, CombatState)
            cst  = self.world.get_component(peid, CombatStats)
            char = self.world.get_component(peid, CharacterStats)
            if not cs or not cst or cst.current_hp <= 0:
                continue

            tm = self.world.get_component(peid, TileMovement)
            self._tick_player_move_grace(tm, dt)

            self._tick_combat_timer(cs, dt)
            self._tick_stun_timer(cs, dt)
            rage_result = self._tick_rage_decay(cs, char, dt)
            if rage_result:
                self.rage_events.append({
                    "player_eid": peid,
                    "new_rage":   rage_result[1],
                })

            hp5_result = self._tick_hp5(cs, cst, dt)
            if hp5_result:
                old_hp, new_hp = hp5_result
                self.hp5_events.append({
                    "player_eid": peid,
                    "old_hp":     old_hp,
                    "new_hp":     new_hp,
                    "hp_max":     cst.max_hp,
                })

            mana_result = self._tick_mana_regen(cs, char, cst, dt)
            if mana_result:
                old_mana, new_mana = mana_result
                self.mana_events.append({
                    "player_eid": peid,
                    "old_mana":   old_mana,
                    "new_mana":   new_mana,
                })
                if cst:
                    cst.mana = new_mana  # CombatStats.mana — espelho lido por outros checks

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
        from engine.stat_fns import remove_modifier
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
        from engine.components import Equipment as _Equip, Modifier as _Mod
        from engine.stat_fns import add_timed_modifier

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
