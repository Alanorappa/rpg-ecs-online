# components.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import NamedTuple

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
                 level: int = 1, tier: str = "normal", mob_key: str = ""):
        self.name         = name
        self.race         = race
        self.entity_class = entity_class
        self.level        = level
        self.tier         = tier          # "normal" | "elite" | "rare" | "boss"
        # Chave EXATA de content/mob_definitions.py::MOB_TABLE usada na
        # criação (ex: "Arqueiro (NPC)", "Goblin") — diferente de `race`
        # acima, que guarda a categoria AMPLA (ex: "Humanoide", só
        # informativa). Entidades sem SpawnZone (Guarda Real, boneco de
        # treino, NPC de serviço) não tinham como o servidor recuperar essa
        # chave específica pro payload de spawn (`_build_mob_spawn_payload`)
        # — sem ela, o cliente reconstrói via `race` (a categoria ampla, que
        # nunca bate com uma entrada de MOB_TABLE) e cai no fallback
        # genérico, perdendo sons/entity_class corretos (bug real 21/07/2026:
        # "Arqueiro (NPC)" virava melee/mudo no cliente mesmo sendo ranged
        # de verdade no servidor).
        self.mob_key      = mob_key

# Nova Classe: Modifier
# Representa um bônus ou penalidade a um atributo de combate.
# Permite que itens, talentos e habilidades modifiquem os atributos de forma flexível.
class Modifier:
    def __init__(self, attribute: str, value: float, type: str = "flat",
                 source: str = "equipment"):
        """
        Inicializa um modificador de atributo.

        Args:
            attribute (str): O nome do atributo a ser modificado (e.g., "stamina", "armor", "attack_power").
            value (float): O valor do modificador.
            type (str): O tipo de modificação ("flat" para soma/subtração, "percentage" para multiplicação).
                        Ex: "flat" com value=10 para +10 de Armadura.
                        Ex: "percentage" com value=0.10 para +10% de Força.
            source (str): origem do modificador — "equipment" (item equipado,
                        DEFAULT — é de longe o caso mais comum, todo
                        Modifier de item em loot_tables.py/crafting_data.py/
                        merchant_data.py usa o default sem precisar passar
                        nada), "talent" (ponto de talento alocado) ou "buff"
                        (efeito temporário de skill/proc). Usado só pra UI
                        (separar "Base" de "Itens" na aba Estatísticas do
                        Inventário, ver CombatStats.equipment_bonus()) — não
                        entra em __eq__/__hash__, não afeta remove_modifier().
        """
        self.attribute = attribute
        self.value = value
        self.type = type
        self.source = source

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
        self.base_physical_damage     = base_physical_damage
        self.base_physical_damage_max = base_physical_damage  # default: min==max (flat); sete maior para range
        self.base_magical_damage      = base_magical_damage
        self.base_attack_interval = base_attack_interval
        self.base_hit_rating          = base_hit_rating
        self.base_armor_penetration:  float = 0.0  # redução flat de armor do alvo
        self.base_dodge_rating = base_dodge_rating
        self.base_parry_rating = base_parry_rating
        self.base_block_rating = base_block_rating
        self.base_block_value  = base_block_value
        # Acerto: chance base de acertar um auto-ataque físico (0–100 %).
        # Default 95.0 → ~5% miss, igual ao comportamento legado de mobs.
        # Classes jogáveis recebem valor específico via CLASS_ACERTO (stats_system).
        self.base_acerto: float = 95.0

        # Intervalo original sem arma equipada (para restaurar ao desequipar)
        self._default_attack_interval: float = base_attack_interval

        # Lista de modificadores ativos (de itens, talentos, etc.)
        self.modifiers: list[Modifier] = []

        # Modificadores temporários tipados
        self.timed_modifiers: list[dict] = []  # {modifier, timer, label}

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
        # Espelho de CharacterStats.mana — só pra checagens client/server que
        # precisam de mana sem puxar o componente CharacterStats inteiro (ver
        # comentários "CombatStats.mana" em world_server.py/network_handlers.py/
        # skill_handlers.py/spell_system.py). Sem essa declaração aqui, o
        # atributo só passava a existir na primeira vez que um desses
        # caminhos de sync rodasse — e nenhum deles roda pra Guerreiro/
        # Arqueiro (não usam mana), então `cs.mana` nunca existia pra essas
        # classes e qualquer leitura (ex: `payload.get("mana", cs.mana)`)
        # crashava com AttributeError. Bug real visto em produção: Guerreiro
        # crashou ao reviver no cemitério (ver PROBLEMAS_ARQUITETURA.md).
        self.mana: int = 0
        self.attack_cooldown_timer: float = 0.0 # Tempo restante para o próximo ataque
        # hp5/mp5: fração de max_hp/max_mana regenerada a cada 5s FORA de
        # combate — base fixa por classe (CLASS_BASE_REGEN, stats_system.py)
        # + Spirit (CharacterStats.spirit, 10 pontos = +1%, só de itens/
        # talentos futuros — nunca cresce com level). mp5_ic é a mana EM
        # combate — NUNCA afetada por Spirit, só por talento (decisão do
        # usuário) — por isso é um _MODIFIABLE_ATTR separado de mp5, não
        # uma fração do mesmo valor. Mob nunca passa por
        # apply_char_stats_to_combat (não tem CharacterStats), então mantém
        # o base_hp5 default abaixo (1%) como sempre teve.
        self.base_hp5: float    = 0.01
        self.hp5: float         = 0.01
        self.hp5_timer: float   = 0.0   # acumulador de tempo para o tick de regen de HP
        self.base_mp5: float    = 0.0
        self.mp5: float         = 0.0
        self.base_mp5_ic: float = 0.0
        self.mp5_ic: float      = 0.0

        # Flags de comportamento de combate — setadas por CLASS_MELEE_OVERRIDES em stats_system.
        # Sistemas lêem esses flags sem precisar conhecer class_id.
        self.arrow_pre_draw_ready: bool = True  # True = pode tocar som de pré-tensionamento no próximo ciclo
        # Taxa de regen de Concentração (pontos/segundo). Lida pelo CombatStateSystem.
        self.concentration_regen_idle:   float = 0.0  # parado
        self.concentration_regen_moving: float = 0.0  # andando
        # Talento Prático (arqueiro) — recarga em movimento
        self.recarregar_in_motion:       bool  = False
        # Talento Calmo e Certeiro — bônus de acerto por segundo parado
        self.standing_seconds:           float = 0.0
        self.acerto_per_standing_second: float = 0.0
        # Talento Tiro Múltiplo — máx alvos no cone (0=inativo, 99=ilimitado)
        self.tiro_multiplo_targets: int   = 0
        # Talento Na Mosca — crit → próxima flecha +25% dano
        self.na_mosca_enabled:      bool  = False   # talento alocado
        self.na_mosca_bonus_active: bool  = False   # True = próxima flecha recebe o bônus
        # Talento Flechas Despadronizadas — proc +50% dano ao disparar
        self.flechas_despadronizadas_chance: float = 0.0
        # Habilidade Camuflagem
        self.camouflage_timer:  float = 0.0   # > 0 = camuflagem ativa
        self.camouflage_object: str   = ""    # ID do objeto do tileset em uso
        # Talento Reciclagem — recupera flechas ao abater inimigos
        self.arrows_received:                int   = 0     # flechas físicas que acertaram esta entidade
        self.arrow_recovery_enabled:        bool  = False # True = reciclagem ativa no atacante
        # Talento Sequência Final — 3ª flecha na Flecha Reiterada quando alvo está baixo
        self.flecha_reiterada_hp_threshold: float = 0.0  # 0 = inativo; > 0 = % HP abaixo do qual ativa
        # Talento Alvo Fácil — bônus contra alvos sob controle (atualizado por StatusEffectSystem)
        self.is_crowd_controlled:           bool  = False  # True = tem stun/sleep/fear/poly/slow/disoriented
        self.alvo_facil_acerto:          int   = 0      # +X% acerto contra alvos sob CC (por ponto)
        self.alvo_facil_crit:            float = 0.0    # +X% crit contra alvos sob CC (por ponto)
        # Buff "Só um Gole" — Concentração grátis + acerto 100%
        self.concentration_free:         bool  = False  # True = skills sem custo de Concentração
        self.concentration_free_timer:   float = 0.0    # segundos restantes do buff

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
        # ── Flags comportamentais — build Piromania (Mago) ──────────────────
        self.fire_mana_discount:        int   = 0     # pir_frieza: desconto fixo no custo de Bola de Fogo
        self.fire_cast_time_reduction:  float = 0.0  # pir_bdf_aperfeicoada: reduz cast time de BdF
        self.fire_burns_on_crit:        bool  = False # pir_queimaduras: crítico de BdF aplica burn
        self.fire_burn_duration:        float = 0.0  # pir_queimaduras: duração do burn (pts × 3s)
        self.ice_cast_time_reduction:   float = 0.0  # pir_precisao_elemental: reduz cast time de NC
        self.fire_shield_enabled:       bool  = False # pir_escudo_fogo: retaliação de fogo
        self.fire_instant_proc_chance:  float = 0.0  # pir_chama_interna: % chance BdF ficará instante e grátis
        self.thermal_shock_enabled:     bool  = False # pir_choque_termico: +100% dano fogo em alvo frozen
        self.pyromania_bonus:           float = 0.0  # pir_piromaníaco: bônus de dano/desconto de mana fogo
        self.elemental_lapse_crit_bonus: float = 0.0 # pir_lapso_elemental: crit% durante proc
        self.fire_crit_counter:         int   = 0    # contagem de crits de fogo para Lapso Elemental
        self.fire_crit_timer:           float = 0.0  # janela de 6s para acumular 3 crits
        self.fire_exhaustion_enabled:   bool  = False # pir_exaustao: slow progressivo por BdF consecutiva
        self.crematoria_enabled:        bool  = False # pir_crematoria: +25% dano em alvos <20% HP

        # ── Bônus derivados de SkillLevels (ver stats_system.apply_skill_bonuses_to_combat) ──
        # Recalculados sob demanda (level-up de skill ou spawn/login), nunca via Modifier —
        # cada um já é o bônus final (0.0 a 0.15) pronto pra somar no cálculo de combate.
        self.weapon_skill_bonus: dict[str, float] = {}  # {"machado":0.0, "espada":0.0, "maca":0.0, "arco":0.0, "baculo":0.0}
        self.shield_skill_block_bonus:  float = 0.0
        self.defense_skill_avoid_bonus: float = 0.0
        self.resist_fogo:     float = 0.0
        self.resist_gelo:     float = 0.0
        self.resist_natureza: float = 0.0
        self.magic_skill_dmg_bonus:  float = 0.0
        self.magic_skill_crit_bonus: float = 0.0

        # Validação de invariantes críticos — falha rápido durante desenvolvimento
        if base_stamina <= 0:
            raise ValueError(f"CombatStats: base_stamina deve ser > 0 (recebido: {base_stamina})")
        if base_attack_interval <= 0:
            raise ValueError(f"CombatStats: base_attack_interval deve ser > 0 (recebido: {base_attack_interval})")
        if not (0.0 <= base_crit_rating <= 1.0):
            raise ValueError(f"CombatStats: base_crit_rating deve estar em [0, 1] (recebido: {base_crit_rating})")

        # HP salvo para restaurar após recálculo ao carregar save (populado por save_system)
        self._saved_hp:  int = 0
        self._saved_max: int = 0

        # Realiza o cálculo inicial de todos os atributos efetivos
        self._recalculate_effective_stats()
        self.current_hp = self.max_hp # Inicia com vida cheia

    def _calculate_max_hp(self) -> int:
        """HP = stamina direto (base_stamina = CLASS_BASE_HP + VIT×10)."""
        return int(self.stamina)

    def _recalculate_effective_stats(self):
        """Delegate — a implementação real (data-driven) vive em
        stat_fns.recalculate_combat_stats: componente é dado, lógica de
        mutação fica em stat_fns (regra do projeto). A tabela de atributos
        modificáveis/clamps está lá (_MODIFIABLE_ATTRS/_STAT_CLAMPS) —
        atributo novo = 1 entrada na tabela, não uma cadeia de elif aqui.

        Import local: components.py não pode importar stat_fns no topo
        (stat_fns importa components — ciclo)."""
        from engine.stat_fns import recalculate_combat_stats
        recalculate_combat_stats(self)

    def equipment_bonus(self, attribute: str) -> float:
        """Soma só os modificadores `source="equipment"` (item equipado) de
        um atributo — usado pela aba Estatísticas do Inventário pra separar
        "Base" (tudo que não é item: atributo cru + talentos + buffs) de
        "Itens" (só o que o equipamento adiciona). Todo modificador de
        equipamento hoje é "flat" (confirmado: nenhum item em
        loot_tables.py/crafting_data.py/merchant_data.py usa "percentage"),
        então a soma direta é exata — não precisa simular ordem de
        aplicação. Se algum dia um item ganhar modificador "percentage",
        esse método vai ignorá-lo silenciosamente (subestima "Itens") —
        revisar se isso passar a ser usado."""
        return sum(m.value for m in self.modifiers
                  if m.attribute == attribute and m.source == "equipment" and m.type == "flat")

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
class RemoteControlled:
    """Tag: jogador online controlado remotamente.
    Exclui da lógica local de input, IA e pathfinding."""
    server_eid: int = -1
    name:       str = ""
    class_id:   str = "guerreiro"
    hp:         int = 100
    hp_max:     int = 100
    level:      int = 1

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
class Combatant:
    """Marcador genérico: entidade plenamente sincronizada pelo servidor
    como combatente (mob OU NPC de combate — engine/entity_factory.py::
    _build_combat_entity anexa em ambos). server/world_server.py usa este
    componente (não `Enemy`) pra decidir quem entra em `_mob_eids`/recebe
    `ENTITY_SPAWN` — `Enemy` sozinho implicaria "hostil ao player", o que
    não é verdade pra um NPC de combate amigável (ex: guarda). Sistema de
    Facções, Fase 4 (ARQUITETURA_ONLINE.md)."""
    pass

@dataclass
class Faction:
    """Facção de combate de um mob/NPC — resolve quem ataca quem via
    content/faction_data.py::get_relationship() (chamado através de
    engine/faction_system.py, nunca direto). Player normalmente NÃO tem
    este componente — a facção dele é a constante PLAYER_FACTION,
    resolvida via PlayerControlled em engine/faction_system.py. Exceção:
    server/team_processor.py anexa Faction("arena_time_a"/"arena_time_b")
    a um player SÓ durante uma partida (Fase G) — sobrescreve
    PLAYER_FACTION enquanto presente, removido ao sair/terminar a
    partida. Entidade sem Faction (NPC estático, boneco de treino,
    blocker) é tratada como "sem facção" (sentinela neutro) por
    engine/faction_system.py — nunca quebra por ausência do componente."""
    faction_id: str = "monstros_hostis"

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
    aggroed_by_damage: bool = False  # True quando o aggro veio de acerto, não de proximidade
    # Ranged: kite limitado a 3 tiles por sessão
    kite_tiles_moved: int = 0    # tiles andados no kite atual
    kite_cooldown: float = 0.0   # pausa forçada antes de kitar novamente (s)
    # Ranged: tempo de cast antes de disparar projétil
    ranged_cast_timer: float = 0.0  # >0 = carregando tiro; 0 = pronto/ocioso
    # base_attack_cooldown foi removido, agora está em CombatStats
    target_eid: int = -1  # eid do alvo atual (multiplayer: cada mob tem o seu)
    target_lost_timer: float = 0.0  # grace period antes de ir pro IDLE quando perde alvo
    regen_timer: float = 0.0  # acumulador do regen fora de combate (3s/tick, ver WorldServer._tick)

@dataclass
class InitialPosition:
    x: float
    y: float

@dataclass
class DetectionRadius:
    radius: float

@dataclass
class Tilemap:
    tile_matrix: list       # list[list[TileType]] — colisão + fallback de cor
    terrain_matrix: list    # list[str]            — chars de terreno (G, W, ~…), para autotile
    object_matrix: list     # list[list[str]]       — IDs de objeto por tile (t, b, k, t1, "." = vazio)
    tile_size: int
    map_width_tiles: int
    map_height_tiles: int
    # Visual override de terreno: sprite_id por tile, "" = sem override.
    # Permite pintar sheet tiles sobre o terreno sem afetar a camada de objetos.
    terrain_visual: list = None   # list[list[str]], inicializado em entity_factory


class Visible:
    """Tag: entidade está no campo de visão do jogador neste frame.
    Adicionada/removida pelo FogSystem a cada frame com base em FogOfWar.visible."""
    __slots__ = ()


class FogOfWar:
    """
    Campo de visão do jogador calculado por shadowcasting.

    visible  — tiles visíveis neste frame (recalculado ao mover de tile)
    explored — tiles já descobertos (persiste entre movimentos; salvo futuramente)
    radius   — raio em tiles; padrão 8 (~256px a 32px/tile)
    """

    def __init__(self, radius: int = 8, explore_radius: int = 20) -> None:
        self.radius:          int   = radius
        self.explore_radius:  int   = explore_radius
        self.visible:         set   = set()           # LOS atual (recalculado a cada movimento)
        self._explored_maps:  dict  = {}              # {map_file: set()} — explorado por mapa
        self.explored:        set   = set()           # aponta para _explored_maps[mapa_atual]
        self._last_tile:      tuple = (-1, -1)
        self._current_map:    str   = ""

    def switch_map(self, map_file: str) -> None:
        """Troca o contexto de exploração para o mapa dado. Cria o set se não existir."""
        if map_file not in self._explored_maps:
            self._explored_maps[map_file] = set()
        self._current_map = map_file
        self.explored     = self._explored_maps[map_file]
        self.visible      = set()
        self._last_tile   = (-1, -1)


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
        self.is_immune:   bool = False   # Imune a todos os danos (Bloco de Gelo, Camuflagem)
        self.is_visible:  bool = True    # False = invisível (ex: Camuflagem); mobs não agrem
        self.is_camouflaged: bool = False  # True = Camuflagem ativa; bloqueia can_act() (atacar/usar skill)
        self.target_entity_id: int = -1 # Alvo atual selecionado
        self.is_pursuing: bool = False  # True = persegue o alvo (direito/skill/espaço). False = só selecionado
        self._just_entered_combat: bool = False  # sinaliza transição para CombatStateSystem disparar procs
        self.combat_timer: float = 0.0  # Conta regressiva para sair do combate
        self.stun_timer:   float = 0.0  # Contador de atordoamento (zerado em CombatStateSystem)
        self.respawn_immunity_ticks: int = 0  # >0 = invisível para mobs (pós-respawn); decrementado pelo servidor

    def can_act(self) -> bool:
        """Retorna True se a entidade pode realizar ações (atacar, usar skill)."""
        return (self.is_alive and not self.is_stunned and not self.is_casting
                and not self.is_camouflaged)

    def can_move(self) -> bool:
        """Retorna True se a entidade pode se mover.
        Cast não bloqueia movimento — o SpellCastSystem cancela o cast se interruptível."""
        return self.is_alive and not self.is_stunned and not self.is_rooted

    # Timers atualizados por CombatStateSystem. Mutações via stat_fns.enter_combat().


@dataclass
class GhostState:
    """
    Fluxo de morte/respawn: corpo fica no local da morte, espírito (ghost)
    spawna no cemitério (intangível, sem dano/aggro) e revive ao ficar
    GHOST_GRAVEYARD_REVIVE_S no raio do cemitério ou confirmar revive no corpo.
    """
    is_dead: bool = False         # corpo morto, espírito ainda não liberado
    is_ghost: bool = False        # espírito liberado, vagando/cemitério
    corpse_tx: int = -1
    corpse_ty: int = -1
    graveyard_timer: float = 0.0  # segundos contínuos dentro do raio do cemitério
    near_corpse: bool = False     # dentro do raio de revive do corpo


@dataclass
class PlayerAutoMove:
    """
    Controla o movimento automático do jogador.
    - Clique direito em inimigo → segue e ataca (target_entity_id em CombatState)
    - Clique esquerdo no chão → move até ground_target
    - Botão "Seguir" do modal de player → acompanha follow_eid (sem combate)
    """
    active: bool = False
    path: list = field(default_factory=list)
    path_recalc_timer: float = 0.0
    ground_target: tuple = None   # (tile_x, tile_y) para movimento de chão
    # "Seguir" (modal de interação com player, 16/07/2026): eid LOCAL do
    # player seguido (-1 = não seguindo). Acompanha o alvo em movimento
    # (mesmo pathing da perseguição de combate, parando adjacente), sem
    # nenhum combate envolvido. Cancelado por WASD/clique de chão/
    # perseguição de combate/alvo sumir.
    follow_eid: int = -1


class CharacterStats:
    """
    Atributos base do personagem que definem o crescimento e derivam CombatStats.
    Também armazena level, XP e pontos de atributo pendentes.
    """
    BASE_XP = 100  # XP base para ir do nível 1 ao 2

    def __init__(self, strength: int = 1, intelligence: int = 1,
                 agility: int = 1, vitality: int = 3, defense: int = 2,
                 spirit: int = 0,
                 spawn_tile_x: int = 0, spawn_tile_y: int = 0,
                 spawn_map: str = "maps/map_1.csv",
                 name: str = "Aventureiro", class_id: str = "guerreiro"):
        self.name     = name            # nome do personagem (exibido no HUD)
        self.class_id = class_id        # classe escolhida na criação
        self.strength = strength        # FOR → attack_power, dano físico
        self.intelligence = intelligence  # INT → spell_power, mana
        self.agility = agility            # AGI → crit, velocidade
        self.vitality = vitality          # VIT → HP, stamina
        self.defense = defense            # DEF → armor
        # ESP → hp5/mp5 (10 pontos = +1%), SÓ fora de combate — mana em
        # combate é controlada por talento, não por spirit (decisão do
        # usuário). Default 0 e SEM entrada em CLASS_BASE_STATS/
        # CLASS_LEVEL_GAINS de propósito — regen já é percentual, então
        # spirit não cresce com level (ficaria absurdo somado a
        # itens/talentos); a base de hp5/mp5 por classe é um valor FIXO
        # (CLASS_BASE_REGEN, stats_system.py) e spirit é só o que
        # itens/talentos futuros adicionarem em cima disso.
        self.spirit = spirit

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
        # Chama Interna: proc ativo — próxima Bola de Fogo é instantânea e grátis
        self.fire_instant_ready: bool = False
        # Choque Térmico: True quando alvo selecionado tem root (atualizado por ManaSystem)
        self.thermal_shock_active: bool = False

        # Punho no Queixo: contador de golpes (reseta ao acumular 3 → 1 carga)
        self.pnq_counter: int = 0

        # Fatiador de Corpos: spin AoE (duração e timer de tick)
        self.fatiador_timer: float = 0.0  # duração total restante (5s)
        self.fatiador_tick:  float = 0.0  # tempo até o próximo tick de dano

        self._init_mana_concentration()

    # Campos voláteis de combate: devem ser resetados no respawn/load para evitar
    # estados persistentes inconsistentes (P3 do plano de ação).
    _VOLATILE_FIELDS: tuple = (
        ("free_executar_charges", 0),
        ("embalo_charges",        0),
        ("fire_instant_ready",    False),
        ("thermal_shock_active",  False),
        ("pnq_counter",           0),
        ("fatiador_timer",        0.0),
        ("fatiador_tick",         0.0),
        ("fire_crit_counter",     0),
        ("fire_crit_timer",       0.0),
        ("rage",                  0),
    )

    def reset_volatile(self) -> None:
        """Reseta campos voláteis de combate para valores iniciais.
        Chamar no respawn e no load de personagem."""
        for field, default in self._VOLATILE_FIELDS:
            setattr(self, field, default)

    def _init_mana_concentration(self) -> None:
        """Chamado ao final de __init__ — campos de Mago/Arqueiro."""
        # Mana (classe Mago)
        self.mana: int = 0
        self.max_mana: int = 0
        self.mana_regen_timer: float = 0.0

        # Concentração (classe Arqueiro) — inicia cheia, skills consomem, tempo regenera
        self.concentration:     int   = 0    # valor atual (setado para max em apply_char_stats)
        self.max_concentration: int   = 0    # 0 = recurso inexistente para esta classe
        # Canção de Ninar — alvos adormecidos durante o canal (limpo ao concluir/cancelar)
        self.lullaby_targets:   list  = []

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
    spirit: int = 0


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
    ability_id: str = ""          # se preenchido: apply_effect ao acertar (não deal_damage)


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
                 max_stack: int = 1,
                 armor_class: str = "",
                 arrow_count: int = 0,
                 max_arrows: int = 0,
                 cast_range: int = 0,
                 item_level: int = 1,
                 level_requirement: int = 1,
                 description: str = ""):
        self.name = name
        self.item_type = item_type  # "weapon", "armor", "shield", "jewelry", "consumable", "quiver", "ammo"
        self.slot = slot            # "mainhand", "offhand", "head", "chest", etc.
        self.modifiers = modifiers if modifiers is not None else []
        self.rarity = rarity        # "common", "uncommon", "rare", "epic", "legendary", "mythic"
        self.value = value
        self.two_handed = two_handed  # se True, bloqueia o slot offhand
        # Atributos exclusivos de armas físicas
        self.damage_min = damage_min    # dano mínimo por ataque
        self.damage_max = damage_max    # dano máximo por ataque
        self.attack_speed = attack_speed  # segundos por ataque (0.0 = não é arma física)
        # Efeito de proc (None ou dict com chaves: attribute, value, duration, chance, label)
        self.proc = proc
        self.subtype = subtype  # categoria visual da arma (ex: "Sword", "Mace", "Bow")
        # Consumível: None ou dict com chaves:
        #   HP:   heal_instant (int), heal_per_tick (int), interval (float), ticks (int)
        #   Mana: mana_restore (int), mana_per_tick (int)  [interval e ticks compartilhados]
        #   ooc_only (bool) — bloqueia uso em combate
        self.consumable = consumable
        # Empilhamento
        self.max_stack = max_stack   # > 1 = empilhável
        self.stack     = 1           # quantidade atual na pilha
        # Tipo de material (apenas item_type=="armor"): "tecido"|"couro"|"placa"|""
        self.armor_class: str = armor_class
        # Aljava (item_type=="quiver"): contador de flechas equipadas
        self.arrow_count: int = arrow_count  # flechas restantes na aljava
        self.max_arrows:  int = max_arrows   # capacidade máxima (100 para aljava padrão)
        # Alcance ranged (item_type=="weapon", subtype=="Bow"): tiles de alcance
        self.cast_range: int = cast_range    # 0 = não ranged
        # Nível do item (exibição, escala com raridade) e nível mínimo do
        # personagem pra equipar (validado em update_player_equipment no
        # servidor — ver server/world_server.py). Descrição é opcional,
        # texto livre de lore (ex.: item lendário com história própria).
        self.item_level:        int = item_level
        self.level_requirement: int = level_requirement
        self.description:       str = description

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


class EnemyAbilitySlot:
    """Estado de runtime de uma habilidade especial de inimigo.

    Armazena apenas o cooldown atual — os dados estáticos (dano, duração, etc.)
    ficam em ABILITY_DEFS (enemy_abilities_data.py).
    """
    __slots__ = ("ability_id", "cooldown", "current_cooldown")

    def __init__(self, ability_id: str, cooldown: float,
                 current_cooldown: float = 0.0) -> None:
        self.ability_id:       str   = ability_id
        self.cooldown:         float = cooldown
        self.current_cooldown: float = current_cooldown


class EnemyAbilities:
    """Contêiner de habilidades especiais de um inimigo.

    Criado pela entity_factory com base em MOB_ABILITIES.
    Gerenciado pelo EnemyAbilitySystem.
    """
    def __init__(self, slots: list) -> None:
        self.slots: list = slots   # list[EnemyAbilitySlot]


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
        # Campos de magia (Mago)
        self.mana_cost:       int   = 0      # custo de mana para ativar
        self.cast_time:       float = 0.0   # 0 = instantâneo; >0 = barra de cast
        self.is_channeled:    bool  = False  # True = habilidade canalizada
        self.channel_duration: float = 0.0  # duração total da canalização
        self.needs_aoe_target: bool = False  # True = requer clique de mira AOE
        self.cast_range:      int   = 0      # alcance máximo em tiles (0 = melee/sem alcance)
        self.school:          str   = ""     # escola de magia: "fogo"|"gelo"|"arcano"|""
        self.mana_cost_pct:   float = 0.0   # custo em % da mana máxima (0 = usa mana_cost fixo)
        self.offensive:       bool  = True  # False = skill utilitária/buff — não inicia combate
        self.needs_target:    bool  = True  # False = AoE/utilitária — dispara sem alvo selecionado
        # Parâmetros de gameplay da skill (vindos do SKILL_CATALOG["params"])
        # Ex: {"damage_multiplier": 0.45, "radius_tiles": 2, "duration": 5.0}
        self.params:          dict  = {}

    def is_ready(self) -> bool:
        if self.current_cooldown > 0:
            return False
        if self.max_charges > 0:
            return self.charges > 0
        return True


class PlayerSkills:
    """Conjunto de habilidades ativas do jogador. Configurado em skill_config.py."""

    # Habilidades baseadas em cargas: skill_id → (max_charges, charge_timeout)
    _CHARGE_BASED: dict = {
        "vitoria_iminente": (1, 30.0),
        "punho_no_queixo":  (1, 0.0),   # 1 carga, sem expiração
    }

    # Skills que disparam GCD
    _GCD_SKILLS: set = {"golpe_poderoso", "executar", "polimorfia"}
    GCD_DURATION: float = 0.8

    def __init__(self):
        from content.skill_config import DEFAULT_KEYBINDS, NUM_SLOTS
        self.gcd_timer: float = 0.0
        # keybinds[i] = pygame.K_* para o slot i
        self.keybinds: list[int] = list(DEFAULT_KEYBINDS)
        # skills[i] = Skill | None (None = slot vazio)
        self.skills: list[Skill | None] = [None] * NUM_SLOTS
        # IDs de skills aprendidas com treinador (controla o que pode ir para a barra)
        self.learned_skill_ids: set = set()

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
            s.mana_cost         = entry.get("mana_cost",         0)
            s.cast_time         = entry.get("cast_time",         0.0)
            s.is_channeled      = entry.get("is_channeled",      False)
            s.channel_duration  = entry.get("channel_duration",  0.0)
            s.needs_aoe_target  = entry.get("needs_aoe_target",  False)
            s.cast_range        = entry.get("cast_range",        0)
            s.school            = entry.get("school",            "")
            s.mana_cost_pct     = entry.get("mana_cost_pct",    0.0)
            s.offensive         = entry.get("offensive",         True)
            s.needs_target      = entry.get("needs_target",      True)
            s.params            = entry.get("params",            {})
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
    slow_mult: float = 1.0           # multiplicador de velocidade — gerenciado por StatusEffectSystem
    debilitate_elapsed: float = 0.0  # tempo acumulado debilitado contínuo (Foco Mortal)
    is_dash: bool = False             # True durante o dash do Interceptar
    elevation: int = 0               # nível de elevação atual (0=chão, 1=estrutura...)
    # Tile autoritativo do servidor (online) — sincronizado via from_tx/from_ty do ENTITY_MOVE.
    # Representa onde o mob ESTÁ no servidor (não a posição visual animada).
    # 0,0 = não inicializado → fallback para current_tile.
    server_tile_x: int = 0
    server_tile_y: int = 0
    # Campos injetados pelo skill_processor do servidor para comunicar direção/alvo AOE ao handler.
    # Declarados aqui para evitar setattr dinâmico em dataclass (viola type safety).
    _server_dir_x: float = 0.0   # direção X normalizada (Pirofagia, Tiro Múltiplo)
    _server_dir_y: float = 0.0   # direção Y normalizada
    _server_aoe_x: float = 0.0   # coordenada X world do alvo AOE (Calamidade Flamejante)
    _server_aoe_y: float = 0.0   # coordenada Y world do alvo AOE
    # Janela de "ainda considerado em movimento" só pro servidor inferir is_moving
    # de PLAYERS — WorldServer.move_player() faz snap instantâneo de tile (sem
    # tween real, diferente de mob/cliente), então sem isso is_moving nunca
    # vira True pra players no servidor, quebrando qualquer mecânica que
    # dependa de "parado vs andando" lá (Calmo e Certeiro, regen de
    # Concentração — ver ServerCombatStateSystem._tick_player_move_grace).
    _server_move_grace: float = 0.0
    # Baseline anti-cheat do servidor pra validação de MOVE (WorldServer.
    # move_player()): última posição "de confiança" + timestamp real
    # (time.time(), nunca o ts do cliente). -1 = não inicializado ainda
    # (primeiro MOVE após spawn usa current_tile_x/y como fallback). Tocado
    # em TODO ponto que escreve a posição de verdade — snap_to_tile()
    # (knockback/teleporte/respawn) e a finalização do tween em
    # TileMovementSystem (cobre o dash do Interceptar) — pra não punir o
    # PRIMEIRO passo normal logo depois de um deslocamento forçado como se
    # fosse um salto implausível. Só tem sentido no servidor; no cliente é
    # bookkeeping morto (sem custo real, mesmo padrão de _server_move_grace).
    _last_valid_tile_x: int = -1
    _last_valid_tile_y: int = -1
    _last_valid_ts:     float = 0.0


@dataclass
class NPC:
    """Identidade compartilhada de qualquer NPC (nome, nível, profissão).
    Adicione junto com componentes de capacidade: Merchant, QuestGiver, Trainer...
    """
    name:       str = "NPC"
    level:      int = 1
    profession: str = "NPC"


@dataclass
class Merchant:
    """Capacidade de loja. Combine com NPC para criar um comerciante."""
    shop_id: str = "general"


@dataclass
class QuestGiver:
    """Capacidade de dador/receptor de quests. Combine com NPC."""
    quest_ids:   tuple = ()   # quests que este NPC pode oferecer
    turn_in_ids: tuple = ()   # quests que este NPC aceita para entrega (vazio = igual a quest_ids)


@dataclass
class Blacksmith:
    """Capacidade de ferreiro (reciclagem + forja). Combine com NPC e Merchant."""
    shop_id: str = "blacksmith"


@dataclass
class Trainer:
    """Capacidade de treinador de classe — ensina skills a preço. Combine com NPC."""
    class_id: str = "guerreiro"   # identificador da classe ensinada


class LearnedRecipes:
    """Receitas que o jogador aprendeu ao consumir itens de receita."""
    def __init__(self):
        self.known: list = []   # lista de recipe_id strings

    # Mutação via stat_fns.learn_recipe().


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


SKILL_IDS = ("machado", "espada", "maca", "arco", "baculo", "escudo",
             "defesa", "resist_fogo", "resist_gelo", "resist_natureza", "magic")
MAX_SKILL_LEVEL = 200


class SkillLevels:
    """
    Progressão Tibia-like por uso (0-200) — armas, escudo, defesa,
    resistências mágicas e magic. Server-autoritativo: só o servidor
    concede xp e persiste; cliente só exibe (ver stats_system.py e
    PROBLEMAS_ARQUITETURA.md, seção skill level).
    """
    def __init__(self):
        self.levels: dict = {sid: 0 for sid in SKILL_IDS}
        self.xp:     dict = {sid: 0 for sid in SKILL_IDS}


class ActiveRegen:
    """Regeneração de HP ativa (de comida). Processa ticks ao longo do tempo."""
    def __init__(self, heal_per_tick: int, interval: float, ticks_total: int):
        self.heal_per_tick   = heal_per_tick
        self.interval        = interval
        self.ticks_total     = ticks_total
        self.ticks_remaining = ticks_total
        self.tick_timer      = interval  # tempo até o próximo tick


class ActiveManaRegen:
    """Regeneração de mana ativa (de odres de água). Mesma estrutura que ActiveRegen."""
    def __init__(self, mana_per_tick: int, interval: float, ticks_total: int):
        self.mana_per_tick   = mana_per_tick
        self.interval        = interval
        self.ticks_total     = ticks_total
        self.ticks_remaining = ticks_total
        self.tick_timer      = interval


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
                 race: str = "Humanoide", entity_class: str = "",
                 faction: str = "monstros_hostis"):
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
        # Facção dos mobs desta zona (content/faction_data.py) — default
        # "monstros_hostis" preserva o comportamento ATUAL de todo mob
        # (100% hostil por proximidade hoje): zona não migrada continua
        # se comportando exatamente como antes, em vez de virar neutra
        # "por acidente" e mudar o balanceamento do jogo sem intenção
        # (ver ARQUITETURA_ONLINE.md, Fase 2 do Sistema de Facções).
        self.faction = faction
        self.active_entity_ids: set = set()
        self.respawn_timers: list = []  # um float por morte; cada um corre independentemente


class SpawnZoneOwner:
    """Marca um inimigo como pertencente a uma SpawnZone específica."""
    def __init__(self, zone_entity_id: int):
        self.zone_entity_id = zone_entity_id


class ActiveEffect:
    """Uma instância de efeito ativo sobre uma entidade."""
    __slots__ = (
        "effect_type", "duration", "magnitude",
        "tick_interval", "tick_timer",
        "on_expire_effect", "on_expire_duration", "on_expire_magnitude",
    )

    def __init__(
        self,
        effect_type: str,
        duration: float,
        magnitude: float = 0.0,
        tick_interval: float = 0.0,
        on_expire_effect: str = "",      # efeito aplicado quando este expira naturalmente
        on_expire_duration: float = 0.0,
        on_expire_magnitude: float = 0.0,
    ) -> None:
        self.effect_type:         str   = effect_type
        self.duration:            float = duration
        self.magnitude:           float = magnitude
        self.tick_interval:       float = tick_interval
        self.tick_timer:          float = tick_interval
        self.on_expire_effect:    str   = on_expire_effect
        self.on_expire_duration:  float = on_expire_duration
        self.on_expire_magnitude: float = on_expire_magnitude


class StatusEffects:
    """
    Contêiner de efeitos ativos (buffs e debuffs) de uma entidade.
    Gerenciado pelo StatusEffectSystem — use apply_effect() para adicionar efeitos.
    """

    def __init__(self) -> None:
        self.effects: dict = {}   # dict[str, ActiveEffect] — keyed by effect_type

    def has(self, effect_type: str) -> bool:
        """Retorna True se o efeito está ativo."""
        return effect_type in self.effects

    def get(self, effect_type: str):
        """Retorna o ActiveEffect ativo do tipo dado, ou None."""
        return self.effects.get(effect_type)

    def remove(self, effect_type: str) -> bool:
        """Remove um efeito pelo tipo. Retorna True se existia."""
        if effect_type in self.effects:
            del self.effects[effect_type]
            return True
        return False


@dataclass
class PendingDeath:
    """Marcador: esta entidade morreu e aguarda processamento por DeathHandlerSystem.

    ECS-puro: CombatSystem adiciona este componente ao invés de criar cadáver
    diretamente. DeathHandlerSystem processa e remove a entidade no mesmo frame.
    """
    killer_entity_id: int = -1


class NpcSounds:
    """Sons explícitos de uma entidade de combate não-jogador (mob OU NPC
    de serviço — renomeado de MobSounds em 21/07/2026, decisão do usuário:
    "todo mob é um NPC, mas nem todo NPC é um mob"). Cada campo é a chave
    base do arquivo OGG (sem extensão). Variações _2/_3/_4 são tentadas
    automaticamente. Campo vazio → sem som para aquele evento.

    Fonte única de dados: content/mob_definitions.py::MOB_TABLE["sounds"].
    """
    def __init__(self,
                 aggro: str = "", death: str = "",
                 attack_melee: str = "", attack_ranged: str = "",
                 attack_magic: str = "", crit: str = "",
                 emote_attack: str = "", emote_get_crit: str = "",
                 attack_impact: str = ""):
        self.aggro          = aggro
        self.death          = death
        self.attack_melee   = attack_melee
        self.attack_ranged  = attack_ranged
        self.attack_magic   = attack_magic
        self.crit           = crit
        self.emote_attack   = emote_attack
        self.emote_get_crit = emote_get_crit
        # Som do PROJÉTIL/golpe deste atacante ACERTANDO o alvo (ex:
        # "arrow_impact" do arqueiro NPC — mesmo som do arqueiro jogador).
        # Novo em 21/07/2026: nenhum mob tinha som de impacto próprio; o
        # combate com alvo mob tocava "hit_normal" fixo (ver
        # client/remote_entity_handlers.py::_apply_combat_result).
        self.attack_impact  = attack_impact


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


# ---------------------------------------------------------------------------
# QuestLog — rastreia quests ativas e completadas do jogador
# ---------------------------------------------------------------------------

class QuestLog:
    """Componente de quests do jogador.

    active:    dict[quest_id → list[int]] — progresso por objetivo (índice = objetivo)
    completed: set[quest_id]              — quests já entregues (permanente)
    """
    __slots__ = ("active", "completed")

    def __init__(self) -> None:
        self.active:    dict = {}   # quest_id → [prog_obj0, prog_obj1, ...]
        self.completed: set  = set()


class CharStatsTracker:
    """Estatísticas acumuladas do personagem pro modal de estatísticas
    (Fase E, 23/07/2026) — server-autoritativo, mesma regra de SkillLevels/
    QuestLog (só o servidor incrementa e persiste; cliente só exibe).
    arena_wins/arena_losses são por modo (chave = "1v1"/"2v2"/"3v3", ver
    Fase H) — usar `.setdefault(mode_id, 0)` ao incrementar, nunca assumir
    que a chave já existe (contas antigas não tinham 3v3 no schema)."""
    def __init__(self) -> None:
        self.pve_damage:     int = 0
        self.pvp_damage:     int = 0
        self.mobs_killed:    int = 0
        self.players_killed: int = 0
        self.duel_wins:      int = 0
        self.duel_losses:    int = 0
        self.arena_wins:     dict = {"1v1": 0, "2v2": 0, "3v3": 0}
        self.arena_losses:   dict = {"1v1": 0, "2v2": 0, "3v3": 0}


# ---------------------------------------------------------------------------
# Componentes de magia (classe Mago)
# ---------------------------------------------------------------------------

@dataclass
class SpellCast:
    """Lançamento de magia em andamento — alimenta a barra de cast."""
    spell_id:           str   = ""
    cast_time:          float = 0.0   # duração total do cast
    elapsed:            float = 0.0   # tempo acumulado
    target_id:          int   = -1    # alvo (entidade) ao ser completado
    mana_cost:          int   = 0     # custo deduzido SOMENTE ao completar — nunca no início
    concentration_cost: int   = 0     # custo de concentração — também só deduzido ao completar
    interruptible:      bool  = True  # False = movimento não cancela o cast
    on_cancel:          str   = ""    # nome do handler chamado se o cast for interrompido
    visual_only:        bool  = False # True = online, barra visual apenas — servidor dispara o efeito


class Channeling:
    """Canalização de magia em andamento (ex: Calamidade Flamejante)."""
    def __init__(self, spell_id: str, duration: float, tick_interval: float,
                 mana_per_tick: int, target_x: float, target_y: float,
                 radius_tiles: float, slow_pct: float = 0.0,
                 dmg_weapon_pct: float = 0.15, dmg_sp_coeff: float = 1.0):
        self.spell_id       = spell_id
        self.duration       = duration        # duração total
        self.elapsed        = 0.0            # tempo acumulado
        self.tick_interval  = tick_interval  # intervalo entre ticks de dano
        self.last_tick      = 0.0            # acumulador de tick
        self.mana_per_tick  = mana_per_tick  # mana consumida por tick
        self.target_x       = target_x       # posição world do centro da área
        self.target_y       = target_y
        self.radius_tiles   = radius_tiles
        self.slow_pct       = slow_pct       # % de lerdeza aplicada nos alvos
        self.dmg_weapon_pct = dmg_weapon_pct
        self.dmg_sp_coeff   = dmg_sp_coeff


class PirofagiaAiming:
    """Mira da Pirofagia ativa — cone segue o mouse enquanto a tecla está pressionada.
    Ao soltar a tecla, PirofagiaSystem dispara o cone e remove este component."""
    def __init__(self):
        self.elapsed: float = 0.0   # tempo desde ativação (evita disparo no mesmo frame)


@dataclass
class FireShieldEffect:
    """Escudo de Fogo ativo — retaliation de fogo em quem atacar o jogador."""
    duration: float = 15.0
    elapsed:  float = 0.0


@dataclass
class IceBlockEffect:
    """Estado do Bloco de Gelo — imunidade + cura por segundo."""
    duration:      float = 5.0
    elapsed:       float = 0.0
    heal_interval: float = 1.0
    last_heal:     float = 0.0


@dataclass
class PlayerProjectile:
    """Projétil lançado pelo jogador (magia ou flecha)."""
    spell_id:       str   = ""
    attacker_id:    int   = -1
    target_id:      int   = -1
    speed:          float = 350.0
    dmg_weapon_pct: float = 0.10    # % do dano médio da arma
    dmg_sp_coeff:   float = 1.0     # multiplicador de spell_power
    color:          tuple = (160, 80, 255)  # roxo arcano
    damage_type:    str   = "magical"  # "magical" | "physical"
    arrow_dmg_min:  int   = 0  # bônus mínimo da flecha (0 = sem bônus)
    arrow_dmg_max:  int   = 0  # bônus máximo da flecha
    # Flecha errando: desvia do alvo e voa além
    is_miss:        bool  = False   # True = erro confirmado, vai ao ponto desviado
    miss_end_x:     float = 0.0     # coordenada X do ponto final desviado
    miss_end_y:     float = 0.0     # coordenada Y do ponto final desviado
    pre_outcome:    str   = ""      # outcome pré-rolado ("" = rolar normalmente)
    # Configuração de skill shots
    damage_multiplier: float = 1.0   # multiplicador de dano (1.0 = normal; 1.5 = +50%)
    launch_delay:      float = 0.0    # segundos antes de começar a mover (0 = imediato)
    ap_multiplier:    float = 1.0    # multiplicador extra de AP (1.0 = normal; 2.0 = +1x AP)
    guaranteed_hit:   bool  = False  # True = ignora miss/dodge/parry, só rola crit
    # Efeito aplicado ao acertar o alvo (dados opcionais — "" = nenhum efeito)
    on_hit_effect:    str   = ""     # ID do efeito (ex: "slow", "burn", "stun")
    on_hit_duration:  float = 0.0    # duração do efeito em segundos
    on_hit_magnitude: float = 0.0    # magnitude (ex: 0.3 = 30% slow)
    deferred_result:  dict  = None   # damage/outcome do servidor — exibido ao colidir (online)
    target_last_x:    float = 0.0    # última pos X conhecida do alvo (voa até aqui se despawnar)
    target_last_y:    float = 0.0    # última pos Y conhecida do alvo
    target_server_id: int   = -1     # server eid do alvo — enviado em PROJECTILE_HIT_CS


@dataclass
class AoeTargeting:
    """Modo de mira AOE: próximo clique esquerdo posiciona a magia."""
    spell_id:          str   = ""
    radius_tiles:      float = 2.0
    cast_range_tiles:  float = 0.0    # 0 = ilimitado
    pending_world_x:   float = 0.0    # alvo armazenado enquanto fora do alcance
    pending_world_y:   float = 0.0
    waiting_for_range: bool  = False  # True = player caminhando até o alcance
    cancel_pending:    bool  = False  # True = cancelado neste frame, removido no próximo


@dataclass
class TrainingDummy:
    """Tag: boneco de treino. HP resetado ao atingir 0 em vez de morrer."""
    pass


@dataclass
class MapLocation:
    """Qual mapa esta entidade pertence. Adicionado a todos os não-players."""
    map_file: str = ""


# ── Online: entidade remota ──────────────────────────────────────────────────

@dataclass
class RemoteEntityMeta:
    """Metadados de uma entidade controlada pelo servidor (mob ou player remoto).

    Adicionado a toda entidade local criada como espelho de uma entidade server-side.
    Centraliza o estado que antes ficava espalhado em dicts avulsos em game.py:
      _mob_hp             → hp / hp_max
      _mob_last_pos       → last_x / last_y
      _remote_mobs_reverse → server_eid (lookup local→server)
    O dict _remote_mobs (server→local) é mantido como cache O(1) em game.py.
    """
    server_eid: int
    hp:         int   = 0
    hp_max:     int   = 100
    last_x:     float = 0.0
    last_y:     float = 0.0