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
      "requires_skill" str | ausente — skill_id que precisa estar aprendida
                       pro talento aceitar ponto (world_systems.
                       talent_skill_requirement_met, 25/10/2026)
      "effects"       list de {"attribute", "value", "type"} | None
      "unlocks_skill" str | None  — nome do handler
      "skill_def"     dict | None — {"name", "description", "cooldown"}
      "icon"          str | None  — chave de ícone (17/09/2026, ver
                       PROBLEMAS_ARQUITETURA.md §182) no MESMO formato
                       que ui/icon_atlas_catalog.py::ICON_ATLAS já usa
                       (ex: "skill_golpe_debilitante") — `None` = nó da
                       árvore continua mostrando o fallback de sempre
                       (retângulo + 2 letras iniciais,
                       ui/talent_system.py::_render_node). Talento que
                       desbloqueia skill (`unlocks_skill` setado) foi
                       auto-preenchido reaproveitando o ícone da
                       própria skill — talento sem skill pra
                       reaproveitar fica `None` até alguém escolher um
                       ícone (reaproveitado ou pintado numa célula
                       livre do atlas).
  }
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Mapeamento classe → build padrão  (uma build por classe por enquanto)
# ---------------------------------------------------------------------------
CLASS_BUILD_MAP: dict[str, str] = {
    "guerreiro": "cavaleiro",
    "mago":      "piromania",
    "arqueiro":  "bardo",
    "xama":      "raiz",
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
                        "+Controle de grupo (stun/medo)"],
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
                        "+Sinergia fogo/gelo (Choque Térmico)"],
        "cons":        ["-Depende de mana",
                        "-Cast times longos sem talentos"],
        "color":       (255, 100, 30),
        "color_dark":  (120, 30, 0),
    },
    "bardo": {
        "name":        "Bardo",
        "description": "Arqueiro versátil que equilibra precisão, concentração "
                       "e habilidades de suporte através da música.",
        "pros":        ["+Alta precisão de combate",
                        "+Suporte e controle de grupo",
                        "+Habilidades únicas de Concentração"],
        "cons":        ["-Depende de Concentração",
                        "-Alguns talentos requerem posicionamento parado"],
        "color":       (80, 180, 220),
        "color_dark":  (20, 60, 100),
    },
    "raiz": {
        "name":        "Raiz",
        "description": "Xamã ligado aos espíritos ancestrais. Cura aliados, "
                       "protege o grupo e drena os inimigos para alimentar o "
                       "próprio Espírito.",
        "pros":        ["+Cura direta em aliados",
                        "+Espírito regenera ao causar dano"],
        "cons":        ["-Depende de Espírito para curar",
                        "-Dano baixo comparado ao mago"],
        "color":       (120, 190, 90),
        "color_dark":  (40, 70, 30),
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
        "icon":          None,
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
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d,
        "cs_flags": [
            {"field": "golpe_poderoso_rage_cost", "reset": 15,
             "formula": lambda pts: max(10, 15 - pts)},
        ],
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
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "interceptar_rage_bonus", "reset": 0,
             "formula": lambda pts: 10 if pts >= 1 else 0},
        ],
    },

    # ── Row 1 ──────────────────────────────────────────────────────────────
    "cav_maquina_matar": {
        "name":          "Máquina de Matar",
        "description":   "Ao ser cercado por múltiplos inimigos, o guerreiro entra em modo de foco, se tornando uma máquina de matar. (15% de dano bônus para cada alvo em um raio de 3 tiles.)",
        "build":         "cavaleiro",
        "col": 0, "row": 1,
        "max_points":    1,
        "requires":      {"cav_reflexos": 1},
        "effects":       None,
        "unlocks_skill": None,
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "impacto_maquina_matar", "reset": False,
             "formula": lambda pts: pts >= 1},
        ],
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
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 10,
        "cs_flags": [
            {"field": "embalo_on_crit",         "reset": False, "formula": lambda pts: pts > 0},
            {"field": "embalo_bonus_per_charge", "reset": 0.0,  "formula": lambda pts: pts * 0.10},
        ],
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
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 2,
        "cs_flags": [
            {"field": "interceptar_cooldown_reduction", "reset": 0.0,
             "formula": lambda pts: pts * 2.0},
        ],
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
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "impacto_assassino", "reset": False, "formula": lambda pts: pts >= 1},
        ],
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
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: round(d * 0.3, 1),
        "cs_flags": [
            {"field": "interceptar_stun_duration", "reset": 0.0,
             "formula": lambda pts: pts * 0.3},
        ],
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
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "executar_horrorizante", "reset": False, "formula": lambda pts: pts >= 1},
        ],
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
        "icon":          "skill_golpe_debilitante",
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
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 15,
        "cs_flags": [
            {"field": "explorador_crit_per_point", "reset": 0,
             "formula": lambda pts: pts},
        ],
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
        "icon":          "skill_brado_provocativo",
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
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "foco_mortal_enabled", "reset": False, "formula": lambda pts: pts >= 1},
        ],
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
        "icon":          "skill_punho_no_queixo",
        "preview_formula": lambda d: d,
        "skill_def":     None,
        "cs_flags": [
            {"field": "pnq_enabled",       "reset": False, "formula": lambda pts: pts >= 1},
            {"field": "pnq_stun_duration", "reset": 1.0,   "formula": lambda pts: float(max(1, pts))},
        ],
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
        "icon":          "skill_fatiador_de_corpos",
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
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "fire_mana_discount", "reset": 0, "formula": lambda pts: pts},
        ],
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
        "icon":          None,
        "skill_def":     None,
        "preview_formula": lambda pts: round(pts * 0.1, 1),
        "cs_flags": [
            {"field": "fire_cast_time_reduction", "reset": 0.0,
             "formula": lambda pts: pts * 0.1},
        ],
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
        "icon":          "skill_escudo_fogo",
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
        "icon":          None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "ice_cast_time_reduction", "reset": 0.0,
             "formula": lambda pts: pts * 0.2},
        ],
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
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "thermal_shock_enabled", "reset": False, "formula": lambda pts: pts >= 1},
        ],
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
        "icon":          None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "elemental_lapse_crit_bonus", "reset": 0.0,
             "formula": lambda pts: pts * 0.05},
        ],
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
        "icon":          "skill_calamidade_flamejante",
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
        "icon":          "skill_calcinar",
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
        "icon":          "skill_pirofagia",
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
        "icon":          None,
        "skill_def":   None,
        "cs_flags": [
            {"field": "crematoria_enabled", "reset": False, "formula": lambda pts: pts >= 1},
        ],
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
        "icon":          None,
        "skill_def":   None,
        "cs_flags": [
            {"field": "fire_exhaustion_enabled", "reset": False, "formula": lambda pts: pts >= 1},
        ],
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
        "icon":          None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "pyromania_bonus", "reset": 0.0, "formula": lambda pts: pts * 0.05},
        ],
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
        "icon":          None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "fire_instant_proc_chance", "reset": 0.0,
             "formula": lambda pts: pts * 0.02},
        ],
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
        "icon":          None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "fire_burns_on_crit", "reset": False, "formula": lambda pts: pts >= 1},
            {"field": "fire_burn_duration", "reset": 0.0,   "formula": lambda pts: pts * 3.0},
        ],
    },

    # ===========================================================================
    # TALENTOS — Bardo (Arqueiro)
    # Layout (col × row):
    #
    #   col0  col1  col2   ← row 0
    #   col0  col1  col2   ← row 1
    #   col0  col1  col2   ← row 2
    #   col0  col1  col2   ← row 3
    #         col1         ← row 4
    # ===========================================================================

    # ── Row 0 ──────────────────────────────────────────────────────────────────
    "bardo_consistencia": {
        "name":           "Consistência",
        "description":    "O arqueiro é consistente em seu treinamento, "
                          "aumentando sua taxa de acerto em {v}%.",
        "build":          "bardo",
        "col": 0, "row": 0,
        "max_points":     5,
        "requires":       {},
        "effects":        [{"attribute": "acerto", "value": 2.0, "type": "flat"}],
        "unlocks_skill":  None,
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 2,
    },

    # ── Row 1 ──────────────────────────────────────────────────────────────────
    "bardo_so_um_gole": {
        "name":          "Só um Gole",
        "description":   "Desbloqueia Só um Gole: o arqueiro bebe e fica mais "
                         "afiado — habilidades de Concentração ficam grátis e "
                         "acerto vai a 100% por 10s. CD: 120s.",
        "build":         "bardo",
        "col": 0, "row": 1,
        "max_points":    1,
        "requires":      {"bardo_consistencia": 1},
        "effects":       None,
        "unlocks_skill": "so_um_gole",
        "icon":          "skill_so_um_gole",
        "skill_def":     None,
    },

    "bardo_pratico": {
        "name":           "Prático",
        "description":    "A prática leva à perfeição: o arqueiro consegue "
                          "recarregar a aljava enquanto se move.",
        "build":          "bardo",
        "col": 1, "row": 1,
        "max_points":     1,
        "requires":       {"bardo_parado_concentrado": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":          None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "recarregar_in_motion",
             "reset": False,
             "formula": lambda pts: pts >= 1},
        ],
    },

    # ── Row 0 ──────────────────────────────────────────────────────────────────
    # ── Row 5 ──────────────────────────────────────────────────────────────────
    "bardo_tiro_multiplo": {
        "name":          "Tiro Múltiplo",
        "description":   "Desbloqueia Tiro Múltiplo: dispara flechas em cone de 90° "
                         "na direção do mouse. 1pt=2 alvos, 2pt=3 alvos, 3pt=múltiplos "
                         "alvos. 100% arma + 300% AP. 60 Conc. 90s CD.",
        "build":         "bardo",
        "col": 1, "row": 5,
        "max_points":    3,
        "unlock_at":     1,          # desbloqueia a skill com 1 ponto; pontos extras aumentam alvos
        "requires":      {"bardo_na_mosca": 1},
        "effects":       None,
        "unlocks_skill": "tiro_multiplo",
        "icon":          "skill_tiro_multiplo",
        "skill_def":     None,
        "preview_formula": lambda d: {1: "2 alvos", 2: "3 alvos", 3: "múltiplos"}.get(d, ""),
        "cs_flags": [
            {"field": "tiro_multiplo_targets",
             "reset": 0,
             "formula": lambda pts: {1: 2, 2: 3, 3: 99}.get(pts, 0)},
        ],
    },

    # ── Row 4 ──────────────────────────────────────────────────────────────────
    "bardo_na_mosca": {
        "name":          "Na Mosca",
        "description":   "Ao acertar um dano crítico, o arqueiro ganha confiança: "
                         "a próxima flecha causa 25% a mais de dano.",
        "build":         "bardo",
        "col": 1, "row": 4,
        "max_points":    1,
        "requires":      {"bardo_flechas_despadronizadas": 1},
        "effects":       None,
        "unlocks_skill": None,
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "na_mosca_enabled",
             "reset": False,
             "formula": lambda pts: pts >= 1},
        ],
    },

    "bardo_camuflagem": {
        "name":          "Camuflagem",
        "description":   "Desbloqueia Camuflagem: o arqueiro se disfarça de "
                         "objeto do cenário por 5s. Velocidade 30%, inimigos "
                         "perdem o alvo. 50 Conc. 45s CD.",
        "build":         "bardo",
        "col": 2, "row": 4,
        "max_points":    1,
        "requires":      {"bardo_tiro_repulsivo": 1},
        "effects":       None,
        "unlocks_skill": "camuflagem",
        "icon":          "skill_camuflagem",
        "skill_def":     None,
    },

    "bardo_tiro_repulsivo": {
        "name":          "Tiro Repulsivo",
        "description":   "Desbloqueia Tiro Repulsivo: repele o alvo 5 tiles. "
                         "Colisão com parede = stun 3s. Colisão com criatura = "
                         "ambos stunam. 100% arma + 150% AP. 100 Conc. 45s CD.",
        "build":         "bardo",
        "col": 2, "row": 3,
        "max_points":    1,
        "requires":      {"bardo_reciclagem": 1},
        "effects":       None,
        "unlocks_skill": "tiro_repulsivo",
        "icon":          "skill_tiro_repulsivo",
        "skill_def":     None,
    },

    "bardo_flechas_despadronizadas": {
        "name":          "Flechas Despadronizadas",
        "description":   "Algumas flechas fora do padrão consertadas pelo arqueiro "
                         "são especiais e causam 50% a mais de dano. Proc: 15%.",
        "build":         "bardo",
        "col": 1, "row": 3,
        "max_points":    1,
        "requires":      {"bardo_sequencia_final": 1},
        "effects":       None,
        "unlocks_skill": None,
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "flechas_despadronizadas_chance",
             "reset": 0.0,
             "formula": lambda pts: 0.15 if pts >= 1 else 0.0},
        ],
    },

    # ── Row 3 ──────────────────────────────────────────────────────────────────
    "bardo_cancao_inspiracao": {
        "name":          "Canção da Inspiração",
        "description":   "Desbloqueia Canção da Inspiração: música épica que "
                         "aumenta 30% o poder de ataque por 20s. CD: 360s.",
        "build":         "bardo",
        "col": 0, "row": 3,
        "max_points":    1,
        "requires":      {"bardo_alvo_facil": 1},
        "effects":       None,
        "unlocks_skill": "cancao_inspiracao",
        "icon":          "skill_cancao_inspiracao",
        "skill_def":     None,
    },

    # ── Row 2 ──────────────────────────────────────────────────────────────────
    "bardo_sequencia_final": {
        "name":           "Sequência Final",
        "description":    "Quando o alvo está com menos de 50% de vida, "
                          "a Flecha Reiterada dispara uma terceira flecha.",
        "build":          "bardo",
        "col": 1, "row": 2,
        "max_points":     1,
        "requires":       {"bardo_pratico": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":          None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "flecha_reiterada_hp_threshold",
             "reset": 0.0,
             "formula": lambda pts: 0.50 if pts >= 1 else 0.0},
        ],
    },

    "bardo_reciclagem": {
        "name":          "Reciclagem",
        "description":   "Após abater um alvo, há 50–100% de chance de "
                         "recuperar as flechas gastas no combate contra ele.",
        "build":         "bardo",
        "col": 2, "row": 2,
        "max_points":    1,
        "requires":      {"bardo_sequencia_final": 1},
        "effects":       None,
        "unlocks_skill": None,
        "icon":          None,
        "skill_def":     None,
        "cs_flags": [
            {"field": "arrow_recovery_enabled",
             "reset": False,
             "formula": lambda pts: pts >= 1},
        ],
    },

    "bardo_alvo_facil": {
        "name":           "Alvo Fácil",
        "description":    "Alvos sob efeito de controle (stun, sleep, fear, "
                          "polimorfia, slow ou desorientado) são mais fáceis de "
                          "acertar (+{v}% acerto) e de criticar (+{v}% crit).",
        "build":          "bardo",
        "col": 0, "row": 2,
        "max_points":     5,
        "requires":       {"bardo_sequencia_final": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 2,   # mostra bônus total de acerto (1pt=2%, 5pt=10%)
        "cs_flags": [
            {"field": "alvo_facil_acerto",
             "reset": 0,
             "formula": lambda pts: pts * 2},        # +2% acerto por ponto
            {"field": "alvo_facil_crit",
             "reset": 0.0,
             "formula": lambda pts: pts * 0.05},     # +5% crit por ponto (0.05 = 5%)
        ],
    },

    "bardo_calmo_certeiro": {
        "name":           "Calmo e Certeiro",
        "description":    "Enquanto estiver parado, cada segundo concede "
                          "+{v}% de taxa de acerto (acumula sem limite de tempo, "
                          "até 100% total).",
        "build":          "bardo",
        "col": 2, "row": 1,
        "max_points":     5,
        "requires":       {"bardo_briguento": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d,   # mostra bônus %/s no {v}
        "cs_flags": [
            {"field": "acerto_per_standing_second",
             "reset": 0.0,
             "formula": lambda pts: float(pts)},  # +1%/s por ponto
        ],
    },

    "bardo_briguento": {
        "name":           "Briguento",
        "description":    "Brigas de bar aumentaram os reflexos do arqueiro, "
                          "aumentando sua esquiva em {v}%.",
        "build":          "bardo",
        "col": 2, "row": 0,
        "max_points":     5,
        "requires":       {},
        "effects":        [{"attribute": "dodge_rating", "value": 20.0, "type": "flat"}],
        "unlocks_skill":  None,
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d,   # 20 rating/ponto = 1%/ponto
    },

    "bardo_parado_concentrado": {
        "name":           "Parado e Concentrado",
        "description":    "O arqueiro se concentra melhor quando parado, "
                          "restaurando mais {v} de Concentração por segundo.",
        "build":          "bardo",
        "col": 1, "row": 0,
        "max_points":     5,
        "requires":       {},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":          None,
        "skill_def":      None,
        "preview_formula": lambda d: d,   # mostra +X regen/s no {v}
        "cs_flags": [
            {"field": "concentration_regen_idle",
             "reset": 5.0,
             "formula": lambda pts: 5.0 + pts * 1.0},
            # regen em movimento: fixo em 5/s (sem escala por ponto)
            # o reset aqui garante o valor base mesmo sem o talento alocado
            {"field": "concentration_regen_moving",
             "reset": 5.0,
             "formula": lambda pts: 5.0},
        ],
    },

    # ── Raiz (Xamã) — Fase 1: só os 3 nós da 1ª entrega; os outros 12 nós
    # do desenho (classe_xama.md) entram na Fase 3. ─────────────────────────
    "raiz_sussurros_constantes": {
        "name":           "Sussurros Constantes",
        "description":    "A cura do corredor do Toque Ancestral aumenta {v}% do valor base.",
        "build":          "raiz",
        "col": 0, "row": 0,
        "max_points":     5,
        "requires":       {},
        "requires_skill": "toque_ancestral",
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 30,
        "cs_flags": [
            {"field": "corredor_cura_bonus", "reset": 0.0,
             "formula": lambda pts: 0.30 * pts},
        ],
    },
    "raiz_alma_generosa": {
        "name":           "Alma Generosa",
        "description":    "Reduz em {v} o custo de Espírito das curas.",
        "build":          "raiz",
        "col": 1, "row": 0,
        "max_points":     5,
        "requires":       {},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 2,
        "cs_flags": [
            {"field": "cura_custo_reducao", "reset": 0,
             "formula": lambda pts: pts * 2},
        ],
    },
    "raiz_sopro_da_coruja": {
        "name":          "Sopro da Coruja",
        "description":   "Desbloqueia Sopro da Coruja: cura um aliado (ou você) "
                         "a até 8 tiles em 60 + 120% do Poder Mágico. Cast 1,5s, "
                         "40 de Espírito.",
        "build":         "raiz",
        "col": 1, "row": 1,
        "max_points":    1,
        "requires":      {"raiz_alma_generosa": 1},
        "effects":       None,
        "unlocks_skill": "sopro_da_coruja",
        "icon":          "skill_sopro_da_coruja",
        "skill_def":     None,
    },
    # ── Raiz — Fase 3, bloco A (25/10/2026) ────────────────────────────────
    "raiz_raizes_profundas": {
        "name":           "Raízes Profundas",
        "description":    "+{v}% de poder de cura (todas as suas curas).",
        "build":          "raiz",
        "col": 2, "row": 0,
        "max_points":     3,
        "requires":       {},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 4,
        "cs_flags": [
            {"field": "cura_poder_bonus", "reset": 0.0,
             "formula": lambda pts: 0.04 * pts},
        ],
    },
    "raiz_eco_restaurador": {
        "name":           "Eco Restaurador",
        "description":    "Sopro da Coruja tem {v}% de chance de curar +50%.",
        "build":          "raiz",
        "col": 0, "row": 1,
        "max_points":     5,
        "requires":       {"raiz_sussurros_constantes": 1},
        "requires_skill": "sopro_da_coruja",
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 4,
        "cs_flags": [
            {"field": "sopro_eco_chance", "reset": 0.0,
             "formula": lambda pts: 0.04 * pts},
        ],
    },
    "raiz_pulso_vital": {
        "name":           "Pulso Vital",
        "description":    "Sopro da Coruja cura +40% em alvos com menos de 35% de vida.",
        "build":          "raiz",
        "col": 0, "row": 2,
        "max_points":     1,
        "requires":       {"raiz_eco_restaurador": 1},
        "requires_skill": "sopro_da_coruja",
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "sopro_pulso_vital", "reset": False,
             "formula": lambda pts: pts > 0},
        ],
    },
    "raiz_purificar_o_espirito": {
        "name":           "Purificar o Espírito",
        "description":    "Sopro da Coruja remove 1 efeito negativo do alvo (veneno, "
                          "sangramento, queimadura, medo ou lentidão) — o que ainda "
                          "duraria mais.",
        "build":          "raiz",
        "col": 1, "row": 2,
        "max_points":     1,
        "requires":       {"raiz_sopro_da_coruja": 1},
        "requires_skill": "sopro_da_coruja",
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "sopro_purifica", "reset": False,
             "formula": lambda pts: pts > 0},
        ],
    },
    "raiz_mare_espiritual": {
        "name":           "Maré Espiritual",
        "description":    "+{v} de Espírito máximo.",
        "build":          "raiz",
        "col": 0, "row": 3,
        "max_points":     3,
        "requires":       {"raiz_pulso_vital": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 10,
        "cs_flags": [
            {"field": "espirito_max_bonus", "reset": 0,
             "formula": lambda pts: 10 * pts},
        ],
    },
    # ── Raiz — Fase 3, bloco B (25/10/2026) — efeitos em core_systems.
    # _heal_talent_effects (via apply_heal) ────────────────────────────────
    "raiz_pele_de_raiz": {
        "name":           "Pele de Raiz",
        "description":    "Quem você cura recebe {v}% a menos de dano por 6s.",
        "build":          "raiz",
        "col": 2, "row": 1,
        "max_points":     3,
        "requires":       {"raiz_raizes_profundas": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 3,
        "cs_flags": [
            {"field": "pele_de_raiz_reducao", "reset": 0.0,
             "formula": lambda pts: 0.03 * pts},
        ],
    },
    "raiz_dadiva_antecipada": {
        "name":           "Dádiva Antecipada",
        "description":    "Cura que passa da vida máxima vira escudo de {v}% do excesso "
                          "por 8s (acumula até 30% da vida máxima).",
        "build":          "raiz",
        "col": 2, "row": 2,
        "max_points":     3,
        "requires":       {"raiz_pele_de_raiz": 1},
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "preview_formula": lambda d: d * 15,
        "cs_flags": [
            {"field": "dadiva_pct", "reset": 0.0,
             "formula": lambda pts: 0.15 * pts},
        ],
    },
    "raiz_rastros_de_alma": {
        "name":           "Rastros de Alma",
        "description":    "O corredor do Toque Ancestral passa a ter 7 tiles de largura "
                          "(3 para cada lado) em vez de 5.",
        "build":          "raiz",
        "col": 2, "row": 3,
        "max_points":     1,
        "requires":       {"raiz_dadiva_antecipada": 1},
        "requires_skill": "toque_ancestral",
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "corredor_meia_largura_bonus", "reset": 0,
             "formula": lambda pts: 1 if pts > 0 else 0},
        ],
    },
    # ── Raiz — Fase 3, bloco C (25/10/2026): skills de talento ─────────────
    "raiz_abraco_do_urso": {
        "name":          "Abraço do Urso",
        "description":   "Desbloqueia Abraço do Urso: cura um aliado (ou você) a até 8 tiles "
                         "em 40 + 160% do Poder Mágico ao longo de 10s. 45 de Espírito, recarga 10s.",
        "build":         "raiz",
        "col": 1, "row": 3,
        "max_points":    1,
        "requires":      {"raiz_purificar_o_espirito": 1},
        "effects":       None,
        "unlocks_skill": "abraco_do_urso",
        "icon":          "skill_abraco_do_urso",
        "skill_def":     None,
    },
    "raiz_matilha_ancestral": {
        "name":          "Matilha Ancestral",
        "description":   "Desbloqueia Matilha Ancestral: cura até 5 aliados a até 4 tiles de um "
                         "aliado (ou de você) em 40 + 80% do Poder Mágico cada. Cast 2s, 80 de "
                         "Espírito, recarga 20s.",
        "build":         "raiz",
        "col": 0, "row": 4,
        "max_points":    1,
        "requires":      {"raiz_mare_espiritual": 1},
        "effects":       None,
        "unlocks_skill": "matilha_ancestral",
        "icon":          "skill_matilha_ancestral",
        "skill_def":     None,
    },
    "raiz_vinculo_duplo": {
        "name":           "Vínculo Duplo",
        "description":    "Abraço do Urso também cura um segundo aliado a até 3 tiles do "
                          "primeiro (o com menos vida), com 50% da cura.",
        "build":          "raiz",
        "col": 1, "row": 4,
        "max_points":     1,
        "requires":       {"raiz_abraco_do_urso": 1},
        "requires_skill": "abraco_do_urso",
        "effects":        None,
        "unlocks_skill":  None,
        "icon":           None,
        "skill_def":      None,
        "cs_flags": [
            {"field": "abraco_vinculo", "reset": False,
             "formula": lambda pts: pts > 0},
        ],
    },
    "raiz_negociacao_ancestral": {
        "name":          "Negociação Ancestral",
        "description":   "Desbloqueia Negociação Ancestral: 100 + 150% do Poder Mágico num "
                         "inimigo e o atordoa por 3s; os aliados a até 6 tiles dividem uma cura "
                         "igual ao dano. 65 de Espírito, recarga 90s.",
        "build":         "raiz",
        "col": 1, "row": 5,
        "max_points":    1,
        "requires":      {"raiz_vinculo_duplo": 1},
        "effects":       None,
        "unlocks_skill": "negociacao_ancestral",
        "icon":          "skill_negociacao_ancestral",
        "skill_def":     None,
    },
}

# ---------------------------------------------------------------------------
# Agrupamento por build — gerado automaticamente
# ---------------------------------------------------------------------------
BUILD_TALENTS: dict[str, list[str]] = {
    build_id: [tid for tid, t in TALENTS.items() if t["build"] == build_id]
    for build_id in BUILDS
}

# Talentos SEM efeito dentro da BG/instância de progressão normalizada
# (16/09/2026, ver PROBLEMAS_ARQUITETURA.md §159/§160) — fonte única lida
# tanto por `server/instance_progression.py::enter_normalized_progression`
# (zera o ponto alocado desse talento no overlay "tudo maxado" da
# instância, em vez de deixar o efeito ligar e não fazer nada) quanto por
# `ui/talent_system.py` (mostra "0/0" em vermelho em vez do valor normal).
# NUNCA duplicar essa lista em outro lugar — adicionar um talento futuro
# aqui é o único passo necessário pra ele ser ignorado dentro da BG nos
# dois lados ao mesmo tempo.
#
# "bardo_reciclagem" (Reciclagem, arqueiro): devolvia flechas recuperadas
# DIRETO pro Inventory ao vivo do servidor sem notificar o cliente —
# causava desync silencioso entre a bag do cliente e a do servidor,
# raiz real do bug da aljava trocada em BG nunca equipando de verdade.
INSTANCE_DISABLED_TALENTS: frozenset[str] = frozenset({"bardo_reciclagem"})
