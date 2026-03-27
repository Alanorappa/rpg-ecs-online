# components.py
from __future__ import annotations
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Identidade de entidade — raças, classes e tiers disponíveis
# ---------------------------------------------------------------------------

RACES = [
    "Humano", "Elfo", "Orc", "Anão",          # Humanoides civilizados
    "Humanoide",                                # Genérico bípede
    "Fera",                                     # Animais selvagens
    "Elemental",                                # Criaturas de fogo/água/terra/ar
    "Mecânico",                                 # Autômatos e construtos
    "Vampiro", "Zumbi", "Espírito",            # Mortos-vivos e espectrais
    "Goblin", "Troll", "Demônio", "Dragão",   # Criaturas fantásticas
    "Planta",                                   # Flora hostil
]

CLASSES = [
    "Guerreiro",   # melee tanque, alta armadura
    "Assassino",   # melee rápido, burst de dano
    "Arqueiro",    # ranged físico
    "Mago",        # ranged mágico, AoE
    "Bruxo",       # ranged mágico, DoT / debuffs
]

TIERS = ["normal", "elite", "rare", "boss"]


class EntityIdentity:
    """Nome, raça, classe, nível e tier de qualquer entidade (jogador ou inimigo)."""
    def __init__(self, name: str, race: str, entity_class: str,
                 level: int = 1, tier: str = "normal"):
        self.name         = name
        self.race         = race
        self.entity_class = entity_class
        self.level        = level
        self.tier         = tier          # "normal" | "elite" | "rare" | "boss"

# Nova Classe: Modifier
# Representa um bônus ou penalidade a um atributo de combate.
# Permite que itens, talentos e habilidades modifiquem os atributos de forma flexível.
class Modifier:
    def __init__(self, attribute: str, value: float, type: str = "flat"):
        """
        Inicializa um modificador de atributo.
        
        Args:
            attribute (str): O nome do atributo a ser modificado (e.g., "stamina", "armor", "attack_power").
            value (float): O valor do modificador.
            type (str): O tipo de modificação ("flat" para soma/subtração, "percentage" para multiplicação).
                        Ex: "flat" com value=10 para +10 de Armadura.
                        Ex: "percentage" com value=0.10 para +10% de Força.
        """
        self.attribute = attribute
        self.value = value
        self.type = type

    def __eq__(self, other):
        # Permite comparar modificadores para remoção (usado para verificar igualdade)
        return isinstance(other, Modifier) and \
               self.attribute == other.attribute and \
               self.value == other.value and \
               self.type == other.type

    def __hash__(self):
        # Permite que modificadores sejam usados em sets/dicionários
        return hash((self.attribute, self.value, self.type))

# Novo Componente: CombatStats
# Contém todos os atributos de combate para uma entidade.
class CombatStats:
    def __init__(self, base_stamina: int = 10, base_armor: int = 0,
                 base_attack_power: int = 5, base_spell_power: int = 0,
                 base_haste_rating: float = 0.0,
                 base_crit_rating: float = 0.05,
                 base_physical_damage: int = 1,
                 base_magical_damage: int = 0,
                 base_attack_interval: float = 3.5,
                 # --- Stats de acerto / esquiva WoW-style ---
                 base_hit_rating: float = 0.0,    # reduz chance de errar e de ser desviado
                 base_dodge_rating: float = 0.0,  # chance de desviar ataques físicos
                 base_parry_rating: float = 0.0,  # chance de aparar ataques físicos
                 base_block_rating: float = 0.0,  # chance de bloquear (redução parcial)
                 base_block_value: float = 0.0):  # valor flat absorvido ao bloquear
        
        # Atributos base (sem modificadores)
        self.base_stamina = base_stamina
        self.base_armor = base_armor
        self.base_attack_power = base_attack_power
        self.base_spell_power = base_spell_power
        self.base_haste_rating = base_haste_rating
        self.base_crit_rating = base_crit_rating
        self.base_physical_damage = base_physical_damage
        self.base_magical_damage = base_magical_damage
        self.base_attack_interval = base_attack_interval
        self.base_hit_rating   = base_hit_rating
        self.base_dodge_rating = base_dodge_rating
        self.base_parry_rating = base_parry_rating
        self.base_block_rating = base_block_rating
        self.base_block_value  = base_block_value

        # Intervalo original sem arma equipada (para restaurar ao desequipar)
        self._default_attack_interval: float = base_attack_interval

        # Lista de modificadores ativos (de itens, talentos, etc.)
        self.modifiers: list[Modifier] = []

        # Modificadores temporários: [{"modifier": Modifier, "timer": float, "label": str}]
        self.timed_modifiers: list = []

        # Atributos efetivos (base + modificadores)
        self.stamina: float = 0.0
        self.armor: float = 0.0
        self.attack_power: float = 0.0
        self.spell_power: float = 0.0
        self.haste_rating: float = 0.0
        self.crit_rating: float = 0.0
        self.attack_interval: float = 0.0
        self.hit_rating:   float = 0.0
        self.dodge_rating: float = 0.0
        self.parry_rating: float = 0.0
        self.block_rating: float = 0.0
        self.block_value:  float = 0.0

        self.max_hp: int = 0
        self.current_hp: int = 0
        self.attack_cooldown_timer: float = 0.0 # Tempo restante para o próximo ataque
        self.hp5: float       = 0.05  # fração de max_hp regenerada a cada 5s (fora de combate)
        self.hp5_timer: float = 0.0   # acumulador de tempo para o tick de regen

        # Flags comportamentais de talento — populadas por apply_talent_effects().
        # CombatSystem e SkillHandlers lêem esses valores sem conhecer IDs de talentos.
        self.explorador_crit_per_point: int = 0   # pontos em cav_explorador (0 = inativo)
        self.foco_mortal_enabled: bool = False    # cav_foco_mortal >= 1
        self.embalo_on_crit: bool = False         # cav_embalo >= 1 (adiciona carga ao dar crit)
        self.pnq_enabled: bool = False            # cav_punho_queixo >= 1
        # Flags para comportamento de skills específicas
        self.embalo_bonus_per_charge: float = 0.0        # bônus de dano de Golpe Poderoso ao consumir Embalo
        self.golpe_poderoso_rage_cost: int = 15          # custo de Raiva (reduzido por Veterano)
        self.interceptar_cooldown_reduction: float = 0.0 # redução de recarga (Sede de Batalha)
        self.interceptar_stun_duration: float = 0.0      # duração do stun ao usar (Alvo Confirmado)
        self.interceptar_rage_bonus: int = 0             # Raiva gerada ao usar Interceptar (Vontade)
        self.pnq_stun_duration: float = 1.0             # duração do stun de Punho no Queixo
        self.impacto_maquina_matar: bool = False         # Impacto bônus por qtd. de inimigos no raio
        self.impacto_assassino: bool = False             # Impacto pode procar Executar grátis
        self.executar_horrorizante: bool = False         # Executar aplica medo se alvo sobreviver

        # Validação de invariantes críticos — falha rápido durante desenvolvimento
        if base_stamina <= 0:
            raise ValueError(f"CombatStats: base_stamina deve ser > 0 (recebido: {base_stamina})")
        if base_attack_interval <= 0:
            raise ValueError(f"CombatStats: base_attack_interval deve ser > 0 (recebido: {base_attack_interval})")
        if not (0.0 <= base_crit_rating <= 1.0):
            raise ValueError(f"CombatStats: base_crit_rating deve estar em [0, 1] (recebido: {base_crit_rating})")

        # Realiza o cálculo inicial de todos os atributos efetivos
        self._recalculate_effective_stats()
        self.current_hp = self.max_hp # Inicia com vida cheia

    def _calculate_max_hp(self) -> int:
        """
        Calcula a vida máxima com base na estamina efetiva.
        (Exemplo: 10 pontos de vida por ponto de estamina)
        """
        return int(self.stamina * 10)

    def add_modifier(self, modifier: Modifier):
        """Adiciona um modificador e recalcula os atributos efetivos."""
        self.modifiers.append(modifier)
        self._recalculate_effective_stats()

    def remove_modifier(self, modifier: Modifier):
        """Remove um modificador e recalcula os atributos efetivos."""
        if modifier in self.modifiers:
            self.modifiers.remove(modifier)
            self._recalculate_effective_stats()

    def _recalculate_effective_stats(self):
        """
        Recalcula todos os atributos efetivos aplicando os modificadores.
        Preserva a porcentagem de vida atual se a vida máxima mudar.
        """
        # Salva o HP absoluto antes de recalcular a vida máxima
        old_current_hp = self.current_hp

        # Reseta os atributos efetivos para os valores base
        self.stamina       = float(self.base_stamina)
        self.armor         = float(self.base_armor)
        self.attack_power  = float(self.base_attack_power)
        self.spell_power   = float(self.base_spell_power)
        self.haste_rating  = float(self.base_haste_rating)
        self.crit_rating   = float(self.base_crit_rating)
        self.attack_interval = float(self.base_attack_interval)
        self.hit_rating    = float(self.base_hit_rating)
        self.dodge_rating  = float(self.base_dodge_rating)
        self.parry_rating  = float(self.base_parry_rating)
        self.block_rating  = float(self.base_block_rating)
        self.block_value   = float(self.base_block_value)

        # Aplica todos os modificadores
        for mod in self.modifiers:
            if mod.attribute == "stamina":
                if mod.type == "flat":
                    self.stamina += mod.value
                elif mod.type == "percentage":
                    self.stamina *= (1 + mod.value)
            elif mod.attribute == "armor":
                if mod.type == "flat":
                    self.armor += mod.value
                elif mod.type == "percentage":
                    self.armor *= (1 + mod.value)
            elif mod.attribute == "attack_power":
                if mod.type == "flat":
                    self.attack_power += mod.value
                elif mod.type == "percentage":
                    self.attack_power *= (1 + mod.value)
            elif mod.attribute == "spell_power":
                if mod.type == "flat":
                    self.spell_power += mod.value
                elif mod.type == "percentage":
                    self.spell_power *= (1 + mod.value)
            elif mod.attribute == "haste_rating":
                if mod.type == "flat":
                    self.haste_rating += mod.value
                elif mod.type == "percentage":
                    self.haste_rating *= (1 + mod.value)
            elif mod.attribute == "crit_rating":
                if mod.type == "flat":
                    self.crit_rating += mod.value
                elif mod.type == "percentage":
                    self.crit_rating *= (1 + mod.value)
            elif mod.attribute == "attack_interval":
                if mod.type == "flat":
                    self.attack_interval += mod.value
                elif mod.type == "percentage":
                    self.attack_interval *= (1 + mod.value)
            elif mod.attribute == "hit_rating":
                if mod.type == "flat":   self.hit_rating += mod.value
                else:                    self.hit_rating *= (1 + mod.value)
            elif mod.attribute == "dodge_rating":
                if mod.type == "flat":   self.dodge_rating += mod.value
                else:                    self.dodge_rating *= (1 + mod.value)
            elif mod.attribute == "parry_rating":
                if mod.type == "flat":   self.parry_rating += mod.value
                else:                    self.parry_rating *= (1 + mod.value)
            elif mod.attribute == "block_rating":
                if mod.type == "flat":   self.block_rating += mod.value
                else:                    self.block_rating *= (1 + mod.value)
            elif mod.attribute == "block_value":
                if mod.type == "flat":   self.block_value += mod.value
                else:                    self.block_value *= (1 + mod.value)

        # APLICA A ACELERAÇÃO (HASTE) AO INTERVALO DE ATAQUE
        # "a cada 10 de haste, diminui 1 segundo o intervalo"
        self.attack_interval -= (self.haste_rating / 10.0)
        
        # Garante que o intervalo de ataque não seja negativo ou muito baixo.
        # Definir um mínimo para o intervalo (e.g., 0.5 segundos para evitar ataques instantâneos)
        self.attack_interval = max(0.5, self.attack_interval) # Ajuste 0.5 conforme o desejado

        self.crit_rating  = max(0.0, min(1.0, self.crit_rating))
        self.hit_rating   = max(0.0, self.hit_rating)
        self.dodge_rating = max(0.0, self.dodge_rating)
        self.parry_rating = max(0.0, self.parry_rating)
        self.block_rating = max(0.0, self.block_rating)
        self.block_value  = max(0.0, self.block_value)
        
        # Atualiza a vida máxima com a estamina efetiva
        self.max_hp = self._calculate_max_hp()
        
        # Preserva o HP absoluto; apenas limita ao novo máximo se necessário
        self.current_hp = max(0, min(old_current_hp, self.max_hp))

    def add_timed_modifier(self, modifier: Modifier, duration: float, label: str = ""):
        """Adiciona um modificador temporário. Se mesmo label já ativo, reseta o timer."""
        for entry in self.timed_modifiers:
            if entry["label"] == label and label:
                entry["timer"] = duration
                return
        self.timed_modifiers.append({"modifier": modifier, "timer": duration, "label": label})
        self.add_modifier(modifier)

    def get_attack_cooldown(self) -> float:
        """
        Retorna o tempo de recarga de ataque efetivo em segundos,
        que é o próprio intervalo de ataque.
        """
        return self.attack_interval


@dataclass
class Position:
    x: float
    y: float
    prev_x: float = 0.0
    prev_y: float = 0.0

@dataclass
class Renderable:
    color: tuple
    width: int
    height: int

@dataclass
class PlayerControlled:
    pass

@dataclass
class Camera:
    target_entity_id: int = -1
    offset_x: float = 0.0
    offset_y: float = 0.0
    zoom: float = 1.0

@dataclass
class Collider:
    width: int
    height: int

@dataclass
class Enemy:
    pass

@dataclass
class AIControlled:
    state: str = "IDLE"
    is_blocked: bool = False
    blocked_by_entity_id: int = -1
    path: list[tuple[int, int]] | None = None
    path_recalc_timer: float = 0.0
    attack_range_tiles: int = 1 # Alcance de ataque em tiles (1 para corpo a corpo)
    is_ranged: bool = False      # Se verdadeiro, dispara projéteis e faz kiting
    entity_class: str = ""       # "Warrior"|"Mage"|"Warlock"|"Hunter"
    disengage_cd: float = 0.0   # Hunter: cooldown do disengage (s)
    disengage_boost: float = 0.0 # Hunter: tempo de velocidade extra após disengage (s)
    last_known_player_tile: tuple = (-1, -1)  # tile do jogador no último recalculo de path
    aggro_delay: float = 0.0  # tempo restante antes de começar a perseguir após detectar
    # Ranged: kite limitado a 3 tiles por sessão
    kite_tiles_moved: int = 0    # tiles andados no kite atual
    kite_cooldown: float = 0.0   # pausa forçada antes de kitar novamente (s)
    # Ranged: tempo de cast antes de disparar projétil
    ranged_cast_timer: float = 0.0  # >0 = carregando tiro; 0 = pronto/ocioso
    # base_attack_cooldown foi removido, agora está em CombatStats
    
@dataclass
class InitialPosition:
    x: float
    y: float

@dataclass
class DetectionRadius:
    radius: float

@dataclass
class Tilemap:
    tile_matrix: list
    tile_size: int
    map_width_tiles: int
    map_height_tiles: int

class CombatState:
    """
    Estado de combate de uma entidade.
    Controla se pode agir, mover, se está em combate, etc.
    Componente opcional — entidades sem ele não têm restrições de estado.
    """
    OUT_OF_COMBAT_DURATION: float = 6.0  # segundos sem ação para sair do combate

    def __init__(self):
        self.is_alive: bool = True
        self.in_combat: bool = False
        self.is_stunned: bool = False   # Não pode agir nem mover
        self.is_rooted: bool = False    # Pode agir mas não mover
        self.is_casting: bool = False   # Não pode se mover nem iniciar outra ação
        self.target_entity_id: int = -1 # Alvo atual selecionado
        self.is_pursuing: bool = False  # True = persegue o alvo (direito/skill/espaço). False = só selecionado
        self._just_entered_combat: bool = False  # sinaliza transição para CombatStateSystem disparar procs
        self.combat_timer: float = 0.0  # Conta regressiva para sair do combate
        self.stun_timer:   float = 0.0  # Contador de atordoamento (zerado em CombatStateSystem)

    def can_act(self) -> bool:
        """Retorna True se a entidade pode realizar ações (atacar, usar skill)."""
        return self.is_alive and not self.is_stunned and not self.is_casting

    def can_move(self) -> bool:
        """Retorna True se a entidade pode se mover."""
        return self.is_alive and not self.is_stunned and not self.is_rooted and not self.is_casting

    def enter_combat(self):
        """Coloca a entidade em modo combate e reinicia o timer."""
        if not self.in_combat:
            self._just_entered_combat = True
        self.in_combat = True
        self.combat_timer = self.OUT_OF_COMBAT_DURATION

    # Timers são atualizados por CombatStateSystem — não há lógica neste componente.


@dataclass
class PlayerAutoMove:
    """
    Controla o movimento automático do jogador.
    - Clique direito em inimigo → segue e ataca (target_entity_id em CombatState)
    - Clique esquerdo no chão → move até ground_target
    """
    active: bool = False
    path: list = field(default_factory=list)
    path_recalc_timer: float = 0.0
    ground_target: tuple = None   # (tile_x, tile_y) para movimento de chão


class CharacterStats:
    """
    Atributos base do personagem que definem o crescimento e derivam CombatStats.
    Também armazena level, XP e pontos de atributo pendentes.
    """
    BASE_XP = 100  # XP base para ir do nível 1 ao 2

    def __init__(self, strength: int = 1, intelligence: int = 1,
                 agility: int = 1, vitality: int = 3, defense: int = 2,
                 spawn_tile_x: int = 0, spawn_tile_y: int = 0,
                 spawn_map: str = "maps/map_1.csv"):
        self.strength = strength        # FOR → attack_power, dano físico
        self.intelligence = intelligence  # INT → spell_power, mana
        self.agility = agility            # AGI → crit, velocidade
        self.vitality = vitality          # VIT → HP, stamina
        self.defense = defense            # DEF → armor

        self.level = 1
        self.current_xp = 0
        self.xp_to_next_level = CharacterStats.BASE_XP

        # Posição e mapa de spawn para a mecânica roguelike de morte
        self.spawn_tile_x = spawn_tile_x
        self.spawn_tile_y = spawn_tile_y
        self.spawn_map    = spawn_map    # mapa onde o player ressurge

        # Raiva (Rage)
        self.rage: int = 0
        self.max_rage: int = 100
        self.rage_decay_timer: float = 0.0  # timer para decair 5 rage a cada 3s fora de combate

        # Assassino: carga livre de Executar (ignora HP e custo de Raiva)
        self.free_executar_charges: int = 0

        # Embalo: cada crítico do player gera 1 carga → Golpe Poderoso consome
        self.embalo_charges: int = 0

        # Punho no Queixo: contador de golpes (reseta ao acumular 3 → 1 carga)
        self.pnq_counter: int = 0

        # Fatiador de Corpos: spin AoE (duração e timer de tick)
        self.fatiador_timer: float = 0.0  # duração total restante (5s)
        self.fatiador_tick:  float = 0.0  # tempo até o próximo tick de dano

    @staticmethod
    def xp_for_level(level: int) -> int:
        """XP necessário para avançar do nível `level` para `level+1`."""
        return int(CharacterStats.BASE_XP * (level ** 1.5))


@dataclass
class PermanentStats:
    """
    Atributos permanentes que sobrevivem à morte (mecânica roguelike).
    A cada morte, os atributos atuais do personagem são somados aqui.
    São aplicados junto com CharacterStats no cálculo de CombatStats.
    """
    strength: int = 0
    intelligence: int = 0
    agility: int = 0
    vitality: int = 0
    defense: int = 0


@dataclass
class XPReward:
    """Quantidade de XP que esta entidade concede ao ser derrotada."""
    amount: int = 10


@dataclass
class EnemyTier:
    """Hierarquia do inimigo: Normal, Elite, Raro ou Boss."""
    tier: str = "normal"  # "normal" | "elite" | "rare" | "boss"


@dataclass
class Projectile:
    """Projétil disparado por inimigo ranged em direção ao jogador."""
    attacker_id: int
    target_id: int
    damage_type: str = "magical"
    speed: float = 200.0  # pixels/segundo
    color: tuple = (255, 80, 0)   # cor de renderização
    is_arrow: bool = False         # True → renderizar como linha (flecha)
    dir_x: float = 0.0            # direção normalizada X (para flecha)
    dir_y: float = 0.0            # direção normalizada Y (para flecha)


class Item:
    """Um item que pode ser carregado no inventário ou equipado."""
    def __init__(self, name: str, item_type: str, slot: str,
                 modifiers: list = None, rarity: str = "common", value: int = 0,
                 two_handed: bool = False,
                 damage_min: int = 0, damage_max: int = 0,
                 attack_speed: float = 0.0,
                 proc: dict = None,
                 subtype: str = "",
                 consumable: dict = None,
                 max_stack: int = 1):
        self.name = name
        self.item_type = item_type  # "weapon", "armor", "shield", "jewelry", "consumable"
        self.slot = slot            # "mainhand", "offhand", "head", "chest", etc.
        self.modifiers = modifiers if modifiers is not None else []
        self.rarity = rarity        # "common", "uncommon", "rare", "epic"
        self.value = value
        self.two_handed = two_handed  # se True, bloqueia o slot offhand
        # Atributos exclusivos de armas físicas
        self.damage_min = damage_min    # dano mínimo por ataque
        self.damage_max = damage_max    # dano máximo por ataque
        self.attack_speed = attack_speed  # segundos por ataque (0.0 = não é arma física)
        # Efeito de proc (None ou dict com chaves: attribute, value, duration, chance, label)
        self.proc = proc
        self.subtype = subtype  # categoria visual da arma (ex: "Sword", "Mace", "Wand")
        # Consumível: None ou dict com chaves:
        #   heal_instant (int), heal_per_tick (int), interval (float),
        #   ticks (int), ooc_only (bool)
        self.consumable = consumable
        # Empilhamento
        self.max_stack = max_stack   # > 1 = empilhável
        self.stack     = 1           # quantidade atual na pilha

    def __repr__(self):
        return f"Item({self.name!r}, {self.rarity})"


class Equipment:
    """Slots de equipamento do jogador."""

    SLOT_LABELS = {
        "head":      "Cabeça",
        "shoulders": "Ombros",
        "chest":     "Torso",
        "wrists":    "Punhos",
        "boots":     "Botas",
        "gloves":    "Luvas",
        "mainhand":  "Mão Princ.",
        "offhand":   "Mão Secund.",
        "ring":      "Anel",
        "neck":      "Pescoço",
    }

    def __init__(self):
        self.slots: dict[str, Item | None] = {s: None for s in self.SLOT_LABELS}

    def is_offhand_locked(self) -> bool:
        """Retorna True se a mão principal tem uma arma de duas mãos."""
        mh = self.slots.get("mainhand")
        return mh is not None and mh.two_handed

    def get_modifier_total(self, attribute: str) -> float:
        total = 0.0
        for item in self.slots.values():
            if item:
                for mod in item.modifiers:
                    if mod.attribute == attribute:
                        total += mod.value
        return total


class Corpse:
    """Cadáver de inimigo: contém loot e expira após um tempo."""
    DECAY_TIME = 120.0         # fallback se não vier cooldown do mob
    LOOTED_DECAY_TIME = 30.0   # segundos restantes após todo o loot ser retirado

    def __init__(self, loot: list = None, coins: int = 0, decay_time: float = None):
        self.loot: list   = loot if loot is not None else []
        self.coins: int   = coins
        self.timer: float = decay_time if decay_time is not None else self.DECAY_TIME
        self.looted: bool = False
        self.is_open: bool = False


@dataclass
class Wallet:
    """Moedas do jogador."""
    gold: int = 0


@dataclass
class Inventory:
    """Inventário do jogador: lista de itens carregados."""
    items: list = field(default_factory=list)
    max_slots: int = 20


class Skill:
    """Habilidade ativa com cooldown ou baseada em cargas."""
    def __init__(self, name: str, description: str, cooldown: float):
        self.name = name
        self.description = description
        self.cooldown = cooldown
        self.current_cooldown: float = 0.0
        self.skill_id:   str = ""   # ID da habilidade (de skill_config.py)
        self.icon_name:  str = ""   # chave do ícone PNG (ex: "skill_golpe_poderoso")
        self.sound_name: str = ""   # chave do som OGG (ex: "skill_golpe_poderoso")
        self.handler:    str = ""   # handler de talento (preenchido pela árvore de talentos)
        self.talent_id:  str = ""   # ID do talento que desbloqueia esta skill
        self.charges:        int   = 0    # cargas disponíveis
        self.max_charges:    int   = 0    # > 0 → habilidade baseada em cargas (sem cooldown)
        self.charge_timer:   float = 0.0  # tempo restante para a carga expirar
        self.charge_timeout: float = 0.0  # 0 = sem expiração
        self.fail_flash_timer: float = 0.0  # escurece o slot por 0.2s ao falhar
        self.rage_cost:          int  = 0     # custo base em Raiva (0 = sem custo)
        self.proc_attr:          str  = ""    # atributo de CharacterStats que sinaliza proc
        self.proc_ignores_cost:  bool = False # proc dispensa rage_cost quando ativo

    def is_ready(self) -> bool:
        if self.current_cooldown > 0:
            return False
        if self.max_charges > 0:
            return self.charges > 0
        return True


class PlayerSkills:
    """Conjunto de habilidades ativas do jogador. Configurado em skill_config.py."""

    # Habilidades baseadas em cargas: skill_id → (max_charges, charge_timeout)
    _CHARGE_BASED: dict = {"vitoria_iminente": (1, 30.0)}

    # Skills que disparam GCD
    _GCD_SKILLS: set = {"golpe_poderoso", "executar"}
    GCD_DURATION: float = 0.5

    def __init__(self):
        from skill_config import SKILL_SLOTS, DEFAULT_KEYBINDS, NUM_SLOTS, SKILL_CATALOG
        self.gcd_timer: float = 0.0
        # keybinds[i] = pygame.K_* para o slot i
        self.keybinds: list[int] = list(DEFAULT_KEYBINDS)
        # skills[i] = Skill | None (None = slot vazio)
        self.skills: list[Skill | None] = []
        for i in range(NUM_SLOTS):
            skill_id = SKILL_SLOTS[i] if i < len(SKILL_SLOTS) else None
            self.skills.append(self._make_skill(skill_id, SKILL_CATALOG))

    @classmethod
    def _make_skill(cls, skill_id: "str | None", catalog: dict) -> "Skill | None":
        if skill_id is None or skill_id not in catalog:
            return None
        entry = catalog[skill_id]
        if isinstance(entry, dict):
            name = entry["name"]
            desc = entry["desc"]
            cd   = entry["cooldown"]
        else:
            name, desc, cd = entry
        s = Skill(name, desc, cd)
        s.skill_id   = skill_id
        s.icon_name  = f"skill_{skill_id}"
        s.sound_name = f"skill_{skill_id}"
        if isinstance(entry, dict):
            s.rage_cost         = entry.get("rage_cost",         0)
            s.proc_attr         = entry.get("proc_attr",         "")
            s.proc_ignores_cost = entry.get("proc_ignores_cost", False)
            if entry.get("sound"):
                s.sound_name = entry["sound"]
        if skill_id in cls._CHARGE_BASED:
            s.max_charges, s.charge_timeout = cls._CHARGE_BASED[skill_id]
        return s

    def skill_by_id(self, skill_id: str) -> "Skill | None":
        """Retorna o objeto Skill pelo skill_id, ou None se não estiver na barra."""
        for s in self.skills:
            if s is not None and s.skill_id == skill_id:
                return s
        return None


@dataclass
class TileMovement:
    current_tile_x: int = 0
    current_tile_y: int = 0
    target_tile_x: int = 0
    target_tile_y: int = 0
    start_pixel_x: float = 0.0
    start_pixel_y: float = 0.0
    target_pixel_x: float = 0.0
    target_pixel_y: float = 0.0
    progress: float = 0.0
    move_duration: float = 0.2
    is_moving: bool = False
    speed: float = 0.0
    slow_timer: float = 0.0          # duração restante do debuff de velocidade
    slow_mult: float = 1.0           # multiplicador de velocidade (< 1.0 = mais lento)
    debilitate_elapsed: float = 0.0  # tempo acumulado debilitado contínuo (Foco Mortal)
    is_dash: bool = False             # True durante o dash do Interceptar


@dataclass
class Merchant:
    """Componente de comerciante NPC."""
    name: str = "Comerciante"
    shop_id: str = "general"


class TalentTree:
    """
    Árvore de talentos do jogador.
    Armazena build escolhida, pontos alocados e modificadores ativos de talentos.
    """
    def __init__(self):
        self.chosen_build: str        = "cavaleiro"
        self.allocated: dict          = {}   # {talent_id: pontos_alocados}
        self.available_points: int    = 0    # pontos ainda não gastos
        self._applied_modifiers: list = []   # Modifier objects ativos (para remoção)
        self._unlocked_skill_ids: set = set()  # handlers de skills desbloqueadas


class ActiveRegen:
    """Regeneração de HP ativa (de comida). Processa ticks ao longo do tempo."""
    def __init__(self, heal_per_tick: int, interval: float, ticks_total: int):
        self.heal_per_tick   = heal_per_tick
        self.interval        = interval
        self.ticks_total     = ticks_total
        self.ticks_remaining = ticks_total
        self.tick_timer      = interval  # tempo até o próximo tick


class SpawnZone:
    """
    Zona de respawn de inimigos.
    Gerencia um grupo de inimigos do mesmo tipo dentro de um raio em tiles.
    Quando um inimigo morre, a zona aguarda o cooldown e spawna um novo
    em um tile caminhável aleatório dentro do raio.
    """
    def __init__(self, center_x: int, center_y: int,
                 enemy_type: str, enemy_tier: str,
                 radius: int, max_count: int,
                 respawn_cooldown: float,
                 level_min: int = 1, level_max: int = 1,
                 race: str = "Humanoide", entity_class: str = ""):
        self.center_x = center_x
        self.center_y = center_y
        self.enemy_type = enemy_type        # "melee" ou "ranged"
        self.enemy_tier = enemy_tier        # "normal" | "elite" | "rare" | "boss"
        self.radius = radius
        self.max_count = max_count
        self.respawn_cooldown = respawn_cooldown
        self.level_min = level_min
        self.level_max = level_max
        self.race = race                    # raça dos inimigos desta zona
        self.entity_class = entity_class   # "" = derivado do tipo (melee→Guerreiro, ranged→Arqueiro)
        self.active_entity_ids: set = set()
        self.respawn_timers: list = []  # um float por morte; cada um corre independentemente


class SpawnZoneOwner:
    """Marca um inimigo como pertencente a uma SpawnZone específica."""
    def __init__(self, zone_entity_id: int):
        self.zone_entity_id = zone_entity_id


class StatusEffects:
    """
    Efeitos de estado temporários sobre uma entidade.
      stun_timer    : entidade fica imóvel e não pode atacar enquanto > 0
      fear_timer    : entidade foge do player e não pode atacar enquanto > 0
      enraged_timer : entidade enlouquecida pelo Brado Provocativo (+5% dano, -10% resist)
    """
    def __init__(self):
        self.stun_timer:   float = 0.0
        self.fear_timer:   float = 0.0
        self.enraged_timer: float = 0.0


@dataclass
class PendingDeath:
    """Marcador: esta entidade morreu e aguarda processamento por DeathHandlerSystem.

    ECS-puro: CombatSystem adiciona este componente ao invés de criar cadáver
    diretamente. DeathHandlerSystem processa e remove a entidade no mesmo frame.
    """
    killer_entity_id: int = -1


class MobSounds:
    """Sons explícitos de um mob. Cada campo é a chave base do arquivo OGG
    (sem extensão). Variações _2/_3/_4 são tentadas automaticamente.
    Campo vazio → sem som para aquele evento."""
    def __init__(self,
                 aggro: str = "", death: str = "",
                 attack_melee: str = "", attack_ranged: str = "",
                 attack_magic: str = "", crit: str = "",
                 emote_attack: str = "", emote_get_crit: str = ""):
        self.aggro          = aggro
        self.death          = death
        self.attack_melee   = attack_melee
        self.attack_ranged  = attack_ranged
        self.attack_magic   = attack_magic
        self.crit           = crit
        self.emote_attack   = emote_attack
        self.emote_get_crit = emote_get_crit


class ConsumableBar:
    """Barra de atalhos para consumíveis do jogador (5 slots configuráveis)."""
    NUM_SLOTS: int = 5
    GCD_DURATION: float = 1.5  # cooldown global após usar qualquer consumível (s)

    # Z, X, C, V, B
    DEFAULT_KEYBINDS: list = [122, 120, 99, 118, 98]

    def __init__(self):
        # Nome do item em cada slot (None = vazio)
        self.slots: list = [None] * self.NUM_SLOTS
        self.keybinds: list = list(self.DEFAULT_KEYBINDS)
        self.global_cooldown: float = 0.0