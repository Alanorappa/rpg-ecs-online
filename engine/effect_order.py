"""
effect_order.py — Ordem de ativação de buff/efeito, ponto único (01/09/2026).

Problema: a buff bar (ui/buff_bar.py) precisa mostrar TODO buff/efeito
numa fila só, mais recente sempre na mesma ponta — mas os efeitos vêm de
mecanismos DIFERENTES (StatusEffects via apply_effect(), componente
dedicado tipo FireShieldEffect/IceBlockEffect, ou uma flag simples em
CombatStats/CharacterStats). Cada mecanismo, sozinho, só sabe sua PRÓPRIA
ordem relativa (StatusEffects usa ordem de inserção do dict) — não existe
como comparar "quando" entre fontes diferentes sem um relógio comum.

Este módulo É esse relógio comum — um contador monotônico só,
incrementado toda vez que QUALQUER efeito liga pela primeira vez,
guardado no componente EffectOrder da própria entidade
(engine/components.py). Não importa gameplay nenhum (StatusEffects,
FireShieldEffect etc. continuam funcionando exatamente como sempre
funcionaram) — só existe pra UI ordenar direito.

Uso: `mark_activated(world, eid, effect_type)` UMA vez, no exato
momento em que o efeito liga (nunca em todo tick/frame — só na
transição off→on). Onde já chamar:
  - engine.core_systems.apply_effect() já chama isso sozinho — todo
    buff/debuff que passa por apply_effect (ou grant_visible_buff, que
    usa apply_effect por baixo) ganha ordenação de graça, sem precisar
    de call site novo.
  - Mecanismo com componente/flag PRÓPRIO (não passa por apply_effect):
    1 chamada no ponto onde o efeito liga de verdade — ver
    PROBLEMAS_ARQUITETURA.md pro inventário completo dos pontos atuais
    (Escudo de Fogo, Bloco de Gelo, Camuflagem, Chama Interna, Na Mosca).
"""
from __future__ import annotations

_counter: int = 0


def _next_seq() -> int:
    global _counter
    _counter += 1
    return _counter


def mark_activated(world, entity_id: int, effect_type: str) -> None:
    """Registra que `effect_type` acabou de ligar em `entity_id` — chamar
    UMA vez na transição off→on, nunca todo tick (senão o efeito vira
    sempre "o mais novo" a cada frame, quebrando a fila)."""
    from engine.components import EffectOrder
    order = world.get_component(entity_id, EffectOrder)
    if order is None:
        order = EffectOrder()
        world.add_component(entity_id, order)
    order.seq[effect_type] = _next_seq()


def get_seq(world, entity_id: int, effect_type: str) -> int:
    """Nº de ordem de `effect_type` em `entity_id` (0 = nunca registrado —
    fail-open no início da fila, nunca quebra a ordenação por falta de
    dado)."""
    from engine.components import EffectOrder
    order = world.get_component(entity_id, EffectOrder)
    if order is None:
        return 0
    return order.seq.get(effect_type, 0)
