# Instruções para Claude — RPG ECS Online

> Branch: **online** — versão multiplayer em desenvolvimento paralelo.
> Versão offline (single-player) está em `rpg_ecs/` (branch `master`). Não confundir.

---

## Workflow obrigatório

### Início de qualquer sessão
1. Ler `arquitetura/ARQUITETURA_ONLINE.md` — decisões, protocolo, estado de implementação
2. Ler `arquitetura/MAPA_PROJETO.md` — localização de tudo (online + herdado)
3. Ler `arquitetura/SISTEMAS_ECS.md` — sistemas offline (referência) + sistemas do servidor
4. Ler `arquitetura/COMPONENTES_ECS.md` — componentes disponíveis
5. Usar esses arquivos como referência ANTES de ler código-fonte

**Regra:** Se a resposta está nos arquivos de arquitetura, não varrer o codebase.

### Após qualquer mudança
Atualizar os arquivos de arquitetura relevantes:
- Nova mensagem no protocolo → `ARQUITETURA_ONLINE.md` (tabela de mensagens) + `shared/messages.py`
- Nova decisão arquitetural → `ARQUITETURA_ONLINE.md` (seção de decisões)
- Mudança de status de implementação → `ARQUITETURA_ONLINE.md` (tabela de estado)
- Novo sistema no servidor → `SISTEMAS_ECS.md` (seção "Sistemas do Servidor")
- Novo arquivo online → `MAPA_PROJETO.md` (tabela "Onde encontrar o quê — Online")
- Novo componente ECS → `COMPONENTES_ECS.md`
- Problema arquitetural → `PROBLEMAS_ARQUITETURA.md`

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
- **Player pode usar a skill?** → `world_systems.is_skill_authorized()`
  (classe + talento + learned) — gate autoritativo chamado pelo
  skill_processor; toda forma nova de adquirir skill entra ALI. Fixtures de
  teste usam `tests.helpers.authorize_skill()`.
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
