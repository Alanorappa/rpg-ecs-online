"""
tests/test_character_animation.py — animação base de personagem
(09/09/2026, pedido do usuário, implementação incremental).

Cobre 4 peças: `engine.utils.facing_from_delta` (direção pura),
`engine.character_animation.get_character_frame` (carrega/corta/
espelha sprite, com fallback None quando o arquivo não existe),
`TileMovementSystem` gravando `TileMovement.facing` durante um passo
real (integração, não atalho), e o topo do nameplate/HUD em
`RenderSystem` acompanhando a altura REAL do sprite (não o
`renderable.height` antigo de 24px — bug real relatado pelo usuário:
nameplate cobria a metade de cima do sprite de 48px de altura).
"""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import pygame
pygame.init()
pygame.display.set_mode((100, 100))

from engine.utils import facing_from_delta
from engine.world import World
from engine.components import Position, TileMovement
from engine.world_systems import TileMovementSystem
from shared.constants import TILE_SIZE


class _BlitSpySurface(pygame.Surface):
    """`pygame.Surface.blit` é read-only na classe/instância (tipo C
    imutável) — não dá pra monkeypatchar direto. Subclasse Python pura
    (permitido, `pygame.draw.rect` funciona igual numa subclasse) só
    pra registrar cada chamada de `blit()` antes de delegar pro real."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.blit_calls = []

    def blit(self, source, dest, *a, **k):
        self.blit_calls.append((source, dest))
        return super().blit(source, dest, *a, **k)


class TestFacingFromDelta(unittest.TestCase):
    """10/09/2026, pedido do usuário: corte por ÂNGULO a partir do eixo
    vertical (0°=reto pra baixo/cima), não mais um corte simétrico
    45/45 — down/up só dentro de um cone estreito de
    `_VERTICAL_FACING_HALF_ANGLE_DEG` (20°) de cada lado do vertical;
    fora disso (os 70° restantes de cada quadrante), left/right."""

    def test_direcoes_cardinais(self):
        self.assertEqual(facing_from_delta(0, 1), "down")
        self.assertEqual(facing_from_delta(0, -1), "up")
        self.assertEqual(facing_from_delta(1, 0), "right")
        self.assertEqual(facing_from_delta(-1, 0), "left")

    def test_delta_zero_usa_default_down(self):
        self.assertEqual(facing_from_delta(0, 0), "down")

    def test_diagonal_exata_45_graus_e_left_right_nao_down_up(self):
        """Mudança de comportamento de propósito: 45° está bem fora do
        cone de 20°, então vira left/right — antes (corte 45/45) caía
        em down/up."""
        self.assertEqual(facing_from_delta(1, 1), "right")
        self.assertEqual(facing_from_delta(-1, 1), "left")
        self.assertEqual(facing_from_delta(1, -1), "right")
        self.assertEqual(facing_from_delta(-1, -1), "left")

    def _delta_no_angulo(self, angle_deg: float, dy_sign: int = 1):
        """(dx, dy) cujo ângulo a partir do vertical é exatamente `angle_deg`."""
        import math
        dy = dy_sign
        dx = math.tan(math.radians(angle_deg)) * abs(dy)
        return dx, dy

    def test_dentro_do_cone_de_20_graus_continua_down_up(self):
        for angle in (0.0, 10.0, 19.9):
            dx, dy = self._delta_no_angulo(angle, dy_sign=1)
            self.assertEqual(facing_from_delta(dx, dy), "down", f"ângulo {angle}° deveria ser down")
            dx, dy = self._delta_no_angulo(angle, dy_sign=-1)
            self.assertEqual(facing_from_delta(dx, dy), "up", f"ângulo {angle}° deveria ser up")

    def test_passando_de_20_graus_vira_left_right(self):
        for angle in (20.1, 30.0, 63.4, 89.0):
            dx, dy = self._delta_no_angulo(angle, dy_sign=1)
            self.assertEqual(facing_from_delta(dx, dy), "right", f"ângulo {angle}° deveria ser right")
            dx, dy = self._delta_no_angulo(-angle, dy_sign=1)
            self.assertEqual(facing_from_delta(dx, dy), "left", f"ângulo {-angle}° deveria ser left")


class TestGetCharacterFrame(unittest.TestCase):
    """`guerreiro_idle_down.png` é um asset real já commitado (309x48,
    8 frames de 32x48) — usado direto, sem fixture sintética, pra
    também servir de teste de regressão do arquivo em si."""

    def setUp(self):
        from engine import character_animation as _ca
        _ca._frame_cache.clear()

    def tearDown(self):
        from engine import character_animation as _ca
        _ca._frame_cache.clear()

    def test_combinacao_existente_retorna_frame(self):
        from engine.character_animation import get_character_frame, CHAR_TILE_SIZE
        frame = get_character_frame("guerreiro", "idle", "down", 0)
        self.assertIsNotNone(frame)
        w, h = frame.get_size()
        self.assertEqual(w, CHAR_TILE_SIZE)

    def test_frame_avanca_com_o_tempo(self):
        from engine.character_animation import get_character_frame, DEFAULT_FRAME_MS
        f0    = get_character_frame("guerreiro", "idle", "down", 0)
        f_adv = get_character_frame("guerreiro", "idle", "down", DEFAULT_FRAME_MS)
        self.assertIsNot(f0, f_adv, "frame deveria avançar após frame_ms (default)")

    def test_combinacao_inexistente_retorna_none(self):
        """class_id fictício — não pode depender de quais sprites reais
        já existem em assets/sprites/ neste momento (o usuário vai
        adicionando aos poucos)."""
        from engine.character_animation import get_character_frame
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "run", "down", 0))
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "idle", "down", 0))

    def test_left_e_right_sem_arquivo_nenhum_retorna_none_sem_recursao_infinita(self):
        """Regressão: bug real pego testando — sem o guard, left tentava
        espelhar right, que tentava espelhar left de volta, infinitamente.
        Usa class_id fictício (nunca "guerreiro") — não pode depender de
        quais sprites reais já existem em assets/sprites/ neste momento."""
        from engine.character_animation import get_character_frame
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "idle", "left", 0))
        self.assertIsNone(get_character_frame("classe_inexistente_xyz", "idle", "right", 0))

    def test_espelha_lado_que_falta_a_partir_do_lado_que_existe(self):
        from engine.character_animation import get_character_frame, CHAR_TILE_SIZE
        path = os.path.join("assets", "sprites", "testclasse_idle_right.png")
        w, h = CHAR_TILE_SIZE * 2, CHAR_TILE_SIZE
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(surf, (255, 0, 0, 255), (0, 0, CHAR_TILE_SIZE // 2, h))
        pygame.draw.rect(surf, (0, 0, 255, 255), (CHAR_TILE_SIZE // 2, 0, CHAR_TILE_SIZE // 2, h))
        pygame.image.save(surf, path)
        try:
            left_frame = get_character_frame("testclasse", "idle", "left", 0)
            self.assertIsNotNone(left_frame)
            # Espelhado: o que era vermelho na borda esquerda de "right"
            # vira a borda DIREITA de "left".
            self.assertEqual(left_frame.get_at((CHAR_TILE_SIZE - 1, 0))[:3], (255, 0, 0))
            self.assertEqual(left_frame.get_at((0, 0))[:3], (0, 0, 255))
        finally:
            os.remove(path)


class TestTileMovementSystemSetsFacing(unittest.TestCase):
    """Integração real: TileMovementSystem.update() (não um atalho de
    teste) deve gravar `facing` a partir do delta target-current durante
    um passo, e preservar o último valor depois que o passo termina."""

    def setUp(self):
        self.world = World()
        self.eid = self.world.create_entity()
        self.world.add_component(self.eid, Position(x=0, y=0))
        self.tm = TileMovement()
        self.world.add_component(self.eid, self.tm)
        self.sys = TileMovementSystem(self.world)

    def _start_step(self, dx: int, dy: int) -> None:
        self.tm.current_tile_x = 5
        self.tm.current_tile_y = 5
        self.tm.target_tile_x  = 5 + dx
        self.tm.target_tile_y  = 5 + dy
        self.tm.start_pixel_x  = 5 * TILE_SIZE
        self.tm.start_pixel_y  = 5 * TILE_SIZE
        self.tm.target_pixel_x = (5 + dx) * TILE_SIZE
        self.tm.target_pixel_y = (5 + dy) * TILE_SIZE
        self.tm.progress    = 0.0
        self.tm.is_moving   = True
        self.tm.move_duration = 0.2

    def test_passo_pra_direita_grava_facing_right(self):
        self._start_step(dx=1, dy=0)
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "right")

    def test_passo_pra_baixo_grava_facing_down(self):
        self._start_step(dx=0, dy=1)
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "down")

    def test_facing_permanece_apos_passo_completar_parado(self):
        self._start_step(dx=-1, dy=0)
        self.sys.update(dt=1.0)   # dt grande o bastante pra completar o passo (progress>=1)
        self.assertFalse(self.tm.is_moving)
        self.assertEqual(self.tm.facing, "left",
            "facing do último passo real deve continuar valendo enquanto parado")

    def test_passo_completar_liga_grace_de_animacao(self):
        self._start_step(dx=1, dy=0)
        self.sys.update(dt=1.0)   # completa o passo
        self.assertFalse(self.tm.is_moving)
        self.assertGreater(self.tm.anim_walk_grace_s, 0.0)

    def test_grace_decai_com_o_tempo_ate_zerar(self):
        self._start_step(dx=1, dy=0)
        self.sys.update(dt=1.0)   # completa o passo, liga o grace
        grace_inicial = self.tm.anim_walk_grace_s
        self.sys.update(dt=grace_inicial + 1.0)   # dt grande o bastante pra zerar
        self.assertEqual(self.tm.anim_walk_grace_s, 0.0)


class TestFacingTracksCombatTargetWhileStationary(unittest.TestCase):
    """09/09/2026, pedido do usuário: personagem parado atacando um alvo
    (ex: mob atrás dele) ficava "olhando" pra última direção que andou,
    em vez de virar pro alvo — `facing` só era gravado durante um passo
    de movimento. Integração real: `TileMovementSystem.update()`."""

    def setUp(self):
        from engine.components import CombatState
        self.world = World()
        self.player = self.world.create_entity()
        self.world.add_component(self.player, Position(x=5 * TILE_SIZE + 16, y=5 * TILE_SIZE + 16))
        self.tm = TileMovement(current_tile_x=5, current_tile_y=5,
                               target_tile_x=5, target_tile_y=5)
        self.tm.is_moving = False
        self.tm.facing = "up"
        self.world.add_component(self.player, self.tm)
        self.cs = CombatState()
        self.world.add_component(self.player, self.cs)
        self.sys = TileMovementSystem(self.world)

    def _put_target_at(self, dx_tiles: int, dy_tiles: int) -> int:
        mob = self.world.create_entity()
        self.world.add_component(mob, Position(
            x=(5 + dx_tiles) * TILE_SIZE + 16, y=(5 + dy_tiles) * TILE_SIZE + 16))
        return mob

    def test_sem_alvo_mantem_ultima_direcao_de_movimento(self):
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "up")

    def test_alvo_ao_sul_vira_pra_baixo(self):
        self.cs.target_entity_id = self._put_target_at(0, 1)
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "down")

    def test_alvo_a_leste_vira_pra_direita(self):
        self.cs.target_entity_id = self._put_target_at(1, 0)
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "right")

    def test_alvo_a_oeste_vira_pra_esquerda(self):
        self.cs.target_entity_id = self._put_target_at(-1, 0)
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "left")

    def test_acompanha_alvo_que_se_move_ao_redor(self):
        target = self._put_target_at(0, 1)   # ao sul
        self.cs.target_entity_id = target
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "down")

        target_pos = self.world.get_component(target, Position)
        target_pos.x = (5 - 1) * TILE_SIZE + 16   # move pro oeste
        target_pos.y = 5 * TILE_SIZE + 16
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "left")

    def test_andando_ignora_alvo_e_usa_direcao_do_movimento(self):
        """Prioridade: movimento real sempre vence — só olha pro alvo
        quando está PARADO (regressão de `TestTileMovementSystemSetsFacing`)."""
        self.cs.target_entity_id = self._put_target_at(0, 1)  # alvo ao sul
        self.tm.is_moving       = True
        self.tm.target_tile_x   = 6   # andando pra LESTE
        self.tm.target_tile_y   = 5
        self.tm.start_pixel_x   = 5 * TILE_SIZE
        self.tm.start_pixel_y   = 5 * TILE_SIZE
        self.tm.target_pixel_x  = 6 * TILE_SIZE
        self.tm.target_pixel_y  = 5 * TILE_SIZE
        self.tm.move_duration   = 0.2
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "right",
            "andando deve continuar decidindo facing pela direção do passo, não pelo alvo")

    def test_sem_combat_state_nao_quebra(self):
        world = World()
        eid = world.create_entity()
        world.add_component(eid, Position(x=0, y=0))
        tm = TileMovement()
        tm.is_moving = False
        world.add_component(eid, tm)
        TileMovementSystem(world).update(dt=0.05)   # não deveria lançar exceção

    def test_alvo_sem_position_nao_quebra(self):
        alvo_sem_posicao = self.world.create_entity()   # sem Position
        self.cs.target_entity_id = alvo_sem_posicao
        self.sys.update(dt=0.05)   # não deveria lançar exceção
        self.assertEqual(self.tm.facing, "up", "sem Position do alvo, mantém a direção anterior")

    def test_caminhada_continua_contra_alvo_em_direcao_oposta_nao_pisca(self):
        """Bug real relatado pelo usuário (09/09/2026, follow-up): andando
        pra baixo com um alvo selecionado ao NORTE (direção oposta),
        o frame único de `is_moving=False` entre 2 passos (mesma causa
        raiz do flicker idle/run, ver `anim_walk_grace_s`) fazia o
        personagem "piscar" de frente pro alvo no meio da caminhada.
        `anim_walk_grace_s` (ligado no instante em que o passo termina)
        precisa suprimir o facing-pro-alvo nesse frame também."""
        target = self._put_target_at(0, -3)   # bem ao norte
        self.cs.target_entity_id = target

        def start_step_down():
            cur_x, cur_y = self.tm.current_tile_x, self.tm.current_tile_y
            self.tm.target_tile_x  = cur_x
            self.tm.target_tile_y  = cur_y + 1
            self.tm.start_pixel_x  = cur_x * TILE_SIZE
            self.tm.start_pixel_y  = cur_y * TILE_SIZE
            self.tm.target_pixel_x = cur_x * TILE_SIZE
            self.tm.target_pixel_y = (cur_y + 1) * TILE_SIZE
            self.tm.progress    = 0.0
            self.tm.is_moving   = True
            self.tm.move_duration = 0.2

        start_step_down()
        self.sys.update(dt=1.0)   # completa o 1º passo NO MESMO frame em que começou
        self.assertEqual(self.tm.facing, "down",
            "frame de transição entre passos não pode piscar pro alvo (norte)")
        self.assertFalse(self.tm.is_moving)

        # Próximo frame: PlayerInputSystem (segurando a tecla) inicia o 2º passo.
        start_step_down()
        self.sys.update(dt=0.05)
        self.assertEqual(self.tm.facing, "down")


class TestResolveAnimState(unittest.TestCase):
    """Ponto único de verdade pra "o que conta como andando" na
    animação — não é só `is_moving` puro (ver docstring da função)."""

    def _fake_tm(self, is_moving: bool, grace: float):
        from engine.components import TileMovement
        tm = TileMovement()
        tm.is_moving = is_moving
        tm.anim_walk_grace_s = grace
        return tm

    def test_andando_e_run(self):
        from engine.character_animation import resolve_anim_state
        self.assertEqual(resolve_anim_state(self._fake_tm(True, 0.0)), "run")

    def test_parado_sem_grace_e_idle(self):
        from engine.character_animation import resolve_anim_state
        self.assertEqual(resolve_anim_state(self._fake_tm(False, 0.0)), "idle")

    def test_parado_com_grace_ainda_ativo_e_run(self):
        from engine.character_animation import resolve_anim_state
        self.assertEqual(resolve_anim_state(self._fake_tm(False, 0.05)), "run")

    def test_caminhada_continua_2_passos_nunca_pisca_idle_no_meio(self):
        """Reproduz o bug real relatado: 2 passos consecutivos (como
        segurar WASD) não devem passar por um frame "idle" no meio,
        mesmo com `is_moving` piscando False entre um passo e outro."""
        from engine.world import World
        from engine.components import Position, TileMovement
        from engine.world_systems import TileMovementSystem
        from engine.character_animation import resolve_anim_state
        from shared.constants import TILE_SIZE

        world = World()
        eid = world.create_entity()
        world.add_component(eid, Position(x=0, y=0))
        tm = TileMovement()
        world.add_component(eid, tm)
        sys = TileMovementSystem(world)

        def start_step(dx, dy):
            tm.current_tile_x = 5
            tm.current_tile_y = 5
            tm.target_tile_x  = 5 + dx
            tm.target_tile_y  = 5 + dy
            tm.start_pixel_x  = 5 * TILE_SIZE
            tm.start_pixel_y  = 5 * TILE_SIZE
            tm.target_pixel_x = (5 + dx) * TILE_SIZE
            tm.target_pixel_y = (5 + dy) * TILE_SIZE
            tm.progress    = 0.0
            tm.is_moving   = True
            tm.move_duration = 0.2

        start_step(1, 0)
        # PlayerInputSystem já rodou (iniciou o passo, is_moving=True) —
        # simula TileMovementSystem completando o passo NO MESMO frame.
        sys.update(dt=1.0)
        self.assertFalse(tm.is_moving)
        # Frame do "gap": PlayerInputSystem ainda não rodou de novo
        # neste teste (roda só no próximo frame, ordem real do jogo) —
        # é EXATAMENTE o frame que piscava "idle" antes do fix.
        self.assertEqual(resolve_anim_state(tm), "run",
            "grace deveria cobrir o frame entre 2 passos de uma caminhada contínua")

        # Próximo frame: PlayerInputSystem inicia o 2º passo (segurando a tecla).
        start_step(1, 0)
        sys.update(dt=0.05)   # meio do 2º passo
        self.assertEqual(resolve_anim_state(tm), "run")


class TestRenderSystemNameplateTopFollowsSpriteHeight(unittest.TestCase):
    """`guerreiro_idle_down.png` (32x48, real) é quase o dobro da altura
    do retângulo antigo (24px) — o topo do nameplate precisa subir pra
    acompanhar, não ficar fixo em `renderable.height/2`."""

    def _make_player(self, world):
        from engine.components import (Position, Renderable, PlayerControlled,
                                       CharacterStats, CombatStats, TileMovement)
        eid = world.create_entity()
        world.add_component(eid, Position(x=100, y=100, prev_x=100, prev_y=100))
        world.add_component(eid, Renderable(color=(40, 50, 60), width=24, height=24))
        world.add_component(eid, PlayerControlled())
        world.add_component(eid, CharacterStats(name="Herói", class_id="guerreiro"))
        cs = CombatStats()
        cs.max_hp = 100
        cs.current_hp = 100
        world.add_component(eid, cs)
        tm = TileMovement()
        tm.is_moving = False
        tm.facing = "down"
        world.add_component(eid, tm)
        return eid

    def test_topo_do_hud_usa_ancora_do_tile_de_colisao_nao_o_retangulo_antigo(self):
        from engine.world import World
        from ui.systems import RenderSystem
        from ui.world_labels import WORLD_LABELS
        from engine.character_animation import character_anchor_offset
        from ui.hud_bars import NAMEPLATE_CHAR_FRAME_TOP_INSET_PX

        world = World()
        eid = self._make_player(world)
        screen = pygame.display.get_surface()
        rs = RenderSystem(world, screen)

        WORLD_LABELS._pending.clear()
        WORLD_LABELS._stack_offset.clear()
        rs.render(0, 0, nameplate_mode=0)

        # Todo elemento empilhado pra este entity_id usa o MESMO
        # world_y de topo (stack_key=eid) — a barra de HP é o 1º.
        entries = [e for e in WORLD_LABELS._pending]
        self.assertTrue(entries, "deveria ter enfileirado pelo menos a barra de HP")
        hud_top_world_y = entries[0][1]

        # Âncora não depende mais da altura do sprite (10/09/2026) — é
        # `position.y - anchor_dy` (tile de colisão de 32×32 dentro do
        # frame, ver `character_anchor_offset`), MAIS um inset fixo pra
        # baixo (26/09/2026, pedido do usuário — nameplate fica DENTRO
        # do frame da animação, não flutuando acima dele; valor fixo
        # pra não "balançar" seguindo pose/direção).
        _, anchor_dy = character_anchor_offset()
        self.assertAlmostEqual(hud_top_world_y,
                               100 - anchor_dy + NAMEPLATE_CHAR_FRAME_TOP_INSET_PX)
        # Regressão: NÃO pode ser o cálculo antigo (baseado em
        # renderable.height=24), que ficaria bem mais baixo — dentro do
        # próprio sprite.
        self.assertNotAlmostEqual(hud_top_world_y, 100 - 24 / 2)


class TestCharacterAnchorOffset(unittest.TestCase):
    """10/09/2026, pedido do usuário: cada frame é 96×80, mas o tile de
    colisão "real" (32×32, == TILE_SIZE) fica no canto (32,32) do frame
    — não o frame inteiro. Âncora de desenho e Y-sort têm que usar ESSE
    tile, nunca a largura/altura crua do frame."""

    def test_offset_e_metade_do_tile_mais_o_offset_do_canto(self):
        from engine.character_animation import (character_anchor_offset,
                                                  CHAR_COLLISION_OFFSET_X, CHAR_COLLISION_OFFSET_Y,
                                                  CHAR_COLLISION_SIZE)
        dx, dy = character_anchor_offset()
        self.assertEqual(dx, CHAR_COLLISION_OFFSET_X + CHAR_COLLISION_SIZE / 2)
        self.assertEqual(dy, CHAR_COLLISION_OFFSET_Y + CHAR_COLLISION_SIZE / 2)

    def test_valores_atuais_sao_48_48(self):
        """Com offset (32,32) e tile 32×32, o centro do tile de colisão
        cai em (48,48) dentro do frame — valor concreto conferido com o
        usuário, guarda de regressão se as constantes mudarem sem querer."""
        from engine.character_animation import character_anchor_offset
        self.assertEqual(character_anchor_offset(), (48, 48))


class TestCharacterSpriteBlitAnchor(unittest.TestCase):
    """Integração real: `RenderSystem.render()` deve blitar o sprite de
    personagem alinhando o CENTRO do tile de colisão (32×32) com
    `position` — não o centro/base do frame inteiro (96×80)."""

    def _make_player(self, world, x=100, y=100):
        from engine.components import (Position, Renderable, PlayerControlled,
                                       CharacterStats, CombatStats, TileMovement)
        eid = world.create_entity()
        world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
        world.add_component(eid, Renderable(color=(40, 50, 60), width=24, height=24))
        world.add_component(eid, PlayerControlled())
        world.add_component(eid, CharacterStats(name="Herói", class_id="guerreiro"))
        cs = CombatStats()
        cs.max_hp = 100
        cs.current_hp = 100
        world.add_component(eid, cs)
        tm = TileMovement()
        tm.is_moving = False
        tm.facing = "down"
        world.add_component(eid, tm)
        return eid

    def test_blit_alinha_centro_do_tile_de_colisao_com_position(self):
        from engine.world import World
        from ui.systems import RenderSystem
        from engine.character_animation import character_anchor_offset

        world = World()
        self._make_player(world, x=200, y=150)
        screen = _BlitSpySurface((300, 300))
        rs = RenderSystem(world, screen)
        rs.render(0, 0)

        # Acha o blit do sprite do personagem (96 de largura) entre
        # todos os blits que aconteceram no frame.
        char_blits = [(src, dest) for src, dest in screen.blit_calls
                      if getattr(src, "get_width", lambda: None)() == 96]
        self.assertTrue(char_blits, "sprite do guerreiro (96 de largura) deveria ter sido blitado")
        _, (bx, by) = char_blits[0]
        anchor_dx, anchor_dy = character_anchor_offset()
        self.assertEqual(bx, int(200 - anchor_dx))
        self.assertEqual(by, int(150 - anchor_dy))


class TestCharacterYSortUsesCollisionTile(unittest.TestCase):
    """Y-sort de personagem com animação usa o tile de colisão de 32×32
    (`position.y + CHAR_COLLISION_SIZE/2`), não `renderable.height`
    antigo (24) nem a altura crua do frame (80) — vale mesmo se ainda
    cair no retângulo de fallback (direção/classe sem arte)."""

    def _make_char(self, world, x, y, class_id="guerreiro", local=True):
        from engine.components import (Position, Renderable, PlayerControlled,
                                       CharacterStats, RemoteControlled, TileMovement)
        eid = world.create_entity()
        world.add_component(eid, Position(x=x, y=y, prev_x=x, prev_y=y))
        world.add_component(eid, Renderable(color=(1, 2, 3), width=24, height=24))
        if local:
            world.add_component(eid, PlayerControlled())
            world.add_component(eid, CharacterStats(class_id=class_id))
        else:
            world.add_component(eid, RemoteControlled(class_id=class_id))
        tm = TileMovement()
        tm.is_moving = False
        tm.facing = "down"
        world.add_component(eid, tm)
        return eid

    def test_ysort_de_personagem_desenha_na_frente_de_mob_que_ficaria_atras_com_formula_antiga(self):
        """Cenário desenhado pra diferenciar a fórmula ANTIGA
        (`position.y + renderable.height/2` == +12) da NOVA
        (`position.y + CHAR_COLLISION_SIZE/2` == +16): personagem em
        y=100 (foot_y antigo=112, novo=116) e um mob-retângulo em y=103
        (foot_y=103+12=115, fica ENTRE os dois valores). Com a fórmula
        antiga o personagem desenharia ATRÁS do mob (112<115); com a
        nova, NA FRENTE (116>115) — é a ordem que tem que sair daqui."""
        from engine.world import World
        from engine.components import Position, Renderable
        from ui.systems import RenderSystem

        world = World()
        char_eid = self._make_char(world, 100, 100)
        mob_eid = world.create_entity()
        world.add_component(mob_eid, Position(x=200, y=103, prev_x=200, prev_y=103))
        world.add_component(mob_eid, Renderable(color=(9, 9, 9), width=24, height=24))

        screen = _BlitSpySurface((300, 300))
        rs = RenderSystem(world, screen)

        # `screen.blit_calls` (via _BlitSpySurface) é o log em ORDEM REAL
        # de execução — o spy de draw.rect grava um marcador NELE MESMO
        # (não numa lista separada), pra os dois eventos (rect do mob,
        # blit do personagem) caírem na MESMA sequência cronológica.
        orig_rect = pygame.draw.rect
        def _spy_rect(surface, color, rect, *a, **k):
            if tuple(color[:3]) == (9, 9, 9):
                screen.blit_calls.append(("mob_rect_marker", None))
            return orig_rect(surface, color, rect, *a, **k)
        pygame.draw.rect = _spy_rect
        try:
            rs.render(0, 0)
        finally:
            pygame.draw.rect = orig_rect

        draw_order = []
        for src, _dest in screen.blit_calls:
            if src == "mob_rect_marker":
                draw_order.append("mob")
            elif getattr(src, "get_width", lambda: None)() == 96:
                draw_order.append("char")

        # Painter's algorithm: `drawables` ordena ASCENDENTE por foot_y e
        # desenha nessa ordem — quem desenha DEPOIS fica NA FRENTE
        # (pintado por cima). mob(115) < char(116) com a fórmula nova,
        # então mob desenha primeiro (atrás), personagem por último
        # (na frente) — é essa ordem de execução que sai daqui.
        self.assertEqual(draw_order, ["mob", "char"],
            "personagem (Y-sort novo, +16, > mob=115) tem que desenhar DEPOIS do mob (fica na frente)")

    def test_ysort_diferente_do_retangulo_antigo_e_da_altura_do_frame(self):
        from engine.character_animation import CHAR_COLLISION_SIZE
        # Documenta os 2 valores que NÃO podem ser usados por engano.
        old_rect_based = 100 + 24 / 2      # renderable.height antigo
        frame_height_based = 100 + 80 / 2  # altura crua do frame (80)
        correct = 100 + CHAR_COLLISION_SIZE / 2
        self.assertNotEqual(correct, old_rect_based)
        self.assertNotEqual(correct, frame_height_based)
        self.assertEqual(correct, 116)


class TestResolveAttackStateName(unittest.TestCase):
    """Cascata de 3 níveis (10/09/2026): sid específico → "skill"
    genérico → "attack" genérico. Auto-attack (sid="") pula direto pro
    nível 3. Usa `class_id` fictício em todos os testes — não pode
    depender de quais sprites reais já existem em assets/sprites/ neste
    momento (guerreiro_skill_*.png já existe de verdade, por exemplo)."""

    def setUp(self):
        from engine import character_animation as _ca
        _ca._frame_cache.clear()

    def tearDown(self):
        from engine import character_animation as _ca
        _ca._frame_cache.clear()

    def _make_sprite(self, class_id: str, state: str, direction: str) -> str:
        path = os.path.join("assets", "sprites", f"{class_id}_{state}_{direction}.png")
        surf = pygame.Surface((96, 80), pygame.SRCALPHA)
        pygame.image.save(surf, path)
        return path

    def test_sem_sid_e_attack_generico(self):
        from engine.character_animation import resolve_attack_state_name
        self.assertEqual(resolve_attack_state_name("classe_inexistente_xyz", "", "down"), "attack")

    def test_skill_sem_sprite_nenhuma_cai_no_attack_generico(self):
        from engine.character_animation import resolve_attack_state_name
        self.assertEqual(
            resolve_attack_state_name("classe_inexistente_xyz", "skill_inexistente_xyz", "down"),
            "attack")

    def test_skill_com_sprite_propria_usa_o_sid(self):
        from engine.character_animation import resolve_attack_state_name
        path = self._make_sprite("classe_teste_1", "skill_teste_1", "down")
        try:
            self.assertEqual(
                resolve_attack_state_name("classe_teste_1", "skill_teste_1", "down"),
                "skill_teste_1")
        finally:
            os.remove(path)

    def test_skill_sem_sprite_propria_mas_com_skill_generico_usa_skill_generico(self):
        from engine.character_animation import resolve_attack_state_name
        path = self._make_sprite("classe_teste_2", "skill", "down")
        try:
            self.assertEqual(
                resolve_attack_state_name("classe_teste_2", "skill_sem_sprite_propria", "down"),
                "skill")
        finally:
            os.remove(path)

    def test_sid_especifico_tem_prioridade_sobre_skill_generico(self):
        from engine.character_animation import resolve_attack_state_name
        path_generic = self._make_sprite("classe_teste_3", "skill", "down")
        path_specific = self._make_sprite("classe_teste_3", "skill_teste_3", "down")
        try:
            self.assertEqual(
                resolve_attack_state_name("classe_teste_3", "skill_teste_3", "down"),
                "skill_teste_3", "sid específico deveria ganhar do genérico de skill")
        finally:
            os.remove(path_generic)
            os.remove(path_specific)

    def test_auto_attack_nunca_usa_skill_generico_mesmo_se_existir(self):
        """sid="" (auto-attack) pula direto pro nível 3 — nunca deveria
        tentar o "skill" genérico, que é conceito exclusivo de skill."""
        from engine.character_animation import resolve_attack_state_name
        path = self._make_sprite("classe_teste_4", "skill", "down")
        try:
            self.assertEqual(
                resolve_attack_state_name("classe_teste_4", "", "down"), "attack")
        finally:
            os.remove(path)


class TestAttackAnimTriggerAndExpiry(unittest.TestCase):
    """Golpe (09/09/2026, pedido do usuário): liga no momento do dano,
    toca 1 ciclo do sheet, volta sozinho pro idle/run — sem precisar de
    componente/sistema novo pra "desligar", só tempo decorrido."""

    def setUp(self):
        from engine.components import TileMovement
        self.tm = TileMovement()
        self.tm.facing = "down"
        self.tm.is_moving = False

    def test_trigger_liga_attack_generico_pro_auto_attack(self):
        from engine.character_animation import trigger_attack_anim
        trigger_attack_anim(self.tm, "guerreiro", "", 1000)
        self.assertEqual(self.tm.anim_attack_state, "attack")
        self.assertEqual(self.tm.anim_attack_started_ms, 1000)

    def test_resolve_display_state_logo_apos_trigger_comeca_do_frame_0(self):
        from engine.character_animation import (
            trigger_attack_anim, resolve_display_state, DEFAULT_FRAME_MS)
        trigger_attack_anim(self.tm, "guerreiro", "", 5000)
        state, anim_t, frame_ms = resolve_display_state(self.tm, "guerreiro", 5000)
        self.assertEqual(state, "attack")
        self.assertEqual(anim_t, 0, "deve sempre começar do frame 0, não do tempo absoluto")
        self.assertEqual(frame_ms, DEFAULT_FRAME_MS, "golpe normal usa a duração padrão")

    def test_resolve_display_state_expira_apos_1_ciclo_e_volta_pro_idle(self):
        from engine.character_animation import (
            trigger_attack_anim, resolve_display_state, DEFAULT_FRAME_MS, _frames_for)
        trigger_attack_anim(self.tm, "guerreiro", "", 1000)
        # Conta frames de verdade via o cache interno (mesmo jeito que
        # resolve_display_state usa) — evita hardcodar "8 frames".
        n_frames = len(_frames_for("guerreiro", "attack", "down"))
        self.assertGreater(n_frames, 0, "guerreiro_attack_down.png precisa existir pra este teste")
        duration_ms = n_frames * DEFAULT_FRAME_MS

        state, _, _ = resolve_display_state(self.tm, "guerreiro", 1000 + duration_ms - 1)
        self.assertEqual(state, "attack", "ainda dentro do ciclo")

        state, _, _ = resolve_display_state(self.tm, "guerreiro", 1000 + duration_ms + 1)
        self.assertEqual(state, "idle", "ciclo terminou, parado -> idle")
        self.assertEqual(self.tm.anim_attack_state, "",
            "expirar deve limpar o estado, não só deixar de retorná-lo")

    def test_resolve_display_state_sem_sprite_nenhum_cai_direto_pro_idle_run(self):
        from engine.character_animation import trigger_attack_anim, resolve_display_state
        trigger_attack_anim(self.tm, "classe_inexistente_xyz", "", 1000)
        state, anim_t, _ = resolve_display_state(self.tm, "classe_inexistente_xyz", 1000)
        self.assertEqual(state, "idle")
        self.assertEqual(anim_t, 1000, "sem golpe ativo, usa tempo absoluto (idle/run normal)")

    def test_golpe_tem_prioridade_sobre_run_enquanto_ativo(self):
        from engine.character_animation import trigger_attack_anim, resolve_display_state
        self.tm.is_moving = True   # andando ao mesmo tempo que ataca
        trigger_attack_anim(self.tm, "guerreiro", "", 1000)
        state, _, _ = resolve_display_state(self.tm, "guerreiro", 1000)
        self.assertEqual(state, "attack", "golpe deve aparecer mesmo andando")

    def test_golpe_que_errou_e_25_por_cento_mais_lento(self):
        from engine.character_animation import (
            trigger_attack_anim, resolve_display_state, DEFAULT_FRAME_MS, MISS_ATTACK_SLOWDOWN)
        trigger_attack_anim(self.tm, "guerreiro", "", 1000, missed=True)
        _, _, frame_ms = resolve_display_state(self.tm, "guerreiro", 1000)
        self.assertEqual(frame_ms, int(DEFAULT_FRAME_MS * MISS_ATTACK_SLOWDOWN))
        self.assertGreater(frame_ms, DEFAULT_FRAME_MS)

    def test_golpe_normal_usa_duracao_padrao_nao_a_de_erro(self):
        from engine.character_animation import trigger_attack_anim, resolve_display_state, DEFAULT_FRAME_MS
        trigger_attack_anim(self.tm, "guerreiro", "", 1000, missed=False)
        _, _, frame_ms = resolve_display_state(self.tm, "guerreiro", 1000)
        self.assertEqual(frame_ms, DEFAULT_FRAME_MS)


class TestLoopingAttackDuration(unittest.TestCase):
    """10/09/2026, pedido do usuário: Fatiador de Corpos (dura 5s no
    SKILL_CATALOG, fonte única) fica em LOOP durante toda essa duração,
    não expira após 1 ciclo do sheet como o resto das skills."""

    def test_fatiador_de_corpos_retorna_duracao_real_do_catalogo(self):
        from engine.character_animation import _looping_attack_duration_ms
        from content.skill_config import SKILL_CATALOG
        duration_s = SKILL_CATALOG["fatiador_de_corpos"]["params"]["duration"]
        self.assertEqual(_looping_attack_duration_ms("fatiador_de_corpos"), int(duration_s * 1000))

    def test_skill_normal_nao_esta_na_lista_de_loop(self):
        from engine.character_animation import _looping_attack_duration_ms
        self.assertIsNone(_looping_attack_duration_ms("golpe_poderoso"))
        self.assertIsNone(_looping_attack_duration_ms(""))

    def test_trigger_grava_duracao_de_loop_pro_fatiador(self):
        from engine.components import TileMovement
        from engine.character_animation import trigger_attack_anim
        from content.skill_config import SKILL_CATALOG
        tm = TileMovement()
        tm.facing = "down"
        trigger_attack_anim(tm, "guerreiro", "fatiador_de_corpos", 1000)
        duration_s = SKILL_CATALOG["fatiador_de_corpos"]["params"]["duration"]
        self.assertEqual(tm.anim_attack_loop_duration_ms, int(duration_s * 1000))

    def test_trigger_golpe_normal_nao_grava_duracao_de_loop(self):
        from engine.components import TileMovement
        from engine.character_animation import trigger_attack_anim
        tm = TileMovement()
        tm.facing = "down"
        trigger_attack_anim(tm, "guerreiro", "golpe_poderoso", 1000)
        self.assertEqual(tm.anim_attack_loop_duration_ms, 0)

    def test_resolve_display_state_fatiador_continua_ativo_apos_1_ciclo_normal(self):
        """Um golpe normal já teria expirado depois de 1 ciclo do sheet
        — o Fatiador tem que continuar mostrando "fatiador_de_corpos"
        bem depois disso, até completar os 5s reais."""
        from engine.components import TileMovement
        from engine.character_animation import (
            trigger_attack_anim, resolve_display_state, DEFAULT_FRAME_MS, _frames_for)
        tm = TileMovement()
        tm.facing = "down"
        tm.is_moving = False
        trigger_attack_anim(tm, "guerreiro", "fatiador_de_corpos", 1000)

        n_frames = len(_frames_for("guerreiro", "fatiador_de_corpos", "down"))
        self.assertGreater(n_frames, 0, "guerreiro_fatiador_de_corpos_down.png precisa existir")
        one_cycle_ms = n_frames * DEFAULT_FRAME_MS
        self.assertLess(one_cycle_ms, 5000, "1 ciclo do sheet precisa ser bem menor que 5s pro teste fazer sentido")

        state, _, _ = resolve_display_state(tm, "guerreiro", 1000 + one_cycle_ms + 1)
        self.assertEqual(state, "fatiador_de_corpos",
            "depois de 1 ciclo normal, um golpe comum já teria expirado — o Fatiador não")

    def test_resolve_display_state_fatiador_expira_apos_5s_reais(self):
        from engine.components import TileMovement
        from engine.character_animation import trigger_attack_anim, resolve_display_state
        from content.skill_config import SKILL_CATALOG
        tm = TileMovement()
        tm.facing = "down"
        tm.is_moving = False
        trigger_attack_anim(tm, "guerreiro", "fatiador_de_corpos", 1000)
        duration_ms = int(SKILL_CATALOG["fatiador_de_corpos"]["params"]["duration"] * 1000)

        state, _, _ = resolve_display_state(tm, "guerreiro", 1000 + duration_ms - 1)
        self.assertEqual(state, "fatiador_de_corpos", "ainda dentro dos 5s reais")

        state, _, _ = resolve_display_state(tm, "guerreiro", 1000 + duration_ms + 1)
        self.assertEqual(state, "idle", "passou dos 5s reais -> volta pro idle/run")
        self.assertEqual(tm.anim_attack_state, "")


class TestLoopingAttackHasPriorityOverConcurrentTrigger(unittest.TestCase):
    """10/09/2026, pedido do usuário — bug real relatado: "a animação do
    tornado é cancelada quando o personagem se move". Causa raiz de
    verdade: `trigger_attack_anim` sobrescrevia o estado sem checar nada,
    então um auto-attack concorrente (que o movimento provoca ao entrar
    em alcance de um mob) cancelava o loop bem antes dos 5s reais. Golpe
    em LOOP ativo agora tem prioridade sobre qualquer golpe concorrente
    de menor prioridade até a própria duração expirar."""

    def setUp(self):
        from engine.components import TileMovement
        self.tm = TileMovement()
        self.tm.facing = "down"

    def test_auto_attack_concorrente_nao_cancela_o_loop_ativo(self):
        from engine.character_animation import trigger_attack_anim, resolve_display_state
        trigger_attack_anim(self.tm, "guerreiro", "fatiador_de_corpos", 1000)
        trigger_attack_anim(self.tm, "guerreiro", "", 1800)  # auto-attack concorrente
        state, anim_t, _ = resolve_display_state(self.tm, "guerreiro", 1800)
        self.assertEqual(state, "fatiador_de_corpos",
            "auto-attack concorrente não deveria cancelar o loop ainda ativo")
        self.assertEqual(anim_t, 800, "elapsed deveria continuar contando do início do loop, não reiniciar")

    def test_outra_skill_sem_loop_tambem_nao_cancela(self):
        from engine.character_animation import trigger_attack_anim, resolve_display_state
        trigger_attack_anim(self.tm, "guerreiro", "fatiador_de_corpos", 1000)
        trigger_attack_anim(self.tm, "guerreiro", "golpe_poderoso", 1800)
        state, _, _ = resolve_display_state(self.tm, "guerreiro", 1800)
        self.assertEqual(state, "fatiador_de_corpos")
        self.assertEqual(self.tm.anim_attack_loop_duration_ms, 5000,
            "duração do loop não deveria ter sido resetada pelo trigger concorrente")

    def test_golpe_comum_funciona_normalmente_apos_o_loop_expirar(self):
        from engine.character_animation import trigger_attack_anim, resolve_display_state
        trigger_attack_anim(self.tm, "guerreiro", "fatiador_de_corpos", 1000)
        trigger_attack_anim(self.tm, "guerreiro", "", 6001)  # depois dos 5s reais
        state, anim_t, _ = resolve_display_state(self.tm, "guerreiro", 6001)
        self.assertEqual(state, "attack", "após o loop expirar, um golpe comum deve tocar normalmente")
        self.assertEqual(anim_t, 0)

    def test_recast_do_mesmo_loop_substitui_normalmente(self):
        from engine.character_animation import trigger_attack_anim, resolve_display_state
        trigger_attack_anim(self.tm, "guerreiro", "fatiador_de_corpos", 1000)
        trigger_attack_anim(self.tm, "guerreiro", "fatiador_de_corpos", 1800)  # novo tick do mesmo loop
        state, anim_t, _ = resolve_display_state(self.tm, "guerreiro", 1800)
        self.assertEqual(state, "fatiador_de_corpos")
        self.assertEqual(anim_t, 0, "retrigger do mesmo loop reinicia o contador (novo tick de dano)")


class TestApplyCombatResultTriggersAttackAnim(unittest.TestCase):
    """Integração real: `_apply_combat_result` (client/
    remote_entity_handlers.py) deve disparar a animação de golpe do
    ATACANTE — local ou remoto, contra mob OU player, nunca em DoT/HoT
    nem quando não causa dano de verdade."""

    def _make_engine_stub(self, attacker_eid, attacker_class_id, is_remote):
        """Stub com `MagicMock` pro resto do GameEngine (função de
        produção real, ~430 linhas, mexe em combat log/som/FLT/PNQ/
        arqueiro — muitos atributos que não importam pra ESTE teste,
        cujo objetivo é só confirmar que o trigger de animação, perto do
        TOPO da função, é alcançado e escreve no componente certo).
        `world`/entidades continuam 100% reais — é a função de PRODUÇÃO
        rodando de verdade, não uma cópia da lógica de trigger (mesma
        régua do CLAUDE.md: "testar objeto real, não só fixture
        monkeypatchada"); só o resto do GameEngine (irrelevante aqui) é
        mockado, pra não precisar adivinhar cada atributo 1 por 1."""
        from unittest.mock import MagicMock
        from engine.world import World
        from engine.components import (Position, TileMovement, CharacterStats,
                                       RemoteControlled, CombatStats)

        world = World()
        eng = MagicMock()
        eng.world = world
        eng._remote_players = {}
        eng._remote_mobs = {}
        eng._my_eid = 999  # server eid do player local (arbitrário)
        eng._bg_silence_nonplayer_combat_fx.return_value = False
        eng._resolve_archer_attack.return_value = (False, None, False)
        eng._player_world_pos.return_value = (0.0, 0.0)

        if is_remote:
            eng.player_entity = world.create_entity()
            world.add_component(eng.player_entity, Position(x=0, y=0))
            world.add_component(eng.player_entity, TileMovement())
            world.add_component(eng.player_entity, CharacterStats(class_id="guerreiro"))

            atk_local = world.create_entity()
            world.add_component(atk_local, Position(x=0, y=0))
            world.add_component(atk_local, TileMovement())
            world.add_component(atk_local, RemoteControlled(class_id=attacker_class_id))
            eng._remote_players[attacker_eid] = atk_local
            eng._atk_local_eid = atk_local
        else:
            eng.player_entity = world.create_entity()
            world.add_component(eng.player_entity, Position(x=0, y=0))
            world.add_component(eng.player_entity, TileMovement())
            world.add_component(eng.player_entity, CharacterStats(class_id=attacker_class_id))
            eng._atk_local_eid = eng.player_entity
            eng._my_eid = attacker_eid  # o ATACANTE é o player local

        # Alvo: um mob remoto genérico, sem CombatStats (não precisa pra
        # este teste — só o efeito no ATACANTE importa aqui).
        target_local = world.create_entity()
        world.add_component(target_local, Position(x=32, y=0))
        eng._remote_mobs[555] = target_local

        return eng, world

    def test_auto_attack_local_causando_dano_liga_animacao_attack(self):
        from client.remote_entity_handlers import RemoteEntityHandlers
        eng, world = self._make_engine_stub(attacker_eid=42, attacker_class_id="guerreiro",
                                            is_remote=False)
        from engine.components import TileMovement
        RemoteEntityHandlers._apply_combat_result(eng, {
            "attacker": 42, "target": 555, "damage": 10, "outcome": "hit",
            "hp_after": 40, "source": "auto",
        })
        tm = world.get_component(eng._atk_local_eid, TileMovement)
        self.assertEqual(tm.anim_attack_state, "attack")

    def test_ataque_remoto_causando_dano_liga_animacao_no_player_remoto_certo(self):
        from client.remote_entity_handlers import RemoteEntityHandlers
        eng, world = self._make_engine_stub(attacker_eid=77, attacker_class_id="guerreiro",
                                            is_remote=True)
        from engine.components import TileMovement
        RemoteEntityHandlers._apply_combat_result(eng, {
            "attacker": 77, "target": 555, "damage": 10, "outcome": "hit",
            "hp_after": 40, "source": "auto",
        })
        tm_remote = world.get_component(eng._atk_local_eid, TileMovement)
        self.assertEqual(tm_remote.anim_attack_state, "attack")
        tm_local = world.get_component(eng.player_entity, TileMovement)
        self.assertEqual(tm_local.anim_attack_state, "",
            "só o ATACANTE deveria animar, não o player local (que aqui é a vítima)")

    def test_dot_nao_liga_animacao_de_golpe(self):
        from client.remote_entity_handlers import RemoteEntityHandlers
        eng, world = self._make_engine_stub(attacker_eid=42, attacker_class_id="guerreiro",
                                            is_remote=False)
        from engine.components import TileMovement
        RemoteEntityHandlers._apply_combat_result(eng, {
            "attacker": 42, "target": 555, "damage": 10, "outcome": "hit",
            "hp_after": 40, "source": "bleed",
        })
        tm = world.get_component(eng._atk_local_eid, TileMovement)
        self.assertEqual(tm.anim_attack_state, "", "DoT/HoT nunca liga a animação de golpe")

    def test_miss_liga_animacao_generica_mais_lenta(self):
        """10/09/2026, pedido do usuário: golpe que erra (miss/dodge/
        parry) não deve deixar o personagem "congelado" — reusa a
        MESMA animação de golpe, só mais lenta (MISS_ATTACK_SLOWDOWN),
        nenhuma arte nova."""
        from client.remote_entity_handlers import RemoteEntityHandlers
        from engine.character_animation import DEFAULT_FRAME_MS, MISS_ATTACK_SLOWDOWN
        eng, world = self._make_engine_stub(attacker_eid=42, attacker_class_id="guerreiro",
                                            is_remote=False)
        from engine.components import TileMovement
        for _outcome in ("miss", "dodge", "parry"):
            RemoteEntityHandlers._apply_combat_result(eng, {
                "attacker": 42, "target": 555, "damage": 0, "outcome": _outcome,
                "hp_after": 50, "source": "auto",
            })
            tm = world.get_component(eng._atk_local_eid, TileMovement)
            self.assertEqual(tm.anim_attack_state, "attack",
                f"outcome={_outcome} deveria ligar a animação genérica de golpe")
            self.assertEqual(tm.anim_attack_frame_ms, int(DEFAULT_FRAME_MS * MISS_ATTACK_SLOWDOWN),
                f"outcome={_outcome} deveria ser mais lento que o golpe normal")

    def test_hit_de_verdade_usa_duracao_normal_nao_a_de_erro(self):
        from client.remote_entity_handlers import RemoteEntityHandlers
        from engine.character_animation import DEFAULT_FRAME_MS
        eng, world = self._make_engine_stub(attacker_eid=42, attacker_class_id="guerreiro",
                                            is_remote=False)
        from engine.components import TileMovement
        RemoteEntityHandlers._apply_combat_result(eng, {
            "attacker": 42, "target": 555, "damage": 10, "outcome": "hit",
            "hp_after": 50, "source": "auto",
        })
        tm = world.get_component(eng._atk_local_eid, TileMovement)
        self.assertEqual(tm.anim_attack_state, "attack")
        self.assertEqual(tm.anim_attack_frame_ms, DEFAULT_FRAME_MS)

    def test_skill_sem_sprite_propria_usa_fallback_via_apply_combat_result(self):
        """class_id E sid fictícios — nem sid específico nem "skill"
        genérico existem pra essa classe, então cai no "attack", 3º e
        último nível da cascata. Não pode usar "guerreiro" real aqui:
        `guerreiro_skill_*.png` existe de verdade (fallback genérico de
        skill), então qualquer sid desconhecido pra essa classe cairia
        no nível 2, não no 3 — não pode depender de qual sprite existe
        no disco neste momento."""
        from client.remote_entity_handlers import RemoteEntityHandlers
        eng, world = self._make_engine_stub(attacker_eid=42, attacker_class_id="classe_inexistente_xyz",
                                            is_remote=False)
        from engine.components import TileMovement
        RemoteEntityHandlers._apply_combat_result(eng, {
            "attacker": 42, "target": 555, "damage": 30, "outcome": "hit",
            "hp_after": 20, "source": "skill", "sid": "skill_inexistente_xyz",
        })
        tm = world.get_component(eng._atk_local_eid, TileMovement)
        self.assertEqual(tm.anim_attack_state, "attack")

    def test_fatiador_de_corpos_liga_loop_pela_duracao_real_da_skill(self):
        """10/09/2026, pedido do usuário: Fatiador de Corpos dura 5s
        (SKILL_CATALOG, fonte única) — a animação deve ficar em loop
        por esse tempo todo, não expirar após 1 ciclo do sheet."""
        from client.remote_entity_handlers import RemoteEntityHandlers
        from content.skill_config import SKILL_CATALOG
        eng, world = self._make_engine_stub(attacker_eid=42, attacker_class_id="guerreiro",
                                            is_remote=False)
        from engine.components import TileMovement
        RemoteEntityHandlers._apply_combat_result(eng, {
            "attacker": 42, "target": 555, "damage": 15, "outcome": "hit",
            "hp_after": 20, "source": "skill", "sid": "fatiador_de_corpos",
        })
        tm = world.get_component(eng._atk_local_eid, TileMovement)
        duration_s = SKILL_CATALOG["fatiador_de_corpos"]["params"]["duration"]
        self.assertEqual(tm.anim_attack_loop_duration_ms, int(duration_s * 1000))

    def test_golpe_normal_nao_tem_duracao_de_loop(self):
        from client.remote_entity_handlers import RemoteEntityHandlers
        eng, world = self._make_engine_stub(attacker_eid=42, attacker_class_id="guerreiro",
                                            is_remote=False)
        from engine.components import TileMovement
        RemoteEntityHandlers._apply_combat_result(eng, {
            "attacker": 42, "target": 555, "damage": 10, "outcome": "hit",
            "hp_after": 40, "source": "auto",
        })
        tm = world.get_component(eng._atk_local_eid, TileMovement)
        self.assertEqual(tm.anim_attack_loop_duration_ms, 0)


if __name__ == "__main__":
    unittest.main()
