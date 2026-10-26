"""
aliados_treino.py — 3 jogadores "aliados de treino" parados perto dos
bonecos de treino, pra testar cura/buff/talento de suporte (25/10/2026,
pedido do usuário — testar a build Raiz do Xamã).

Usa contas JÁ EXISTENTES do teste de carga (`identity.py`, nunca cria
conta nova — pedido do usuário). Os 2 primeiros DUELAM entre si em
ciclos (o duelo nunca mata: o golpe letal deixa 1 de vida) pra ficarem
machucados; o 3º fica parado de vida cheia. Fala o protocolo real, com
TLS fixado igual ao cliente do jogo.

    py -3.10 tools/loadtest/aliados_treino.py --host 5.161.104.174 --tls

Ctrl+C manda LOGOUT e encerra.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import websockets

from shared.constants import PROTOCOL_VERSION
from shared.messages import MsgType, decode, encode
from shared.auth_utils import hash_password
from tools.loadtest import map_data
from tools.loadtest.identity import bot_password, bot_username

# (índice da conta loadbotNNNN, tile de destino perto dos bonecos 115/118/121,383)
_BOTS = ((0, (117, 386)), (3, (118, 386)), (6, (120, 386)))
_STEP_S = 0.30            # cadência de passo de um jogador real
_DUEL_PAUSE_S = 35.0      # tempo machucado entre um duelo e outro (pra curar)


class Aliado:
    def __init__(self, idx: int, dest: tuple, host: str, port: int, tls: bool):
        self.idx, self.dest = idx, dest
        self.user = bot_username(idx)
        self.pw = hash_password(bot_password(idx))
        self.uri = f"{'wss' if tls else 'ws'}://{host}:{port}"
        self.tls = tls
        self.ws = None
        self.eid = -1
        self.tx = self.ty = 0
        self.hp = self.hp_max = 1
        self.name = "?"
        self.in_duel = False
        self.duel_ended_at = 0.0
        self.opponent: "Aliado | None" = None
        self.seq = 0

    async def send(self, t: MsgType, p: dict) -> None:
        self.seq += 1
        await self.ws.send(encode(t, p, seq=self.seq))

    async def recv(self, timeout=10.0):
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            try:
                return decode(raw)
            except ValueError:
                continue

    async def login(self) -> None:
        kw = {}
        if self.tls:
            import ssl
            cert = os.path.join(os.path.dirname(__file__), "..", "..", "certs", "server_cert.pem")
            ctx = ssl.create_default_context(cafile=os.path.abspath(cert))
            ctx.check_hostname = False   # certificado fixado, sem domínio
            kw["ssl"] = ctx
        self.ws = await websockets.connect(self.uri, **kw)
        await self.send(MsgType.LOGIN, {"username": self.user, "password": self.pw,
                                         "version": PROTOCOL_VERSION})
        msg = await self.recv()
        if msg[0] != MsgType.AUTH_OK:
            raise RuntimeError(f"{self.user}: login recusado {msg[1]}")
        chars = msg[1].get("characters") or []
        if not chars:
            raise RuntimeError(f"{self.user}: conta sem personagem")
        await self.send(MsgType.SELECT_CHARACTER, {"char_id": int(chars[0]["id"])})
        deadline = time.monotonic() + 15
        while self.eid == -1 and time.monotonic() < deadline:
            msg = await self.recv()
            self.handle(msg[0], msg[1])
        if self.eid == -1:
            raise RuntimeError(f"{self.user}: LOGIN_OK não chegou")
        print(f"[{self.name}] logado em ({self.tx},{self.ty}) HP {self.hp}/{self.hp_max}")

    def handle(self, t, p) -> None:
        if t == MsgType.LOGIN_OK:
            self.eid = int(p["eid"])
            ch = p.get("char") or {}
            self.name = ch.get("name", self.user)
            self.tx, self.ty = int(ch.get("tile_x", 0)), int(ch.get("tile_y", 0))
            self.hp = int(p.get("hp", 1)); self.hp_max = int(p.get("hp_max", 1)) or 1
        elif t == MsgType.STATS_UPDATE and int(p.get("player_eid", -1)) in (self.eid, -1):
            if "hp" in p:
                self.hp = int(p["hp"])
            if "hp_max" in p:
                self.hp_max = int(p["hp_max"]) or 1
        elif t == MsgType.DUEL_INVITE:
            asyncio.ensure_future(self.send(MsgType.DUEL_ACCEPT, {}))
        elif t == MsgType.DUEL_START:
            self.in_duel = True
            print(f"[{self.name}] duelo começou")
        elif t == MsgType.DUEL_END:
            self.in_duel = False
            self.duel_ended_at = time.monotonic()
            print(f"[{self.name}] duelo acabou ({p.get('reason')}) — HP {self.hp}/{self.hp_max}")

    async def recv_loop(self) -> None:
        async for raw in self.ws:
            try:
                t, p, _s, _ts = decode(raw)
            except ValueError:
                continue
            self.handle(t, p)

    async def walk_to_dest(self) -> None:
        path = await asyncio.to_thread(map_data.find_path, (self.tx, self.ty), self.dest)
        if not path:
            print(f"[{self.name}] sem caminho até {self.dest}, fica onde está")
            return
        for tx, ty in path:
            if (tx, ty) == (self.tx, self.ty):
                continue
            await self.send(MsgType.MOVE, {"tx": tx, "ty": ty})
            self.tx, self.ty = tx, ty
            await asyncio.sleep(_STEP_S)
        print(f"[{self.name}] chegou em {self.dest}")

    async def duel_loop(self) -> None:
        """Só o 1º do par chama: convida, os dois se atacam até o duelo
        acabar, pausa machucados, repete."""
        assert self.opponent is not None
        while True:
            if not self.in_duel and time.monotonic() - self.duel_ended_at >= _DUEL_PAUSE_S:
                await self.send(MsgType.DUEL_REQUEST, {"target_eid": self.opponent.eid})
                await asyncio.sleep(2.0)
            if self.in_duel:
                await self.send(MsgType.AUTO_ATTACK, {"tid": self.opponent.eid})
                await self.opponent.send(MsgType.AUTO_ATTACK, {"tid": self.eid})
            await asyncio.sleep(2.0)

    async def logout(self) -> None:
        try:
            await self.send(MsgType.LOGOUT, {})
            await self.ws.close()
        except Exception:
            pass


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--tls", action="store_true")
    ap.add_argument("--sem-duelo", action="store_true", help="todos parados de vida cheia")
    a = ap.parse_args()

    bots = [Aliado(i, d, a.host, a.port, a.tls) for i, d in _BOTS]
    for b in bots:
        await b.login()
    tasks = [asyncio.create_task(b.recv_loop()) for b in bots]
    await asyncio.gather(*(b.walk_to_dest() for b in bots))
    if not a.sem_duelo:
        bots[0].opponent, bots[1].opponent = bots[1], bots[0]
        tasks.append(asyncio.create_task(bots[0].duel_loop()))
    print("Aliados prontos. Ctrl+C pra encerrar.")
    try:
        await asyncio.gather(*tasks)
    finally:
        for b in bots:
            await b.logout()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
