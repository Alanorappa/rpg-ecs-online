# Instruções para Claude — RPG ECS Online

> Branch: **online** — versão multiplayer em desenvolvimento paralelo.
> Versão offline (single-player) está em `rpg_ecs/` (branch `master`). Não confundir.

---

## Workflow obrigatório

### Início de qualquer sessão
1. Ler `arquitetura/VISAO_PRODUTO.md` — o que o jogo quer ser, prioridade
   de modalidades, horizonte de escala, decisões de rumo (06/08/2026,
   documento vivo — leitura obrigatória ANTES de julgar se uma mudança
   arquitetural vale o esforço agora)
2. Ler `arquitetura/ARQUITETURA_ONLINE.md` — decisões, protocolo, estado de implementação
3. Ler `arquitetura/MAPA_PROJETO.md` — localização de tudo (online + herdado)
4. Ler `arquitetura/SISTEMAS_ECS.md` — sistemas offline (referência) + sistemas do servidor
5. Ler `arquitetura/COMPONENTES_ECS.md` — componentes disponíveis
6. Usar esses arquivos como referência ANTES de ler código-fonte

**Regra:** Se a resposta está nos arquivos de arquitetura, não varrer o codebase.
**Contrapartida desta regra (06/08/2026):** ela só funciona se os arquivos
estiverem certos. Auditoria de 06/08/2026 achou 4 casos de doc dizendo o
OPOSTO do código real (`gold` documentado como "cliente autoritativo"
quando é servidor; `MAPA_PROJETO.md` afirmando "servidor nunca importa
`ui/`" quando importa de propósito; `instance_progression.py` ainda
descrito como "inerte" um mês depois de entrar em produção via fila de
BG; fluxo de "nova stat" ensinando um mecanismo removido do código). Se
algo que você lê num arquivo de arquitetura conflita com o que o código
realmente faz, **o código vence, sempre** — e o doc errado é ele mesmo
um problema a registrar (`PROBLEMAS_ARQUITETURA.md`), não só a ignorar.

### Após qualquer mudança
Atualizar os arquivos de arquitetura relevantes:
- Nova mensagem no protocolo → `ARQUITETURA_ONLINE.md` (tabela de mensagens) + `shared/messages.py`
- Nova decisão arquitetural → `ARQUITETURA_ONLINE.md` (seção de decisões)
- Mudança de status de implementação → `ARQUITETURA_ONLINE.md` (tabela de estado)
- Novo sistema no servidor → `SISTEMAS_ECS.md` (seção "Sistemas do Servidor")
- Novo arquivo online → `MAPA_PROJETO.md` (tabela "Onde encontrar o quê — Online")
- Novo componente ECS → `COMPONENTES_ECS.md`
- Problema arquitetural → `PROBLEMAS_ARQUITETURA.md`
- **Isso vale NO MESMO commit/sessão da mudança, nunca "depois que sobrar
  tempo"** (06/08/2026 — `PROBLEMAS_ARQUITETURA.md` ficou 16 dias sem
  entrada nova enquanto pelo menos 4 bugs da mesma família catalogada em
  §11/A4 aconteciam de verdade; nenhum foi cruzado de volta com a
  auditoria que já os previa). Um débito documentado e nunca revisitado
  vale tanto quanto um débito nunca documentado.

---

## Disciplina de qualidade (06/08/2026 — resposta a bugs recorrentes e a uma auditoria que se repetiu sozinha)

### Antes de agir sobre QUALQUER pedido — implementação OU correção (06/08/2026, decisão explícita do usuário)
- **Nunca tratar o primeiro achado "relacionado" ao sintoma/pedido como
  se fosse a causa raiz ou a única forma de implementar.** Antes de
  escrever qualquer código, mapear explicitamente QUAIS sistemas/
  componentes ECS a mudança toca e o efeito colateral em cada um —
  histórico real desta sessão: mais de uma correção "isolada" não
  resolveu o problema relatado, e num caso o mecanismo real só foi
  achado numa camada totalmente diferente da que o fix inicial mexeu
  (ver `PROBLEMAS_ARQUITETURA.md` §12, "Lesson" da investigação do PNQ).
- **Se um pedido do usuário (ou o caminho mais rápido de implementar
  algo) conflita com a arquitetura ECS** — acoplamento novo, estado
  global fora do ECS, lógica duplicada em vez de reusar um ponto único
  de verdade, componente virando "deus" com responsabilidade que não é
  dele, etc. — **NUNCA decidir sozinho, nem para casos que pareçam
  pequenos.** Parar, explicar o conflito, sugerir uma alternativa que
  respeita a arquitetura, e esperar a decisão do usuário antes de agir.
  Sem exceção por tamanho — decisão explícita do usuário, 06/08/2026
  ("nunca decido sozinho — sempre pergunto").
- **Testes: sem política automática fixa.** Nível de teste (nenhum,
  manual em playtest, automatizado) é decidido junto, por mudança, não
  presumido — decisão explícita do usuário, 06/08/2026.
- Ler `arquitetura/VISAO_PRODUTO.md` antes de julgar se uma refatoração
  arquitetural vale o esforço agora — a ordem de trabalho decidida é
  VISÃO primeiro, débito técnico depois (a arquitetura serve a visão do
  produto, não o contrário).
- **PESQUISAR/DESENHAR ANTES DE CODAR — pra QUALQUER correção ou
  implementação, sem exceção de tamanho.** Decisão explícita do
  usuário, 07/08/2026, depois de um incidente com 3 tentativas
  seguidas erradas no mesmo arquivo (`engine/tileset.py` →
  `engine/camouflage.py` → parametrização escrita direto no código, SEM
  pesquisa nova — só reciclando o benchmark geral de Veloren feito
  antes, não uma checagem específica pra ESTA decisão) — a 3ª tentativa
  aconteceu MESMO DEPOIS de eu já ter escrito a versão anterior desta
  regra, o que prova que "já pesquisei algo parecido antes" não é a
  mesma coisa que pesquisar a decisão específica em jogo. Fluxo
  obrigatório antes de tocar em qualquer arquivo de código:
  1. Mapear o problema arquiteturalmente — que sistema/categoria isso
     é de verdade, não só o sintoma imediato.
  2. Consultar as referências de verdade (Veloren/AzerothCore, ou
     pesquisa nova via WebSearch/WebFetch se o caso for específico e
     não coberto pelo benchmark já feito — nunca assumir que uma
     pesquisa ANTERIOR sobre um tópico mais amplo já cobre uma decisão
     nova e específica).
  3. Verificar se já existe um ponto único de verdade no PRÓPRIO
     projeto que já resolve isso (`fx.py`, `ui/effect_animator.py`,
     `ui/icon_manager.py`, `ui/sound_manager.py` são o padrão certo pra
     "1 módulo por CATEGORIA de asset/efeito, usado por QUALQUER skill
     que precisar" — nunca 1 arquivo por skill, nunca hardcode do nome
     da skill dentro da função quando o mecanismo é genérico).
  4. Propor o desenho pro usuário e ESPERAR aprovação antes de
     escrever/editar qualquer arquivo — mesmo que a mudança pareça
     pequena ou óbvia.
  Caso registrado (revertido depois de 3 idas e vindas):
  `arquitetura/PROBLEMAS_ARQUITETURA.md` §12/§13.

Contexto que gerou esta seção: uma auditoria arquitetural ampla já
existia (`PROBLEMAS_ARQUITETURA.md` §11, 15/07/2026) e já tinha nomeado
corretamente a causa raiz de bugs que se repetiram três semanas depois
(autoridade híbrida na persistência = item A4; servidor despachando
skill via `getattr` numa classe de UI do cliente = item B3). Os fixes
pontuais aconteceram, mas ninguém cruzou o bug novo com o item já
catalogado — cada um foi tratado como incidente isolado. Isso é o
padrão a quebrar, não só os bugs individuais.

- **Antes de investigar qualquer bug, checar `PROBLEMAS_ARQUITETURA.md`
  (seções numeradas + §11) pelo sintoma.** Se bater com um item marcado
  "MITIGADO" (não "RESOLVIDO"), o fix de verdade é terminar aquele item
  — não outro patch pontual em cima do sintoma novo. Um item "mitigado"
  que gera um 4º/5º incidente da mesma família é sinal de que a
  mitigação nunca foi suficiente; dizer isso ao usuário explicitamente
  em vez de tratar como bug novo.
- **"Corrigido" exige reproduzir o sintoma que foi relatado, não só um
  cenário sintético plausível.** Um teste automatizado que você mesmo
  escreve prova que O SEU cenário passa — não que o bug relatado sumiu.
  Antes de declarar algo corrigido: (a) reproduza o cenário exato
  descrito (mesma sequência, mesma configuração), ou (b) diga
  explicitamente que não foi possível confirmar contra o caso real e
  que falta dado adicional (log, replay, resposta a uma pergunta
  direta) — nunca generalize de "meu teste sintético passou" pra
  "corrigido". Rodar a suíte inteira (900+ testes) não é prova de nada
  além de "não quebrei outra coisa que já era testada".
- **Campo novo persistido a partir de `session.last_client_payload`
  (autoridade híbrida)**: antes de aceitar esse padrão pra um campo
  novo, checar se ele é afetado por QUALQUER overlay/estado temporário
  (progressão normalizada de instância, ou qualquer mecanismo parecido
  futuro) — `server/instance_progression.py` troca `TalentTree`/
  `PlayerSkills`/`Wallet`/`Inventory`/`Equipment`/`PermanentStats`. Se
  for, o campo nasce com um `live_X` lido do ECS ao vivo desde o
  commit inicial (mesmo padrão de `get_player_equipment_data`/
  `get_player_hotbar_data`/`get_player_talent_data`) — não como reação
  a um bug relatado depois. Auditoria de 06/08/2026 achou 5 campos
  afetados; só 3 tinham esse fallback.
- **Proibido estado mutável ad-hoc fora do ECS.** Nunca anexar
  cache/estado via `hasattr(func, "_x")`/atributo de função, nem dict
  mutável no CORPO de uma classe (é atributo de classe, compartilhado
  entre todas as instâncias — armadilha clássica do Python, não estado
  por-instância). Padrão certo já existe no projeto e é pra ser
  copiado, não reinventado: dict/lista de MÓDULO com nome visível +
  `global` explícito na função que muta (`core_systems.
  register_lethal_interceptor`, `faction_system.register_pvp_context`,
  o `_svc` já documentado acima), ou — se for cache de recurso
  (sprite/superfície) com necessidade real de limite — um dict de
  módulo com teto explícito e `clear()` documentado
  (`ui/floating_text.py::_outline_cache` é o exemplo a seguir).
- **Antes de escrever qualquer lógica nova, verificar se um dos "Pontos
  únicos de verdade" acima já cobre o caso** — a lista existe
  precisamente pra evitar reimplementação local. Se a lógica que você
  está prestes a escrever se parece com algo que já devia ter um dono
  único (dano, teleporte, autorização de skill, broadcast), procurar
  o dono antes de escrever um novo caminho paralelo.

---

## Contexto do projeto

### Versão offline (branch master — referência, não modificar)
- RPG Tibia/WoW-style, Python 3.9 + Pygame 2.x
- ECS puro: `world.py` (registry), `components.py` (dados), `systems.py` (lógica)
- Personagens: Guerreiro (Cavaleiro), Mago (Piromania), Arqueiro (Bardo)
- TILE_SIZE = 32px, tela 1280×720, 60 FPS

### Versão online (este branch)
- Servidor: Python asyncio + WebSocket, **sem Pygame**, 30 ticks/s
- Cliente: Pygame (evolução do `game.py` offline) + `client/network.py`
- Banco: SQLite (dev) → PostgreSQL (prod)
- Protocolo: JSON via WebSocket (→ MessagePack antes do lançamento)

---

## Regras do projeto online

### Separação cliente/servidor (inviolável)
- `server/` **nunca importa Pygame** — código de servidor deve rodar headless
- `client/` **nunca calcula gameplay** — apenas renderiza estado recebido do servidor
- `shared/` **sem estado** — só constantes e funções puras de serialização

### Pontos únicos de verdade (03/07/2026 — usar SEMPRE, nunca reimplementar)
- **Sistema de gameplay novo** → `world_systems.py` (headless, ZERO pygame no
  topo; efeitos via `from fx import FLT, SOUNDS, ...`). Sistema de UI/render →
  `systems.py` (que re-exporta world_systems pra compatibilidade).
- **Efeito visual/som em código compartilhado** → façade `fx.py` (no-op no
  servidor; cliente vincula via `fx.bind_client_fx()` no GameEngine).
- **Teleporte/knockback/respawn** → `utils.snap_to_tile()` — NUNCA escrever
  `current_tile_x/y` direto (classe de bug: tween antigo sobrevive ao snap).
- **Escrita final de dano em HP** → `core_systems.apply_damage_core()` —
  guards imune/morto, overkill preservado, quebra polymorph/sleep. Regra nova
  de mitigação/resistência entra SÓ ali (deal_damage/_apply_final_damage/
  _apply_magic_damage são delegates).
- **STATS_UPDATE privado (servidor→dono)** → `WorldServer.queue_stats_update()`
  (schema na docstring; valida player_eid na origem).
- **Atributo de combate novo** → par `base_X`/`X` em `CombatStats` + 1 entrada
  em `stat_fns._MODIFIABLE_ATTRS` (+ `_STAT_CLAMPS` se tiver limite).
- **Dano base de skill FÍSICA** → `damage_calculator.ability_physical_damage()`
  (`arma×dmg_weapon_pct + AP×(damage_multiplier + 0.01×skill_level_da_arma)`) —
  multiplicador vem SÓ do SKILL_CATALOG, NUNCA hardcodear no handler (classe
  de bug: golpe_poderoso com 3.0 fixo tornava o catálogo letra morta).
  Skill sem arma → `dmg_weapon_pct: 0.0` no catálogo. Magia → `spell_damage()`.
- **Dano base de AUTO-ATTACK físico** (melee/ranged, guerreiro/arqueiro) →
  `damage_calculator.calculate_base_damage(..., ap_skill_mult=...)` — mesma
  fórmula de skill_level das skills (`1.0 + 0.01×weapon_skill_level`), só que
  aplicada ao AP dentro do branch `damage_type=="physical"`. Resolvida pelo
  CHAMADOR (`world_systems.py::_calculate_damage`/`deal_damage`,
  `server/spell_completion_processor.py::_server_apply_ranged_physical`) —
  NUNCA hardcodear `ap_skill_mult=1.0` num call site novo de auto-attack, ou
  ele fica pra sempre fora da escala de skill_level (era o estado ORIGINAL,
  corrigido em 07/07/2026).
- **Broadcast direto novo (fora do AOI_UPDATE)** →
  `SessionManager._sessions_in_aoi(tx, ty, map_file, origin_eid=...)` —
  NUNCA iterar `self._sessions` com check de distância à mão (classe de bug:
  vazamento cross-map + ignora visibilidade de camuflado + métrica errada).
  Visão compartilhada de time (instanciado — arena/battlefield/dungeon,
  30/07/2026) já é embutida em `_sessions_in_aoi` E em
  `_build_update_for_session` via `SessionManager._ally_vision_centers`
  (recomputado 1x/tick, `_compute_ally_vision_centers()`) — chamadas novas
  não precisam reimplementar isso.
- **Player pode usar a skill?** → `world_systems.is_skill_authorized()`
  (classe + talento + learned) — gate autoritativo chamado pelo
  skill_processor; toda forma nova de adquirir skill entra ALI. Fixtures de
  teste usam `tests.helpers.authorize_skill()`.
- **Troca de aparência temporária de entidade** (disfarce, transformação,
  futura poção de metamorfose, etc.) → `engine/entity_disguise.py::
  get_active_appearance_override()` + tabela `_APPEARANCE_SOURCES` — 1
  função checadora por fonte (lê o estado que a fonte já mantém, sem
  componente novo nem sync de rede novo), nunca um `if/elif` novo no
  render loop. `discover_sprite_variants(base_name)`/
  `get_animated_disguise_frame(base_name, ...)` são genéricas, parametrizadas
  por `base_name` — NUNCA hardcodear o nome de uma skill dentro dessas
  funções (classe de bug: 3 tentativas erradas em sequência até acertar,
  ver PROBLEMAS_ARQUITETURA.md §12/§13 — inclusive DEPOIS da regra
  "pesquisar antes de codar" já existir, o que prova que reciclar
  pesquisa de um tópico mais amplo não substitui pesquisar a decisão
  específica em jogo).
- **Entry point server-side novo que executa handler compartilhado em nome
  de um player** (skill/spell/projectile/channeling) →
  `WorldServer.register_map_services_for(player_eid)` ANTES do handler —
  aponta `_svc` (is_tile_walkable/find_path/get_tilemap) pro bundle do MAPA
  do player (classe de bug: `_svc` fica no último mapa carregado; Interceptar
  "bloqueado" em terreno aberto, Tiro Repulsivo stunando em parede fantasma).
  NUNCA pegar "o primeiro" `Tilemap` do world — usar o bundle via
  `get_entity_map(eid)`.

### Atualização coesa ao adicionar sistema novo (22/07/2026 — feedback recorrente do usuário)
- **Nunca** deixar uma atualização acontecer "de carona" em outra ação sem
  relação direta (ex: HUD de grupo só atualizava quando o player se movia;
  tile de portão de arena só re-renderizava quando a câmera cruzava
  fronteira de tile ao andar) — se a causa de uma atualização de tela/estado
  não é a ação que a gerou, é sintoma desta classe de bug.
- Toda vez que um sistema/evento/mensagem novo for adicionado, **enumerar
  explicitamente** todos os componentes, sistemas, entidades e elementos de
  UI afetados por ele e garantir que cada um propague na hora certa — não
  assumir que "vai atualizar em algum tick futuro" é aceitável.
- Casos já resolvidos, ambos a mesma causa raiz (dois lados diferentes do
  cliente/servidor):
  - **Servidor**: `SessionManager._on_tick`'s `has_pending`
    (`server/session.py`) só despacha pacotes se algum buffer específico
    tiver conteúdo — qualquer `_..._this_tick`/evento novo que não entrar
    nessa lista fica PRESO até atividade alheia (movimento, combate, etc.)
    destravar por acaso. Já aconteceu com arena (§34.34.1) E com grupo/
    duelo/trade/correção de posição de skill (mesmo dia, mesmo padrão —
    ver ARQUITETURA_ONLINE.md). Todo buffer novo consumido em
    `_dispatch_tick_deltas` tem que entrar em `has_pending` no MESMO commit.
  - **Cliente**: `TileRenderSystem` (`ui/systems.py`) desenha em cima de uma
    Surface em cache indexada pela posição da CÂMERA, não pelo conteúdo do
    tile — qualquer mutação direta de `Tilemap.tile_matrix` fora do fluxo
    normal de troca de mapa precisa chamar
    `GameEngine._tile_render_system.invalidate_cache()` explicitamente, ou
    o visual só se autocorrige quando o jogador anda o bastante pra cruzar
    fronteira de tile.
- Se não estiver óbvio QUANDO uma atualização nova deve disparar (a cada
  tick? só na ação que a causou? em resposta a outro evento?), **perguntar
  ao usuário antes de implementar** em vez de adivinhar — mesma régua de
  `feedback_ask_before_deciding` (memória).

### Protocolo
- Todo pacote tem `type` (MsgType), `p` (payload), `seq` (int), `ts` (ms epoch)
- Novos tipos de mensagem: adicionar em `MsgType` + documentar payload em `shared/messages.py`
- Servidor valida TUDO — nunca confiar em dados de gameplay do cliente

### Lag compensation
- Skills de cone (Pirofagia, Tiro Múltiplo): cliente envia `dir_x/dir_y` + `ts`
- Servidor usa `WorldServer.get_snapshot_at(tick)` para validar no estado correto
- Janela máxima: `LAG_COMP_WINDOW_MS = 200` em `shared/constants.py`

### Segurança
- Invisibilidade (Camuflagem): servidor **nunca** inclui jogador invisível no AOI de outros
- Dano, drops, posição final de knockback: sempre calculados no servidor
- SHA-256 do password no cliente antes de enviar — nunca texto puro na rede

### Validação de alvo no cliente — usar `utils.is_target_alive()`
- **Nunca** checar só `CombatStats.current_hp` pra decidir se um alvo está morto/válido no cliente.
  Player remoto (PvP) não tem `CombatStats` local — só `RemoteControlled.hp`. Mob remoto só tem
  `RemoteEntityMeta.hp`. Um check que só olha `CombatStats` nunca detecta a morte desses alvos
  (bug real: arqueiro continuava tocando som de "nock" e contando cooldown de ataque contra um
  guerreiro remoto já morto, pois `tgt_cs.current_hp <= 0` nunca era True com `tgt_cs is None`).
- Toda skill/sistema/classe nova que precisa saber se um alvo está vivo deve chamar
  `utils.is_target_alive(world, target_id)` (cobre os 3 casos) em vez de reimplementar o check.
  `skill_handlers.SkillHandlers._target_alive()` é um atalho que já delega pra essa função.
- Isso só importa no **cliente** — no servidor todo player (local ou remoto) tem `CombatStats`
  completo, porque o servidor é autoritativo pra todo mundo.

### Regras herdadas do offline (ainda válidas no cliente)
- Skills → `SKILL_CATALOG` em `skill_config.py` (fonte única)
- Talentos → `talent_data.py`, efeitos em `talent_system.apply_talent_effects()`
- `stat_fns.py` para mutações de stats
- Skills `offensive=False` não iniciam combate nem perseguem alvo

---

## Como rodar

```bash
# Instalar dependências do servidor
pip install -r requirements_server.txt

# Iniciar servidor (cria banco e conta de teste automaticamente)
python server/main.py

# Em outro terminal: iniciar cliente (ainda usa game.py offline)
python main.py
```

Conta de teste criada automaticamente: `usuario=teste  senha=123456`

Testar algo que depende de nível/talento/gold/item: conceder GM
(`python -m server.grant_gm <username>`) + `"debug_mode": true` em
`config.json` — abre F12 com nível/ouro/itens server-autoritativos de
verdade (não é mais mutação só local). Ver `ARQUITETURA_ONLINE.md`
§34.74.54.
