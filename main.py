# main.py
import pygame
import config
from game import GameEngine


def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="RPG ECS")
    p.add_argument("--user",      default="",   help="Username")
    p.add_argument("--password",  default="",   help="Password")
    p.add_argument("--host",      default="",   help="Servidor host (sobrescreve config)")
    p.add_argument("--port",      type=int, default=0, help="Servidor porta")
    return p.parse_args()


if __name__ == "__main__":
    args  = _parse_args()
    pygame.init()
    cfg   = config.load()
    scale = cfg.get("scale", 1.0)

    # Sobrescreve host/port no config se passados por argumento
    if args.host:
        cfg["server_host"] = args.host
        config.save({"server_host": args.host})
    if args.port:
        cfg["server_port"] = args.port
        config.save({"server_port": args.port})

    tmp_w  = int(1280 * scale)
    tmp_h  = int(720  * scale)
    screen = pygame.display.set_mode((tmp_w, tmp_h))
    pygame.display.set_caption("RPG ECS [ONLINE]")

    pygame.display.quit()
    pygame.display.init()
    game_engine = GameEngine(
        scale=scale, char_data=None, save_slot=0,
        net_user=args.user or cfg.get("net_user", "teste"),
        net_pass=args.password or cfg.get("net_pass", "123456"),
    )

    game_engine.run()
