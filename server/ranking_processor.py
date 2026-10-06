"""
server/ranking_processor.py
Mixin para WorldServer: ranking geral (06/10/2026, pedido do usuário) —
nível de personagem + nível de skill (machado/espada/maça/arco/báculo/
escudo/defesa/resistências/magia). Inclui TODOS os personagens já
criados, online ou não (decisão explícita do usuário) — por isso a
consulta real vive no banco (`server/auth.py::get_ranking`, único ponto
de acesso ao banco), nunca varrendo só quem está conectado agora.
"""
from __future__ import annotations


class RankingProcessorMixin:

    async def process_ranking_request(self, category: str) -> list:
        from server import auth
        return await auth.get_ranking(category)
