# stats_system.py
"""
Sistemas de progressão do personagem:
  - XPSystem          : concede XP ao jogador após kills e dispara level-up
  - DeathRespawnSystem     : mecânica roguelike — acumula stats na morte e respawna
"""
from __future__ import annotations
from world import World
from components import (
    CharacterStats, PermanentStats, CombatStats, CombatState,
    PlayerControlled, PlayerAutoMove, TileMovement, Position, StatusEffects,
    SkillLevels, SKILL_IDS, MAX_SKILL_LEVEL,
)
from systems import System
from tileset import TILE_SIZE
from combat_log import LOG
from floating_text import FLT
from sound_manager import SOUNDS
from save_system import request_autosave


# ---------------------------------------------------------------------------
# Ganhos de atributo por level up — específicos por classe.
# Adicionar nova classe: inserir entrada aqui.
# Campos ausentes recebem 0 (atributo não cresce).
# ---------------------------------------------------------------------------
CLASS_LEVEL_GAINS: dict[str, dict] = {
    "guerreiro": {"strength": 1, "vitality": 1, "agility": 0, "intelligence": 0, "defense": 2},
    "mago":      {"strength": 0, "vitality": 1, "agility": 0, "intelligence": 1, "defense": 1},
    "arqueiro":  {"strength": 0, "vitality": 1, "agility": 1, "intelligence": 0, "defense": 1},
}

# Atributos base ao criar um personagem novo — específicos por classe.
# Atributo principal inicia em 3; os demais em valores mínimos.
CLASS_BASE_STATS: dict[str, dict] = {
    "guerreiro": {"strength": 3, "intelligence": 1, "agility": 1, "vitality": 3, "defense": 2},
    "mago":      {"strength": 1, "intelligence": 3, "agility": 1, "vitality": 2, "defense": 1},
    "arqueiro":  {"strength": 1, "intelligence": 1, "agility": 3, "vitality": 2, "defense": 1},
}


# ---------------------------------------------------------------------------
# Dados de melee por classe — independentes do crescimento de atributos.
# Adicionar uma nova classe: inserir uma entrada aqui.
# Campos omitidos mantêm o valor calculado por apply_char_stats_to_combat.
# ---------------------------------------------------------------------------
# Tipos de armadura permitidos por classe.
# Adicionar nova classe: inserir entrada aqui.
CLASS_ARMOR_ALLOWED: dict[str, frozenset] = {
    "guerreiro": frozenset({"placa", "couro", "tecido"}),
    "arqueiro":  frozenset({"couro", "tecido"}),
    "mago":      frozenset({"tecido"}),
}

# HP base fixo por classe (antes dos ganhos de VIT).
CLASS_BASE_HP: dict[str, int] = {
    "guerreiro": 180,
    "mago":      100,
    "arqueiro":  120,
}

# Acerto base por classe. Todos os atributos contribuem +0.1% por ponto (universal).
CLASS_BASE_ACERTO: dict[str, float] = {
    "guerreiro": 80.0,
    "mago":      75.0,
    "arqueiro":  50.0,
}

# Concentração máxima por classe (0 = recurso inexistente para a classe).
# Inicia sempre cheia — é um recurso de sustain, não de acúmulo como a Raiva.
CLASS_CONCENTRATION: dict[str, int] = {
    "arqueiro": 100,
}

CLASS_MELEE_OVERRIDES: dict[str, dict] = {
    "guerreiro": {
        "_default_attack_interval": 2.6,
        "base_physical_damage":     3,
        "base_physical_damage_max": 6,
    },
    "mago": {
        "base_attack_power":        0,
        "_default_attack_interval": 3.2,
        "base_physical_damage":     2,
        "base_physical_damage_max": 5,
    },
    "arqueiro": {
        "_default_attack_interval":    2.2,
        "base_physical_damage":        2,
        "base_physical_damage_max":    4,
        "can_kite":                    True,
        "is_ranged":                   True,
        # concentration_regen_idle/moving NÃO ficam aqui — são gerenciados por
        # cs_flags em talent_data.py (reset=5.0 garante o valor base).
        # Colocar aqui sobrescreveria o efeito do talento "Parado e Concentrado".
    },
}


# ---------------------------------------------------------------------------
# Função utilitária compartilhada
# ---------------------------------------------------------------------------

def apply_char_stats_to_combat(char_stats: CharacterStats,
                                combat_stats: CombatStats,
                                permanent: PermanentStats | None = None) -> None:
    """
    Deriva CombatStats a partir dos atributos do personagem (STR/INT/AGI/VIT/DEF).

    Responsabilidade única: derivação de atributos → stats de combate.
    NÃO toca base_attack_interval nem _default_attack_interval — esses são
    gerenciados por sync_attack_interval (init/load) e pelo sistema de equip/unequip.
    """
    p = permanent
    total_str = char_stats.strength      + (p.strength      if p else 0)
    total_int = char_stats.intelligence  + (p.intelligence  if p else 0)
    total_agi = char_stats.agility       + (p.agility       if p else 0)
    total_vit = char_stats.vitality      + (p.vitality      if p else 0)
    total_def = char_stats.defense       + (p.defense       if p else 0)

    # ── HP: base fixo por classe + VIT×10 (stamina = HP direto, ×1) ──────────
    _base_hp = CLASS_BASE_HP.get(char_stats.class_id, 100)
    combat_stats.base_stamina      = _base_hp + total_vit * 10

    # ── Armor: DEF×2 + STR×1 ─────────────────────────────────────────────────
    combat_stats.base_armor        = total_def * 2 + total_str * 1

    # ── Poder de ataque: STR×2 + AGI×2 ───────────────────────────────────────
    combat_stats.base_attack_power = total_str * 2 + total_agi * 2

    # ── Poder mágico: INT×2 ───────────────────────────────────────────────────
    combat_stats.base_spell_power  = total_int * 2

    # ── Crit: AGI×0.003 (0.3% por AGI) ───────────────────────────────────────
    combat_stats.base_crit_rating  = total_agi * 0.003

    # ── Dodge: AGI×1 → 1 dodge_rating = 0.1% esquiva ─────────────────────────
    combat_stats.base_dodge_rating = total_agi * 1.0

    # ── Parry: STR×1 → 1 parry_rating = 0.1% aparo ───────────────────────────
    combat_stats.base_parry_rating = total_str * 1.0

    # Overrides de classe: dano sem arma e AP base (sem tocar velocidade de ataque)
    overrides = CLASS_MELEE_OVERRIDES.get(char_stats.class_id)
    if overrides:
        for _attr, _val in overrides.items():
            setattr(combat_stats, _attr, _val)

    # ── Acerto: base por classe + todos os atributos ×0.1% ────────────────────
    _base_acerto = CLASS_BASE_ACERTO.get(char_stats.class_id, 50.0)
    combat_stats.base_acerto = _base_acerto + (total_str + total_int + total_agi) * 0.1

    combat_stats._recalculate_effective_stats()

    # ── Mana: INT×15 — só existe para o Mago (0 = recurso inexistente pra
    # classe, mesmo princípio de CLASS_CONCENTRATION abaixo). Sem esse gate,
    # Arqueiro/Guerreiro também ganhavam max_mana>0 (150+INT×15) e o painel
    # de Inventário (que escolhe Mana/Concentração/Raiva olhando qual desses
    # 3 é >0) mostrava "Mana" errado pra essas classes.
    new_max_mana = (150 + total_int * 15) if char_stats.class_id == "mago" else 0
    if char_stats.max_mana != new_max_mana:
        if char_stats.max_mana == 0:
            char_stats.mana = new_max_mana
        elif char_stats.mana > new_max_mana:
            char_stats.mana = new_max_mana
        char_stats.max_mana = new_max_mana

    # Concentração — valor fixo por classe, inicia sempre cheia
    new_max_conc = CLASS_CONCENTRATION.get(char_stats.class_id, 0)
    if char_stats.max_concentration != new_max_conc:
        char_stats.max_concentration = new_max_conc
        char_stats.concentration     = new_max_conc  # inicia cheia


def sync_attack_interval(combat_stats: CombatStats, equipment=None) -> None:
    """
    Sincroniza base_attack_interval com o estado atual do equipamento.

    Deve ser chamada APENAS em dois momentos:
      1. Criação de novo personagem (classe definida, sem arma)
      2. Load de save (reconstituição do estado completo)

    Nunca chamar em level-up ou respawn — velocidade de ataque não muda com evolução.

    Regras:
      - Arma equipada na mainhand com attack_speed > 0 → usa velocidade da arma
      - Sem arma → usa _default_attack_interval (padrão da classe)
    """
    weapon = equipment.slots.get("mainhand") if equipment else None
    weapon_speed = getattr(weapon, "attack_speed", 0.0) if weapon else 0.0
    if weapon_speed > 0:
        combat_stats.base_attack_interval = weapon_speed
    else:
        combat_stats.base_attack_interval = combat_stats._default_attack_interval
    combat_stats._recalculate_effective_stats()


# ---------------------------------------------------------------------------
# Função utilitária de level-up
# ---------------------------------------------------------------------------

def process_levelups(world: World, entity_id: int,
                     char: CharacterStats, cs: CombatStats,
                     perm: "PermanentStats | None",
                     give_talent_points: bool = True) -> None:
    """Processa todos os level-ups pendentes em `char` e aplica os efeitos.

    Centraliza a lógica que estava triplicada em XPSystem, QuestSystem e
    GameEngine._debug_levelup. Deve ser chamada após adicionar XP a `char`.
    """
    from components import TalentTree
    from quest_events import fire as _qfire

    leveled = False
    while char.current_xp >= char.xp_to_next_level:
        char.current_xp     -= char.xp_to_next_level
        char.level          += 1
        char.xp_to_next_level = CharacterStats.xp_for_level(char.level)
        gains = CLASS_LEVEL_GAINS.get(char.class_id, {})
        char.strength     += gains.get("strength",     0)
        char.intelligence += gains.get("intelligence", 0)
        char.agility      += gains.get("agility",      0)
        char.vitality     += gains.get("vitality",     0)
        char.defense      += gains.get("defense",      0)
        tt = world.get_component(entity_id, TalentTree)
        if tt is not None and give_talent_points:
            tt.available_points += 1
        # SOUNDS e LOG podem falhar no servidor (sem áudio/display) — nunca devem
        # interromper a lógica de level-up que precisa rodar tanto no cliente quanto no servidor.
        try:
            SOUNDS.play_ui("levelup")
        except Exception:
            pass
        try:
            gains_str = ", ".join(f"+{v} {k[:3].upper()}" for k, v in gains.items() if v > 0)
            LOG.add(f"Level up! Nivel {char.level} — {gains_str} | 1 ponto de talento (T).", (255, 200, 0))
        except Exception:
            pass
        try:
            _qfire("reach_level", player_eid=entity_id, level=char.level)
        except Exception:
            pass
        leveled = True

    if leveled:
        apply_char_stats_to_combat(char, cs, perm)
        cs.current_hp = cs.max_hp  # HP cheio ao subir de nível


# ---------------------------------------------------------------------------
# Skill Level (Tibia-like) — progressão por uso: armas, escudo, defesa,
# resistências mágicas e magic. Ver arquitetura/PROBLEMAS_ARQUITETURA.md.
# Server-autoritativo: grant_skill_xp só deve ser chamado pelo servidor.
# ---------------------------------------------------------------------------

# Mapeia item.subtype (arma) → skill_id agrupado. Adicionar nova arma:
# inserir o subtype aqui (1 dos 5 grupos existentes, não cria grupo novo).
WEAPON_SUBTYPE_TO_SKILL: dict[str, str] = {
    "Axe":     "machado",
    "Sword":   "espada",
    "Dagger":  "espada",
    "Club":    "maca",
    "Mace":    "maca",
    "Hammer":  "maca",
    "Bow":     "arco",
    "Staff":   "baculo",
    "Wand":    "baculo",
    "Scepter": "baculo",
}

SKILL_XP_BASE     = 20
SKILL_XP_EXPONENT = 1.2


def skill_xp_for_level(level: int) -> int:
    """XP necessário para avançar do skill level `level` para `level+1`."""
    return int(SKILL_XP_BASE * (level + 1) ** SKILL_XP_EXPONENT)


def skill_bonus_pct(level: int) -> float:
    """Bônus linear 0% (level 0) a 15% (level MAX_SKILL_LEVEL)."""
    return min(1.0, level / MAX_SKILL_LEVEL) * 0.15


def grant_skill_xp(skill_levels: SkillLevels, combat_stats: CombatStats,
                    skill_id: str, amount: int = 1) -> bool:
    """Concede xp a uma trilha de skill, processa level-ups pendentes (while,
    igual a process_levelups) e recalcula os bônus derivados se subiu nível.
    Retorna True se houve ao menos um level-up."""
    if skill_id not in SKILL_IDS:
        return False
    level = skill_levels.levels[skill_id]
    if level >= MAX_SKILL_LEVEL:
        return False
    skill_levels.xp[skill_id] += amount
    leveled = False
    while level < MAX_SKILL_LEVEL and skill_levels.xp[skill_id] >= skill_xp_for_level(level):
        skill_levels.xp[skill_id] -= skill_xp_for_level(level)
        level += 1
        leveled = True
    skill_levels.levels[skill_id] = level
    if leveled:
        apply_skill_bonuses_to_combat(skill_levels, combat_stats)
    return leveled


def apply_skill_bonuses_to_combat(skill_levels: SkillLevels, combat_stats: CombatStats) -> None:
    """Recalcula todos os campos de bônus derivados de SkillLevels em
    CombatStats de uma vez. Chamar após qualquer level-up de skill e no
    spawn/login (mesmo gatilho de apply_char_stats_to_combat)."""
    combat_stats.weapon_skill_bonus = {
        skill_id: skill_bonus_pct(skill_levels.levels[skill_id])
        for skill_id in ("machado", "espada", "maca", "arco", "baculo")
    }
    combat_stats.shield_skill_block_bonus  = skill_bonus_pct(skill_levels.levels["escudo"])
    combat_stats.defense_skill_avoid_bonus = skill_bonus_pct(skill_levels.levels["defesa"])
    combat_stats.resist_fogo     = skill_bonus_pct(skill_levels.levels["resist_fogo"])
    combat_stats.resist_gelo     = skill_bonus_pct(skill_levels.levels["resist_gelo"])
    combat_stats.resist_natureza = skill_bonus_pct(skill_levels.levels["resist_natureza"])
    combat_stats.magic_skill_dmg_bonus  = skill_bonus_pct(skill_levels.levels["magic"])
    combat_stats.magic_skill_crit_bonus = skill_bonus_pct(skill_levels.levels["magic"])


def weapon_skill_extras(world: World, attacker_id: int, weapon) -> tuple[float, float]:
    """Resolve (extra_acerto_pontos_pct, extra_crit_fracao) do skill de arma do
    atacante, prontos para passar a `resolve_attack_outcome`. `weapon` é o item
    na mainhand (ou None — desarmado não tem skill de arma). Retorna (0.0, 0.0)
    se o atacante não tem SkillLevels rastreado (mob) ou arma sem subtype mapeado.
    """
    if weapon is None:
        return 0.0, 0.0
    skill_id = WEAPON_SUBTYPE_TO_SKILL.get(getattr(weapon, "subtype", ""))
    if not skill_id:
        return 0.0, 0.0
    attacker_cs = world.get_component(attacker_id, CombatStats)
    if not attacker_cs:
        return 0.0, 0.0
    bonus = attacker_cs.weapon_skill_bonus.get(skill_id, 0.0)
    return bonus * 100.0, bonus


def weapon_skill_level(world: World, attacker_id: int, weapon) -> int:
    """Level do skill da ARMA equipada (espada/machado/maça/arco/báculo).

    Usado pelo bônus de dano de skill física (damage_calculator.
    ability_physical_damage: +0.01 no damage_multiplier por level) —
    trocou de arma, o bônus passa a ser o do skill da arma nova.
    Retorna 0 se desarmado, subtype sem mapeamento ou sem SkillLevels (mob).
    """
    if weapon is None:
        return 0
    skill_id = WEAPON_SUBTYPE_TO_SKILL.get(getattr(weapon, "subtype", ""))
    if not skill_id:
        return 0
    skl = world.get_component(attacker_id, SkillLevels)
    if skl is None:
        return 0
    return int(skl.levels.get(skill_id, 0))


def defense_skill_extras(world: World, target_id: int) -> tuple[float, float]:
    """Resolve (extra_block_fracao, extra_avoid_fracao) do alvo, prontos para
    passar a `resolve_attack_outcome`. extra_block só conta se o alvo tem
    escudo equipado na offhand (ver Equipment) — defesa (avoid) é incondicional."""
    from components import Equipment as _EqDef
    target_cs = world.get_component(target_id, CombatStats)
    if not target_cs:
        return 0.0, 0.0
    extra_avoid = getattr(target_cs, "defense_skill_avoid_bonus", 0.0)
    extra_block = 0.0
    equip = world.get_component(target_id, _EqDef)
    if equip:
        offhand = equip.slots.get("offhand")
        if offhand is not None and getattr(offhand, "item_type", "") == "shield":
            extra_block = getattr(target_cs, "shield_skill_block_bonus", 0.0)
    return extra_block, extra_avoid


def grant_weapon_skill_xp(world: World, attacker_id: int, weapon) -> None:
    """Concede 1 xp na trilha de arma do atacante (ataque físico, hit ou não —
    1 chamada = 1 uso). Server-autoritativo: só chamar a partir do servidor."""
    if weapon is None:
        return
    skill_id = WEAPON_SUBTYPE_TO_SKILL.get(getattr(weapon, "subtype", ""))
    if not skill_id:
        return
    skl = world.get_component(attacker_id, SkillLevels)
    cs  = world.get_component(attacker_id, CombatStats)
    if skl and cs:
        grant_skill_xp(skl, cs, skill_id, 1)


def grant_defense_skill_xp(world: World, target_id: int) -> None:
    """Concede 1 xp de Defesa (sempre) e 1 xp de Escudo (se equipado) ao alvo
    de um ataque físico — "ganha de acordo com o dano que recebe". Chamado a
    cada ataque físico resolvido contra o alvo, hit ou não. Server-autoritativo."""
    from components import Equipment as _EqDef2
    skl = world.get_component(target_id, SkillLevels)
    cs  = world.get_component(target_id, CombatStats)
    if not (skl and cs):
        return
    grant_skill_xp(skl, cs, "defesa", 1)
    equip = world.get_component(target_id, _EqDef2)
    if equip:
        offhand = equip.slots.get("offhand")
        if offhand is not None and getattr(offhand, "item_type", "") == "shield":
            grant_skill_xp(skl, cs, "escudo", 1)


def grant_resist_skill_xp(world: World, target_id: int, school: str) -> None:
    """Concede 1 xp de resistência da escola (fogo/gelo/natureza) ao alvo que
    recebeu dano dessa escola. Server-autoritativo."""
    skill_id = f"resist_{school}"
    if skill_id not in SKILL_IDS:
        return
    skl = world.get_component(target_id, SkillLevels)
    cs  = world.get_component(target_id, CombatStats)
    if skl and cs:
        grant_skill_xp(skl, cs, skill_id, 1)


# ---------------------------------------------------------------------------
# XPSystem
# ---------------------------------------------------------------------------

class XPSystem(System):
    """
    Processa os prêmios de XP acumulados por DeathHandlerSystem.pending_xp
    e dispara level-up quando o limiar é atingido.
    """

    def __init__(self, world: World, death_handler):
        self.world         = world
        self.death_handler = death_handler

    def update(self, events: list = None, dt: float = 0) -> None:
        if not self.death_handler.pending_xp:
            return

        for entity_id, char_stats, combat_stats, _ in \
                self.world.get_entities_with(CharacterStats, CombatStats, PlayerControlled):
            perm = self.world.get_component(entity_id, PermanentStats)

            pos = self.world.get_component(entity_id, Position)
            for xp in self.death_handler.pending_xp:
                char_stats.current_xp += xp
                if pos:
                    FLT.add(f"+{xp} xp", pos.x, pos.y,
                            (255, 160, 0), size="normal", target_id=entity_id)

            process_levelups(self.world, entity_id, char_stats, combat_stats, perm)

        self.death_handler.pending_xp.clear()
        request_autosave()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# DeathRespawnSystem
# ---------------------------------------------------------------------------

class DeathRespawnSystem(System):
    """
    Mecânica roguelike: quando o jogador morre (hp <= 0),
      1. Acumula os atributos atuais em PermanentStats
      2. Reseta CharacterStats para os valores iniciais
      3. Recalcula CombatStats (agora mais forte pelos permanentes)
      4. Restaura HP cheio e sinaliza ao GameEngine para trocar de mapa
    """

    INITIAL_STATS = dict(strength=1, intelligence=1, agility=1, vitality=3, defense=2)

    def __init__(self, world: World):
        self.world = world
        self.pending_respawn: "dict | None" = None  # lido e consumido pelo GameEngine
        self.online_mode: bool = False  # True → respawn server-autoritativo, sistema não dispara

    def update(self, events: list = None, dt: float = 0) -> None:
        if self.online_mode:
            return
        for entity_id, combat_stats, char_stats, _ in \
                self.world.get_entities_with(CombatStats, CharacterStats, PlayerControlled):
            if combat_stats.current_hp > 0:
                continue
            self._respawn(entity_id, combat_stats, char_stats)

    def _respawn(self, entity_id: int, combat_stats: CombatStats,
                 char_stats: CharacterStats) -> None:
        perm = self.world.get_component(entity_id, PermanentStats)

        # Nenhum reset de level, atributos ou talentos — apenas restaura HP + voláteis de combate
        char_stats.reset_volatile()
        apply_char_stats_to_combat(char_stats, combat_stats, perm)
        combat_stats.current_hp = combat_stats.max_hp
        combat_stats.attack_cooldown_timer = 0.0

        # 4. Sinaliza ao GameEngine para carregar mapa principal e teleportar ao cemitério
        self.pending_respawn = {
            "target_map": char_stats.spawn_map,
            "target_x":   char_stats.spawn_tile_x,
            "target_y":   char_stats.spawn_tile_y,
        }

        # 5. Limpa efeitos de estado ativos
        sfx = self.world.get_component(entity_id, StatusEffects)
        if sfx:
            sfx.effects.clear()
        tm_player = self.world.get_component(entity_id, TileMovement)
        if tm_player:
            tm_player.slow_mult = 1.0
            tm_player.debilitate_elapsed = 0.0

        # 6. Limpa estado de combate
        cs = self.world.get_component(entity_id, CombatState)
        if cs:
            cs.target_entity_id = -1
            cs.in_combat = False
            cs.is_pursuing = False
        am = self.world.get_component(entity_id, PlayerAutoMove)
        if am:
            am.active = False
            am.path.clear()

        perm_str = f"FOR+{perm.strength} VIT+{perm.vitality} DEF+{perm.defense}" if perm else ""
        LOG.add(f"Renasceu no nivel 1. Bônus permanentes: {perm_str}", (200, 100, 220))
        LOG.add(f"HP maximo agora: {combat_stats.max_hp}", (200, 100, 220))
