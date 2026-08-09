# Benchmark contra Arquiteturas de Referência

> Documento vivo (07/08/2026). O roteiro de saneamento arquitetural de
> `PROBLEMAS_ARQUITETURA.md` §13 está **PAUSADO** até este benchmark
> terminar — decisão explícita do usuário: é difícil julgar sozinho
> "isso está certo arquiteturalmente" sem um ponto de comparação
> externo real. Ver `VISAO_PRODUTO.md` pra contexto de produto e a nota
> de pausa.

## Referências escolhidas

- **Veloren** — RPG multiplayer voxel, Rust, ECS real via `specs`,
  GPL-3.0, ativo. `book.veloren.net` (manual oficial) +
  `deepwiki.com/veloren/veloren` (wiki auto-gerada). Referência de
  **FORMA**: como organizar componentes/sistemas, separação
  client/server/common. Fraco/imaturo em quest system (issue aberta,
  não construído) e em "dungeons instanciadas por grupo" (as dele são
  no mundo compartilhado) — não usar como referência nesses 2 pontos
  especificamente.
- **AzerothCore** — emulador de servidor WoW 3.3.5a, C++, NÃO é ECS
  (hierarquia OOP `Unit`→`Player`/`Creature`), GPL-2.0, ativo — "escolha
  mais popular de 2026" pra esse tipo de projeto.
  `deepwiki.com/azerothcore/azerothcore-wotlk`. Referência de
  **CONTEÚDO**: como modelar quest chains, instância/dungeon/raid,
  árvore de talento REAL do WoW. Traduzir o MODELO conceitual pra ECS,
  nunca copiar a forma OOP.

## Metodologia (repetida por sistema)

1. Resumo de como Veloren e/ou AzerothCore resolve o problema (o mais
   relevante pro sistema em questão) — componentes/dados, sistemas,
   relações. Fonte citada.
2. Resumo equivalente do nosso sistema — arquivo:linha real.
3. Comparação — achados concretos, não opinião vaga.
4. Veredito: manter como está / ajustar (menor) / candidato a débito
   arquitetural real (entra em `PROBLEMAS_ARQUITETURA.md` se for grande).

## Inventário e progresso

**Cluster A — Núcleo de combate**
- [x] A.1 Skills/Habilidades
- [x] A.2 Talentos
- [x] A.3 Skill Level
- [x] A.4 Combate core (pipeline de dano)
- [x] A.5 IA de mobs

**Cluster C — Modos sociais/competitivos**
- [x] C.1 PvP zona (mundo aberto)
- [x] C.2 Duelo (mundo aberto)
- [x] C.3 Arena 2x2 (instanciado)
- [x] C.4 Battleground MOBA (instanciado)
- [x] C.5 Progressão normalizada de instância

**Cluster B — Conteúdo/progressão**
- [x] B.1 Quests
- [x] B.2 Itens/Equipamento/Inventário
- [x] B.3 Economia/loja
- [x] B.4 Crafting

**Cluster D — Suporte social**
- [x] D.1 Party/Grupo
- [x] D.2 Trade
- [x] D.3 Chat
- [x] D.4 Facções

**Cluster E — Infraestrutura** (avaliar depois se vale entrar)
- [ ] E.1 AOI/rede/sessão
- [ ] E.2 Save/persistência
- [ ] E.3 NPCs de suporte
- [ ] E.4 Loot
- [ ] E.5 Estatísticas de personagem
- [ ] E.6 Morte/respawn/fantasma

---

## A.1 — Skills/Habilidades

### Veloren (fonte: `book.veloren.net/contributors/guides/adding-weapon-skills/`, DeepWiki `3.2-character-states`/`3.3-character-abilities-and-combat`)

Estrutura em 3 camadas, todas em `common/` (compartilhado client+server):

1. **Dado** — arquivos RON (`basic_guard.ron`, `flamethrower.ron`, ...)
   definem parâmetros de cada habilidade. `common/src/comp/inventory/
   item/tool.rs` associa vetor de habilidades a cada arma (1ª = M1, 2ª =
   M2, demais = slots da barra).
2. **Estado unificado** — componente único `CharacterState` (enum com
   40+ variantes: Idle, Wielding, DashMelee, ChargedMelee, ...) por
   entidade,é A ÚNICA fonte de "o que esse personagem está fazendo
   agora". Toda habilidade de ação segue o MESMO ciclo de fases,
   embutido no próprio state machine: **Buildup → Charge (opcional) →
   Action → Recover**. Um sistema só (`character_behavior`) dirige
   TODAS as habilidades: coleta componentes → `JoinData` (snapshot
   imutável) → chama o método de comportamento do estado ATUAL → aplica
   `StateUpdate` de volta nos componentes.
3. **Efeito** — na fase Action, cria um objeto `Attack` (dano/
   knockback/combo); sistemas especializados (melee/projectile/beam/
   shockwave) processam por TIPO de habilidade, não por habilidade
   individual.

Cliente prediz localmente (ativação + fases), servidor é autoritativo
pro resultado — mesma filosofia geral que a nossa. Adicionar habilidade
nova = (a) arquivo RON de dado, (b) expor via `AbilitySet` da arma, (c)
SÓ cria uma variante NOVA de `CharacterState` se o mecanismo for
genuinamente inédito (a maioria reusa fases já existentes).

### Nosso projeto

- **Dado**: `content/skill_config.py::SKILL_CATALOG` — equivalente
  direto aos RON do Veloren, já data-driven, ponto único de verdade
  (bem alinhado).
- **Estado**: NÃO existe um componente único "o que este personagem
  está fazendo agora". Cada mecanismo inventou o SEU PRÓPRIO
  componente de estado, sem fases em comum: `SpellCast` (cast bar),
  `Channeling` (canalização), `PirofagiaAiming` (mira de cone),
  `AoeTargeting` (mira de área), `IceBlockEffect`, `FireShieldEffect` —
  todos em `engine/components.py`, cada um com seus próprios campos de
  timer/fase reinventados do zero. `Skill` (por slot, em
  `PlayerSkills.skills[]`) guarda cooldown/cargas por habilidade
  individual, sem um conceito de "fase" (buildup/charge/action/
  recover) compartilhado entre skills.
- **Dispatch/execução**: `_skill_<id>` por habilidade em
  `ui/skill_handlers.py`, despachado por `getattr(self._skill_system,
  f"_skill_{sid}")` — TANTO no cliente (predição/visual) QUANTO no
  servidor (`server/skill_processor.py:314`, autoritativo) — já
  catalogado como item B3 (débito arquitetural, `PROBLEMAS_ARQUITETURA.md`
  §11/§12). Cada handler implementa sua PRÓPRIA lógica de fases quando
  precisa (ex: cast time, canalização), duplicando o conceito de
  "buildup"/"action" que o Veloren já tem embutido no state machine.

### Comparação

| Aspecto | Veloren | Nosso projeto |
|---|---|---|
| Dado da habilidade | RON, por arma/slot | `SKILL_CATALOG`, dict Python — equivalente, OK |
| Estado "o que estou fazendo" | 1 componente único (`CharacterState`), fases embutidas | Espalhado em 6+ componentes ad-hoc, sem fases em comum |
| Dispatch de execução | 1 sistema (`character_behavior`) + sistemas especializados por TIPO de efeito | 1 método Python por skill_id (`_skill_<id>`), getattr, reusado client+server (B3) |
| Adicionar skill nova | Dado + (raramente) variante de estado | Sempre: `SKILL_CATALOG` + handler novo do zero — reinventa fase se precisar |
| Separação client/server | `common/` de propósito, servidor nunca importa o crate de render (`voxygen`) | Servidor importa `ui.systems.SkillSystem` (`world_server.py:478`) — estruturalmente impossível no Veloren, aqui é debt real |

### Achados

1. **A causa raiz do débito B2/B3 (handlers duplicados + getattr) tem
   uma forma concreta de resolução, não só "unificar em 1 handler"**:
   Veloren mostra que o padrão certo é um **componente de estado único
   com fases padronizadas** (Buildup/Charge/Action/Recover), não
   simplesmente mover o código pra um lugar compartilhado mantendo
   "1 função por skill". Isso muda o desenho da Fase 3 do roteiro
   pausado — vale reconsiderar um `ActionState`/fases genéricas em vez
   de só um dict `SKILL_HANDLERS: dict[str, callable]` como o plano
   original propunha.
2. **Confirma (não descobre) o B3**: a separação `common/` vs `voxygen`
   do Veloren é estrutural (Cargo workspace — o crate do servidor
   fisicamente não pode importar o crate de render). Nosso equivalente
   (`server/` nunca importar `ui/`) é só uma REGRA de convenção, sem
   reforço estrutural — é por isso que `world_server.py:478` conseguiu
   quebrar a regra sem nada travar na hora.
3. **Cooldown/GCD por skill individual (nosso) vs fases compartilhadas
   (Veloren)**: não é claramente pior — WoW/Tibia também têm cooldown
   por skill + GCD global, e isso já é o modelo que o usuário quer
   (não é uma fórmula 1:1 de Veloren). Registrar como "não é um
   problema", não forçar mudança só por diferença.

**Veredito**: candidato a débito arquitetural REAL — o achado 1 muda
como a Fase 3 do roteiro pausado deveria ser desenhada. Registrar em
`PROBLEMAS_ARQUITETURA.md` quando o benchmark fechar (não abrir agora,
pra não fragmentar o rastro — populado no fim, junto da síntese).

---

## A.2 — Talentos

### AzerothCore (fonte: `azerothcore.org/wiki/character_talent`, DeepWiki `3.1-spell-management-and-information`, GitHub `SpellMgr.cpp`/`Player.cpp`)

Modelo radicalmente mais simples do que se imagina: **um talento é,
na prática, só um SPELL**. `character_talent` (tabela de persistência)
tem 3 colunas: `guid`, `spell` (o ID do spell que o talento
concede/sobe de rank), `specMask` (bitmask — 1/2/3 — qual das DUAS
specs salvas do personagem tem esse talento; WoW real suporta 2 builds
por personagem, trocáveis fora de combate). O SERVIDOR não guarda a
FORMA da árvore (posição/coluna/linha/ícone) — isso vive só no cliente,
em dado estático do jogo (Talent.dbc) que o servidor nunca precisa
tocar. Aprender um talento = aplicar `SPELL_EFFECT_LEARN_SPELL` (efeito
GENÉRICO de spell, o mesmo mecanismo que qualquer spell usa pra
conceder outro spell) — não existe um "if talent_id == X" em lugar
nenhum; é tudo resolvido por `SpellMgr` genericamente via
`SpellModifier`/`spell_ranks`.

### Nosso projeto

- `TalentTree(chosen_build, allocated{}, available_points, ...)` —
  UM build por personagem (sem dual-spec).
- `content/talent_data.py::TALENTS` — definimos a FORMA da árvore
  também (posição/coluna/linha, porque diferente do WoW não temos um
  "cliente com dado de jogo pré-instalado" separado do nosso próprio
  código — não há um "Talent.dbc" nosso, e não faria sentido criar um
  só pra imitar isso).
- Efeito de talento: `ui/talent_system.py::apply_talent_effects()` —
  a MAIORIA dos ~25 talentos por build aplica um FLAG NOMEADO
  individual direto em `CombatStats` (`fire_burns_on_crit`,
  `pnq_enabled`, `interceptar_rage_bonus`, ...) — já catalogado como
  item A1 (`PROBLEMAS_ARQUITETURA.md` §11, "componente-deus").
  `unlocks_skill` (concede skill, não flag) já segue o espírito
  "talento = spell/skill genérico", mas é minoria dos casos.

### Comparação

| Aspecto | AzerothCore | Nosso projeto |
|---|---|---|
| O que um talento concede | Spell genérico (`SPELL_EFFECT_LEARN_SPELL`, mecanismo compartilhado) | Skill (poucos casos, genérico) OU flag nomeado em `CombatStats` (maioria) |
| Forma da árvore | Client-only, dado estático (DBC) — servidor não sabe/não precisa | Servidor E cliente compartilham `talent_data.py` — necessário, não temos DBC próprio |
| Validação de orçamento | Server-side, genérica via sistema de spell | Server-side, `validate_talent_allocation` — já alinhado |
| Specs salvas por personagem | 2 (troca fora de combate) | 1 |

### Achados

1. **Reforça o item A1 já catalogado, com uma resposta concreta**: a
   razão de `CombatStats` ter ~25 flags nomeadas não é falta de opção
   — AzerothCore mostra que dá pra fazer TODO talento conceder algo
   genérico (spell/modifier) em vez de um flag bespoke por talento.
   Isso é 100% backend (não muda a EXPERIÊNCIA do jogador, só como o
   efeito é implementado por baixo) — fortalece a recomendação já
   existente do A1 ("flags de talento viram componente por build ou
   dict `talent_flags` genérico"), sem precisar de decisão do usuário.
2. **Dual-spec (2 builds salvos, troca fora de combate) é uma
   FEATURE que muda a experiência do jogador** — não existe hoje no
   nosso projeto. Não é um "problema arquitetural" a corrigir, é uma
   possível melhoria de produto. Trago pra decisão conjunta, não
   assumo.
3. **A diferença "forma da árvore vive no servidor" não é um erro
   nosso** — é uma consequência de não termos (nem fazer sentido ter)
   um datapack de cliente separado como o WoW tem. Registrar
   explicitamente como "diferença justificada", pra ninguém tentar
   "corrigir" isso numa sessão futura sem contexto.

**Veredito**: achado 1 é candidato real a `PROBLEMAS_ARQUITETURA.md`
(reforço do A1, populado na síntese final). Achado 2 fica registrado
aqui como pergunta em aberto pro usuário, não decidido.

---

**DECISÃO DO USUÁRIO (07/08/2026)**: sim, quer múltiplas builds
salvas por personagem — hoje só existe 1 build por classe (conteúdo,
não arquitetura), mas o plano é ter 3 no futuro. Quando essas 3
existirem, o personagem deve poder SALVAR as 3 (não realocar do zero
toda vez) e USAR só 1 por vez, trocando apenas fora de combate — mesmo
modelo do WoW/AzerothCore (specMask). **Não implementar agora** — é
registro de rumo pro roteiro/backlog, ver `VISAO_PRODUTO.md`.
Implicações arquiteturais de quando isso for feito: `TalentTree`
precisaria guardar N alocações (uma por build salva) em vez de 1
`allocated{}` único, e um ponteiro pra qual está ATIVA — o
`chosen_build` de `talent_data.py` já é o conceito de "qual build", só
falta o "salvar mais de uma alocação por build" quando a hora chegar.

---

## A.3 — Skill Level (progressão por uso, estilo Tibia)

### AzerothCore (fonte: `azerothcore.org/wiki/character_skills`, `wowhead.com/wotlk/skills/weapon-skills`)

WoW real TEM progressão de skill de arma por uso (herança da era
clássica, ainda ativa em WotLK, menos visível pro jogador mas funcional
— `character_skills`: `guid, skill, value, max`). `value` sobe em
incrementos de +1 por chance de acerto bem-sucedida em combate, `max`
= 5×level (teto rígido por nível). Sem curva de XP intermediária —
é um contador discreto simples, sem "progresso até o próximo ponto".

### Nosso projeto

`SkillLevels(levels{}, xp{})` — MAIS granular que o WoW real: além do
`levels[skill_id]` (0-200, equivalente ao `value`), guardamos
`xp[skill_id]` (progresso fracionário até o próximo nível, curva
`skill_xp_for_level(level) = 20×(level+1)^1.2`), dando feedback de
progresso contínuo (barra de XP visível) em vez de só "subiu ou não".
Fonte única (`stats_system.grant_skill_xp`, só servidor escreve),
sync via dirty-check (`_sync_player_skill_levels_dirty`) — já
centralizado, sem duplicação.

### Comparação

| Aspecto | AzerothCore | Nosso projeto |
|---|---|---|
| Granularidade | Inteiro discreto, +1 por sucesso | Inteiro (0-200) + XP fracionário até o próximo ponto |
| Teto | 5×level (varia com o level) | 200 fixo, independente de level |
| Fonte de verdade | Server (tabela por char) | Server (`SkillLevels`, único escritor) |
| Feedback ao jogador | Só o número muda | Barra de progresso (`skill_level_ui.py`) |

### Achados

Nenhum problema encontrado — nosso sistema é estruturalmente **mais
simples de manter** (1 componente, 1 função de escrita, já sem
duplicação) e **mais rico pro jogador** (progresso fracionário visível)
do que o equivalente real do WoW. Item já catalogado como "o que está
genuinamente bem" na auditoria de 15/07 (`PROBLEMAS_ARQUITETURA.md` §11)
— o benchmark CONFIRMA isso com um ponto de comparação externo real,
não muda nada.

**Veredito**: manter como está. Nenhuma entrada nova em
`PROBLEMAS_ARQUITETURA.md`.

---

## A.4 — Combate core (pipeline de dano)

### Referências (fontes: `docs.veloren.net/veloren_common/combat/`, DeepWiki AzerothCore `2.1-unit-and-player-system` + issues públicas sobre `Unit::DealDamage`)

- **Veloren**: `Attack` (vetor de `AttackDamage` + `AttackEffect` +
  `precision_multiplier`) é o objeto que representa um golpe RESOLVIDO
  — cálculo de armadura/resiliência é feito por funções genéricas do
  módulo `combat::`, aplicadas a QUALQUER entidade (sem "if for
  jogador, if for mob"). `HealthChange` (o delta final de HP) carrega
  `instance_id` — agrupa múltiplos hits da MESMA ação (ex: golpe em
  área acertando vários alvos) sob uma origem comum.
- **AzerothCore**: `Unit::DealDamage` é o ÚNICO ponto que decrementa HP
  por dano não-DoT — confirmado por issues públicas do próprio projeto
  discutindo mudanças nesse método. Imunidade a mecânica/escola de
  dano é DADO, não código: tabela `creature_immunities` (por
  criatura, por spell school/mechanic), consultada genericamente —
  não é um `if creature.id == X: return` espalhado pelo código.

### Nosso projeto

`engine/core_systems.py::apply_damage_core()` — já é exatamente o
choke-point único que CLAUDE.md manda usar ("Escrita final de dano em
HP... guards imune/morto, overkill preservado"). Guards de
imune/morto são CÓDIGO dentro da função (`if cs.current_hp <= 0`,
`if cst.is_immune`), não uma tabela de dado consultada — imunidade
hoje é só um FLAG booleano genérico (`CombatState.is_immune`, usado
por Bloco de Gelo etc.), não "este mob específico é imune a
poison/stun mas não a fogo".

### Comparação

| Aspecto | Veloren / AzerothCore | Nosso projeto |
|---|---|---|
| Choke-point de escrita de HP | 1 função/método único (`Unit::DealDamage`) | 1 função única (`apply_damage_core`) — JÁ alinhado |
| Redução de armadura/resistência | Funções genéricas, mesma pra todos | `damage_calculator.py::apply_resistance_reduction`, genérica por escola — já alinhado |
| Imunidade | DADO (tabela por criatura×mecânica) | CÓDIGO (flag booleano genérico, sem granularidade por mecânica/escola) |
| Agrupar hits da mesma ação (AoE) | `HealthChange.instance_id` | Não identificado equivalente — não investigado a fundo |

### Achados

1. **O choke-point já está certo** — `apply_damage_core` é
   estruturalmente equivalente ao `DealDamage` do AzerothCore e ao
   fluxo `Attack`→`HealthChange` do Veloren. Nenhuma mudança
   necessária aqui, é validação do que já existe.
2. **Imunidade granular por mecânica/escola, hoje inexistente, pode
   valer a pena como TABELA DE DADO** (ex: um mob "imune a stun mas
   não a poison") em vez de um único flag booleano genérico — mas só
   vale a pena se algum design de conteúdo FUTURO precisar disso
   (nenhum mob hoje precisa). Registrar como ideia latente, não como
   débito — implementar sem um caso de uso real seria over-engineering,
   contra o próprio princípio "sem código morto" do projeto.
3. Agrupamento de hits por instância de ataque (Veloren) não foi
   investigado se já temos equivalente — baixo valor, não aprofundado
   agora (nada indica que isso seja hoje um problema real, ex: XP
   split de dano por mob já funciona via `damage_log`).

**Veredito**: nenhuma entrada nova em `PROBLEMAS_ARQUITETURA.md` —
achado 1 é validação, achado 2 é ideia latente sem urgência (registrar
aqui mesmo, não abrir item de débito sem caso de uso).

---

## A.5 — IA de mobs

### Veloren (fonte: `veloren_rtsim` docs, devblogs 111/134)

Servidor é autoritativo pra física/IA/persistência (mesma filosofia
nossa). Dois níveis de simulação: **`rtsim`** — simulação de ALTO
NÍVEL/baixa resolução do mundo INTEIRO (inclusive fora de área
carregada), cada NPC com um "Brain" guardando destino/memórias;
entidades rtsim FORA da visão de qualquer player atualizam a cada ~30
ticks (~1x/segundo), entidades PERTO de players atualizam todo tick
(sem diferença perceptível pro jogador). Pathfinding local via A*
(mesma família de algoritmo que a nossa).

### Nosso projeto

`EnemyAISystem` — state machine por mob (IDLE/AGGRO_DELAY/CHASING/
RETURNING/ATTACKING, +KITING em `EnemyAbilitySystem`), `SLEEP_RADIUS_
TILES=40` de aggro, `MAX_PATHFINDS=4`/tick (A*, mesmo algoritmo). JÁ
temos throttle por tier de atividade — pesquisa de escala registrada em
`ARQUITETURA_ONLINE.md` §34.74.37-45 (Fases 1-4: throttle de
reavaliação em IDLE, depois em CHASING/FIGHTING, mais agressivo pra
mobs longe de qualquer player) — mesmo PRINCÍPIO do rtsim (menos
frequência de update pra quem está longe/irrelevante pro jogador),
convergido independentemente antes deste benchmark existir.

### Comparação

| Aspecto | Veloren | Nosso projeto |
|---|---|---|
| Autoridade | Servidor | Servidor — igual |
| Pathfinding | A*, orçado por frame | A*, orçado por tick (`MAX_PATHFINDS`) — igual |
| Throttle por relevância (longe do player = atualiza menos) | Sim, 2 níveis (rtsim vs loaded) | Sim, já implementado (§34.74.37-45) — já convergimos pro mesmo padrão |
| Simulação de mundo INTEIRO mesmo sem player por perto | Sim (rtsim, todo o mapa) | Não — nosso `ACTIVATION_RADIUS=999999` desativa culling por distância, mas não simula NADA fora do que já processamos normalmente (não há um nível "macro" de simulação de NPCs longe de todo mundo) |

### Achados

1. **Validação forte**: o throttle por tier de atividade que já
   implementamos (Fases 1-4 da pesquisa de escala) é o MESMO princípio
   arquitetural do `rtsim` do Veloren — não é coincidência, é a solução
   natural pro mesmo problema (custo de IA escalando com número de
   mobs). Nenhuma mudança necessária, só reforça que a decisão já
   tomada estava certa.
2. **Simulação "macro" de NPCs fora de área ativa** (rtsim simula o
   mundo INTEIRO, sempre, mesmo remoto) não existe no nosso projeto —
   mas também não faz sentido pro NOSSO escopo hoje: Veloren precisa
   disso pra um mundo persistente com economia/NPCs civis simulados
   (contexto de jogo diferente do nosso, que é mais combat-focused,
   sem simulação social de NPC). Registrar como "não aplicável ao
   nosso design", não como gap.

**Veredito**: nenhuma entrada nova em `PROBLEMAS_ARQUITETURA.md` —
Cluster A fecha aqui com 1 achado real (A.1/A.2, ambos já registrados),
2 validações fortes (A.3, A.5) e 1 validação com ideia latente (A.4).

---

## C.1 — PvP zona (mundo aberto) & C.2 — Duelo (mundo aberto)

Pesquisa mais rasa aqui de propósito — são sistemas de flag/estado, não
arquitetura rica o suficiente pra render muito material novo em
nenhuma das 2 referências.

### AzerothCore

PvP em mundo aberto no WoW real é resolvido por FLAG (`UNIT_FIELD_
FLAGS` PvP bit) + zonas de santuário onde combate é bloqueado — não
achei arquitetura documentada além disso na pesquisa. Duelo: existe um
módulo COMUNITÁRIO opcional (`mod-duel-reset`) especificamente porque o
comportamento PADRÃO do WoW real NÃO reseta HP/mana/cooldown ao entrar
em duelo — duelar com recursos já gastos é o comportamento base, reset
é extra opcional.

### Nosso projeto

`pvp_zone_processor.py` (`PvpZoneProcessorMixin`) — zona retangular por
mapa, stateless, "solo=hostil, grupo=exceção", consultado por
`_pvp_allowed_between`. `duel_processor.py` — golpe letal SEMPRE vira
1 HP (nunca mata de verdade), interceptor de dano letal centralizado
(`register_lethal_interceptor`).

### Comparação e achados

1. Nosso duelo "nunca mata de verdade" é uma escolha de design MAIS
   casual/segura que o padrão WoW real (que deixa o perdedor morto se
   ninguém interceptar) — não é melhor nem pior, é INTENCIONAL e já
   documentado. Nenhuma mudança.
2. Zona PvP por retângulo simples é suficiente pro nosso caso (zona
   PEQUENA e CONTIDA, decisão do usuário em `VISAO_PRODUTO.md`) — WoW
   real precisa de zonas de santuário complexas por causa da escala do
   mundo; não é um padrão que precisamos importar.

**Veredito**: nenhuma entrada nova em `PROBLEMAS_ARQUITETURA.md` —
ambos já bem dimensionados pro nosso escopo.

---

## C.3 — Arena 2x2 & C.4 — Battleground MOBA & C.5 — Progressão normalizada de instância

Tratados juntos — são a mesma FAMÍLIA de sistema (conteúdo instanciado
competitivo) e é onde a maioria dos bugs desta sessão aconteceu.

### AzerothCore (fonte: DeepWiki `8-game-systems-and-commands`, `Map.h`)

Estrutura em camadas bem separadas:
- **`Map`** — container espacial genérico, serve continente, dungeon,
  battleground E arena igualmente (mesma classe base).
- **`InstanceScript`** (dungeons/raids) — gerencia estado de encontro
  (`SetBossState`, portas, spawn de minions), delega mecânica
  individual pra `BossAI` de cada chefe. Progressão SALVA em banco via
  `InstanceSaveMgr`/`InstanceSave` — grupo pode sair e voltar
  mantendo o progresso.
- **`Battleground`** (classe BASE, DIFERENTE de `InstanceScript`) —
  arenas/BGs específicos (Ring of Valor, Dalaran Sewers, ...) herdam
  dela e só implementam a MECÂNICA própria (estado de portão/fogo/
  pilar, ou empurrão de cachoeira via `TaskScheduler`) — fila,
  pontuação, início/fim de partida são responsabilidade da base
  COMPARTILHADA, escrita 1 vez.

### Nosso projeto

`match_processor.py` (Arena) e `bg_queue_processor.py` (BG) COMPARTILHAM
a camada de baixo (`WorldServer._load_instance`/`_unload_instance` —
equivalente ao `Map` genérico do AzerothCore, bom alinhamento) — mas
CADA UM reimplementa, de forma paralela e independente, a fila/entrada/
saída de partida: `request_arena_queue_join`/`request_arena_queue_leave`/
`_arena_leave_now` (`match_processor.py`) vs `request_bg_queue_join`/
`request_bg_queue_leave`/`request_bg_leave` (`bg_queue_processor.py`) —
métodos com nomes espelhados, mesma FORMA de problema resolvida 2x.
Não existe uma camada compartilhada tipo `Battleground` base do
AzerothCore — cada modo tem sua própria fila/matchmaking/lifecycle do
zero. `instance_progression.py` (level/talento/skill/gold/itens
"normalizados" dentro da instância) é conceitualmente PARECIDO com o
`InstanceSave`/progresso salvo do AzerothCore, mas resolve um problema
diferente (nivelamento pra competição justa, não progresso de PvE
persistente entre sessões).

### Comparação

| Aspecto | AzerothCore | Nosso projeto |
|---|---|---|
| Container espacial genérico | `Map` (1 classe, todo tipo de instância) | `_load_instance`/`_unload_instance` (1 mecanismo, mesma ideia) — já alinhado |
| Lógica de fila/lifecycle de partida PvP | `Battleground` base COMPARTILHADA, subclasses só adicionam mecânica | Reimplementada 2x, paralela, sem base comum (`match_processor.py` vs `bg_queue_processor.py`) |
| Progresso de dungeon/raid salvo | `InstanceSaveMgr`, persistente entre sessões | N/A — não temos dungeon PvE instanciado ainda, só Arena/BG competitivos |
| Nivelamento/normalização de stats pra instância | Não é um conceito do WoW real (gear conta pra tudo) | `instance_progression.py` — conceito NOSSO, sem equivalente direto |

### Achados

1. **Débito arquitetural real, ainda não catalogado em nenhuma
   auditoria anterior**: fila/matchmaking/lifecycle de partida
   duplicados entre Arena e Battleground é candidato a um item novo
   tipo B2/B3 (duplicação client/servidor), só que aqui é duplicação
   ENTRE MODOS. Uma base compartilhada (`InstancedMatchMixin` ou
   similar — fila genérica, entrada/saída genérica, cada modo só
   define o QUE acontece dentro) seguiria o padrão `Battleground` do
   AzerothCore. Isso é 100% backend — não muda a experiência de fila
   pro jogador, só como o código está organizado por baixo. Vale
   registrar como item novo em `PROBLEMAS_ARQUITETURA.md` na síntese
   final.
2. **Quando o roadmap tiver dungeon PvE instanciado de verdade**
   (mencionado como possível modalidade futura), o padrão
   `InstanceSaveMgr` (progresso de encontro salvo, retomável) é um
   conceito que hoje não existe no nosso projeto — não é urgente
   agora (não temos esse conteúdo ainda), mas vale ter em mente
   quando o momento chegar. Registrar como nota, não como pergunta
   agora (não é uma decisão que precise ser tomada hoje).

**Veredito**: achado 1 é candidato REAL a `PROBLEMAS_ARQUITETURA.md`
(populado na síntese final, junto com A.1/A.2). Achado 2 é nota de
roadmap, não ação imediata.

---

## B.1 — Quests

### AzerothCore (fonte: DeepWiki `7.3-quest-system`)

`quest_template` (dado estático) + estado por-player em MAPA
`QuestStatusData` (quest_id → progresso), carregado no login — MESMA
forma que a nossa. Reset de quest diária/semanal/mensal/sazonal tem
TABELAS DEDICADAS (`character_queststatus_daily`/`_weekly`/`_monthly`/
`_seasonal`) — conceito que não existe no nosso sistema hoje. Crédito
de kill objective em grupo é centralizado (`KillRewarder`), dividido
entre membros — mesmo espírito do nosso split de XP por proximidade
(`party_processor.py`). Cadeia/pré-requisito filtra a OFERTA da quest
(não aparece no gossip se não cumprido) — igual ao nosso
`QuestDialogSystem._get_available_quests`/`_get_locked_quests`.

### Nosso projeto

`quest_logic.py` (puro, sem Pygame, usado client+server) +
`QuestLog(active{qid→progresso[]}, completed{qid})` — mesma forma de
estado por-player. `QuestDef.class_req`/`requires`/`next_quest` já
cobrem restrição de classe e encadeamento. Sem conceito de quest
diária/semanal/repetível hoje.

### Comparação e achados

1. **Forma já muito alinhada** — estado por-player em mapa, validação
   server-side, cadeia por pré-requisito filtrando oferta, split de
   crédito em grupo: tudo já bate com o padrão AzerothCore. Nenhuma
   mudança de arquitetura necessária.
2. **Quest diária/repetível é uma FEATURE que muda a experiência do
   jogador** (não existe hoje) — não decido sozinho, registro como
   pergunta.

**Veredito**: nenhuma entrada nova em `PROBLEMAS_ARQUITETURA.md` —
sistema já bem alinhado. Achado 2 vira pergunta abaixo.

**PERGUNTA PRO USUÁRIO**: quer quests diárias/repetíveis no roadmap
(ex: "mate 10 lobos, resetável a cada dia", comum em MMORPGs pra dar
motivo de logar todo dia)? Só registro de intenção, não implementar
agora.

---

## B.2 — Itens/Equipamento/Inventário & B.3 — Economia/loja & B.4 — Crafting

Tratados juntos — mesma família de dado (`content/item_table.py`
alimenta os 3).

### AzerothCore (fonte: `azerothcore.org/wiki/item_template`, `npc_vendor`)

Identidade do item é um **ID numérico estável** (`item_template.entry`)
— nome é só exibição, pode mudar sem quebrar nada salvo. `npc_vendor`
liga Creature ID + Item ID + slot — vendedor é só uma LISTA de
referências ao catálogo central, igual ao conceito que já
consolidamos.

### Nosso projeto

`item_table.py::ITEMS` já é catálogo único (consolidado 07/07/2026,
loot/loja/forja todos referenciam daqui — bom alinhamento
estrutural). MAS identidade ainda é o **NOME** (string) — já
catalogado como item **C2** em `PROBLEMAS_ARQUITETURA.md` §11
("Identidade de item é o nome... renomear item no catálogo quebra
saves existentes silenciosamente").

### Comparação e achados

1. **Reforça o item C2 já catalogado, com validação externa direta**:
   AzerothCore confirma que "ID numérico estável, nome só pra exibir"
   é o padrão maduro — exatamente o que o C2 já recomendava. 100%
   backend, não muda a experiência do jogador (o nome exibido continua
   igual). Não é achado novo, é reforço de prioridade — talvez o C2
   mereça subir de prioridade no roteiro pausado dado que agora tem
   confirmação externa.
2. **Loja/crafting já seguem o padrão certo** (referência ao catálogo
   central, sem duplicação de dado) — nenhuma mudança.

**Veredito**: nenhuma entrada NOVA (C2 já existe) — mas achado 1 é
argumento pra revisar a prioridade do C2 na síntese final.

---

## D.1 — Party/Grupo & D.2 — Trade & D.3 — Chat & D.4 — Facções

Tratados juntos — sistemas de suporte, pesquisa focada no que rende
achado real (loot de grupo), leve no resto.

### AzerothCore (fonte: DeepWiki `7.1-group-and-lfg-management`)

`Group` — membros via `MemberSlot` (guid/nome/papel), várias
`LootMethod` SELECIONÁVEIS pelo líder: Free-for-all, Round Robin,
Master Loot (e Need-before-Greed, não detalhado na busca mas padrão
conhecido do jogo real). Reputação (`ReputationMgr`) é sistema
separado de facção de COMBATE — no WoW real, facção também rege loja/
acesso, não só hostilidade.

### Nosso projeto

`party_processor.py` (`PartyProcessorMixin`) — grupo N-ário, convite,
líder, XP compartilhado por proximidade. `server/loot_processor.py::
request_loot` — regra ÚNICA fixa: dono do kill OU mesmo grupo pode
sacar, sem modo selecionável. `faction_system.py`/`faction_data.py` —
só rege hostilidade (`get_relationship`), não temos reputação
acumulável nem ela afetando preço de loja/acesso a conteúdo. Trade e
Chat são utilitários bem contidos, sem achado arquitetural relevante
nas duas referências pra esse escopo.

### Comparação e achados

1. **Loot de grupo é regra única, sem modo selecionável** — WoW real
   deixa o líder escolher entre vários métodos. Isso MUDA a
   experiência do jogador se adicionado — não decido, viro pergunta.
2. **Facção sem reputação acumulável** (só hostilidade binária/por
   tier) — reputação afetando loja/desconto/acesso é um sistema de
   PROGRESSÃO que muda a experiência — também vira pergunta, não
   decisão minha.
3. Trade e Chat: nenhum achado arquitetural — já são utilitários bem
   dimensionados pro escopo atual.

**Veredito**: nenhuma entrada nova em `PROBLEMAS_ARQUITETURA.md` — os
2 achados são perguntas de produto, não débito técnico.

**PERGUNTAS PRO USUÁRIO**:
- Quer modo de loot selecionável (Round Robin, Master Loot, etc.) em
  vez da regra única atual (dono/grupo pode sacar)? Só registro de
  intenção.
- Quer sistema de reputação por facção (acumulável, afeta loja/acesso)
  além da hostilidade atual? Também só registro, não é decisão
  urgente.

---

## Síntese final (passo 6 do pedido original)

19 sistemas revisados (Clusters A/B/C/D — E ficou de fora por baixo
valor comparativo, como o plano previa). Resultado, separado por tipo:

### Achados arquiteturais reais (100% backend, entram em `PROBLEMAS_ARQUITETURA.md`)

1. **Novo item — duplicação de fila/lifecycle entre Arena e
   Battleground** (C.3/C.4/C.5): `match_processor.py` e
   `bg_queue_processor.py` reimplementam, cada um do zero, a mesma
   forma de fila→instância→partida→saída, com métodos espelhados
   (`request_arena_queue_join`/`request_bg_queue_join`, etc.). Achado
   NOVO, não estava em nenhuma auditoria anterior.
2. **Reforço do B2/B3 (já conhecidos) — forma concreta de resolução**
   (A.1): a causa raiz não é só "handlers duplicados", é FALTA de um
   componente de estado único com fases padronizadas
   (Buildup/Charge/Action/Recover, modelo Veloren). Muda o DESENHO da
   Fase 3 do roteiro pausado, não só a prioridade.
3. **Reforço do A1 (já conhecido)** (A.2): flags de talento
   nomeados em `CombatStats` têm alternativa provada (modificador
   genérico via efeito de spell, modelo AzerothCore).
4. **Reforço do C2 (já conhecido), argumento pra subir prioridade**
   (B.2-B.4): identidade de item por nome (não ID estável) é
   confirmada como o padrão "errado" por comparação externa direta.

### Validações (nenhuma mudança necessária — já fazemos certo)

Skill Level (A.3), throttle de IA por proximidade (A.5), pipeline de
dano/choke-point único (A.4), zona PvP/Duelo (C.1/C.2), forma de
quest/estado por-player (B.1), catálogo único de item (B.2-B.4),
Trade/Chat (D). Vale registrar que a MAIORIA dos sistemas comparados
validou o que já existe — o projeto não está "errado" de forma ampla,
os problemas são pontuais e já eram, em boa parte, conhecidos.

### Perguntas de produto levantadas (aguardando resposta, sem decisão minha)

- ✅ Multi-spec de talento (3 builds salvas, 1 ativa por vez, troca
  fora de combate) — RESPONDIDO, registrado em `VISAO_PRODUTO.md`.
- Quests diárias/repetíveis — em aberto.
- Modo de loot de grupo selecionável (Round Robin/Master Loot/etc) —
  em aberto.
- Sistema de reputação por facção (acumulável, afeta loja/acesso) —
  em aberto.

### Proposta de nova estratégia (mantendo os pontos já decididos: nunca decidir sozinho em conflito ECS, teste caso a caso, visão antes de conteúdo)

O roteiro de 8 fases pausado (`PROBLEMAS_ARQUITETURA.md` §13) precisa
de 2 ajustes antes de retomar:

1. **Fase 3 (B2+B3, handler headless de skill) muda de forma**: em vez
   de só um `SKILL_HANDLERS: dict[str, callable]`, avaliar um
   componente de estado único com fases (achado A.1) — decisão de
   DESENHO, não so prioridade. Como isso pode ser um redesenho maior
   do que o originalmente planejado, esse é um ponto que prefiro
   discutir com você antes de reescrever a Fase 3, não decidir
   sozinho.
2. **Nova fase**: unificar fila/lifecycle de Arena e Battleground
   (achado C.3/C.4/C.5) — candidata a entrar entre a Fase 3 (mesma
   área de handlers/registry) e a Fase 4 do roteiro original.
3. C2 (item_id estável) já estava na Fase 7 (baixa prioridade,
   "pré-lançamento") — o reforço externo (achado B.2-B.4) é argumento
   pra subir de prioridade, mas não é urgente igual aos itens 1/2
   acima.

Fases 0 (feita), 1, 2, 4, 5, 6 do roteiro original continuam válidas
como estavam — o benchmark não achou nada que as contradiga.


