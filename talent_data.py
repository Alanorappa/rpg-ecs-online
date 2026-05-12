"""
Árvore de Talentos — Cavaleiro
================================
15 talentos dispostos em 6 linhas.
Efeitos comportamentais são lidos em runtime pelos sistemas via TalentTree.allocated.

Estrutura de cada talento:
  "id": {
      "name"          str
      "description"   str
      "build"         str
      "col"           int  (0, 1, 2)
      "row"           int  (0–5)
      "max_points"    int
      "requires"      dict {talent_id: min_points}
      "effects"       list de {"attribute", "value", "type"} | None
      "unlocks_skill" str | None  — nome do handler
      "skill_def"     dict | None — {"name", "description", "cooldown"}
  }
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Mapeamento classe → build padrão  (uma build por classe por enquanto)
# ---------------------------------------------------------------------------
CLASS_BUILD_MAP: dict[str, str] = {
    "guerreiro": "cavaleiro",
    "mago":      "piromania",
}

# ---------------------------------------------------------------------------
# BUILDS
# ---------------------------------------------------------------------------
BUILDS: dict[str, dict] = {
    "cavaleiro": {
        "name":        "Cavaleiro",
        "description": "Guerreiro de campo aberto. Raiva, controle de grupo e "
                       "habilidades devastadoras para dominar múltiplos inimigos.",
        "pros":        ["+Alta mobilidade com Interceptar",
                        "+Controle de grupo (stun/medo)",
                        "+3 habilidades desbloqueáveis"],
        "cons":        ["-Depende de Raiva para habilidades",
                        "-Cooldowns longos"],
        "color":       (80, 200, 100),
        "color_dark":  (30, 80, 40),
    },
    "piromania": {
        "name":        "Piromania",
        "description": "Mago especializado na escola de fogo. Maximiza o dano "
                       "de Bola de Fogo e desbloqueia habilidades devastadoras.",
        "pros":        ["+Alto dano mágico de fogo",
                        "+4 habilidades desbloqueáveis",
                        "+Sinergia fogo/gelo (Choque Térmico)"],
        "cons":        ["-Depende de mana",
                        "-Cast times longos sem talentos"],
        "color":       (255, 100, 30),
        "color_dark":  (120, 30, 0),
    },
}

# ---------------------------------------------------------------------------
# TALENTOS — Cavaleiro (15 nós)
# ---------------------------------------------------------------------------
#
#  Estrutura da árvore (col × row):
#
#   [1]col0      [2]col1      [3]col2      ← row 0
#    |            |            |
#   [4]col0      [5]col1      [6]col2      ← row 1
#    |            |
#   [7]col0      [8]col1 ─── [9]col2      ← row 2
#    |                         |
#  [10]col0 ── [11]col1      [12]col2     ← row 3
#               |              |
#             [13]col1       [14]col2     ← row 4
#               |
#             [15]col1                    ← row 5
#
# ---------------------------------------------------------------------------
TALENTS: dict[str, dict] = {

    # ── Row 0 ──────────────────────────────────────────────────────────────
    "cav_reflexos": {
        "name":          "Reflexos Apurados",
        "description":   "Aumenta {v}% a chance de aparar um golpe",
        "build":         "cavaleiro",
        "col": 0, "row": 0,
        "max_points":    3,
        "requires":      {},
        "effects":       [{"attribute": "parry_rating", "value": 20.0, "type": "flat"}],
        "unlocks_skill": None,
        "skill_def":     None,
    },
    "cav_veterano": {
        "name":           "Veterano",
        "description":    "Reduz {v} de raiva do custo do Golpe Poderoso",
        "build":          "cavaleiro",
        "col": 1, "row": 0,
        "max_points":     5,
        "requires":       {},
        "effects":        None,
        "unlocks_skill":  None,
        "skill_def":      None,
        "preview_formula": lambda d: d,          # mostra pontos de redução no {v}
    },
    "cav_vontade": {
        "name":          "Vontade",
        "description":   "Interceptar gera 10 de raiva",
        "build":         "cavaleiro",
        "col": 2, "row": 0,
        "max_points":    1,
        "requires":      {},
        "effects":       None,
        "unlocks_skill": None,
        "skill_def":     None,
    },

    # ── Row 1 ──────────────────────────────────────────────────────────────
    "cav_maquina_matar": {
        "name":          "Máquina de Matar",
        "description":   "+15% de dano do Impacto por inimigo em raio de 3 tiles",
        "build":         "cavaleiro",
        "col": 0, "row": 1,
        "max_points":    1,
        "requires":      {"cav_reflexos": 1},
        "effects":       None,
        "unlocks_skill": None,
        "skill_def":     None,
    },
    "cav_embalo": {
        "name":           "Embalo",
        "description":    "Aumenta {v}% o dano de Golpe Poderoso após golpe crítico",
        "build":          "cavaleiro",
        "col": 1, "row": 1,
        "max_points":     5,
        "requires":       {"cav_veterano": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 10,     # mostra bônus % no {v}
    },
    "cav_sede_batalha": {
        "name":           "Sede de Batalha",
        "description":    "Reduz {v}s do cooldown de Interceptar",
        "build":          "cavaleiro",
        "col": 2, "row": 1,
        "max_points":     5,
        "requires":       {"cav_vontade": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 2,      # mostra redução em segundos no {v}
    },

    # ── Row 2 ──────────────────────────────────────────────────────────────
    "cav_assassino": {
        "name":          "Assassino",
        "description":   "Impacto tem 5% de chance por alvo golpeado de ativar carga de Executar",
        "build":         "cavaleiro",
        "col": 0, "row": 2,
        "max_points":    1,
        "requires":      {"cav_maquina_matar": 1},
        "effects":       None,
        "unlocks_skill": None,
        "skill_def":     None,
    },
    "cav_alvo_confirmado": {
        "name":           "Alvo Confirmado",
        "description":    "Interceptar atordoa o alvo por {v}s",
        "build":          "cavaleiro",
        "col": 1, "row": 2,
        "max_points":     5,
        "requires":       {"cav_embalo": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "skill_def":      None,
        "preview_formula": lambda d: round(d * 0.3, 1),  # mostra duração do stun no {v}
    },
    "cav_horrorizante": {
        "name":          "Horrorizante",
        "description":   "Executar sem matar faz o alvo fugir de medo por 1 segundo",
        "build":         "cavaleiro",
        "col": 2, "row": 2,
        "max_points":    1,
        "requires":      {"cav_alvo_confirmado": 1},
        "effects":       None,
        "unlocks_skill": None,
        "skill_def":     None,
    },

    # ── Row 3 ──────────────────────────────────────────────────────────────
    "cav_golpe_debilitante": {
        "name":          "Golpe Debilitante",
        "description":   "Desbloqueia Golpe Debilitante: 50% dano + dano da arma, -50% velocidade por 5s",
        "build":         "cavaleiro",
        "col": 0, "row": 3,
        "max_points":    1,
        "requires":      {"cav_assassino": 1},
        "effects":       None,
        "unlocks_skill": "golpe_debilitante",
        "skill_def":     None,
    },
    "cav_explorador": {
        "name":           "Explorador de Fraquezas",
        "description":    "Aumenta {v}% a chance de crítico contra alvos com velocidade reduzida",
        "build":          "cavaleiro",
        "col": 1, "row": 3,
        "max_points":     3,
        "requires":       {"cav_golpe_debilitante": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 15,     # mostra bônus de crit % no {v}
    },
    "cav_provocacao": {
        "name":          "Brado Provocativo",
        "description":   "Desbloqueia Brado Provocativo: provoca inimigos em raio 3 tiles, "
                         "enlouquecendo-os por 10s (+5% dano causado, +10% dano recebido)",
        "build":         "cavaleiro",
        "col": 2, "row": 3,
        "max_points":    1,
        "requires":      {"cav_horrorizante": 1},
        "effects":       None,
        "unlocks_skill": "brado_provocativo",
        "skill_def":     None,
    },

    # ── Row 4 ──────────────────────────────────────────────────────────────
    "cav_foco_mortal": {
        "name":          "Foco Mortal",
        "description":   "+8% de dano por segundo que o alvo está debilitado, acumulando até 40%",
        "build":         "cavaleiro",
        "col": 1, "row": 4,
        "max_points":    1,
        "requires":      {"cav_explorador": 1},
        "effects":       None,
        "unlocks_skill": None,
        "skill_def":     None,
    },
    "cav_punho_queixo": {
        "name":           "Punho no Queixo",
        "description":    "Desbloqueia Punho no Queixo: Após 3 golpes bem sucedidos, "
                          "causa 45% dano e atordoa o alvo por {v} segundo(s). 15s CD.",
        "build":          "cavaleiro",
        "col": 2, "row": 4,
        "max_points":     3,
        "unlock_at":      1,
        "requires":       {"cav_provocacao": 1},
        "effects":        None,
        "unlocks_skill":  "punho_no_queixo",
        "preview_formula": lambda d: d,          # mostra duração do stun em segundos no {v}
        "skill_def":     None,
    },

    # ── Row 5 ──────────────────────────────────────────────────────────────
    "cav_fatiador": {
        "name":          "Fatiador de Corpos",
        "description":   "Desbloqueia Fatiador de Corpos: spin AoE causando 45% dano/s por 5s",
        "build":         "cavaleiro",
        "col": 1, "row": 5,
        "max_points":    1,
        "requires":      {"cav_foco_mortal": 1},
        "effects":       None,
        "unlocks_skill": "fatiador_de_corpos",
        "skill_def":     None,
    },

    # ===========================================================================
    # TALENTOS — Piromania (Mago / build Fogo)
    # Layout (col × row):
    #
    #        col1          ← row 0
    #   col0  col1  col2  ← row 1
    #   col0  col1  col2  ← row 2
    #   col0  col1  col2  ← row 3
    #   col0  col1  col2  ← row 4
    #         col1        ← row 5
    # ===========================================================================

    # ── Row 0 ──────────────────────────────────────────────────────────────────
    "pir_frieza": {
        "name":        "Frieza",
        "description": "Reduz em {v} o custo de mana de Bola de Fogo.",
        "build":       "piromania",
        "col": 1, "row": 0,
        "max_points":  5,
        "requires":    {},
        "effects":     None,
        "unlocks_skill": None,
        "skill_def":     None,
    },

    # ── Row 1 ──────────────────────────────────────────────────────────────────
    "pir_bdf_aperfeicoada": {
        "name":        "Bola de Fogo Aperfeiçoada",
        "description": "Reduz em {v}s o tempo de lançamento de Bola de Fogo.",
        "build":       "piromania",
        "col": 0, "row": 1,
        "max_points":  5,
        "requires":    {"pir_frieza": 1},
        "effects":     None,
        "unlocks_skill": None,
        "skill_def":     None,
    },

    # ── Row 2 ──────────────────────────────────────────────────────────────────
    "pir_escudo_fogo": {
        "name":           "Escudo de Fogo",
        "description":    "Desbloqueia Escudo de Fogo: envolve o corpo em chamas. "
                          "Atacantes recebem 10 + 20% SP de dano. 15s / 20s CD / 25 mana.",
        "build":          "piromania",
        "col": 0, "row": 2,
        "max_points":     1,
        "requires":       {"pir_bdf_aperfeicoada": 1},
        "effects":        None,
        "unlocks_skill":  "escudo_fogo",
        "skill_def": None,
    },

    "pir_precisao_elemental": {
        "name":           "Precisão Elemental",
        "description":    "Reduz em {v}s o tempo de lançamento de Nova Congelante.",
        "build":          "piromania",
        "col": 2, "row": 1,
        "max_points":     5,
        "requires":       {"pir_frieza": 1},
        "effects":        None,
        "preview_formula": lambda pts: round(pts * 0.2, 1),
        "unlocks_skill":  None,
        "skill_def":      None,
    },

    "pir_choque_termico": {
        "name":          "Choque Térmico",
        "description":   "Habilidades de fogo causam 100% mais dano em alvos enraizados "
                         "pela Nova Congelante.",
        "build":         "piromania",
        "col": 2, "row": 2,
        "max_points":    1,
        "requires":      {"pir_precisao_elemental": 1},
        "effects":       None,
        "unlocks_skill": None,
        "skill_def":     None,
    },

    # ── Row 3 ──────────────────────────────────────────────────────────────────
    "pir_lapso_elemental": {
        "name":           "Lapso Elemental",
        "description":    "3 crits de fogo em 6s procam Lapso Elemental: +"
                          "{v}% crit por 5s e queima 1% HP/s.",
        "build":          "piromania",
        "col": 1, "row": 3,
        "max_points":     3,
        "requires":       {"pir_chama_interna": 1},
        "effects":        None,
        "preview_formula": lambda pts: pts * 5,
        "unlocks_skill":  None,
        "skill_def":      None,
    },

    # ── Row 4 ──────────────────────────────────────────────────────────────────
    "pir_calamidade": {
        "name":        "Calamidade Flamejante",
        "description": "Desbloqueia Calamidade Flamejante: bombardeia uma área com bolas "
                       "de fogo causando 50 + 50% SP/s e slow 50% por 5s.",
        "build":       "piromania",
        "col": 0, "row": 4,
        "max_points":  1,
        "requires":    {"pir_piromaníaco": 1},
        "effects":     None,
        "unlocks_skill": "calamidade_flamejante",   # já existe em SKILL_CATALOG
        "skill_def":   None,
    },

    "pir_combustao": {
        "name":        "Combustão",
        "description": "Desbloqueia Calcinar: 0.6s cast em movimento, 50 + 25% SP. Escola fogo.",
        "build":       "piromania",
        "col": 2, "row": 4,
        "max_points":  1,
        "requires":    {"pir_exaustao": 1},
        "effects":     None,
        "unlocks_skill": "calcinar",
        "skill_def":   None,
    },

    # ── Row 5 ──────────────────────────────────────────────────────────────────
    "pir_pirofagia": {
        "name":        "Pirofagia",
        "description": "Desbloqueia Pirofagia: cone de fogo rotacionado pelo mouse. "
                       "150 + 150% SP + desorientado 3s. 1s ativo. 90s CD. 75 mana.",
        "build":       "piromania",
        "col": 1, "row": 5,
        "max_points":  1,
        "requires":    {"pir_crematoria": 1},
        "effects":     None,
        "unlocks_skill": "pirofagia",
        "skill_def":   None,
    },

    "pir_crematoria": {
        "name":        "Crematória",
        "description": "Skills de fogo causam 25% mais dano em alvos com menos de 20% de vida.",
        "build":       "piromania",
        "col": 1, "row": 4,
        "max_points":  1,
        "requires":    {"pir_lapso_elemental": 1},
        "effects":     None,
        "unlocks_skill": None,
        "skill_def":   None,
    },

    "pir_exaustao": {
        "name":        "Exaustão",
        "description": "Bolas de Fogo consecutivas reduzem 5% a velocidade do alvo "
                       "por acerto, acumulando até 25% por 6s.",
        "build":       "piromania",
        "col": 2, "row": 3,
        "max_points":  1,
        "requires":    {"pir_lapso_elemental": 1},
        "effects":     None,
        "unlocks_skill": None,
        "skill_def":   None,
    },

    "pir_piromaníaco": {
        "name":           "Piromaníaco",
        "description":    "Skills de fogo custam {v}% menos mana e causam {v}% mais dano.",
        "build":          "piromania",
        "col": 0, "row": 3,
        "max_points":     3,
        "requires":       {"pir_lapso_elemental": 1},
        "effects":        None,
        "preview_formula": lambda pts: pts * 5,
        "unlocks_skill":  None,
        "skill_def":      None,
    },

    "pir_chama_interna": {
        "name":           "Chama Interna",
        "description":    "Suas habilidades de fogo têm {v}% de chance de tornar a próxima "
                          "Bola de Fogo instantânea e gratuita.",
        "build":          "piromania",
        "col": 1, "row": 2,
        "max_points":     5,
        "requires":       {"pir_queimaduras": 1},
        "effects":        None,
        "preview_formula": lambda pts: pts * 2,
        "unlocks_skill":  None,
        "skill_def":      None,
    },

    "pir_queimaduras": {
        "name":           "Queimaduras Profundas",
        "description":    "Críticos de Bola de Fogo fazem o alvo arder em chamas por {v} segundos.",
        "build":          "piromania",
        "col": 1, "row": 1,
        "max_points":     5,
        "requires":       {"pir_frieza": 1},
        "effects":        None,
        "preview_formula": lambda pts: pts * 3,
        "unlocks_skill":  None,
        "skill_def":      None,
    },
}

# ---------------------------------------------------------------------------
# Agrupamento por build — gerado automaticamente
# ---------------------------------------------------------------------------
BUILD_TALENTS: dict[str, list[str]] = {
    build_id: [tid for tid, t in TALENTS.items() if t["build"] == build_id]
    for build_id in BUILDS
}
