"""
server/world_server.py
ECS headless — roda toda a lógica do jogo SEM Pygame.

Separação de responsabilidades:
  WorldServer   → estado do mundo + tick loop
  SystemsServer → sistemas de lógica (movimento, combate, IA, skills)

Regra: NADA aqui pode importar pygame.
"""
from __future__ import annotations
import asyncio
import time
import sys
import os

# Adiciona a raiz do projeto ao path para importar os módulos originais
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world import World
from shared.constants import TICK_RATE, TICK_INTERVAL, SNAPSHOT_HISTORY


class WorldServer:
    """
    Gerencia o estado do mundo e o loop de ticks.

    Responsabilidades:
    - Manter o World (ECS registry)
    - Rodar os sistemas de lógica a cada tick
    - Guardar histórico de snapshots para lag compensation
    - Notificar SessionManager sobre mudanças para envio aos clientes
    """

    def __init__(self, zone_id: str = "world_main"):
        self.zone_id    = zone_id
        self.world      = World()
        self.tick_count = 0
        self.running    = False

        # Histórico de snapshots: list[(tick, snapshot_dict)]
        # Usado por lag compensation (skills de cone, projéteis)
        self._snapshots: list[tuple[int, dict]] = []

        # Callbacks registrados pelo SessionManager
        # on_tick(tick_count, deltas) — chamado ao fim de cada tick
        self._on_tick_callbacks: list = []

        # Sistemas de lógica (inicializados em _init_systems)
        self._systems: list = []

        self._init_systems()

    def _init_systems(self) -> None:
        """
        Instancia os sistemas que rodam no servidor.
        Apenas sistemas de LÓGICA — sem render, sem input de teclado.
        """
        # Os sistemas serão adicionados progressivamente nas próximas fases.
        # Por agora, placeholder para o loop funcionar.
        self._systems = []

    def register_on_tick(self, callback) -> None:
        """Registra callback chamado ao fim de cada tick com os deltas do mundo."""
        self._on_tick_callbacks.append(callback)

    async def run(self) -> None:
        """Loop principal do servidor. Roda a TICK_RATE ticks/segundo."""
        self.running = True
        print(f"[WorldServer] zona='{self.zone_id}' iniciada @ {TICK_RATE} ticks/s")

        next_tick = time.perf_counter()
        while self.running:
            now = time.perf_counter()
            if now >= next_tick:
                dt = TICK_INTERVAL
                self._tick(dt)
                next_tick += TICK_INTERVAL

                # Cede controle ao event loop sem acumular atraso
                if time.perf_counter() - next_tick > TICK_INTERVAL:
                    next_tick = time.perf_counter()
            else:
                await asyncio.sleep(0)   # yield sem bloquear

    def _tick(self, dt: float) -> None:
        """Executa um tick: roda sistemas, coleta deltas, notifica callbacks."""
        self.tick_count += 1

        # Roda sistemas de lógica
        for system in self._systems:
            system.update(dt=dt)

        # Coleta deltas do mundo neste tick
        deltas = self._collect_deltas()

        # Guarda snapshot para lag compensation
        self._store_snapshot()

        # Notifica SessionManager (que enviará pacotes aos clientes)
        for cb in self._on_tick_callbacks:
            cb(self.tick_count, deltas)

    def _collect_deltas(self) -> dict:
        """
        Coleta o que mudou neste tick para enviar como AOI_UPDATE.
        Por agora retorna dict vazio — será implementado ao adicionar entidades.
        """
        return {
            "moved":     [],   # entidades que se moveram
            "stats":     [],   # HP/MP que mudaram
            "effects":   [],   # efeitos aplicados/removidos
            "spawned":   [],   # entidades que nasceram
            "despawned": [],   # entidades que morreram/saíram
        }

    def _store_snapshot(self) -> None:
        """
        Guarda snapshot leve do estado atual (posições das entidades).
        Mantém apenas os últimos SNAPSHOT_HISTORY snapshots.
        """
        # Snapshot mínimo: posição de todas as entidades com Position
        from components import Position, TileMovement
        snapshot: dict[int, tuple[int, int]] = {}
        for eid, tm in self.world.get_entities_with(TileMovement):
            snapshot[eid] = (tm.current_tile_x, tm.current_tile_y)

        self._snapshots.append((self.tick_count, snapshot))
        if len(self._snapshots) > SNAPSHOT_HISTORY:
            self._snapshots.pop(0)

    def get_snapshot_at(self, tick: int) -> dict:
        """Retorna o snapshot mais próximo do tick solicitado (lag compensation)."""
        if not self._snapshots:
            return {}
        # Busca o tick mais próximo sem exceder
        best = self._snapshots[0][1]
        for t, snap in self._snapshots:
            if t <= tick:
                best = snap
            else:
                break
        return best

    def stop(self) -> None:
        self.running = False
        print(f"[WorldServer] zona='{self.zone_id}' encerrada no tick {self.tick_count}")
