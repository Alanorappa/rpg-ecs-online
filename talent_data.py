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
        "skill_def":     {
            "name":        "Golpe Debilitante",
            "description": "Golpe certeiro nos tendões: 50% do dano de ataque + dano da arma. "
                           "Reduz a velocidade de movimento do alvo em 50% por 5s.",
            "cooldown":    0.0,
        },
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
        "skill_def":     {
            "name":        "Brado Provocativo",
            "description": "Enlouquece inimigos (raio 3): +5% dano, +10% dano recebido. 10s / 45s CD.",
            "cooldown":    45.0,
            "sound":       "skill_brado_provocativo",
        },
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
        "skill_def":     {
            "name":           "Punho no Queixo",
            "description":    "Após 3 golpes bem sucedidos, desfere um soco causando 45% "
                              "do poder de ataque e atordoando o alvo (duração escala com pontos).",
            "cooldown":       15.0,
            "sound":          "skill_punho_no_queixo",
            "max_charges":    1,     # skill baseada em cargas: 1 carga por ciclo de 3 hits
            "charge_timeout": 0.0,   # cargas não expiram
        },
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
        "skill_def":     {
            "name":        "Fatiador de Corpos",
            "description": "O cavaleiro gira desferindo golpes a todos ao redor, causando "
                           "45% do dano de ataque + dano da arma a cada 1s durante 5s.",
            "cooldown":    45.0,
        },
    },
}

# ---------------------------------------------------------------------------
# Agrupamento por build — gerado automaticamente
# ---------------------------------------------------------------------------
BUILD_TALENTS: dict[str, list[str]] = {
    build_id: [tid for tid, t in TALENTS.items() if t["build"] == build_id]
    for build_id in BUILDS
}
