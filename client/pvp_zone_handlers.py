"""
client/pvp_zone_handlers.py — Mixin com o indicador visual de Zona PvP
(Fase F). A decisão de dano continua 100% autoritativa no servidor via
can_engage/_pvp_allowed_between (server/pvp_zone_processor.py) — isto aqui
é PURAMENTE cosmético, mesmo padrão de _ambient_zones em game.py: a
geometria das zonas vem do MESMO _entities.json que o cliente já carrega
localmente, então nenhuma mensagem de rede nova é necessária.
"""
import pygame

from ui.combat_log import LOG

_PVP_ZONE_ENTER_COLOR = (220, 70, 70)
_PVP_ZONE_LEAVE_COLOR = (170, 170, 170)


class PvpZoneHandlers:

    def _load_pvp_zones(self, spawn_points: dict) -> None:
        """Armazena as zonas PvP do mapa recém-carregado e força reavaliação."""
        self._pvp_zones          = spawn_points.get("pvp_zones", [])
        self._in_pvp_zone_flag   = False
        self._current_pvp_zone   = "\x00"  # sentinel: força reavaliação no 1º frame

    def _update_pvp_zone_indicator(self, tile_x: int, tile_y: int) -> None:
        """Verifica se o jogador está dentro de alguma Zona PvP do mapa
        atual e dispara log de entrada/saída na MUDANÇA de estado (mesmo
        algoritmo de _update_ambient_zone)."""
        new_zone = ""
        for z in self._pvp_zones:
            x1, y1, x2, y2 = z["rect"]
            if x1 <= tile_x <= x2 and y1 <= tile_y <= y2:
                new_zone = z["name"] or "Zona PvP"
                break

        if new_zone == self._current_pvp_zone:
            return
        self._current_pvp_zone = new_zone
        self._in_pvp_zone_flag = bool(new_zone)

        if new_zone:
            LOG.add(f"Você entrou em uma Zona PvP! ({new_zone})", _PVP_ZONE_ENTER_COLOR)
        else:
            LOG.add("Você saiu da Zona PvP.", _PVP_ZONE_LEAVE_COLOR)

    def _draw_pvp_zone_banner(self) -> None:
        """Aviso fixo no topo-centro da tela enquanto o jogador estiver
        dentro de uma Zona PvP — só cosmético (ver docstring do módulo)."""
        if not getattr(self, "_in_pvp_zone_flag", False):
            return
        text = "ZONA PVP"
        surf = self.font_sm.render(text, False, (255, 235, 235))
        pad_x, pad_y = self._u(10), self._u(4)
        box = pygame.Rect(0, 0, surf.get_width() + pad_x * 2, surf.get_height() + pad_y * 2)
        box.centerx = self.screen.get_width() // 2
        box.y       = self._u(8)
        bg = pygame.Surface(box.size, pygame.SRCALPHA)
        bg.fill((120, 20, 20, 180))
        self.screen.blit(bg, box.topleft)
        pygame.draw.rect(self.screen, (220, 70, 70), box, 1)
        self.screen.blit(surf, (box.x + pad_x, box.y + pad_y))
