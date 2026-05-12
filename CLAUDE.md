# Instruções para Claude — RPG ECS

## Workflow obrigatório

### Início de qualquer sessão
1. Ler `arquitetura/MAPA_PROJETO.md` — localização de tudo no projeto
2. Ler `arquitetura/SISTEMAS_ECS.md` — ordem dos sistemas e responsabilidades
3. Ler `arquitetura/COMPONENTES_ECS.md` — componentes disponíveis
4. Ler `arquitetura/DADOS_JOGO.md` — skills, talentos, itens, mobs existentes
5. Usar esses arquivos como referência ANTES de ler código-fonte

**Regra:** Se a resposta está nos arquivos de arquitetura, não varrer o codebase. Só ler arquivos .py quando precisar de detalhes de implementação específicos.

### Após qualquer mudança
Atualizar os arquivos de arquitetura relevantes:
- Nova skill/talento → `DADOS_JOGO.md`
- Novo componente → `COMPONENTES_ECS.md`
- Novo sistema → `SISTEMAS_ECS.md`
- Novo arquivo/responsabilidade → `MAPA_PROJETO.md`
- Problema arquitetural encontrado → `PROBLEMAS_ARQUITETURA.md`

### Identificação de problemas
Se durante uma implementação encontrar código morto, violação de ECS, dado inconsistente ou gambiaras → registrar em `arquitetura/PROBLEMAS_ARQUITETURA.md` com severidade e esforço de fix.

---

## Contexto rápido do projeto

- RPG Tibia/WoW-style, Python 3.9 + Pygame 2.x
- Arquitetura ECS: `world.py` (registry), `components.py` (dados), `systems.py` (lógica)
- Personagens: Guerreiro (build Cavaleiro), Mago (build Piromania)
- TILE_SIZE = 32px, tela 1280×720, 60 FPS
- Sem sprites — retângulos coloridos por enquanto

## Regras do projeto
- ECS puro: components = dados, systems = lógica
- Skills → `SKILL_CATALOG` em `skill_config.py` (fonte única)
- Talentos → `talent_data.py`, efeitos em `talent_system.apply_talent_effects()`
- Fontes de dados nunca ficam em sistemas — ficam em arquivos `*_data.py`
- `stat_fns.py` para mutações de stats (não métodos em componentes)
- Render que aparece sobre tiles → chamar explicitamente após `render_fog()` em `game.py`
- Skills `offensive=False` não iniciam combate nem perseguem alvo
