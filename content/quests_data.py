"""
quests_data.py — Dados puros do sistema de quests (sem lógica).

Adicionar uma nova quest: inserir uma entrada em QUESTS.
Adicionar um item de quest: inserir uma lambda em QUEST_ITEMS.

Tipos de objetivo (ObjectiveDef.type):
    kill              Matar N inimigos. target = nome | raça | "*" (qualquer)
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

    Detalhes de implementação (protocolo/servidor/UI) em
    arquitetura/ARQUITETURA_ONLINE.md §34.46.
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
    "Pelo de Urso":     lambda: Item("Pelo de Urso",     "material", slot=None, rarity="common", value=3, max_stack=10),
    "Presa de Lobo":    lambda: Item("Presa de Lobo",    "material", slot=None, rarity="common", value=2, max_stack=10),
    "Veneno de Aranha": lambda: Item("Veneno de Aranha", "material", slot=None, rarity="common", value=4, max_stack=10),
    "Cauda de Escorpião": lambda: Item("Cauda de Escorpião", "material", slot=None, rarity="common", value=3, max_stack=10),
    "Escama de Cobra":  lambda: Item("Escama de Cobra",  "material", slot=None, rarity="common", value=2, max_stack=10),
    "Osso de Goblin":   lambda: Item("Osso de Goblin",   "material", slot=None, rarity="common", value=2, max_stack=10),
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
        description="Seja bem-vendo {player_name}! Você já está bem grandinho, está na hora de conhecer " \
                    "o mundo lá fora. Você fez uma boa escolha, guerreiros são necessparios para" \
                    "manter os magos e os arqueiros livres para eliminar os oponentes. E aqui você" \
                    "encontrará oponentes com frequência, muitas vezes precisará criar um grupo para" \
                    "lidar com eles." \
                    "Fale com seu treinador, ele se chama Avido Faseo, ele lhe fornecerá equipamento" \
                    "e treinamento para iniciar sua jornada",
        objectives=(
            ObjectiveDef(type="talk_to_npc", target="Avido Faseo", count=1),
        ),
        reward=QuestReward(xp=15, items=("training_sword",)),
        class_req=  "guerreiro",
        completion= "Bem-vindo ao lado cruel da vida, prepáre-se pois daqui pra frente a vida não será" \
                    "um morango. Tome uma espada, com ela você fará seus primeiros movimentos",
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

    "prova_valor": QuestDef(
        title="Prova de Valor",
        description="Então você escolheu ser um guerreiro. "
                    "Preciso te contar uma coisa, no começo, será fácil, mas não se acostume "
                    "A medida que você vai evoluindo, os desafios são maiores, "
                    "o medo, o sangue, as mortes vão cada vez de consumindo, "
                    "para você não desistir, terá que focar no seu objetivo, e não se desviar."
                    "Além disso, você precisa aprender alguns golpes, irei te ensinar um golpe "
                    "extremamente poderoso que conforme você evolui, esse golpe evluirá também."
                    "Prove seu valor, vou lhe ensinar um golpe poderoso, assim que aprender"
                    "desfira-o algumas vezes no boneco de treino aqui na frente.",
        objectives=(
            ObjectiveDef(type="learn_skill", target="golpe_poderoso", count=1),
            ObjectiveDef(type="use_skill", target="golpe_poderoso", count=6,
                        params={"on_dummy": True}),
        ),
        requires= ["bem_vindo"],
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
}
