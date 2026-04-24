# main.py
import pygame
import config
import char_creation_screen
from game import GameEngine

if __name__ == "__main__":
    pygame.init()
    cfg   = config.load()
    scale = cfg.get("scale", 1.0)

    tmp_w  = int(1280 * scale)
    tmp_h  = int(720  * scale)
    screen = pygame.display.set_mode((tmp_w, tmp_h))
    pygame.display.set_caption("RPG ECS")

    # Tela de seleção / criação de personagem
    slot, char_data = char_creation_screen.run(screen)

    pygame.display.quit()
    pygame.display.init()

    game_engine = GameEngine(scale=scale, char_data=char_data, save_slot=slot)
    game_engine.run()