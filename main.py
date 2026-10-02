# main.py
import os
import sys

# Build (PyInstaller): CWD = pasta do exe ANTES de qualquer import de jogo —
# todos os caminhos relativos ("assets/...", "maps/...", logs/, saves/)
# passam a resolver ao lado do executável, mesmo se o atalho definir outro
# diretório de trabalho.
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

# TEMPORÁRIO (29/09/2026) — investigações em aberto do Interceptar (§217)
# e do mundo preto ao sair da BG (§219): promove flags de config.json pra
# env var ANTES de qualquer módulo debug.* ser importado (todos leem a env
# var 1x, no import — precisa vir antes até de pygame.init()). Existe só
# pra o usuário não precisar digitar `$env:RPG_DEBUG_...` toda vez que for
# testar — REMOVER este bloco (e as 2 chaves de config.json) quando as 2
# investigações fecharem; nunca deixar um debug fixo esquecido no código
# pra sempre (mesma razão de RPG_DEBUG_* ser env var e não constante,
# ver debug/aoi_debug.py e item D3/seção 11 de PROBLEMAS_ARQUITETURA.md).
try:
    import json as _json_dbg_boot
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
              encoding="utf-8") as _f_dbg_boot:
        _cfg_dbg_boot = _json_dbg_boot.load(_f_dbg_boot)
    if _cfg_dbg_boot.get("debug_rpg_aoi"):
        os.environ["RPG_DEBUG_AOI"] = "1"
    if _cfg_dbg_boot.get("debug_rpg_interceptar"):
        os.environ["RPG_DEBUG_INTERCEPTAR"] = "1"
except Exception:
    pass


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
from game import GameEngine, compute_window_geometry


def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="RPG ECS")
    p.add_argument("--user",     default="", help="Login direto (dev)")
    p.add_argument("--password", default="", help="Senha direta (dev)")
    p.add_argument("--host",     default="", help="Servidor host")
    p.add_argument("--port",     type=int, default=0, help="Servidor porta")
    return p.parse_args()


def _connect_and_login(host: str, port: int, username: str, password: str,
                       use_tls: bool = False):
    """Conecta e loga sem UI — usado no fast-path --user/--password e ao
    reconectar depois de "Deslogar" (reaproveita usuário/senha já guardados
    em memória, sem pedir de novo — mesmo mecanismo de re-login automático
    que client/network.py::NetworkClient já usa pra reconexão por queda de
    conexão, só que aqui é um NetworkClient novo, não uma retomada do
    antigo). Retorna (net, char_list) em sucesso, ou (None, []) se a
    conexão/login não confirmar dentro do timeout (nunca silenciosamente
    "sem personagem" — ver _reconnect_or_login_screen, que trata esse
    caso; distinguir isso de uma conta legitimamente vazia é o que faltava
    aqui antes, 29/09/2026, bug real relatado pelo usuário: tela de
    seleção de personagem aparecia vazia — sem erro nenhum — quando o
    servidor não respondia a tempo pra reconectar depois de "Deslogar",
    coincidindo com um restart do servidor no meio do teste)."""
    from client.network import NetworkClient
    from shared.messages import MsgType as _MT
    import time as _t

    net = NetworkClient(host=host, port=port, use_tls=use_tls)
    net.connect()
    _deadline = _t.time() + 10.0
    while not net.connected and _t.time() < _deadline:
        _t.sleep(0.05)
    if not net.connected:
        try: net.disconnect()
        except Exception: pass
        return None, []
    net.login(username, password)

    char_list = None
    _got_response = False
    _deadline2 = _t.time() + 10.0
    while _t.time() < _deadline2:
        for mt, payload, _s, _ts in net.poll():
            if mt == _MT.AUTH_OK:
                char_list = payload.get("characters", [])
                _got_response = True
                break
            if mt == _MT.LOGIN_ERROR:
                _got_response = True
                break
        if _got_response:
            break
        _t.sleep(0.02)
    if not _got_response or char_list is None:
        try: net.disconnect()
        except Exception: pass
        return None, []
    return net, char_list


def _reconnect_or_login_screen(screen, host: str, port: int, username: str, password: str,
                               use_tls: bool = False):
    """Wrapper de `_connect_and_login` pros 2 pontos que reconectam sem
    passar pela tela de login normal (fast-path --user/--password e
    "Deslogar") — se a reconexão falhar/expirar, cai na tela de login de
    verdade (`ui.login_screen.run`, mesma UI que já trata erro/timeout de
    login do zero) em vez de seguir com uma lista de personagens vazia.
    Usuário/senha ficam pré-preenchidos, só precisa confirmar. Retorna
    (username, password, net, char_list) — mesmo shape de
    `ui.login_screen.run` — ou None se o usuário fechar a janela na tela
    de fallback."""
    net, char_list = _connect_and_login(host, port, username, password, use_tls=use_tls)
    if net is not None:
        return username, password, net, char_list

    from ui.login_screen import run as _login_run
    result = _login_run(
        screen, host, port, use_tls=use_tls, prefill_user=username,
        initial_status="Conexão perdida ao reconectar — faça login de novo.",
    )
    return result


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

    # Login/seleção de personagem precisam abrir no MESMO window_mode
    # salvo que o jogo já usa (GameEngine._compute_and_set_window_mode)
    # — antes, essas 2 telas sempre abriam numa janela de tamanho fixo
    # (1280*scale, 720*scale), ignorando window_mode por completo (bug
    # real relatado pelo usuário 28/07/2026: com scale=1.5 == 1920x1080,
    # batendo com a resolução comum de monitor Full HD, a janela cobria
    # a tela inteira sem NENHUMA flag de fullscreen — visualmente
    # indistinguível de tela cheia de verdade, mesmo com window_mode
    # salvo como "windowed_fullsize"). "maximized" (config legado) migra
    # pra "windowed_fullsize", mesmo tratamento de game.py::GameEngine.
    window_mode = cfg.get("window_mode", "windowed_fullsize")
    if window_mode == "maximized":
        window_mode = "windowed_fullsize"
    W, H, win_flags = compute_window_geometry(window_mode, scale)
    screen = pygame.display.set_mode((W, H), win_flags, vsync=1)
    pygame.display.set_caption("RPG ECS [ONLINE]")

    host = cfg.get("server_host", "localhost")
    port = int(cfg.get("server_port", 8765))
    use_tls = bool(cfg.get("use_tls", False))

    # --user/--password só pula a tela de login na PRIMEIRA volta — se o
    # jogador apertar ESC na seleção de personagem (quer trocar de conta),
    # a volta seguinte já mostra a tela de login de verdade.
    use_dev_login = bool(args.user and args.password)

    while True:
        # ── Etapa 1: Login / Cadastro ─────────────────────────────────────────────
        if use_dev_login:
            use_dev_login = False
            result = _reconnect_or_login_screen(screen, host, port, args.user, args.password,
                                                use_tls=use_tls)
            if result is None:
                pygame.quit()
                raise SystemExit(0)
            net_user, net_pass, net, char_list = result
        else:
            from ui.login_screen import run as _login_run
            result = _login_run(screen, host, port, use_tls=use_tls)
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
            # quit()+init() ANTES de set_mode() de novo — GameEngine pode
            # ter deixado o display em qualquer modo (inclusive FULLSCREEN,
            # ver game.py::_apply_window_mode/§34.48-49); chamar set_mode()
            # direto em cima de um display já inicializado segfaulta nesta
            # stack (pygame-ce 2.5.7 + SDL 2.32.10 — mesma causa do crash
            # real reportado pelo usuário 24/07/2026, ver ARQUITETURA_ONLINE.md).
            # Feito ANTES da reconexão (não depois) porque o fallback de
            # falha (_reconnect_or_login_screen) também precisa de um
            # `screen` válido pra desenhar a tela de login.
            pygame.display.quit()
            pygame.display.init()
            screen = pygame.display.set_mode((W, H), win_flags, vsync=1)
            pygame.display.set_caption("RPG ECS [ONLINE]")

            result = _reconnect_or_login_screen(screen, host, port, net_user, net_pass,
                                                use_tls=use_tls)
            if result is None:
                pygame.quit()
                raise SystemExit(0)
            net_user, net_pass, net, char_list = result

        if back_to_login:
            continue
