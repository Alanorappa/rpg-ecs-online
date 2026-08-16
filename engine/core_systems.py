"""
core_systems.py — Sistemas ECS compartilhados entre cliente e servidor.

Regra: NENHUMA dependência de Pygame aqui.
Sistemas visuais (FLT, LOG, PROC, sons) ficam nas subclasses de cada lado.

Exporta:
  apply_effect()              — aplica/atualiza status effect numa entidade
  apply_damage_core()         — núcleo ÚNICO de aplicação final de dano em HP
  build_channeling_from_skill() — dono único da tradução SKILL_CATALOG → Channeling
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


# ── Outcome por alvo (por-tick) ───────────────────────────────────────────────
# Débito de "outcome compartilhado numa AOE" (PROBLEMAS_ARQUITETURA.md, item
# sobre Pirofagia/§14, 10/08/2026) — referências (Veloren Outcome::HealthChange
# com target: Uid; AzerothCore TargetInfo por alvo em m_UniqueTargetInfo)
# confirmaram: cada alvo atingido carrega o PRÓPRIO outcome, nunca 1 valor só
# compartilhado pro cast/tick inteiro (era o caso de `CombatSystem.last_outcome`
# em engine/world_systems.py, usado só pro auto-attack físico de alvo único).
# dict simples (int → str), NÃO objeto por evento — critério de performance em
# caminho quente (CLAUDE.md, "Performance e concorrência").
# Lifecycle: apply_damage_core grava aqui a cada chamada (automático, nenhum
# call site precisa lembrar); o CHAMADOR que vai LER (ex: server/
# skill_processor.py, antes de montar o relatório de uma skill) limpa o dict
# ANTES de disparar a resolução daquele lote — mesmo ponto/mesmo motivo que já
# resetava `last_outcome = "hit"` antes de cada handler.
LAST_DAMAGE_OUTCOMES: dict[int, str] = {}


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

    def _finish(outcome: str) -> str:
        LAST_DAMAGE_OUTCOMES[target_id] = outcome
        return outcome

    cs = world.get_component(target_id, CombatStats)
    if not cs or cs.current_hp <= 0:
        return _finish("blocked_dead")
    cst = world.get_component(target_id, CombatState)
    if cst and cst.is_immune:
        return _finish("blocked_immune")
    ai = world.get_component(target_id, AIControlled)
    if ai and ai.state == "RETURNING":
        # Modo evasão (estilo WoW): mob voltando pro spawn é imune a
        # dano/aggro até chegar — rede de segurança final aqui; o feedback
        # visual ("Evadiu!") e o bloqueio de re-aggro ficam por conta de
        # cada chamador (ver EnemyAISystem/CombatSystem.deal_damage).
        return _finish("blocked_evade")
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
            return _finish("blocked_friendly")

    cs.current_hp -= dmg  # overkill preservado por contrato

    if dmg > 0:
        if on_damage_dealt is not None and killer_eid != -1:
            on_damage_dealt(killer_eid, target_id, dmg)
        if _damage_tracker is not None and killer_eid != -1:
            _damage_tracker(killer_eid, target_id, dmg)
        if killer_eid != -1:
            # Bush "atacar revela" (13/08/2026, §55, estilo LoL) — só
            # player tem CombatState (minion não, create_minion nunca
            # anexa), então isso já escopa "só player revela a si mesmo
            # ao atacar" sem checagem extra de tipo de entidade.
            from shared.constants import BUSH_REVEAL_DURATION_S
            killer_cst = world.get_component(killer_eid, CombatState)
            if killer_cst is not None:
                killer_cst.bush_reveal_timer = BUSH_REVEAL_DURATION_S
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
            return _finish("applied")
        if add_pending_death and not world.get_component(target_id, PendingDeath):
            world.add_component(target_id, PendingDeath(killer_entity_id=killer_eid))
        return _finish("killed")
    return _finish("applied")


# ── apply_magic_damage_shared ─────────────────────────────────────────────────

def apply_magic_damage_shared(attacker_id: int, target_id: int, dmg: int, world,
                              is_crit: bool = False) -> bool:
    """Aplica dano mágico a um alvo fora do fluxo de spell_completion_processor
    (cliente offline E qualquer handler de skill que precise resolver dano
    mágico inline, ex: cone de Pirofagia). Retorna True se matou.

    Extraído de ui/spell_system.py::_apply_magic_damage (débito B3/CRÍTICO B,
    PROBLEMAS_ARQUITETURA.md — 10/08/2026): a função em si sempre foi
    pygame-free (só delegava a apply_damage_core + FLT/SOUNDS pra feedback),
    mas morava em ui/spell_system.py, que importa pygame no topo — qualquer
    chamador server-side (engine/skill_handlers.py::_skill_pirofagia, modo
    servidor) arrastava pygame pro processo do servidor só por causa do
    import, mesmo lógica 100% headless. Usa FLT/SOUNDS via engine.fx (façade
    no-op no servidor) em vez de ui.floating_text/ui.sound_manager direto —
    mesmo padrão já usado no guard de cc_immune acima.

    Guards + escrita de HP + quebra de CC + PendingDeath delegados a
    apply_damage_core — MESMO núcleo do servidor (_server_apply_magic_damage)
    e do melee (deal_damage). Aqui fica só o que é do CALLER por convenção:
    FLT, som de aggro, enter_combat, estado de IA.
    """
    from engine.components import Position, CombatState, AIControlled, NpcSounds
    from engine.fx import FLT, SOUNDS
    from engine.stat_fns import enter_combat

    pos = world.get_component(target_id, Position)

    def _on_cc_break(kind: str) -> None:
        if not pos:
            return
        if kind == "polymorph":
            FLT.add("Polimorfia quebrada!", pos.x, pos.y, (160, 80, 200),
                    "small", target_id=target_id)
        elif kind == "sleep":
            FLT.add("Acordou!", pos.x, pos.y, (200, 200, 100), "small",
                    target_id=target_id)

    result = apply_damage_core(world, target_id, dmg,
                               killer_eid=attacker_id,
                               on_cc_break=_on_cc_break)
    if result == "blocked_evade":
        if pos:
            FLT.add("Evadiu!", pos.x, pos.y, (150, 150, 150), "small", target_id=target_id)
        return False
    if result == "blocked_immune":
        if pos:
            FLT.add("Imune", pos.x, pos.y, (200, 200, 200), "small", target_id=target_id)
        return False
    if result == "blocked_dead":
        return False

    if pos:
        if is_crit:
            FLT.add(f"{dmg}", pos.x, pos.y, (255, 180, 80), target_id=target_id, is_crit=True)
        else:
            FLT.add(f"-{dmg}", pos.x, pos.y, (180, 100, 255), size="normal", target_id=target_id)
    attacker_cs = world.get_component(attacker_id, CombatState)
    if attacker_cs:
        enter_combat(attacker_cs)
    # Aggro por dano mágico — usa AGGRO_DELAY (alinhado com servidor)
    _ai = world.get_component(target_id, AIControlled)
    from engine.faction_system import can_engage as _can_engage_magic_shared
    if _ai and _ai.state == "IDLE" and _can_engage_magic_shared(world, attacker_id, target_id):
        _ms = world.get_component(target_id, NpcSounds)
        SOUNDS.play_mob_sounds(_ms, "aggro", dedup_key=f"dmg_{target_id}")
        _ai.state             = "AGGRO_DELAY"
        _ai.aggro_delay       = 0.5
        _ai.aggroed_by_damage = True
        # Quem bateu vira o alvo — espelha o bloco melee de deal_damage
        # (ver comentário lá): aquisição é filtrada por hostilidade+raio,
        # então sem isto um mob neutro atacado nunca recebia alvo.
        _ai.target_eid        = attacker_id
        _ai.path_recalc_timer = 0.0
    return result == "killed"


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

    # Imunidade a controle (Fatiador de Corpos, 07/08/2026) — guard genérico
    # e DINÂMICO: bloqueia qualquer efeito que tire o controle do jogador
    # (blocks_move/blocks_act em EFFECT_DEFS — stun/sleep/fear/root/
    # polymorph/disoriented, cobre efeito NOVO automaticamente, sem precisar
    # listar nomes) + "slow" explicitamente (não usa blocks_move/act, é só
    # redução de velocidade, mas é mobilidade igual). NÃO cobre dano (DoT
    # continua passando) nem "taunted" (não é bloqueio de controle no nosso
    # modelo, ver comentário no catálogo) — mesma separação do AzerothCore
    # (SPELL_AURA_MECHANIC_IMMUNITY_MASK ≠ imunidade a dano).
    # A imunidade em si é UM STATUS EFFECT (StatusEffects.has("cc_immune")),
    # não um flag bespoke — qualquer skill futura que quiser o mesmo efeito
    # só chama apply_effect(world, eid, "cc_immune", duration=X); esta
    # função nem precisa saber que existe.
    if effect_type == "slow" or defn.blocks_move or defn.blocks_act:
        _sfx_cc_check = world.get_component(entity_id, StatusEffects)
        if _sfx_cc_check and _sfx_cc_check.has("cc_immune"):
            from engine.fx import FLT as _FLTimm
            from engine.components import Position as _PosImm
            _pos_imm = world.get_component(entity_id, _PosImm)
            if _pos_imm:
                _FLTimm.add("Imune", _pos_imm.x, _pos_imm.y, (200, 200, 200),
                           "small", target_id=entity_id)
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


def build_channeling_from_skill(skill, target_x: float, target_y: float) -> "Channeling":
    """Constrói um `Channeling` a partir de `skill.params`/`skill.channel_duration`
    (SKILL_CATALOG) — dono único da tradução catálogo → componente vivo.
    Antes desta função, servidor (`ui/skill_handlers.py`) e cliente
    (`ui/spell_system.py::AoeTargetingSystem`) construíam `Channeling(...)`
    cada um com os mesmos 6 números duplicados como literais Python; skill
    de canalização nova só precisa preencher `params` no catálogo, nunca
    mais tocar em código pra isso."""
    from engine.components import Channeling
    p = skill.params
    return Channeling(
        spell_id       = skill.skill_id,
        duration       = skill.channel_duration,
        tick_interval  = p.get("tick_interval",  1.0),
        mana_per_tick  = p.get("mana_per_tick",  0),
        target_x       = target_x,
        target_y       = target_y,
        radius_tiles   = p.get("radius_tiles",   1.0),
        slow_pct       = p.get("slow_pct",       0.0),
        dmg_weapon_pct = p.get("dmg_weapon_pct", 0.15),
        dmg_sp_coeff   = p.get("dmg_sp_coeff",   1.0),
    )


def sync_status_derived_state(world, eid: int, sfx) -> None:
    """Recalcula, a partir do StatusEffects ATUAL, os 3 estados derivados
    que outros sistemas leem direto (nunca vasculham `sfx.effects` sozinhos):
    `TileMovement.slow_mult`/`debilitate_elapsed`, `CombatState.is_rooted`,
    `CombatStats.is_crowd_controlled`.

    Extraído de StatusEffectSystem.update() (chamado de lá a cada tick,
    depois de remover efeitos expirados) para poder ser chamado TAMBÉM fora
    do loop principal — client/remote_entity_handlers.py::_sync_player_
    effects/_sync_mob_effects fazem `sfx.effects.pop()`/`.clear()` DIRETO
    (efeito que o servidor parou de reportar via STATS_UPDATE/mob_effects),
    e sem recalcular aqui os 3 estados derivados ficavam PRESOS no último
    valor pra sempre — `StatusEffectSystem.update()` nunca mais os tocava
    porque seu guard `if not sfx.effects: continue` nunca roda de novo pra
    uma entidade cujo dict já ficou (e continua) vazio. Bug real relatado
    pelo usuário 20/07/2026: alvo de Polimorfia ficava permanentemente
    lento (slow_mult preso em 0.5) depois do efeito expirar no servidor —
    mesma classe de bug já resolvida em 3 pontos de leash de mob
    (engine/world_systems.py, ver ARQUITETURA_ONLINE.md) mas nunca
    replicada aqui."""
    from engine.components import TileMovement, CombatState, CombatStats

    tm = world.get_component(eid, TileMovement)
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

    cst = world.get_component(eid, CombatState)
    if cst:
        cst.is_rooted = sfx.has("root")

    cs_cc = world.get_component(eid, CombatStats)
    if cs_cc:
        _CC = ("stun", "sleep", "fear", "polymorph", "slow",
               "disoriented", "root")
        cs_cc.is_crowd_controlled = any(sfx.has(e) for e in _CC)


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
        from engine.components import StatusEffects

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

            sync_status_derived_state(self.world, eid, sfx)

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
    def _tick_bush_reveal_timer(cs, dt: float) -> None:
        """Bush "atacar revela" (13/08/2026, §55) — decrementa até 0,
        nunca negativo. `_has_tile_los` (servidor) trata `>0` como
        isenção de bloqueio de bush/copa (nunca de sólido) pra esta
        entidade."""
        if cs.bush_reveal_timer > 0:
            cs.bush_reveal_timer -= dt
            if cs.bush_reveal_timer < 0:
                cs.bush_reveal_timer = 0.0

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
            self._tick_bush_reveal_timer(cs, dt)
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
