# main.py
import os
import sys

# Build (PyInstaller): CWD = pasta do exe ANTES de qualquer import de jogo —
# todos os caminhos relativos ("assets/...", "maps/...", logs/, saves/)
# passam a resolver ao lado do executável, mesmo se o atalho definir outro
# diretório de trabalho.
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))


def _make_dpi_aware() -> None:
    """Windows: um processo "DPI-unaware" (padrão sem manifesto) faz o
    Windows aplicar bitmap-stretch de compatibilidade em CIMA da janela
    inteira, desde o primeiro frame — inclusive a tela de login, antes de
    qualquer set_mode/SCALED do jogo entrar em cena. Sintoma relatado
    (24/07/2026): janela abre parecendo "tela cheia" (maior que o
    pedido) com fontes borradas/esticadas — é exatamente esse
    bitmap-stretch do Windows compensando o processo "mentir" que roda a
    96 DPI. DEVE rodar ANTES de pygame.init()/qualquer set_mode.
    Cascata de fallback (só tenta o próximo se o atual não existir/falhar
    — nunca deixar isso derrubar o boot): Per-Monitor V2 (Win10 1703+) →
    Per-Monitor (Win 8.1+) → System DPI Aware (Vista+, sempre disponível).
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return
    except (AttributeError, OSError):
        pass
    try:
        PROCESS_PER_MONITOR_DPI_AWARE = 2
        if ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


_make_dpi_aware()

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


def _connect_and_login(host: str, port: int, username: str, password: str):
    """Conecta e loga sem UI — usado no fast-path --user/--password e ao
    reconectar depois de "Deslogar" (reaproveita usuário/senha já guardados
    em memória, sem pedir de novo — mesmo mecanismo de re-login automático
    que client/network.py::NetworkClient já usa pra reconexão por queda de
    conexão, só que aqui é um NetworkClient novo, não uma retomada do
    antigo). Retorna (net, char_list)."""
    from client.network import NetworkClient
    from shared.messages import MsgType as _MT
    import time as _t

    net = NetworkClient(host=host, port=port)
    net.connect()
    _deadline = _t.time() + 10.0
    while not net.connected and _t.time() < _deadline:
        _t.sleep(0.05)
    net.login(username, password)

    char_list = []
    _deadline2 = _t.time() + 10.0
    while _t.time() < _deadline2:
        got_auth = False
        for mt, payload, _s, _ts in net.poll():
            if mt == _MT.AUTH_OK:
                char_list = payload.get("characters", [])
                got_auth = True
                break
        if got_auth:
            break
        _t.sleep(0.02)
    return net, char_list


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

    # --user/--password só pula a tela de login na PRIMEIRA volta — se o
    # jogador apertar ESC na seleção de personagem (quer trocar de conta),
    # a volta seguinte já mostra a tela de login de verdade.
    use_dev_login = bool(args.user and args.password)

    while True:
        # ── Etapa 1: Login / Cadastro ─────────────────────────────────────────────
        if use_dev_login:
            use_dev_login = False
            net, char_list = _connect_and_login(host, port, args.user, args.password)
            net_user, net_pass = args.user, args.password
        else:
            from ui.login_screen import run as _login_run
            result = _login_run(screen, host, port)
            if result is None:
                pygame.quit()
                raise SystemExit(0)
            net_user, net_pass, net, char_list = result

        # ── Etapa 2/3: Seleção de personagem <-> Jogo ─────────────────────────────
        # ESC na seleção de personagem => volta pro login (outra conta).
        # "Deslogar" dentro do jogo => reconecta com a MESMA conta e volta
        # direto pra seleção de personagem, sem pedir login de novo.
        back_to_login = False
        while True:
            from ui.char_creation_screen import run_online as _char_run
            ok = _char_run(screen, char_list, net)
            if not ok:
                net.disconnect()
                back_to_login = True
                break

            pygame.display.quit()
            pygame.display.init()
            game_engine = GameEngine(
                scale=scale, char_data=None, save_slot=0,
                net_user=net_user, net_pass=net_pass,
                net_client=net,          # NetworkClient já conectado, com LOGIN_OK na fila
            )
            try:
                action = game_engine.run()
            except Exception:
                # Build sem console: crash silencioso é indepurável no alpha —
                # grava traceback em crash.log ao lado do exe pro testador anexar.
                import traceback
                with open("crash.log", "w", encoding="utf-8") as _f:
                    traceback.print_exc(file=_f)
                raise

            if action != "logout":
                raise SystemExit(0)   # "Sair do jogo" ou fechar a janela

            # "Deslogar": run() não chamou pygame.quit() — só reseta o
            # display (GameEngine deixou seu próprio modo/legenda) e
            # reconecta com a mesma conta antes de voltar à seleção.
            net.disconnect()
            net, char_list = _connect_and_login(host, port, net_user, net_pass)
            # quit()+init() ANTES de set_mode() de novo — GameEngine pode
            # ter deixado o display em qualquer modo (inclusive FULLSCREEN,
            # ver game.py::_apply_window_mode/§34.48-49); chamar set_mode()
            # direto em cima de um display já inicializado segfaulta nesta
            # stack (pygame-ce 2.5.7 + SDL 2.32.10 — mesma causa do crash
            # real reportado pelo usuário 24/07/2026, ver ARQUITETURA_ONLINE.md).
            pygame.display.quit()
            pygame.display.init()
            screen = pygame.display.set_mode((W, H))
            pygame.display.set_caption("RPG ECS [ONLINE]")

        if back_to_login:
            continue
