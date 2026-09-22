# tests/conftest.py
"""
Hook global de suíte — força coleta de lixo (inclusive referências
circulares) depois de CADA teste (22/09/2026, pedido do usuário: suíte
completa chegava a ~11,2GB de memória, derrubando o container do
Postgres/qualquer outra coisa rodando na máquina por falta de RAM).

Causa raiz medida (não suposição — ver PROBLEMAS_ARQUITETURA.md §199):
não é vazamento de memória de verdade, é o coletor de lixo cíclico do
Python não rodando rápido o bastante. Arquitetura ECS cria referências
circulares naturalmente (World↔Entity↔Component↔System se referenciando
de volta) — cada teste que cria um World/WorldServer novo deixa lixo
cíclico pra trás, e ao longo de ~1700 testes isso se acumula mais rápido
do que o coletor automático (baseado em contagem de alocações, não em
tempo) dá conta de limpar sozinho.

Medido: sem isto, pico de ~11,2GB (RAM livre da máquina caindo a
~260MB). Com isto, pico de ~300MB — mesmos testes passando, ~15% mais
lento no total (356s vs 311s). Escopo 100% test-infra — não toca em
nenhum arquivo de `server/`, `client/`, `engine/` ou `ui/`.
"""
import gc


def pytest_runtest_teardown(item, nextitem):
    gc.collect()
