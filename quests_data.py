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
"""
from __future__ import annotations
from typing import NamedTuple
from components import Item


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
    xp:   int = 0
    gold: int = 0


class QuestDef(NamedTuple):
    title:       str
    description: str
    objectives:  tuple           # tuple[ObjectiveDef, ...]
    reward:      QuestReward
    auto_start:  bool  = False   # inicia automaticamente sem NPC
    repeatable:  bool  = False   # reseta ao completar
    requires:    tuple = ()      # tuple[quest_id, ...] pré-requisitos
    next_quest:  str   = ""      # quest_id a iniciar automaticamente ao completar
    level_req:   int   = 0       # nível mínimo para aceitar a quest
    completion:  str   = ""      # texto do NPC ao receber a entrega (vazio = usa título)


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

    # ── Habilidades ───────────────────────────────────────────────────────────
    "warrior_trial": QuestDef(
        title="Prova do Guerreiro",
        description="Todo guerreiro precisa dominar suas habilidades em combate real. "
                    "Use Golpe Poderoso 3 vezes em batalha.",
        objectives=(
            ObjectiveDef(type="use_skill", target="golpe_poderoso", count=3),
        ),
        reward=QuestReward(xp=80),
        next_quest="executioner",
        completion="Sua técnica está melhorando. Um golpe poderoso na hora certa decide batalhas. "
                   "Mas você ainda tem muito a aprender...",
    ),

    "executioner": QuestDef(
        title="O Executor",
        description="Há uma arte em acabar com inimigos enfraquecidos de forma eficiente. "
                    "Use Executar 5 vezes para provar seu domínio.",
        objectives=(
            ObjectiveDef(type="use_skill", target="executar", count=5),
        ),
        reward=QuestReward(xp=150, gold=8),
        requires=("warrior_trial",),
        level_req=3,
        completion="Cinco execuções. Frio, calculista, eficiente. "
                   "Você tem o que é preciso para ser um verdadeiro executor.",
    ),

    # ── Social ────────────────────────────────────────────────────────────────
    "merchant_greeting": QuestDef(
        title="Contatos Locais",
        description="Conhecer os comerciantes da região é essencial para qualquer aventureiro. "
                    "Vá falar com um mercador.",
        objectives=(
            ObjectiveDef(type="talk_to_npc", target="*", count=1),
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
        completion="Então é isso que está acontecendo lá dentro... Obrigado por investigar. "
                   "Precisamos fazer algo a respeito disso.",
    ),
    "report_coveiro": QuestDef(
        title="Reporte o Coveiro",
        description="Tenho um amigo coveiro que se chama Custodio Benevide, ele disse que no cemitério" \
        "está acontecendo algo parecido. Vá até ele no cemitério Freesoul e reporte o que está" \
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
        completion="Ferdinando te mandou aqui?" \
        "Nossa, eu achei que era só aqui, esses malditos desmiolados são lentos e fracos" \
        "mas baixe a guarda e vai ver quantos deles estarão em cima de você, eu já tentei de tudo para" \
        "prendê-los de alguma forma, mas eles são pacientes e nunca desistem. Quer me ajudar com isso?",
    ),
    "de_volta_a_terra": QuestDef(
        title="De volta a terra",
        description="Precisamos descobrir como dar um jeito nesses desmiolados, eu os chamo assim, " \
        "mas cada um que vem aqui chamam eles de um jeito, não existe um consenso, não que isso seja" \
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
