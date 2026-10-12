"""engine/mob_animation.py — estado de animação de mob.

Cobre especificamente o bug real relatado pelo usuário (10/10/2026,
PROBLEMAS_ARQUITETURA.md §270): mob perseguindo "piscava" pra idle no
meio do run — `resolve_mob_anim_state` ignorava `anim_walk_grace_s`
(campo genérico que `TileMovementSystem` liga ao fim de QUALQUER passo,
inclusive de mob remoto esperando o próximo ENTITY_MOVE da rede)."""
import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))


class TestResolveMobAnimState(unittest.TestCase):
    def test_andando_e_run(self):
        from engine.components import TileMovement
        from engine.mob_animation import resolve_mob_anim_state
        tm = TileMovement(is_moving=True)
        self.assertEqual(resolve_mob_anim_state(tm), "run")

    def test_parado_sem_grace_e_idle(self):
        from engine.components import TileMovement
        from engine.mob_animation import resolve_mob_anim_state
        tm = TileMovement(is_moving=False, anim_walk_grace_s=0.0)
        self.assertEqual(resolve_mob_anim_state(tm), "idle")

    def test_parado_com_grace_ainda_e_run(self):
        """Reprodução direta do bug: passo acabou de terminar
        (is_moving=False) mas ainda dentro da janela de graça — tem que
        continuar "run", não piscar pra "idle"."""
        from engine.components import TileMovement
        from engine.mob_animation import resolve_mob_anim_state
        tm = TileMovement(is_moving=False, anim_walk_grace_s=0.05)
        self.assertEqual(resolve_mob_anim_state(tm), "run")

    def test_mesmo_resultado_de_character_animation(self):
        """resolve_mob_anim_state delega pra
        character_animation.resolve_anim_state — nunca uma 2ª
        implementação divergente."""
        from engine.components import TileMovement
        from engine.mob_animation import resolve_mob_anim_state
        from engine.character_animation import resolve_anim_state
        for is_moving, grace in ((True, 0.0), (False, 0.0), (False, 0.3)):
            tm = TileMovement(is_moving=is_moving, anim_walk_grace_s=grace)
            self.assertEqual(resolve_mob_anim_state(tm), resolve_anim_state(tm))


class TestResolveMobDisplayStateDuranteGrace(unittest.TestCase):
    """Reprodução fim-a-fim via resolve_mob_display_state (o que
    ui/render_system.py de fato chama todo frame) — confirma que o
    "piscar" não acontece passando pelo caminho real."""

    def test_perseguicao_continua_nao_pisca_pra_idle(self):
        from engine.components import TileMovement
        from engine.mob_animation import resolve_mob_display_state

        tm = TileMovement(is_moving=True, facing="down")
        state, _, _ = resolve_mob_display_state(tm, "goblin_guerreiro", 1000)
        self.assertEqual(state, "run")

        # TileMovementSystem terminou o passo neste frame — mesmo padrão
        # real: is_moving vira False, anim_walk_grace_s liga.
        tm.is_moving = False
        tm.anim_walk_grace_s = 0.1
        state, _, _ = resolve_mob_display_state(tm, "goblin_guerreiro", 1016)
        self.assertEqual(state, "run", "não pode piscar pra idle durante a janela de graça")


if __name__ == "__main__":
    unittest.main()
