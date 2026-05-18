# Dados de Jogo — Inventário de Conteúdo

> Contagem e localização de todo o conteúdo do jogo.

---

> ⚠️ **Problema de dados:** Fórmulas de skills (multiplicadores de dano, percentuais de cura) estão hardcoded nos handlers em vez de no catálogo. Ver `PROBLEMAS_ARQUITETURA.md` problema #12.

## Sistema de Atributos — `stats_system.py`

### Atributos primários e o que cada um dá

| Atributo | Sigla | Fórmula → Stats de combate |
|---|---|---|
| **Força** | STR | `attack_power = 5 + STR×2` · `parry_rating = STR×1` |
| **Inteligência** | INT | `spell_power = INT×2` · `max_mana = 100 + INT×15` |
| **Agilidade** | AGI | `crit_rating = 0.10 + AGI×0.01` · `dodge_rating = AGI×2` |
| **Vitalidade** | VIT | `stamina = 5 + VIT×5` → `max_hp = stamina×10` |
| **Defesa** | DEF | `armor = DEF×2` (redução: `dmg × 100/(100+armor)`) |

### Atributos base por classe — `CLASS_BASE_STATS` em `stats_system.py`

| Atributo | Guerreiro | Mago | Arqueiro |
|---|---|---|---|
| STR | 1 | 1 | 1 |
| INT | 1 | **5** | 1 |
| AGI | 1 | 1 | **5** |
| VIT | **3** | 2 | 2 |
| DEF | **2** | 1 | 1 |

### Ganhos por level up — `CLASS_LEVEL_GAINS` em `stats_system.py`

| Atributo | Guerreiro | Mago | Arqueiro |
|---|---|---|---|
| STR | **+2** | +0 | +0 |
| INT | +0 | **+3** | +0 |
| AGI | +1 | +0 | **+3** |
| VIT | **+2** | +1 | +1 |
| DEF | **+2** | +0 | +0 |

> Para adicionar nova classe: inserir entrada em `CLASS_LEVEL_GAINS` e `CLASS_MELEE_OVERRIDES` e `CLASS_ARMOR_ALLOWED` em `stats_system.py`. Sem código novo necessário.

---

## Skills — `skill_config.py` (SKILL_CATALOG)

**Total: 18 skills**

### Arqueiro — Inicial (1)
| ID | Nome | CD | Cast | Obs |
|----|------|----|------|-----|
| `recarregar` | Recarregar | — | 1.8s | Concedida grátis ao criar; reabastece aljava com flechas da bag |

### Guerreiro — Treinador (5)
| ID | Nome | CD | Custo |
|----|------|----|-------|
| `golpe_poderoso` | Golpe Poderoso | — | 15 Raiva |
| `vitoria_iminente` | Vitória Iminente | — | carga |
| `impacto` | Impacto | 15s | — |
| `executar` | Executar | — | 10 Raiva |
| `interceptar` | Interceptar | 22s | — |

### Guerreiro — Talentos Cavaleiro (4)
| ID | Nome | CD | Desbloqueado por |
|----|------|----|-----------------|
| `golpe_debilitante` | Golpe Debilitante | — | cav_golpe_debilitante |
| `brado_provocativo` | Brado Provocativo | 45s | cav_provocacao |
| `punho_no_queixo` | Punho no Queixo | 15s | cav_punho_queixo |
| `fatiador_de_corpos` | Fatiador de Corpos | 45s | cav_fatiador |

### Mago — Treinador (5)
| ID | Nome | CD | Custo | Cast |
|----|------|----|-------|------|
| `bola_de_fogo` | Bola de Fogo | — | 25 mana | 1.5s |
| `calamidade_flamejante` | Calamidade Flamejante | — | 10 mana/s | canal 5s |
| `nova_congelante` | Nova Congelante | 6s | 10 mana | 1.0s |
| `bloco_de_gelo` | Bloco de Gelo | 45s | — | instant |
| `polimorfia` | Polimorfia | — | 10% mana | 1.5s |

### Mago — Talentos Piromania (3)
| ID | Nome | CD | Custo | Desbloqueado por |
|----|------|----|-------|-----------------|
| `escudo_fogo` | Escudo de Fogo | 20s | 25 mana | pir_escudo_fogo |
| `calcinar` | Calcinar | — | 15 mana | pir_combustao |
| `pirofagia` | Pirofagia | 90s | 75 mana | pir_pirofagia |

---

## Talentos — `talent_data.py` (TALENTS)

**Total: 33 talentos**

### Build: Cavaleiro (15 nós — guerreiro)

```
Row 0: cav_reflexos(3), cav_veterano(5), cav_vontade(1)
Row 1: cav_maquina_matar(1), cav_embalo(5), cav_sede_batalha(5)
Row 2: cav_assassino(1), cav_alvo_confirmado(5), cav_horrorizante(1)
Row 3: cav_golpe_debilitante(1)★, cav_explorador(3), cav_provocacao(1)★
Row 4: cav_foco_mortal(1), cav_punho_queixo(3)★
Row 5: cav_fatiador(1)★
```
★ = desbloqueia skill

### Build: Piromania (18 nós — mago)

```
Row 0: pir_frieza(5)
Row 1: pir_bdf_aperfeicoada(5), pir_queimaduras(5), pir_precisao_elemental(5)
Row 2: pir_escudo_fogo(1)★, pir_chama_interna(5), pir_choque_termico(1)
Row 3: pir_piromaníaco(3), pir_lapso_elemental(3), pir_exaustao(1)
Row 4: pir_calamidade(1)★, pir_crematoria(1), pir_combustao(1)★
Row 5: pir_pirofagia(1)★
```
★ = desbloqueia skill

---

## Efeitos de Status — `status_effects_data.py`

**Total: 14 efeitos**

### Debuffs de Controle
| ID | Label | Tick | Comportamento em EnemyAI |
|----|-------|------|--------------------------|
| `stun` | Atordoado | — | Imóvel, sem ataque |
| `fear` | Medo | — | Foge do player |
| `root` | Imobilizado | — | Ataca mas não move |
| `slow` | Lento | — | TileMovement.slow_mult reduzido |
| `polymorph` | Polimorfizado | 1.0s | Wander aleatório (40% velocidade) + regen HP |
| `disoriented` | Desorientado | — | Wander aleatório (mesma lógica polymorph, sem regen) |

### Debuffs de Dano (DoT)
| ID | Label | Tick | Dano por tick |
|----|-------|------|---------------|
| `poison` | Veneno | 1.0s | magnitude |
| `bleed` | Sangramento | 1.0s | magnitude |
| `burn` | Queimadura | 1.0s | magnitude |
| `exhaustion` | Exaustão | — | tracking de stacks para slow progressivo |
| `elemental_lapse` | Lapso Elemental | 1.0s | 1% HP max do MAGO (auto-burn) |

### Buffs
| ID | Label | Tick | Efeito |
|----|-------|------|--------|
| `enraged` | Enfurecido | — | mob enraivecido: +5% dano, +10% dano recebido |
| `haste` | Acelerado | — | aumenta slow_mult > 1.0 |
| `regen` | Regeneração | 1.0s | cura HP (magnitude) |

---

## Itens — `loot_tables.py` (_T dict)

**Total: ~99 itens** (loot_tables.py conta)

### Por tipo de armadura
| armor_class | Foco | Classes | Exemplos |
|-------------|------|---------|---------|
| `placa` | STR, stamina, armor | guerreiro | bone_shoulders, iron_helm, plate_armor, soul_armor |
| `couro` | crit, armor | guerreiro, arqueiro | leather_vest, hunter_vest, stalker_vest, assassin_vest |
| `tecido` | spell_power, INT | todos | worn_hood, silk_robe, arcane_robe, lich_robe |
| `""` | — | — | weapons, shields, jewelry, consumables |

### Por raridade
| Raridade | Cor UI | Faixa de nível aproximada |
|----------|--------|--------------------------|
| common | branco | 1-10 |
| uncommon | verde | 5-15 |
| rare | azul | 12-25 |
| epic | roxo | 20+ |

---

## Mobs — `mob_definitions.py`

**Total: 14 tipos**

| Nome | Raça | Classe | Ranged |
|------|------|--------|--------|
| Lobo | Fera | Warrior | Não |
| Urso | Fera | Warrior | Não |
| Aranha | Fera | Warlock | Não |
| Rato | Fera | Warrior | Não |
| Escorpião | Bestial | Hunter | Não |
| Cobra | Fera | Warlock | Não |
| Goblin | Humanoide | Warrior | Não |
| Zumbi | Morto-Vivo | Warrior | Não |
| Orc | Orc | Warrior | Não |
| Troll | Troll | Warrior | Não |
| Elfo | Humanoide | Mage | **Sim** |
| Minotauro | Bestial | Warrior | Não |
| Vampiro | Morto-Vivo | Warlock | **Sim** |
| Dragão | Dragão | Warrior | Não |

---

## Quests — `quests_data.py`

**Total: ~20 quests**

### Tipos de objetivo disponíveis
| Tipo | Parâmetros | Exemplo |
|------|-----------|---------|
| `kill` | target (nome/raça/"*"), count | Matar 10 Lobos |
| `collect_item` | target, count, loot_item, loot_chance | Coletar 5 Presas de Lobo |
| `reach_level` | count | Alcançar nível 5 |
| `reach_tile` | location (tx,ty) ou (x0,y0,x1,y1) | Chegar ao ponto X |
| `use_skill` | target (skill_id) | Usar Golpe Poderoso 3x |
| `use_consumable` | target | Usar poção |
| `talk_to_npc` | target | Falar com NPC |
| `equip_item` | target (item_name ou type) | Equipar uma arma |

---

## Receitas — `crafting_data.py`

**Materiais (10):** fragmento_ferro, fibra_madeira, tira_couro, fibra_resistente, po_de_joia, fio_de_prata, essencia_comum, essencia_arcana, joia_bruta, cristal_poder

**Receitas:** ~20 (espadas, armaduras, joias craftáveis)

---

## Mapas — `maps/`

| Arquivo | Status | Conteúdo |
|---------|--------|---------|
| `map_main.csv` | Ativo (mapa principal) | Cidade, NPCs, spawns, transições |
| `map_1.csv` | Ativo (mapa de teste) | Área de teste com mobs |
| `map_cave_east.csv` | Ativo | Caverna leste |
| `map_cave_west.csv` | Ativo | Caverna oeste |
| `map_worm_cave.csv` | Ativo | Caverna de vermes |
