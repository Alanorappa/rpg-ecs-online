"""
swarm.py — CLI que sobe N `LoadBot` de verdade contra um servidor já
rodando (`provision.py` já deve ter sido rodado antes, ao menos 1x).
Rampa de conexão escalonada, distribuição mundo/arena/bg configurável,
encerramento gracioso (Ctrl+C ou `--duration-s`) — ver
arquitetura/PROBLEMAS_ARQUITETURA.md e o plano aprovado (04/09/2026).

Uso:
    py -3.10 tools/loadtest/swarm.py --count 300
    py -3.10 tools/loadtest/swarm.py --count 20 --ratio 14:3:3 --duration-s 60
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.loadtest.bot import LoadBot

_STATUS_INTERVAL_S = 10.0


def _roles_for_count(count: int, ratio: "tuple[int, int, int]") -> list:
    """Distribui `count` índices em papéis mundo/arena/bg respeitando a
    proporção — round-robin ponderado, nunca todos-mundo-depois-todos-
    arena (isso enviesaria a rampa de conexão por papel)."""
    w, a, b = ratio
    total = max(w + a + b, 1)
    roles = []
    acc = {"world": 0.0, "arena": 0.0, "bg": 0.0}
    for _ in range(count):
        acc["world"] += w / total
        acc["arena"] += a / total
        acc["bg"] += b / total
        role = max(acc, key=acc.get)
        acc[role] -= 1.0
        roles.append(role)
    return roles


def _parse_ratio(text: str) -> "tuple[int, int, int]":
    parts = text.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError('formato esperado "mundo:arena:bg", ex: "68:12:20"')
    try:
        w, a, b = (int(p) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError('formato esperado "mundo:arena:bg" (inteiros)')
    if w < 0 or a < 0 or b < 0 or (w + a + b) == 0:
        raise argparse.ArgumentTypeError("proporção precisa de ao menos 1 valor positivo")
    return (w, a, b)


async def _status_loop(bots: list, stats: dict, stop_event: asyncio.Event) -> None:
    start = time.monotonic()
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_STATUS_INTERVAL_S)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        by_state: dict = {}
        for bot in bots:
            by_state[bot.state] = by_state.get(bot.state, 0) + 1
        elapsed = time.monotonic() - start
        state_str = " ".join(f"{k}={v}" for k, v in sorted(by_state.items()))
        print(f"[{elapsed:6.0f}s] bots={len(bots)} conectados={sum(1 for b in bots if b.eid != -1)} "
             f"reconnects={stats.get('reconnects', 0)} erros={stats.get('errors', 0)} | {state_str}")


async def _run(count: int, host: str, port: int, ratio: "tuple[int, int, int]",
                batch_size: int, batch_interval_s: float, duration_s: "float | None",
                seed: int, think_min_s: float, think_max_s: float, requeue: bool) -> None:
    roles = _roles_for_count(count, ratio)
    bot_eids: set = set()
    stats: dict = {}
    bots = [
        LoadBot(i, roles[i], host, port, seed, bot_eids, think_min_s, think_max_s, requeue, stats)
        for i in range(count)
    ]

    stop_event = asyncio.Event()

    def _handle_signal():
        print("\nSinal de encerramento recebido — desconectando bots com LOGOUT...")
        stop_event.set()
        for bot in bots:
            bot.stop()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _handle_signal)
    except NotImplementedError:
        pass  # Windows: Ctrl+C ainda funciona via KeyboardInterrupt, só sem handler custom.

    async def _staggered_run(i: int, bot: LoadBot):
        await asyncio.sleep((i // batch_size) * batch_interval_s)
        await bot.run()

    print(f"Subindo {count} bot(s) contra {host}:{port} — "
         f"proporção mundo:arena:bg = {ratio[0]}:{ratio[1]}:{ratio[2]}, "
         f"lotes de {batch_size} a cada {batch_interval_s}s.")

    bot_tasks = [asyncio.create_task(_staggered_run(i, bot)) for i, bot in enumerate(bots)]
    status_task = asyncio.create_task(_status_loop(bots, stats, stop_event))

    try:
        if duration_s is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=duration_s)
            except asyncio.TimeoutError:
                print(f"\n--duration-s={duration_s} atingido — desconectando bots com LOGOUT...")
            stop_event.set()
            for bot in bots:
                bot.stop()
        else:
            await stop_event.wait()
    except KeyboardInterrupt:
        _handle_signal()

    await asyncio.gather(*bot_tasks, return_exceptions=True)
    stop_event.set()
    await status_task
    print("Todos os bots encerrados.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ratio", type=_parse_ratio, default=(68, 12, 20),
                        help='proporção "mundo:arena:bg", ex: "68:12:20" (default)')
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--batch-interval-s", type=float, default=1.0)
    parser.add_argument("--duration-s", type=float, default=None,
                        help="encerra sozinho após N segundos (default: roda até Ctrl+C)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--think-min-s", type=float, default=0.6)
    parser.add_argument("--think-max-s", type=float, default=1.8)
    parser.add_argument("--no-requeue", action="store_true",
                        help="bots de arena/bg não entram na fila de novo após uma partida acabar")
    args = parser.parse_args()

    asyncio.run(_run(
        args.count, args.host, args.port, args.ratio,
        args.batch_size, args.batch_interval_s, args.duration_s,
        args.seed, args.think_min_s, args.think_max_s, not args.no_requeue,
    ))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
