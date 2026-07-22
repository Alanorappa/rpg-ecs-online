# sound_manager.py
"""Sistema de áudio do jogo.

Uso:
    from ui.sound_manager import SOUNDS

    SOUNDS.play("skill_interceptar")              # SFX pelo nome
    SOUNDS.play_random(["hit_1","hit_2","hit_3"]) # SFX aleatório da lista
    SOUNDS.play_mob_sounds(comp, "aggro")         # Som de mob pelo componente NpcSounds
    SOUNDS.play_mob_sounds_at(comp, "death", sx, sy, lx, ly)  # Posicional (com atenuação)
    SOUNDS.play_ambient("map_surface")            # Loop de ambiente
    SOUNDS.set_context("cave")                    # Ativa eco dinâmico de caverna
    SOUNDS.update(dt)                             # Chamado a cada frame

Sons de mob — única fonte de verdade: mob_definitions.py → componente NpcSounds
    Cada mob define suas chaves de som em mob_definitions.py["sounds"].
    Acesso sempre via componente: SOUNDS.play_mob_sounds(mob_entity.NpcSounds, evento)
    Para adicionar sons a um mob:
        1. Crie o .ogg em assets/sounds/sfx/   (ex: mob_orc_aggro.ogg)
        2. Adicione a chave em mob_definitions.py["sounds"]["aggro"] = "mob_orc_aggro"
    Variações automáticas: crie mob_orc_aggro_2.ogg, _3.ogg, _4.ogg — são tentadas automaticamente.
    Sons compartilhados entre mobs (ex: "mob_bite_melee") são perfeitamente válidos.

Sons posicionais (online/multiplayer):
    play_mob_sounds_at, play_skill_at, play_random_at — versões com distância.
    Volume: 100% em dist=0, 5% em AOI_RADIUS tiles (borda do AOI), 0% (não toca)
    além do AOI — fora da área de interesse o cliente não deveria nem saber
    que a fonte existe.

Sons com variação aleatória:
    Crie múltiplos arquivos com sufixo _1, _2, _3 e passe a lista:
    SOUNDS.play_random(["hit_normal_1", "hit_normal_2", "hit_normal_3"])
"""
from __future__ import annotations

import os
import random
import pygame
from paths import resource_path
from shared.constants import AOI_RADIUS, TILE_SIZE


# ---------------------------------------------------------------------------
# Registry — mapeamento nome → arquivo
# ---------------------------------------------------------------------------

def _sfx(name: str) -> str:
    return f"assets/sounds/sfx/{name}.ogg"

def _amb(name: str) -> str:
    return f"assets/sounds/ambient/{name}.ogg"


_REGISTRY: dict[str, str] = {
    # ── Skills do player ────────────────────────────────────────────────
    "skill_interceptar":        _sfx("skill_interceptar"),
    "skill_executar":           _sfx("skill_executar"),
    "skill_impacto":            _sfx("skill_impacto"),
    "skill_golpe_poderoso":     _sfx("skill_golpe_poderoso"),
    "skill_golpe_debilitante":  _sfx("skill_golpe_debilitante"),
    "skill_fatiador_de_corpos": _sfx("skill_fatiador"),
    "skill_punho_no_queixo":    _sfx("skill_punho_no_queixo"),
    "skill_vitoria_iminente":   _sfx("skill_vitoria_iminente"),

    # ── Arqueiro — ciclo completo de sons ────────────────────────────────
    "arrow_nock_1":             _sfx("arrow_nock_1"),     # pré-tensionamento (~1s antes)
    "arrow_nock_2":             _sfx("arrow_nock_2"),
    "arrow_draw_1":             _sfx("arrow_draw_1"),     # encaixe/tensão no disparo
    "arrow_draw_2":             _sfx("arrow_draw_2"),
    "arrow_release_1":          _sfx("arrow_release_1"),  # soltura da corda
    "arrow_release_2":          _sfx("arrow_release_2"),
    "arrow_impact_1":           _sfx("arrow_impact_1"),   # impacto no alvo
    "arrow_impact_2":           _sfx("arrow_impact_2"),

    # ── Emotes do player ─────────────────────────────────────────────────
    "player_emote_attack_1":    _sfx("player_emote_attack_1"),
    "player_emote_attack_2":    _sfx("player_emote_attack_2"),
    "player_emote_attack_3":    _sfx("player_emote_attack_3"),
    "player_emote_attack_4":    _sfx("player_emote_attack_4"),
    "player_emote_get_crit_1":  _sfx("player_emote_get_crit_1"),
    "player_emote_get_crit_2":  _sfx("player_emote_get_crit_2"),
    "player_emote_get_crit_3":  _sfx("player_emote_get_crit_3"),
    "player_emote_get_crit_4":  _sfx("player_emote_get_crit_4"),

    # ── Combate — auto-ataque (variações aleatórias) ─────────────────────
    # Crie hit_normal_1.ogg, hit_normal_2.ogg, hit_normal_3.ogg etc.
    "hit_normal_1":             _sfx("hit_normal_1"),
    "hit_normal_2":             _sfx("hit_normal_2"),
    "hit_normal_3":             _sfx("hit_normal_3"),
    "hit_crit_1":               _sfx("hit_crit_1"),
    "hit_crit_2":               _sfx("hit_crit_2"),
    # Fallbacks genéricos (usados se as variações não existirem)
    "hit_normal":               _sfx("hit_normal"),
    "hit_crit":                 _sfx("hit_crit"),

    # ── Mobs — sons genéricos de emote (fallback quando o mob não tem som definido) ──
    "mob_emote_attack_1":       _sfx("mob_emote_attack_1"),
    "mob_emote_get_crit_1":     _sfx("mob_emote_get_crit_1"),

    # ── Zumbi — sons de mordida (melee + emote de ataque) ────────────────
    "mob_bite_melee":           _sfx("mob_bite_melee"),
    "mob_bite_melee_1":         _sfx("mob_bite_melee_1"),
    "mob_bite_melee_2":         _sfx("mob_bite_melee_2"),
    "mob_bite_melee_3":         _sfx("mob_bite_melee_3"),

    # ── Passos do player (4 variações) ───────────────────────────────────
    "step_1":                   _sfx("step_1"),
    "step_2":                   _sfx("step_2"),
    "step_3":                   _sfx("step_3"),
    "step_4":                   _sfx("step_4"),
    "step_5":                   _sfx("step_5"),
    "step_6":                   _sfx("step_6"),
    "step_7":                   _sfx("step_7"),
    "step_8":                   _sfx("step_8"),
    "step_9":                   _sfx("step_9"),

    # ── Combate — eventos defensivos (aparo, esquiva, erro, bloqueio) ─────
    "combat_miss":             _sfx("combat_miss"),
    "combat_miss_1":           _sfx("combat_miss_1"),
    "combat_miss_2":           _sfx("combat_miss_2"),
    "combat_miss_3":           _sfx("combat_miss_3"),
    "combat_miss_4":           _sfx("combat_miss_4"),
    "combat_parry":            _sfx("combat_parry"),
    "combat_parry_1":          _sfx("combat_parry_1"),
    "combat_parry_2":          _sfx("combat_parry_2"),
    "combat_parry_3":          _sfx("combat_parry_3"),
    "combat_parry_4":          _sfx("combat_parry_4"),
    "combat_dodge":            _sfx("combat_dodge"),
    "combat_dodge_1":          _sfx("combat_dodge_1"),
    "combat_dodge_2":          _sfx("combat_dodge_2"),
    "combat_dodge_3":          _sfx("combat_dodge_3"),
    "combat_dodge_4":          _sfx("combat_dodge_4"),
    "combat_block":            _sfx("combat_block"),
    "combat_block_1":          _sfx("combat_block_1"),
    "combat_block_2":          _sfx("combat_block_2"),
    "combat_block_3":          _sfx("combat_block_3"),
    "combat_block_4":          _sfx("combat_block_4"),

    # ── Loot ─────────────────────────────────────────────────────────────
    "loot_gold":               _sfx("loot_gold"),
    "loot_item":               _sfx("loot_item"),

    # ── UI ───────────────────────────────────────────────────────────────
    "inventory_open":          _sfx("inventory_open"),
    "inventory_close":         _sfx("inventory_close"),
    "talent_open":             _sfx("talent_open"),
    "talent_close":            _sfx("talent_close"),
    "map_open":                _sfx("map_open"),
    "map_close":               _sfx("map_close"),
    "levelup":                 _sfx("levelup"),

    # ── Ambientes ────────────────────────────────────────────────────────
    "ambient_surface":         _amb("map_surface"),
    "ambient_cave":            _amb("map_cave"),
}

# Auto-gera variações _2, _3, _4 para skills e emotes do player.
for _base in list(_REGISTRY.keys()):
    if _base.startswith("skill_") or _base.startswith("player_emote_"):
        for _n in (2, 3, 4):
            _var = f"{_base}_{_n}"
            if _var not in _REGISTRY:
                _REGISTRY[_var] = _sfx(_var)

# Variações dos emotes genéricos de mob (2, 3, 4)
for _evt in ("mob_emote_attack", "mob_emote_get_crit"):
    for _n in (2, 3, 4):
        _var = f"{_evt}_{_n}"
        if _var not in _REGISTRY:
            _REGISTRY[_var] = _sfx(_var)

# ---------------------------------------------------------------------------
# Canais dedicados
# ---------------------------------------------------------------------------
_CH_AMBIENT_A = 0               # canal ambient SFX ativo
_CH_AMBIENT_B = 1               # canal ambient SFX incoming (crossfade)
_CH_MUSIC     = 2               # canal de música sequencial (playlist)
_CH_SKILLS    = (3, 4, 5)
_CH_UI        = (6, 7)
_CH_EMOTES    = (8, 9)
_CH_MOBS      = tuple(range(10, 22))   # 12 canais
_CH_GENERAL   = tuple(range(22, 28))

def _sfx_cave(name: str) -> str:
    return f"assets/sounds/sfx/cave/{name}.ogg"


class SoundManager:
    """Gerencia todos os sons do jogo com suporte a contexto de caverna."""

    # ── Áudio posicional ────────────────────────────────────────────────────
    # Raio audível = borda do AOI (mesma área que o servidor considera "em
    # vista" para esta sessão) — fora dele, a fonte nem deveria ser conhecida
    # pelo cliente, então o som não toca.
    MAX_WORLD_SOUND_DIST: float = AOI_RADIUS * TILE_SIZE   # 480px (15 tiles)
    # Volume mínimo na borda do raio (5%)
    MIN_WORLD_SOUND_VOL:  float = 0.05

    def pan_at(self, sx: float, lx: float) -> float:
        """Posição estéreo da fonte em relação ao ouvinte: -1.0 (totalmente à
        esquerda) .. 0.0 (centro) .. +1.0 (totalmente à direita).

        Baseado apenas no eixo X do mundo — a câmera deste jogo não rotaciona,
        então "direita no mundo" == "direita na tela" == canal direito do fone.
        """
        dx = sx - lx
        return max(-1.0, min(1.0, dx / self.MAX_WORLD_SOUND_DIST))

    @staticmethod
    def _pan_gains(pan: float) -> "tuple[float, float]":
        """Converte pan [-1..1] em ganhos (esquerda, direita) para Channel.set_volume.

        Linear, sem normalização: no centro (pan=0) ambos os canais ficam em
        1.0 (idêntico ao comportamento mono anterior — sem perda de volume
        para sons centrados). Ao se mover para um lado, o canal oposto vai
        fadeando até 0.
        """
        pan = max(-1.0, min(1.0, pan))
        left  = min(1.0, 1.0 - pan)
        right = min(1.0, 1.0 + pan)
        return left, right

    def volume_at(self, sx: float, sy: float,
                  lx: float, ly: float,
                  base: float = 1.0) -> float:
        """Calcula volume baseado na distância fonte → ouvinte.

        Curva linear: 100% em dist=0, 5% em dist=MAX (borda do AOI),
        0% (silencioso) além do AOI.
        """
        import math
        dist = math.hypot(sx - lx, sy - ly)
        if dist >= self.MAX_WORLD_SOUND_DIST:
            return 0.0
        t = 1.0 - dist / self.MAX_WORLD_SOUND_DIST   # 1.0 → 0.0
        return base * (self.MIN_WORLD_SOUND_VOL + (1.0 - self.MIN_WORLD_SOUND_VOL) * t)

    def play_at(self, name: str,
                sx: float, sy: float, lx: float, ly: float,
                base: float = 1.0,
                channel_group: "tuple[int,...] | None" = None) -> None:
        """Toca som com volume/pan proporcional à posição (nada se fora do AOI)."""
        vol = self.volume_at(sx, sy, lx, ly, base)
        if vol <= 0.0:
            return
        self.play(name, vol, channel_group, pan=self.pan_at(sx, lx))

    def play_random_at(self, names: "list[str]",
                       sx: float, sy: float, lx: float, ly: float,
                       base: float = 1.0,
                       channel_group: "tuple[int,...] | None" = None) -> None:
        """Toca som aleatório com volume/pan proporcional à posição (nada se fora do AOI)."""
        vol = self.volume_at(sx, sy, lx, ly, base)
        if vol <= 0.0:
            return
        self.play_random(names, vol, channel_group, pan=self.pan_at(sx, lx))

    def play_skill_at(self, name: str,
                      sx: float, sy: float, lx: float, ly: float,
                      base: float = 1.0) -> None:
        """Toca skill com volume/pan proporcional à posição (nada se fora do AOI)."""
        vol = self.volume_at(sx, sy, lx, ly, base)
        if vol <= 0.0:
            return
        self.play_skill(name, vol, pan=self.pan_at(sx, lx))

    def play_mob_sounds_at(self, mob_sounds_comp, event: str,
                           sx: float, sy: float, lx: float, ly: float,
                           base: float = 1.0, dedup_key: str = "") -> None:
        """Toca evento de mob (por componente NpcSounds) com atenuação de distância.

        Usa o campo correto do componente — idêntico ao offline play_mob_sounds()
        mas com volume/pan calculados pela posição. Nada toca se fora do AOI.
        """
        vol = self.volume_at(sx, sy, lx, ly, base)
        if vol <= 0.0:
            return
        self.play_mob_sounds(mob_sounds_comp, event, vol, dedup_key, pan=self.pan_at(sx, lx))

    def play_emote_at(self, is_player: bool, mob_sounds_comp,
                      sx: float, sy: float, lx: float, ly: float,
                      is_crit: bool = False, base: float = 0.8) -> None:
        """Toca emote de ataque ou crit com volume/pan proporcional à posição (nada se fora do AOI)."""
        vol = self.volume_at(sx, sy, lx, ly, base)
        if vol <= 0.0:
            return
        pan = self.pan_at(sx, lx)
        if is_crit:
            self.play_emote_get_crit(is_player, mob_sounds_comp, vol, pan=pan)
        else:
            self.play_emote_attack(is_player, mob_sounds_comp, vol, pan=pan)

    def __init__(self) -> None:
        self._ready      = False
        self._cache:      dict[str, pygame.mixer.Sound | None] = {}
        self._cache_cave: dict[str, pygame.mixer.Sound | None] = {}
        self._context:   str = "surface"
        self._steal_index: dict = {}
        self._last_step:   str  = ""
        # Ambient crossfade (canal _CH_AMBIENT_A / _CH_AMBIENT_B)
        self._amb_idx:    int   = 0
        self._amb_name:   str   = ""
        self._amb_target: str   = ""
        self._amb_base:   float = 0.4
        self._fade_t:     float = 0.0
        self._fade_dur:   float = 5.0
        # Playlist de música (canal _CH_MUSIC)
        self._music_cache:    dict[str, "pygame.mixer.Sound | None"] = {}
        self._playlist:       list[str] = []   # filenames carregados
        self._playlist_idx:   int       = 0
        self._playlist_base:  float     = 0.5  # volume base da playlist
        # Stingers de ambient (SFX aleatórios, intervalo 15-60 s)
        self._stinger_list:  list[str] = []
        self._stinger_cache: dict[str, "pygame.mixer.Sound | None"] = {}
        self._stinger_timer: float = 0.0
        # Configurações de volume globais
        self.music_volume:   float = 0.4
        self.sfx_volume:     float = 1.0
        self.music_enabled:  bool  = True
        self.sfx_enabled:    bool  = True
        # Deduplicação de sons por frame: chave = (nome_evento, mob_race)
        self._played_this_frame: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Inicialização
    # ------------------------------------------------------------------

    def init(self) -> None:
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            pygame.mixer.set_num_channels(26)
        except Exception as exc:
            print(f"[SoundManager] Mixer indisponível: {exc}")
            return

        for name, rel_path in _REGISTRY.items():
            full = resource_path(rel_path)
            if os.path.isfile(full):
                try:
                    self._cache[name] = pygame.mixer.Sound(full)
                except Exception as exc:
                    print(f"[SoundManager] Erro ao carregar '{name}': {exc}")
                    self._cache[name] = None
            else:
                self._cache[name] = None

        # Carrega versões de caverna — mesmo nome, pasta sfx/cave/
        cave_loaded = 0
        for name in list(_REGISTRY.keys()):
            cave_path = resource_path(_sfx_cave(name))
            if os.path.isfile(cave_path):
                try:
                    self._cache_cave[name] = pygame.mixer.Sound(cave_path)
                    cave_loaded += 1
                except Exception as exc:
                    print(f"[SoundManager] Erro ao carregar cave '{name}': {exc}")
                    self._cache_cave[name] = None
            else:
                self._cache_cave[name] = None

        pygame.mixer.set_num_channels(28)   # garante canais suficientes

        # Pré-carrega todos os .ogg da pasta sfx que ainda não estão no cache
        # (sons de mob e de spell são lazy por convenção de nome, não por entrada em _REGISTRY)
        sfx_dir = resource_path("assets/sounds/sfx")
        if os.path.isdir(sfx_dir):
            for fname in os.listdir(sfx_dir):
                if not fname.endswith(".ogg"):
                    continue
                key = fname[:-4]
                if key not in self._cache:
                    full = os.path.join(sfx_dir, fname)
                    try:
                        self._cache[key] = pygame.mixer.Sound(full)
                    except Exception as exc:
                        print(f"[SoundManager] Erro ao pré-carregar '{key}': {exc}")
                        self._cache[key] = None

        self._ready = True
        loaded = sum(1 for v in self._cache.values() if v is not None)
        print(f"[SoundManager] Iniciado. {loaded}/{len(_REGISTRY)} sons | {cave_loaded} versões cave carregadas.")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def set_context(self, context: str) -> None:
        """'surface' ou 'cave'."""
        self._context = context

    def play(self, name: str, volume: float = 1.0,
             channel_group: tuple[int, ...] | None = None,
             pan: float = 0.0) -> None:
        """Toca um SFX pelo nome. Em contexto 'cave' usa a versão processada da pasta cave/.

        pan: -1.0 (esquerda) .. 0.0 (centro) .. +1.0 (direita) — ver `pan_at`.
        """
        if not self._ready:
            return
        if self._context == "cave":
            sound = self._cache_cave.get(name) or self._cache.get(name)
        else:
            sound = self._cache.get(name)
        if sound is None:
            return
        group = channel_group if channel_group is not None else _CH_GENERAL
        ch = self._find_free_channel(group)
        if ch is None:
            return
        eff = volume * (self.sfx_volume if self.sfx_enabled else 0.0)
        sound.set_volume(eff)
        ch.play(sound)
        left, right = self._pan_gains(pan)
        ch.set_volume(left, right)

    def play_random(self, names: list[str], volume: float = 1.0,
                    channel_group: tuple[int, ...] | None = None,
                    pan: float = 0.0) -> None:
        """Toca um som escolhido aleatoriamente da lista.

        Apenas sons disponíveis (arquivo carregado) são candidatos.
        Se nenhum estiver disponível, nada é tocado.
        """
        if not self._ready:
            return
        available = [n for n in names if self._cache.get(n) is not None]
        if not available:
            return
        self.play(random.choice(available), volume, channel_group, pan=pan)

    # ------------------------------------------------------------------
    # DEPRECATED — não usar em código novo
    # Use play_mob_sounds(comp, event) que lê do componente NpcSounds.
    # Mantido apenas para compatibilidade com código legado eventual.
    # ------------------------------------------------------------------
    def play_mob_event(self, mob_name: str, event: str, volume: float = 1.0,
                       dedup_race: str = "") -> None:
        """DEPRECATED. Usa convenção de nome para achar o som (quebrável).

        Prefira: SOUNDS.play_mob_sounds(mob_sounds_comp, event, volume)
        que lê do componente NpcSounds diretamente e é sempre correto.
        """
        if dedup_race:
            key = (event, dedup_race.lower())
            if key in self._played_this_frame:
                return
            self._played_this_frame.add(key)
        base     = f"mob_{mob_name.lower()}_{event}"
        variants = [base, f"{base}_2", f"{base}_3", f"{base}_4"]
        available = [v for v in variants if self._cache.get(v) is not None]
        if available:
            self.play_mob(random.choice(available), volume)
            return
        generic = f"mob_{event}"
        if self._cache.get(generic) is not None:
            self.play_mob(generic, volume)

    def _lazy_load(self, key: str) -> "pygame.mixer.Sound | None":
        """Carrega um som pelo nome da chave se ainda não estiver em cache."""
        if key not in self._cache:
            path = resource_path(_sfx(key))
            try:
                self._cache[key] = pygame.mixer.Sound(path) if os.path.isfile(path) else None
            except Exception:
                self._cache[key] = None
        return self._cache[key]

    def play_mob_sounds(self, mob_sounds_comp, event: str,
                        volume: float = 1.0, dedup_key: str = "",
                        pan: float = 0.0) -> None:
        """Toca o som de um mob pelo componente NpcSounds e o nome do evento.

        Args:
            mob_sounds_comp: componente NpcSounds da entidade (pode ser None).
            event:           "aggro", "death", "crit",
                             "attack_melee", "attack_ranged" ou "attack_magic".
            dedup_key:       Se não vazio, apenas 1 som desta (event, dedup_key)
                             por frame — evita sobrecarga quando vários mobs agem juntos.
        """
        if not self._ready:
            return
        if dedup_key:
            k = (event, dedup_key)
            if k in self._played_this_frame:
                return
            self._played_this_frame.add(k)

        base = getattr(mob_sounds_comp, event, "") if mob_sounds_comp else ""
        if not base:
            return

        variants = [base] + [f"{base}_{n}" for n in (1, 2, 3, 4)]
        available = [v for v in variants if self._lazy_load(v) is not None]
        if available:
            self.play_mob(random.choice(available), volume, pan=pan)

    def play_emote_attack(self, is_player: bool, mob_sounds_comp=None,
                          volume: float = 1.0, pan: float = 0.0) -> None:
        """Emote de ataque — 50% de chance. Toca antes do som do golpe."""
        if random.random() > 0.50:
            return
        if is_player:
            variants = [f"player_emote_attack_{n}" for n in (1, 2, 3, 4)]
            self.play_random(variants, volume, _CH_EMOTES, pan=pan)
        else:
            base = getattr(mob_sounds_comp, "emote_attack", "") if mob_sounds_comp else ""
            if base:
                variants = [base] + [f"{base}_{n}" for n in (1, 2, 3, 4)]
                available = [v for v in variants if self._lazy_load(v) is not None]
                if available:
                    self.play_mob(random.choice(available), volume, pan=pan)
                    return
            # fallback genérico
            self.play_random([f"mob_emote_attack_{n}" for n in (1, 2, 3, 4)], volume, _CH_EMOTES, pan=pan)

    def play_emote_get_crit(self, is_player: bool, mob_sounds_comp=None,
                            volume: float = 1.0, pan: float = 0.0) -> None:
        """Emote ao receber crítico — 100% de chance."""
        if is_player:
            variants = [f"player_emote_get_crit_{n}" for n in (1, 2, 3, 4)]
            self.play_random(variants, volume, _CH_EMOTES, pan=pan)
        else:
            base = getattr(mob_sounds_comp, "emote_get_crit", "") if mob_sounds_comp else ""
            if base:
                variants = [base] + [f"{base}_{n}" for n in (1, 2, 3, 4)]
                available = [v for v in variants if self._lazy_load(v) is not None]
                if available:
                    self.play_mob(random.choice(available), volume, pan=pan)
                    return
            self.play_random([f"mob_emote_get_crit_{n}" for n in (1, 2, 3, 4)], volume, _CH_EMOTES, pan=pan)

    def play_spell(self, spell_id: str, phase: str, volume: float = 1.0) -> None:
        """Toca o som de uma spell em uma fase específica.

        Args:
            spell_id: identificador da skill (ex: "bola_de_fogo")
            phase:    "cast" | "launch" | "impact"

        Arquivo esperado: assets/sounds/sfx/skill_{spell_id}_{phase}.ogg
        Suporta variações _1, _2, _3, _4 automaticamente.
        Se o arquivo não existir, nada é tocado (sem erro).
        """
        self.play_skill(f"skill_{spell_id}_{phase}", volume)

    def play_spell_at(self, spell_id: str, phase: str,
                      sx: float, sy: float, lx: float, ly: float,
                      base: float = 1.0) -> None:
        """Toca som de spell com volume/pan proporcional à distância fonte→ouvinte."""
        self.play_skill_at(f"skill_{spell_id}_{phase}", sx, sy, lx, ly, base)

    def play_skill(self, name: str, volume: float = 1.0, pan: float = 0.0) -> None:
        """Toca a skill — carrega variantes dinamicamente se necessário.

        Tenta o arquivo base (sem sufixo) e as variações _1, _2, _3, _4.
        Qualquer arquivo em assets/sounds/sfx/ com esses nomes funciona sem
        precisar ser registrado manualmente no _REGISTRY.
        """
        variants = [name] + [f"{name}_{n}" for n in (1, 2, 3, 4)]
        for v in variants:
            if v not in self._cache:
                path = resource_path(_sfx(v))
                try:
                    self._cache[v] = pygame.mixer.Sound(path) if os.path.isfile(path) else None
                except Exception:
                    self._cache[v] = None
        self.play_random(variants, volume, _CH_SKILLS, pan=pan)

    def play_footstep(self, volume: float = 0.6) -> None:
        """Toca um passo aleatório. 35% de chance de silêncio."""
        if random.random() < 0.20:
            return
        all_steps = ["step_1", "step_2", "step_3", "step_4",
                     "step_5", "step_6", "step_7", "step_8", "step_9"]
        candidates = [s for s in all_steps
                      if s != self._last_step and self._cache.get(s) is not None]
        if not candidates:
            return
        chosen = random.choice(candidates)
        self._last_step = chosen
        self.play(chosen, volume, _CH_GENERAL)

    def fadeout_skills(self, ms: int = 300) -> None:
        """Fade out em todos os canais de skill (cast/canalização interrompidos)."""
        if not self._ready:
            return
        for ch_id in _CH_SKILLS:
            pygame.mixer.Channel(ch_id).fadeout(ms)

    def play_ui(self, name: str, volume: float = 0.8) -> None:
        self.play(name, volume, _CH_UI)

    def play_mob(self, name: str, volume: float = 1.0, pan: float = 0.0) -> None:
        self.play(name, volume, _CH_MOBS, pan=pan)

    def play_ambient(self, name: str, volume: float = 0.4) -> None:
        """Inicia um ambient imediatamente (sem crossfade). Usado na carga do mapa."""
        if not self._ready:
            return
        self._fade_t    = 0.0
        self._amb_name  = name
        self._amb_base  = volume
        ch_a = pygame.mixer.Channel(_CH_AMBIENT_A)
        ch_b = pygame.mixer.Channel(_CH_AMBIENT_B)
        ch_b.stop()
        sound = self._cache.get(name)
        if sound:
            eff = volume * (self.music_volume if self.music_enabled else 0.0)
            ch_a.set_volume(eff)
            ch_a.play(sound, loops=-1, fade_ms=1500)
        else:
            ch_a.stop()
        self._amb_idx = 0

    def crossfade_ambient(self, name: str, volume: float = 0.4,
                          duration: float = 5.0) -> None:
        """Faz crossfade suave para um novo ambient ao longo de `duration` segundos."""
        if not self._ready or name == self._amb_name:
            return
        self._amb_target = name
        self._amb_base   = volume
        self._fade_dur   = duration
        self._fade_t     = duration
        # Inicia incoming no canal inativo com volume 0
        ch_in = pygame.mixer.Channel(
            _CH_AMBIENT_B if self._amb_idx == 0 else _CH_AMBIENT_A)
        sound = self._cache.get(name)
        if sound:
            ch_in.set_volume(0.0)
            ch_in.play(sound, loops=-1)
        else:
            ch_in.stop()

    def apply_music_settings(self) -> None:
        """Aplica music_volume/music_enabled nos canais ativos sem reiniciar os sons."""
        eff = self.music_volume if self.music_enabled else 0.0
        if self._fade_t > 0.0 and self._fade_dur > 0.0:
            progress = 1.0 - self._fade_t / self._fade_dur
            ch_a = pygame.mixer.Channel(
                _CH_AMBIENT_A if self._amb_idx == 0 else _CH_AMBIENT_B)
            ch_b = pygame.mixer.Channel(
                _CH_AMBIENT_B if self._amb_idx == 0 else _CH_AMBIENT_A)
            ch_a.set_volume(self._amb_base * eff * (1.0 - progress))
            ch_b.set_volume(self._amb_base * eff * progress)
        else:
            ch = pygame.mixer.Channel(
                _CH_AMBIENT_A if self._amb_idx == 0 else _CH_AMBIENT_B)
            ch.set_volume(self._amb_base * eff)
        # Canal de música
        pygame.mixer.Channel(_CH_MUSIC).set_volume(
            self._playlist_base * eff)

    def stop_ambient(self) -> None:
        pygame.mixer.Channel(_CH_AMBIENT_A).fadeout(800)
        pygame.mixer.Channel(_CH_AMBIENT_B).fadeout(800)
        self._fade_t   = 0.0
        self._amb_name = ""

    # ------------------------------------------------------------------
    # Playlist de música sequencial
    # ------------------------------------------------------------------

    def play_music_playlist(self, filenames: list[str], base_vol: float = 0.5) -> None:
        """
        Inicia uma playlist de faixas de música.
        `filenames` são nomes de arquivo (ex.: 'track.ogg') dentro de assets/sounds/music/.
        As faixas tocam em sequência; ao terminar a última, volta para a primeira.
        Aceita lista com um único item.
        """
        if not self._ready or not filenames:
            return
        self._playlist_base = base_vol
        # Carrega faixas não ainda em cache (no-op se preload_music já foi chamado)
        self.preload_music(filenames)
        self._playlist     = filenames
        self._playlist_idx = 0
        self._play_playlist_track(0)

    def stop_music(self) -> None:
        """Para a playlist e silencia o canal de música."""
        pygame.mixer.Channel(_CH_MUSIC).fadeout(int(5.0 * 1000))
        self._playlist     = []
        self._playlist_idx = 0

    # ------------------------------------------------------------------
    # Stingers de ambient (SFX aleatórios em intervalos 15-60 s)
    # ------------------------------------------------------------------

    def preload_stingers(self, filenames: list[str]) -> None:
        """Pré-carrega stingers de sfx/ sem ativá-los. Chamado na carga do mapa."""
        if not self._ready:
            return
        for fname in filenames:
            if fname not in self._stinger_cache:
                path = resource_path(f"assets/sounds/sfx/{fname}")
                if os.path.isfile(path):
                    try:
                        self._stinger_cache[fname] = pygame.mixer.Sound(path)
                    except Exception as exc:
                        print(f"[SoundManager] Erro ao pré-carregar stinger '{fname}': {exc}")
                        self._stinger_cache[fname] = None
                else:
                    self._stinger_cache[fname] = None

    def preload_music(self, filenames: list[str]) -> None:
        """Pré-carrega faixas de music/ sem iniciá-las. Chamado na carga do mapa."""
        if not self._ready:
            return
        for fname in filenames:
            if fname not in self._music_cache:
                path = resource_path(f"assets/sounds/music/{fname}")
                if os.path.isfile(path):
                    try:
                        self._music_cache[fname] = pygame.mixer.Sound(path)
                    except Exception as exc:
                        print(f"[SoundManager] Erro ao pré-carregar música '{fname}': {exc}")
                        self._music_cache[fname] = None
                else:
                    self._music_cache[fname] = None

    def start_ambient_stingers(self, filenames: list[str]) -> None:
        """
        Ativa a lista de stingers para a zona atual.
        `filenames` são nomes de arquivo (ex.: 'ghoul_ambient_song.ogg')
        dentro de assets/sounds/sfx/.
        O primeiro stinger toca após um intervalo aleatório de 15-60 s.
        """
        if not self._ready or not filenames:
            self._stinger_list  = []
            self._stinger_timer = 0.0
            return
        self._stinger_list  = filenames
        self._stinger_timer = random.uniform(15.0, 60.0)
        # Carrega arquivos não ainda em cache (caso preload não tenha sido chamado)
        self.preload_stingers(filenames)

    def stop_ambient_stingers(self) -> None:
        """Para o sistema de stingers da zona atual."""
        self._stinger_list  = []
        self._stinger_timer = 0.0

    def _play_playlist_track(self, idx: int) -> None:
        fname = self._playlist[idx]
        sound = self._music_cache.get(fname)
        ch    = pygame.mixer.Channel(_CH_MUSIC)
        if sound:
            eff = self._playlist_base * (self.music_volume if self.music_enabled else 0.0)
            ch.set_volume(eff)
            ch.play(sound)          # sem loop — update() detecta fim e avança
        else:
            ch.stop()

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def new_frame(self) -> None:
        """Limpa o set de deduplicação. Chamar uma vez no início de cada frame."""
        self._played_this_frame.clear()

    def update(self, dt: float) -> None:
        """Atualiza crossfade de ambient e avanço de playlist a cada frame."""
        if not self._ready:
            return

        # --- Crossfade de ambient SFX ---
        if self._fade_t > 0.0:
            self._fade_t = max(0.0, self._fade_t - dt)
            progress = 1.0 - self._fade_t / self._fade_dur
            eff = self._amb_base * (self.music_volume if self.music_enabled else 0.0)
            ch_cur = pygame.mixer.Channel(
                _CH_AMBIENT_A if self._amb_idx == 0 else _CH_AMBIENT_B)
            ch_new = pygame.mixer.Channel(
                _CH_AMBIENT_B if self._amb_idx == 0 else _CH_AMBIENT_A)
            ch_cur.set_volume(eff * (1.0 - progress))
            ch_new.set_volume(eff * progress)
            if self._fade_t <= 0.0:
                ch_cur.stop()
                self._amb_idx  = 1 - self._amb_idx
                self._amb_name = self._amb_target

        # --- Avanço da playlist de música ---
        if self._playlist and not pygame.mixer.Channel(_CH_MUSIC).get_busy():
            self._playlist_idx = (self._playlist_idx + 1) % len(self._playlist)
            self._play_playlist_track(self._playlist_idx)

        # --- Stingers de ambient ---
        if self._stinger_list and self._stinger_timer > 0.0:
            self._stinger_timer -= dt
            if self._stinger_timer <= 0.0:
                available = [f for f in self._stinger_list
                             if self._stinger_cache.get(f) is not None]
                if available:
                    fname = random.choice(available)
                    sound = self._stinger_cache[fname]
                    ch = self._find_free_channel(_CH_GENERAL)
                    if ch and sound:
                        eff = 0.8 * (self.sfx_volume if self.sfx_enabled else 0.0)
                        sound.set_volume(eff)
                        ch.play(sound)
                self._stinger_timer = random.uniform(15.0, 60.0)

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _find_free_channel(self, group: tuple[int, ...]) -> pygame.mixer.Channel | None:
        for ch_id in group:
            ch = pygame.mixer.Channel(ch_id)
            if not ch.get_busy():
                return ch
        # Todos ocupados: interrompe o canal de índice rotativo para não sempre cortar o mesmo
        if group:
            idx = self._steal_index.get(group, 0)
            self._steal_index[group] = (idx + 1) % len(group)
            return pygame.mixer.Channel(group[idx])
        return None


# Singleton global
SOUNDS = SoundManager()
