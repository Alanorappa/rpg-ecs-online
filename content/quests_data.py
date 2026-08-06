"""
quests_data.py — Dados puros do sistema de quests (sem lógica).

Adicionar uma nova quest: inserir uma entrada em QUESTS.
Adicionar um item de quest: inserir uma lambda em QUEST_ITEMS.

Tipos de objetivo (ObjectiveDef.type):
    kill              Matar N inimigos. target = nome | raça | "*" (qualquer)
    auto_attack_hit   Acertar N ataques básicos (auto-attack) em target = nome |
                      raça | "*". Conta ACERTOS (miss/dodge/parry/block não
                      contam), não dano acumulado — não conta hit de SKILL
                      (isso é "use_skill"). Só PvE (mob) por enquanto, mesmo
                      escopo de "kill". Disparado em
                      server/combat_processor.py::_process_player_attacks.
    collect_item      Coletar N de loot_item de target. Drop condicional via loot_chance.
    reach_tile        Chegar em location=(tx, ty) ou área (x0, y0, x1, y1).
    use_skill         Usar skill_id N vezes.
    use_consumable    Usar consumível N vezes. target = nome do item | "*".
    reach_level       Alcançar o nível count. (target ignorado)
    talk_to_npc       Interagir com mercador. target = nome | "*".
    equip_item        Equipar item. target = nome | item_type | "*".
    use_item_on_target  Usar um item específico (params["item_name"]) sobre um
                        alvo. target = nome | raça do alvo | "*" (qualquer).
                        Evento esperado: quest_events.fire("use_item_on_target",
                        item_name=..., target_name=..., target_race=...).
    learn_skill       Aprender (treinar) uma skill no treinador. target = skill_id.
                        Dispara ao comprar/aprender com sucesso em
                        trainer_system.py::_do_learn — não conta skills
                        iniciais (INITIAL_SKILLS_BY_CLASS).
    use_skill (extra) params={"on_dummy": True} exige que o alvo da skill tenha
                        o componente TrainingDummy (boneco de treino) no
                        momento do uso — sem o param, aceita qualquer alvo
                        (comportamento padrão, usado por warrior_trial etc.).

Adicionar um tipo de objetivo NOVO (que não é só "item usado em alvo"):
    1. Documentar aqui (acima) e descrever a forma do evento esperado.
    2. Se o tipo precisar de algum dado extra que os campos comuns (target/
       count/location) não cobrem, usar `params: dict` em vez de adicionar
       outro campo nomeado ao ObjectiveDef — ele existe exatamente pra isso,
       ver exemplo de use_item_on_target acima.
    3. Adicionar o branch de match em QuestSystem._matches() e o de label em
       QuestSystem._obj_label() (quest_system.py).
    4. Disparar quest_events.fire(tipo, **dados) no sistema que detecta a
       ação (ex: ConsumableSystem, SkillSystem) — ver quest_events.py.

Recompensas (QuestReward) — xp/gold são simples (int). Para ITENS:
    items   tuple  Itens SEMPRE concedidos ao entregar a quest.
    choice  tuple  Pool de itens — jogador escolhe exatamente 1 (aparece no
                   mesmo diálogo de entrega, com o texto "Escolha uma
                   recompensa:" e os ícones clicáveis; "Concluir" só libera
                   depois de uma escolha).

    Cada entrada de `items`/`choice` é:
        "item_key"            stack = 1
        ("item_key", stack)   stack customizado (é limitado ao max_stack do
                               item; nunca precisa se preocupar em passar
                               mais que o cabimento)

    `item_key` é a CHAVE (não o nome de exibição) em um destes catálogos,
    nessa ordem de busca:
        1. content/item_table.py::ITEMS      (equipáveis/consumíveis normais,
                                               ex.: "training_sword",
                                               "hp_potion", "mana_potion")
        2. content/quests_data.py::QUEST_ITEMS (materiais de quest logo
                                               abaixo — usar a CHAVE do dict,
                                               ex.: "Pelo de Urso")
    Se o `item_key` não existir em nenhum dos dois, o item é silenciosamente
    ignorado (log de warning no servidor) — o resto da recompensa (xp/gold/
    outros itens) é concedido normalmente mesmo assim. Então: SEMPRE conferir
    o nome exato da chave em item_table.py/QUEST_ITEMS antes de usar — um
    typo não quebra a quest, só faz o item nunca chegar.

    Exemplo — xp/gold + 2 itens fixos (1 com stack) + escolha entre 3:
        reward=QuestReward(
            xp=100, gold=20,
            items=("hp_potion", ("mana_potion", 2)),
            choice=("training_sword", "iron_mace", "apprentice_axe"),
        )

    Só itens fixos, sem escolha (não precisa de `choice` nenhum):
        reward=QuestReward(xp=15, items=("training_sword",))

    Pra SKILL: `skill` (str) — chave de content/skill_config.py::
    SKILL_CATALOG (ex.: "golpe_poderoso"), não o nome de exibição. Só 1
    skill por quest (sem escolha entre skills, por enquanto). Se a
    `class_id` do skill não bater com a do player, é ignorada com warning
    — sempre conferir que a quest só é oferecida pra classe certa
    (`QuestDef.class_req`) antes de dar skill de recompensa:
        reward=QuestReward(xp=50, skill="golpe_poderoso")

    Detalhes de implementação (protocolo/servidor/UI) em
    arquitetura/ARQUITETURA_ONLINE.md §34.46 (itens) e §34.51 (skill).

ITEM_GRANTS_QUEST (item de loot concede quest nova ao ser saqueado):
    dict[nome_de_exibição_do_item, quest_id] — ver comentário junto ao dict,
    logo abaixo de QUEST_ITEMS. Detalhes em §34.51 (Fase M4).
"""
from __future__ import annotations
from typing import NamedTuple
from engine.components import Item


# ---------------------------------------------------------------------------
# Tipos de dados
# ---------------------------------------------------------------------------

class ObjectiveDef(NamedTuple):
    type:        str            # tipo do objetivo (ver docstring do módulo)
    target:      str   = "*"   # alvo; "*" = qualquer
    count:       int   = 1     # quantidade necessária
    location:    tuple = ()    # (tx, ty) ou (x0, y0, x1, y1) para reach_tile
    loot_item:   str   = ""    # nome do item a dropar condicionalmente (collect_item)
    loot_chance: float = 1.0   # chance de drop do item condicional (0.0–1.0)
    params:      dict  = {}    # catch-all pra dados específicos de um tipo novo —
                               # NUNCA mutar em runtime (default compartilhado entre
                               # instâncias); só ler. Ex: use_item_on_target usa
                               # params["item_name"].


class QuestReward(NamedTuple):
    xp:     int   = 0
    gold:   int   = 0
    # Itens de recompensa (23/07/2026, pedido do usuário) — cada entrada
    # aceita "item_key" (stack=1) ou ("item_key", stack). item_key é a
    # CHAVE de content/item_table.py::ITEMS (ex.: "training_sword",
    # "hp_potion", "mana_potion" — não o nome de exibição do item) — cai
    # pra content/quests_data.py::QUEST_ITEMS (chave = nome de exibição)
    # como fallback, ver engine/quest_logic.py::resolve_reward_item_factory.
    items:  tuple = ()   # SEMPRE concedidos ao entregar a quest
    choice: tuple = ()   # jogador escolhe 1 destes (mesmo formato de items) —
                          # UI de escolha só aparece no diálogo de entrega
                          # (ui/quest_system.py::QuestDialogSystem._render_turnin)
    # Skill de recompensa (25/07/2026, pedido do usuário) — chave de
    # content/skill_config.py::SKILL_CATALOG (não o nome de exibição).
    # Diferente de items: o SERVIDOR grava em PlayerSkills.learned_skill_ids
    # na hora da entrega (skill tem gate de autorização server-side,
    # is_skill_authorized — precisa ser real no servidor imediatamente, não
    # só depender do cliente sincronizar depois). Se a classe do skill não
    # bater com a do player, é ignorado com warning (mesmo padrão de
    # item_key inválido) — resto da recompensa concedido normalmente.
    skill: str = ""


class QuestDef(NamedTuple):
    title:       str
    description: str             # aceita o placeholder {player_name} — vira o
                                  # nome do personagem (engine/quest_logic.py::
                                  # format_quest_text). Ex.: "Bem-vindo, {player_name}!"
    objectives:  tuple           # tuple[ObjectiveDef, ...]
    reward:      QuestReward
    auto_start:  bool  = False   # inicia automaticamente sem NPC
    repeatable:  bool  = False   # reseta ao completar
    requires:    tuple = ()      # tuple[quest_id, ...] pré-requisitos
    next_quest:  str   = ""      # quest_id a iniciar automaticamente ao completar
    level_req:   int   = 0       # nível mínimo para aceitar a quest
    class_req:   str   = ""      # "" = qualquer classe; senão restrita a essa classe
                                  # (ex: "mago") — totalmente invisível pra outras
                                  # classes (sem ícone/indicador, não aparece nem
                                  # como bloqueada). Cadeia de quests da classe: ligar
                                  # via requires=(quest_anterior,) / next_quest.
    completion:  str   = ""      # texto do NPC ao receber a entrega (vazio = usa
                                  # título) — mesmo placeholder {player_name} aceito


# ---------------------------------------------------------------------------
# Itens de quest (materiais drop-only, sem slot de equipamento)
# ---------------------------------------------------------------------------

QUEST_ITEMS: dict[str, callable] = {
    "Pelo de Urso":         lambda: Item("Pelo de Urso",        "material",     slot=None, rarity="common", value=3,    max_stack=10),
    "Presa de Lobo":        lambda: Item("Presa de Lobo",       "material",     slot=None, rarity="common", value=2,    max_stack=10),
    "Veneno de Aranha":     lambda: Item("Veneno de Aranha",    "material",     slot=None, rarity="common", value=4,    max_stack=10),
    "Cauda de Escorpião":   lambda: Item("Cauda de Escorpião",  "material",     slot=None, rarity="common", value=3,    max_stack=10),
    "Escama de Cobra":      lambda: Item("Escama de Cobra",     "material",     slot=None, rarity="common", value=2,    max_stack=10),
    "Osso de Goblin":       lambda: Item("Osso de Goblin",      "material",     slot=None, rarity="common", value=2,    max_stack=10),
    "Vômito de Zumbi":      lambda: Item("Vômito de Zumbi",     "material",     slot=None, rarity="common", value=0,    max_stack=10),
    "Pá":                   lambda: Item("Pá",                  "ferramenta",   slot=None, rarity="common", value=15,   max_stack=1),
    "Picareta":             lambda: Item("Picareta",            "ferramenta",   slot=None, rarity="common", value=15,   max_stack=1),
    "Mochila de mineração": lambda: Item("Mochila de mineração","ferramenta",   slot=None, rarity="common", value=15,   max_stack=1),
    "Lampião":              lambda: Item("Lampião",             "ferramenta",   slot=None, rarity="common", value=15,   max_stack=1),
    "Cantil":               lambda: Item("Cantil",              "ferramenta",   slot=None, rarity="common", value=15,   max_stack=1),

    # Itens que CONCEDEM quest ao serem saqueados (Fase M4) — sem função de
    # combate/equipamento, só "material" + description como texto de sabor
    # entre aspas. A tag "Este item inicia uma quest" no tooltip é
    # AUTOMÁTICA (ui/ui_helpers.py::item_tooltip_lines, detecta via
    # ITEM_GRANTS_QUEST logo abaixo) — não precisa escrever isso na
    # description, só o texto de sabor mesmo.
    "Artefato Misterioso": lambda: Item(
        "Artefato Misterioso", "material", slot=None,
        rarity="rare", value=0, max_stack=1,
        description="\"Um artefato estranho, cheira mal e parece ter uma "
                    "tecnologia avançada, mas nenhum botão funciona.\""),
}


# ---------------------------------------------------------------------------
# Item de loot que PODE conceder uma quest nova (Fase M4, 25/07/2026;
# REVISADA no mesmo dia — usuário pediu fluxo de decisão em vez de
# automático). Chave = NOME DE EXIBIÇÃO do item (não item_key).
#
# Vale pra QUALQUER origem do item (corpse de mob morto OU harvestable de
# mapa) — mas NÃO inicia a quest sozinho ao ser saqueado. É consultado só
# no CLIENTE, como metadado puro:
#   1. Tag "Este item inicia uma quest" no tooltip (ui/ui_helpers.py::
#      item_tooltip_lines).
#   2. Gatilho do MESMO modal de diálogo de quest do NPC (ui/quest_system.
#      py::QuestDialogSystem.open_for_item) ao clicar direito no item na
#      bag (client/inventory_handlers.py::_try_open_item_quest_dialog) —
#      item fica inerte na bag até o jogador decidir; recusar/fechar não
#      descarta nada, só fecha o modal (reaparece no próximo clique
#      direito). Aceitar manda QUEST_ACCEPT (o MESMO que o diálogo de NPC
#      já usa) — server/session.py::_handle_quest_accept não exige
#      proximidade de NPC, só quest_id, então funciona igual vindo daqui.
#
# Objetivo típico da quest concedida: `collect_item` com `loot_item` igual
# ao NOME deste item — como o item já está na bag no momento de aceitar
# (não foi coletado DEPOIS), quest_logic.py::try_start pré-completa esse
# objetivo automaticamente (senão nunca fecharia, já que o evento
# "collect_item" só dispara em pickups NOVOS).
# ---------------------------------------------------------------------------

ITEM_GRANTS_QUEST: dict[str, str] = {
    "Artefato Extremamente Misterioso": "artefato_misterioso",
}


# ---------------------------------------------------------------------------
# Tabela de quests
# ---------------------------------------------------------------------------

QUESTS: dict[str, QuestDef] = {

    # ── Introdução ───────────────────────────────────────────────────────────
    "first_blood": QuestDef(
        title="Primeiro Sangue",
        description="Este mundo não perdoa os fracos. Se quer sobreviver aqui, precisa provar que tem coragem. "
                    "Vá lá fora e mate seu primeiro inimigo.",
        objectives=(
            ObjectiveDef(type="kill", target="*", count=1),
        ),
        reward=QuestReward(xp=50),
        # ("prova_valor",) com vírgula: sem ela é STRING, e o check de
        # pré-requisito itera letra por letra ('p','r','o'...) — nunca passa.
        requires=("prova_valor",),
        next_quest="survivor",
        completion="Sabia que você conseguiria. Todo guerreiro começa com o primeiro sangue — "
                   "o resto é só questão de prática. Continue assim.",
    ),

    "survivor": QuestDef(
        title="Sobrevivente",
        description="Sobreviver não basta — você precisa crescer. "
                    "Cada batalha deve te deixar mais forte. Alcance o Nível 3.",
        objectives=(
            ObjectiveDef(type="reach_level", count=3),
        ),
        reward=QuestReward(xp=120, gold=5),
        requires=("first_blood",),
        completion="Nível 3 já! Você está evoluindo mais rápido do que eu esperava. "
                   "Tome essa recompensa — vai precisar dela nas batalhas que estão por vir.",
    ),

    # ── Caça ─────────────────────────────────────────────────────────────────
    "bear_hunter": QuestDef(
        title="Caçador de Ursos",
        description="Os ursos dessa região estão cada vez mais agressivos e já atacaram alguns aldeões. "
                    "Faça um favor a todos e abata 5 deles.",
        objectives=(
            ObjectiveDef(type="kill", target="Urso", count=5),
        ),
        reward=QuestReward(xp=200, gold=10),
        next_quest="bear_pelt",
        completion="Cinco ursos! Impressionante. A região já está mais segura graças a você. "
                   "Aliás, me lembrei de outra coisa que preciso...",
    ),

    "bear_pelt": QuestDef(
        title="Peles Valiosas",
        description="Já que você está caçando ursos de qualquer forma, traga-me 3 pelos. "
                    "Valem bom dinheiro e não quero desperdiçar.",
        objectives=(
            ObjectiveDef(
                type="collect_item",
                target="Urso",
                count=3,
                loot_item="Pelo de Urso",
                loot_chance=0.75,
            ),
        ),
        reward=QuestReward(xp=150, gold=15),
        requires=("bear_hunter",),
        completion="Perfeitos. Esses pelos vão render bem no mercado. "
                   "Aqui está sua parte — você merece.",
    ),

    "wolf_fangs": QuestDef(
        title="Presas Afiadas",
        description="Preciso de presas de lobo para um remédio especial. "
                    "Traga 5 delas e pagarei bem.",
        objectives=(
            ObjectiveDef(
                type="collect_item",
                target="Lobo",
                count=5,
                loot_item="Presa de Lobo",
                loot_chance=0.6,
            ),
        ),
        reward=QuestReward(xp=180, gold=12),
        completion="Exatamente o que eu precisava. Com essas presas vou conseguir preparar o remédio. "
                   "Você acabou de salvar uma vida sem nem saber.",
    ),

    "spider_venom": QuestDef(
        title="Veneno Mortal",
        description="As aranhas dessa região produzem um veneno que uso para tratar mordidas de serpente. "
                    "Colete 3 frascos para mim.",
        objectives=(
            ObjectiveDef(
                type="collect_item",
                target="Aranha",
                count=3,
                loot_item="Veneno de Aranha",
                loot_chance=0.7,
            ),
        ),
        reward=QuestReward(xp=130, gold=10),
        completion="Ótimo trabalho. Poucos têm coragem de se aproximar dessas criaturas. "
                   "Seu serviço foi inestimável.",
    ),

    "beast_slayer": QuestDef(
        title="Matador de Feras",
        description="As feras desta floresta estão se tornando um problema sério. "
                    "Apenas guerreiros experientes deveriam tentar enfrentá-las. "
                    "Se você se acha capaz, elimine 10 delas.",
        objectives=(
            ObjectiveDef(type="kill", target="Fera", count=10),
        ),
        reward=QuestReward(xp=300, gold=20),
        requires=("first_blood",),
        level_req=5,
        completion="Dez feras! Você é uma força da natureza. A floresta está em paz graças a você — "
                   "por enquanto, pelo menos.",
    ),

    # ── Quests Guerreiro ───────────────────────────────────────────────────────────


    "executioner": QuestDef(
        title="O Executor",
        description="Há uma arte em acabar com inimigos enfraquecidos de forma eficiente. "
                    "Use Executar 5 vezes para provar seu domínio.",
        objectives=(
            ObjectiveDef(type="use_skill", target="executar", count=5),
        ),
        reward=QuestReward(xp=150, gold=8),
        class_req="guerreiro",
        requires=("warrior_trial",),
        level_req=3,
        completion="Cinco execuções. Frio, calculista, eficiente. "
                   "Você tem o que é preciso para ser um verdadeiro executor.",
    ),    

    # ── Quests Guerreiro ───────────────────────────────────────────────────────────

    "bem_vindo_guerreiro": QuestDef(
        title="Bem-vindo!",
        description="Bem-vindo, {player_name}. Chega de mamar nas tetas da vila — lá fora tem gente " \
                    "morrendo por muito menos que um pedaço de pão, e o Império não vai mandar ninguém pra te proteger. " \
                    "Guerreiro que se preza aprende rápido: os magos queimam à distância, os arqueiros furam de longe, " \
                    "mas quem segura a linha e leva o corte primeiro é você. E vai precisar de companhia — sozinho, " \
                    "essa terra come qualquer um vivo. Vá falar com Avido Faseo, ele é treinador dos que sobrevivem " \
                    "ao primeiro ano. Ele te dá o que precisa pra começar.",
        objectives=(
            ObjectiveDef(type="talk_to_npc", target="Avido Faseo", count=1),
        ),
        reward=QuestReward( xp=10, 
                            choice=("training_sword", "training_mace", "training_axe")),
        class_req=  "guerreiro",
        completion= "Bem-vindo ao lado cruel da vida, pirralho. Daqui pra frente esquece conforto — " \
                    "aqui a gente sangra antes de aprender a sorrir. Escolha uma arma. " \
                    "Com ela você vai dar seus primeiros golpes... e, se tiver sorte, " \
                    "vai sobreviver o suficiente pra dar os segundos.",
    ),

    "primeiros_golpes": QuestDef(
        title="Primeiros Golpes",
        description="Agora que já empunha uma arma, escuta bem, porque eu não repito. "
                    "No começo, tudo parece fácil — mas não se acostume. "
                    "À medida que você evolui, os desafios crescem junto: o medo, o sangue, "
                    "as mortes vão te consumindo aos poucos. Pra não desistir no meio do caminho, "
                    "você vai precisar de foco — sem desviar o olhar. "
                    "Mas antes de qualquer coisa: eu não ensino golpe nenhum pra quem nem sabe "
                    "segurar essa arma direito. Vai até o boneco ali na frente e desfira uns "
                    "golpes básicos. Quero ver se seu braço aguenta o peso do aço.",
        objectives=(
            ObjectiveDef(type="auto_attack_hit", target="*", count=6),
        ),
        reward=QuestReward( xp=15, 
                            skill="golpe_poderoso"),
        class_req=  "guerreiro",
        requires=("bem_vindo_guerreiro",),
        completion= "Não foi elegante, mas serviu. Seu braço já não treme tanto quanto antes. "
                    "Acho que está na hora de te ensinar algo de verdade: preste atenção, "
                    "porque não vou repetir o movimento duas vezes. Isso aqui é o Golpe "
                    "Poderoso — um golpe que evolui junto com você, conforme fica mais forte. "
                    "Guarde bem essa lição.",
    ),    

    "prova_valor": QuestDef(
        title="Prova de Valor",
        description="Agora que já conhece o Golpe Poderoso, quero ver ele em ação. "
                    "Vai até o boneco e desfira-o algumas vezes. Dessa vez eu vou estar "
                    "olhando de verdade. Quero ver se essa arma foi um bom investimento, "
                    "ou se eu devia ter dado ela pra outro recruta.",
        objectives=(
            ObjectiveDef(type="use_skill", target="golpe_poderoso", count=3,
                        params={"on_dummy": True}),
        ),
        requires= ["primeiros_golpes"],
        reward=QuestReward(xp=25),
        class_req="guerreiro",
        completion= "Sua arma não foi desperdício, isso eu reconheço. "
                    "O problema é que eu não sabia que você já batia tão forte — "
                    "o boneco de treino ficou irreconhecível. "
                    "Bom... pelo menos agora sei que não vou perder tempo com você.",
    ),


     # ── Quests Mago ───────────────────────────────────────────────────────────────

    "iniciacao_arcana": QuestDef(
        title="Iniciação Arcana",
        description="Então você se diz ser um mago? Que tipo de mago se quer usa alguma magia? "
                    "Bom vamos lá, não tenho tempo a perder, eu vou lhe conceder treinamento a uma "
                    "habilidade sem custos para você iniciar. Após aprender a habilidade, treine em "
                    "um desses bonecos de treino aqui na frente, vou ficar de olho",
        objectives=(
            ObjectiveDef(type="learn_skill", target="bola_de_fogo", count=1),
            ObjectiveDef(type="use_skill", target="bola_de_fogo", count=5,
                        params={"on_dummy": True}),
        ),
        reward=QuestReward(xp=80),
        class_req="mago",
        completion="Pelo visto você tem jeito pra coisa, mas na próxima vez tente pausar um pouco "
                   "entre os ataques, para não queimar o boneco de treino, você sabe quanto eles custam?",
    ),

    "coach_quantic": QuestDef(
        title="Conheça seu mentor",
        description="Olá Guerreiro, você chegou em boa hora, precisamos de muita força nova "
                    "para nos ajudar a lidar com alguns problemas. ",
        objectives=(
            ObjectiveDef(type="learn_skill", target="golpe_poderoso", count=1),
            ObjectiveDef(type="use_skill", target="golpe_poderoso", count=6,
                        params={"on_dummy": True}),
        ),
        reward=QuestReward(xp=80),
        class_req="guerreiro",
        completion="Sua escolha faz sentido, você provou seu valor. "
                   "O problema é que eu não sabia que você era forte, o boneco de treino"
                   "ficou todo desfigurado. Sniff...",
    ),

    # ── Social ────────────────────────────────────────────────────────────────
    "merchant_greeting": QuestDef(
        title="Contatos Locais",
        description="Conhecer os comerciantes da região é essencial para qualquer aventureiro. "
                    "Vá falar com um mercador.",
        objectives=(
            ObjectiveDef(
                type="talk_to_npc", 
                target="Fabian Hardek", 
                count=1),
        ),
        reward=QuestReward(xp=30, gold=5),
        completion="Bons contatos valem ouro nesse mundo. Você está aprendendo rápido.",
    ),

    "atividade_suspeita": QuestDef(
        title="Atividade Suspeita",
        description="Há algum tempo vejo que alguns aventureiros entram nessa caverna "
                    "aqui ao lado e não voltam. Estou desconfiado que aconteceu algo, mas não tenho coragem de entrar. "
                    "Explore a caverna e veja o que está acontecendo.",
        objectives=(
            ObjectiveDef(
                type="reach_tile",
                target="maps/map_cave_west.csv",
                location=(4, 4, 33, 33),
                count=1,
            ),
        ),
        reward=QuestReward(xp=150, gold=10),
        completion="Que bizarro, isso quer dizer que talvez eles tenham sido comidos por esses monstros? "
                   "Precisamos fazer algo a respeito disso.",
    ),
    "report_coveiro": QuestDef(
        title="Reporte o Coveiro",
        description="Tenho um amigo coveiro chamado Custodio, ele disse que no cemitério " 
        "tem uns monstros parecidos. Vá até ele no cemitério da Luz da Lua e reporte o que está " 
        "acontecendo aqui na caverna, talvez o ajude em algo",
        objectives=(
            ObjectiveDef(
                type="talk_to_npc",
                target="Custodio Benevide",
                count=1,
            ),
        ),
        reward=QuestReward(xp=125, gold=25),
        requires= ["atividade_suspeita"],
        completion="Ferdinando te mandou aqui? " \
        "Nossa, eu achei que era só aqui, esses malditos desmiolados são lentos e fracos " \
        "mas baixe a guarda e vai ver quantos deles estarão em cima de você, eu já tentei de tudo para " \
        "prendê-los de alguma forma, mas eles são pacientes e nunca desistem. Quer me ajudar com isso?",
    ),
    "de_volta_a_terra": QuestDef(
        title="De volta a terra",
        description="Precisamos descobrir como dar um jeito nesses desmiolados, eu os chamo assim, " \
        "mas cada um que vem aqui chama eles de um jeito, não existe um consenso, não que isso seja" \
        " um problema, desde que estejam a sete palmos e não voltem mais." \
        "Mate 10 desmiolados para que eu possa enterrá-los novamente.",
        objectives=(
            ObjectiveDef(
                type="kill",
                target="Zumbi",
                count=12,
            ),
        ),
        reward=QuestReward(xp=60, gold=15),
        requires= ["report_coveiro"],
        completion="Isso já me ajuda muito!" \
        "Há dias estou tentando lidar com esse problema, mas você resolveu isso com apenas alguns golpes." \
        "Você é realmente talentoso, continue usando essa força contra nossos inimigos!" \
        "A propósito, tenho um novo desafio para você!",
    ),

    # ── Equipamento ───────────────────────────────────────────────────────────
    "first_equip": QuestDef(
        title="Armado e Perigoso",
        description="Equipe uma arma.",
        objectives=(
            ObjectiveDef(type="equip_item", target="weapon", count=1),
        ),
        reward=QuestReward(xp=60),
    ),

    # ── Teste (Fase M4 — item concede quest, 25/07/2026) ────────────────────
    # Quest de VALIDAÇÃO simples, sem compromisso com o conteúdo final —
    # oferecida ao saquear "Artefato Extremamente Misterioso" (ver
    # ITEM_GRANTS_QUEST acima), aceita via popup no clique direito do item
    # (client/inventory_handlers.py). Objetivo collect_item com o PRÓPRIO
    # artefato como loot_item: como o item já está na bag no momento de
    # aceitar (não foi "coletado" DEPOIS), quest_logic.py::try_start já
    # nasce esse objetivo completo (ver comentário lá) — falta só entregar
    # a algum NPC. Entrega via turn_in_ids no NPC escolhido no mapa (mesmo
    # mecanismo de qualquer outra quest) — usuário decide qual NPC recebe,
    # ainda não configurado.
    "artefato_misterioso": QuestDef(
        title="O Artefato Misterioso",
        description="Você encontrou algo estranho — um artefato que não "
                    "parece ter vindo daqui. Talvez valha a pena perguntar "
                    "a alguém que já viu muita coisa esquisita por aí.",
        objectives=(
            ObjectiveDef(type="collect_item", target="*",
                        loot_item="Artefato Extremamente Misterioso", count=1),
        ),
        reward=QuestReward(xp=15),
    ),
}
