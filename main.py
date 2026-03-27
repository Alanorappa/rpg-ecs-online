# main.py
import pygame
import config
import settings_screen
from game import GameEngine

if __name__ == "__main__":
    pygame.init()

    # Tela de configurações inicial (sempre em 1280x720)
    cfg    = config.load()
    tmp_w  = int(1280 * cfg.get("scale", 1.0))
    tmp_h  = int(720  * cfg.get("scale", 1.0))
    screen = pygame.display.set_mode((tmp_w, tmp_h))
    pygame.display.set_caption("RPG ECS — Configuracoes")

    scale = settings_screen.run(screen)

    pygame.display.quit()   # fecha janela temporária antes do GameEngine criar a sua
    pygame.display.init()

    game_engine = GameEngine(scale=scale)
    game_engine.run()