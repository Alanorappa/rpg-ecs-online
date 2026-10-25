"""
status_effects_data.py — Definições estáticas de efeitos de estado.

  EffectDef   : dados estáticos de cada tipo de efeito (label, cor, tick_interval).
  EFFECT_DEFS : registro global de todos os efeitos do jogo.

Apenas dados — nenhuma lógica de jogo vive aqui.
apply_effect() foi movida para systems.py (StatusEffectSystem.apply).
"""
from __future__ import annotations
from typing import NamedTuple


class EffectDef(NamedTuple):
    label:         str    # nome exibido em UI / floating text
    color:         tuple  # (R, G, B) para ícone e floating text
    is_buff:       bool   # True = buff, False = debuff
    tick_interval: float  # segundos entre ticks de dano/cura (0.0 = sem tick)
    # CC estrutural (21/07/2026): fonte única de "este efeito tira o controle
    # do jogador" — engine/utils.py (is_action_locked/is_movement_locked) e
    # server/world_server.py (anti-cheat de movimento bruto) leem DAQUI em
    # vez de manter listas de nomes hardcodadas duplicadas em cada gate; um
    # efeito de CC novo só precisa marcar as flags certas aqui, nunca mais
    # caçar call site por call site (pedido do usuário, ver
    # PROBLEMAS_ARQUITETURA.md — bug real: "stun" já existia como
    # StatusEffects de verdade mas nenhum gate de ação/movimento olhava pra
    # ele, só pra CombatState.is_stunned setado manualmente em 3 lugares
    # sem relação nenhuma com este efeito).
    blocks_move:   bool = False   # bloqueia movimento manual (WASD/clique/perseguição)
    blocks_act:    bool = False   # bloqueia skill/auto-attack
    # Tooltip da buff bar (01/09/2026, ui/buff_bar.py) — 1 frase fixa
    # descrevendo o efeito, mesmo catálogo central que já dá label/cor/
    # ícone. "" = sem tooltip de texto (debuff ainda fora de escopo,
    # ver PROBLEMAS_ARQUITETURA.md) — nunca quebra nada, é só o
    # fallback padrão do campo.
    description:   str = ""
    # Mecânicas genéricas lidas por dado (24/10/2026, Xamã) — efeito novo só
    # marca o campo aqui, nunca um `if effect_type == ...` num sistema:
    #   speed_mult        — multiplica a velocidade de movimento enquanto ativo
    #                       (core_systems._resync_derived_states; >1 acelera)
    #   damage_taken_mult — multiplica TODO dano que o portador recebe
    #                       (core_systems.apply_damage_core; DoT não passa lá)
    #   absorbs           — escudo: `magnitude` é o dano restante que absorve
    #                       (apply_damage_core consome; some ao zerar)
    #   blocks_stealth    — portador não pode ficar camuflado (Camuflagem
    #                       recusa e a aplicação do efeito quebra a ativa)
    #   heals             — cura por tick: cada tick cura `magnitude` via
    #                       core_systems.apply_heal (HoT de skill, ex: corredor
    #                       do Toque Ancestral). Nunca dano.
    speed_mult:        float = 1.0
    damage_taken_mult: float = 1.0
    absorbs:           bool  = False
    blocks_stealth:    bool  = False
    heals:             bool  = False


EFFECT_DEFS: dict[str, EffectDef] = {
    # ── Debuffs de controle ──────────────────────────────────────────────────
    "stun":   EffectDef("Atordoado",    (255, 220,  40), False, 0.0, blocks_move=True, blocks_act=True),
    "sleep":  EffectDef("Dormindo",     (160, 200, 255), False, 0.0, blocks_move=True, blocks_act=True),  # imóvel; quebra ao tomar dano
    "fear":   EffectDef("Medo",         (180,  60, 220), False, 0.0, blocks_move=True, blocks_act=True),
    "root":   EffectDef("Imobilizado",  (140, 100,  40), False, 0.0, blocks_move=True),  # pode agir, só não se mover
    "slow":   EffectDef("Lento",        (100, 160, 220), False, 0.0),
    "polymorph":   EffectDef("Polimorfizado",  (160,  80, 200), False, 1.0, blocks_move=True, blocks_act=True),  # tick: regen HP
    "disoriented": EffectDef("Desorientado",   (200, 100, 255), False, 0.0, blocks_move=True, blocks_act=True),  # wander sem regen
    # Taunt (Brado Provocativo, 23/07/2026, referência LoL — Rammus/Galio/
    # Shen): NÃO usa blocks_move/blocks_act (ver ui/systems.py::
    # PlayerInputSystem.update) — o alvo continua autoatacando o taunter via
    # combat_state.is_pursuing (mecanismo genérico de auto-attack), então
    # marcar blocks_act=True bloquearia esse auto-attack forçado também
    # (combat_processor.py usa is_action_locked como early-exit). Bloqueio
    # de movimento livre + skill é feito à mão (move_player rejeita MOVE,
    # skill_processor rejeita CAST_SKILL do caster taunted) — só o gate
    # genérico client-side (PlayerInputSystem) é especial-casado também.
    "taunted": EffectDef("Provocado", (255, 60, 30), False, 0.0),
    # ── Debuffs de dano periódico (DoT) ─────────────────────────────────────
    "poison": EffectDef("Veneno",       ( 80, 200,  40), False, 1.0),
    "bleed":  EffectDef("Sangramento",  (200,  30,  30), False, 1.0),
    "burn":   EffectDef("Queimadura",   (255, 120,   0), False, 3.0),
    # ── Buffs ────────────────────────────────────────────────────────────────
    # is_buff=False (03/10/2026, corrigido -- era True): o efeito É
    # mecanicamente misto (+5% dano causado / +10% dano recebido, ver
    # engine/world_systems.py::_resolve_damage_modifiers), mas o sinal
    # visual que importa é no ALVO provocado (Brado Provocativo) -- e
    # nameplate (ui/render_system.py::_filter_nameplate_effects) só
    # mostra is_buff=False; buff bar (ui/buff_bar.py) só mostra
    # is_buff=True e É EXCLUSIVA DO JOGADOR LOCAL. Com True, o ícone
    # nunca aparecia sobre mob/oponente de PvP (só "taunted" aparecia,
    # e só durante os 3s do CC) -- exatamente o bug relatado pelo
    # usuário ("ícone fica só 3s, deveria ficar os 10s do debuff").
    "enraged":         EffectDef("Enfurecido",      (255,  80,   0), False, 0.0),
    "elemental_lapse": EffectDef("Lapso Elemental", (255, 100, 200), False, 1.0),  # tick: auto-burn
    "exhaustion":      EffectDef("Exaustão",        (200, 140,  60), False, 0.0),  # rastreia stacks
    "haste":   EffectDef("Acelerado",   (100, 220, 255), True,  0.0),
    "regen":   EffectDef("Regeneração", ( 80, 255, 120), True,  1.0),
    # Imunidade a controle (07/08/2026, Fatiador de Corpos) — imunidade de
    # MECÂNICA (bloqueia qualquer efeito com blocks_move/blocks_act + slow),
    # separada de imunidade a dano (mesmo padrão AzerothCore:
    # SPELL_AURA_MECHANIC_IMMUNITY_MASK ≠ imunidade de dano). É o PRÓPRIO
    # efeito que concede a proteção — apply_effect() lê isto genericamente
    # (engine/core_systems.py), nenhuma skill precisa de flag bespoke;
    # qualquer skill futura que quiser o mesmo efeito só chama
    # apply_effect(world, eid, "cc_immune", duration=X).
    "cc_immune": EffectDef("Imune a Controle", (200, 200, 200), True, 0.0,
                          description="Imune a atordoamento, sono, medo, lentidão e outros efeitos de controle."),
    # ── Buffs temporários (31/08/2026, buff bar unificada) ─────────────────
    # Ponto único de verdade: todo buff temporário de verdade do jogo (proc
    # de item, self-buff de skill, buff de matar chefe de jungle) passa a
    # registrar um ActiveEffect aqui via engine.core_systems.grant_visible_buff
    # — é isso que dá ícone (fallback: quadrado da cor abaixo, sem PNG ainda)
    # e sincronização de rede de graça (mesmo canal AOI_UPDATE/deltas
    # ["effects"] que debuff já usa). Chave SEMPRE estável e única por fonte
    # (nunca uma chave genérica compartilhada — StatusEffects.effects só
    # guarda 1 instância por chave, e é normal 2+ procs de itens DIFERENTES
    # estarem ativos ao mesmo tempo). Buff temporário NOVO no futuro (item/
    # skill/mob) precisa de uma entrada aqui, mesma disciplina de adicionar
    # skill nova ao SKILL_CATALOG. Ver PROBLEMAS_ARQUITETURA.md.
    #
    # Procs de item — chave f"proc_{item_id}" (content/item_table.py).
    "proc_amulet_warrior": EffectDef("Fúria do Guerreiro", (255, 120,  40), True, 0.0,
                                     description="Aumenta o Poder de Ataque em 24 por 16s."),
    "proc_amulet_hunter":  EffectDef("Olho de Águia",      (200, 220,  80), True, 0.0,
                                     description="Aumenta a chance de crítico em 20% por 12s."),
    "proc_amulet_arcane":  EffectDef("Surto Arcano",       (140, 100, 255), True, 0.0,
                                     description="Aumenta o Poder de Magia em 30 por 12s."),
    "proc_death_blade":    EffectDef("Sede de Sangue",     (200,  30,  30), True, 0.0,
                                     description="Aumenta a chance de crítico em 35% por 16s."),
    "proc_doom_axe":       EffectDef("Carcaça de Ferro",   (150, 150, 160), True, 0.0,
                                     description="Aumenta a Armadura em 50 por 20s."),
    "proc_phantom_bow":    EffectDef("Tiro Fantasma",      (180, 200, 255), True, 0.0,
                                     description="Aumenta a chance de crítico em 40% por 12s."),
    "proc_amulet_undying": EffectDef("Vontade Imortal",    ( 80, 255, 120), True, 0.0,
                                     description="Aumenta a Vida Máxima em 600 por 15s."),
    "proc_ring_fury":      EffectDef("Fúria Épica",        (255,  80,   0), True, 0.0,
                                     description="Aumenta o Poder de Ataque em 50 por 16s."),
    # Self-buffs de skill — chave = o label já usado por add_timed_modifier.
    "cancao_inspiracao": EffectDef("Canção da Inspiração", (220, 200, 120), True, 0.0,
                                   description="Aumenta o Poder de Ataque em 30% por 20s."),
    "so_um_gole":        EffectDef("Só um Gole",           (180, 220, 255), True, 0.0,
                                   description="Habilidades de Concentração ficam grátis e o acerto vai a 100% por 10s."),
    # Buff de matar chefe de jungle — chave f"jungle_boss_buff_{mob_key}"
    # (content/jungle_definitions.py::JUNGLE_BOSS_TABLE). Limitação conhecida:
    # matar o MESMO camp 2x antes do buff anterior expirar renova a duração
    # deste único ícone em vez de empilhar um 2º (o efeito real em
    # timed_modifiers continua empilhando normalmente — só a representação
    # visual simplifica, StatusEffects só tem 1 slot por chave).
    "jungle_boss_buff_jungle_boss_ancestral":             EffectDef("Bênção do Ancestral", (150,  40,  40), True, 0.0,
        description="Aumenta Poder de Ataque e/ou Velocidade de Ataque — o bônus muda a cada abate do Ancestral."),
    "jungle_boss_buff_jungle_boss_guardiao_de_pedra":     EffectDef("Bênção do Guardião",  (110, 110, 120), True, 0.0,
        description="Aumenta Armadura e/ou Vida Máxima — o bônus muda a cada abate do Guardião de Pedra."),
    "jungle_boss_buff_jungle_boss_dragao_jovem":          EffectDef("Bênção do Dragão",    (180,  40, 150), True, 0.0,
        description="Aumenta Poder de Magia e/ou chance de crítico — o bônus muda a cada abate do Dragão Jovem."),
    "jungle_boss_buff_jungle_boss_horror_das_profundezas": EffectDef("Bênção do Horror",   ( 40,  90,  60), True, 0.0,
        description="Aumenta Poder de Ataque e/ou chance de crítico — o bônus muda a cada abate do Horror das Profundezas."),
    # Bônus de crítico do Lapso Elemental — mesmo proc de "elemental_lapse"
    # (a queima, debuff, continua com a própria chave/ícone), só que a
    # parte de crit_rating (timed_modifiers, label "lapso_elemental")
    # nunca tinha registro visível nenhum — corrigido junto (31/08/2026,
    # fase 2 da buff bar), roteado por grant_visible_buff.
    "lapso_elemental_crit": EffectDef("Lapso Elemental (crítico)", (255, 150, 220), True, 0.0,
        description="Bônus de chance de crítico por 3 críticos de fogo seguidos."),
    # ── Fase 2 da buff bar (31/08/2026) — efeitos com mecanismo PRÓPRIO,
    # não StatusEffects. Estas 5 entradas NUNCA são aplicadas de verdade
    # via apply_effect — só existem pra dar label/cor de fallback aos
    # "fake effect" sintéticos que ui/buff_bar.py::_gather_extra_buffs()
    # monta a partir do componente/campo real de cada mecanismo (ver
    # PROBLEMAS_ARQUITETURA.md). Não confundir com uma StatusEffects real.
    "escudo_fogo":   EffectDef("Escudo de Fogo",   (255, 100,  30), True, 0.0,
                              description="Devolve dano de fogo a quem te atacar."),
    "bloco_de_gelo": EffectDef("Bloco de Gelo",    (140, 210, 255), True, 0.0,
                              description="Imune a todo dano e efeito negativo; cura 10% da Vida Máxima por segundo."),
    "camuflagem":    EffectDef("Camuflagem",       (110, 160, 110), True, 0.0,
                              description="Invisível a inimigos; velocidade de movimento reduzida."),
    "chama_interna": EffectDef("Chama Interna",    (255, 170,  60), True, 0.0,
                              description="A próxima Bola de Fogo é instantânea e gratuita."),
    "na_mosca":      EffectDef("Na Mosca",         (200, 220,  80), True, 0.0,
                              description="A próxima flechada causa 25% de dano a mais."),
    # ── Xamã (24/10/2026) — valores numéricos de verdade vêm do SKILL_CATALOG;
    # speed_mult/damage_taken_mult aqui são o efeito do próprio buff/debuff.
    "espirito_do_lobo": EffectDef("Espírito do Lobo", (170, 200, 230), True, 0.0,
                                  speed_mult=1.20,
                                  description="Velocidade de movimento aumentada em 20%."),
    "pele_do_urso":     EffectDef("Pele do Urso",     (150, 110,  70), True, 0.0,
                                  absorbs=True,
                                  description="Escudo espiritual que absorve dano."),
    "corredor_ancestral": EffectDef("Corredor Ancestral", (120, 225, 190), True, 1.0,
                                    heals=True,
                                    description="Recebe cura espiritual a cada segundo."),
    "marca_do_gaviao":  EffectDef("Marca do Gavião",  (220, 170,  60), False, 0.0,
                                  damage_taken_mult=1.05, blocks_stealth=True,
                                  description="Recebe 5% a mais de dano e não pode se camuflar."),
}
