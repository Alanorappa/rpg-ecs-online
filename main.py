# main.py
import os
import sys

# Build (PyInstaller): CWD = pasta do exe ANTES de qualquer import de jogo —
# todos os caminhos relativos ("assets/...", "maps/...", logs/, saves/)
# passam a resolver ao lado do executável, mesmo se o atalho definir outro
# diretório de trabalho.
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

import pygame
import config
from game import GameEngine


def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="RPG ECS")
    p.add_argument("--user",     default="", help="Login direto (dev)")
    p.add_argument("--password", default="", help="Senha direta (dev)")
    p.add_argument("--host",     default="", help="Servidor host")
    p.add_argument("--port",     type=int, default=0, help="Servidor porta")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    pygame.init()
    cfg   = config.load()
    scale = cfg.get("scale", 1.0)

    if args.host:
        cfg["server_host"] = args.host
        config.save({"server_host": args.host})
    if args.port:
        cfg["server_port"] = args.port
        config.save({"server_port": args.port})

    W = int(1280 * scale)
    H = int(720  * scale)
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("RPG ECS [ONLINE]")

    host = cfg.get("server_host", "localhost")
    port = int(cfg.get("server_port", 8765))

    # ── Etapa 1: Login / Cadastro ─────────────────────────────────────────────
    if args.user and args.password:
        # Modo dev: --user/--password pula a tela de login, conecta diretamente
        from client.network import NetworkClient
        import time as _t
        net = NetworkClient(host=host, port=port)
        net.connect()
        _deadline = _t.time() + 10.0
        while not net.connected and _t.time() < _deadline:
            _t.sleep(0.05)
        net.login(args.user, args.password)
        char_list = []
        _deadline2 = _t.time() + 10.0
        while _t.time() < _deadline2:
            for mt, payload, _s, _ts in net.poll():
                from shared.messages import MsgType as _MT
                if mt == _MT.AUTH_OK:
                    char_list = payload.get("characters", [])
                    break
            else:
                _t.sleep(0.02)
                continue
            break
        net_user, net_pass = args.user, args.password
    else:
        from login_screen import run as _login_run
        result = _login_run(screen, host, port)
        if result is None:
            pygame.quit()
            raise SystemExit(0)
        net_user, net_pass, net, char_list = result

    # ── Etapa 2: Seleção / Criação de Personagem ──────────────────────────────
    from char_creation_screen import run_online as _char_run
    ok = _char_run(screen, char_list, net)
    if not ok:
        # Usuário voltou ao login → reinicia (recursão simples via re-exec ou loop)
        # Por ora apenas encerra; o usuário pode reabrir o jogo
        pygame.quit()
        raise SystemExit(0)

    # ── Etapa 3: Jogo ─────────────────────────────────────────────────────────
    pygame.display.quit()
    pygame.display.init()
    game_engine = GameEngine(
        scale=scale, char_data=None, save_slot=0,
        net_user=net_user, net_pass=net_pass,
        net_client=net,          # NetworkClient já conectado, com LOGIN_OK na fila
    )
    try:
        game_engine.run()
    except Exception:
        # Build sem console: crash silencioso é indepurável no alpha —
        # grava traceback em crash.log ao lado do exe pro testador anexar.
        import traceback
        with open("crash.log", "w", encoding="utf-8") as _f:
            traceback.print_exc(file=_f)
        raise
