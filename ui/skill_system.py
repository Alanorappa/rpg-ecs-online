# skill_system.py
"""
SkillSystem — hotbar de habilidades (teclas 1-7, incluindo talentos).
Handlers de habilidade (_skill_*/_talent_*) vivem em
engine/skill_handlers.py via herança de SkillHandlers. Extraído de
ui/systems.py (18/09/2026, ver PROBLEMAS_ARQUITETURA.md §185, item #2
do backlog de 14).
"""
from __future__ import annotations
import pygame
import math
# TODO(B4): _font é usado apenas em sistemas visuais (HUD, FloatingText). O servidor
# importa `from systems import EnemyAISystem` e acaba carregando Pygame fonts pelo
# caminho. Funciona com SDL_VIDEODRIVER=dummy, mas é dependência desnecessária.
# Fix futuro: mover sistemas visuais para ClientSystems ou usar lazy import em render().
from ui.fonts import make as _font
from ui.ui_sizes import UI

# Re-exporta apply_effect de core_systems para compatibilidade com todo o código
# que já faz `from systems import apply_effect`.
from engine.core_systems import apply_effect
try:
    from debug.mob_combat_debug import MCL as _MCL
except ImportError:
    _MCL = None  # não disponível no ambiente do cliente

from engine.components import Position, TileMovement, CombatStats, CombatState, CharacterStats, Visible, StatusEffects, PlayerSkills, NPC, RemoteControlled
from engine.world import World
from engine.tileset import TILE_SIZE
from engine.utils import chebyshev, in_range, get_camera_offset
# Hoisted pro topo (14/09/2026, revisão de performance — ver
# arquitetura/CLIENT_PERFORMANCE_REVIEW.md item 2): estavam sendo
# reimportados dentro de RenderSystem.render(), no loop de desenho mais
# quente do cliente (uma vez por ENTIDADE visível, todo frame). Módulos
# puros (`from __future__ import annotations` só), sem ciclo de import
# com ui/systems.py — seguro no topo.
from ui.combat_log import LOG
from ui.floating_text import WARN
from engine.quest_events import fire as quest_fire
from engine.stat_fns import enter_combat

# Lookup reverso skill_id → (talent_id, min_points) — fonte única em
# world_systems (_TALENT_SKILL_REQS, usado também por is_skill_authorized
# no gate autoritativo do servidor). Alias mantido pro gate de UI local.
from engine.world_systems import _TALENT_SKILL_REQS as _TALENT_SKILL_REQ_SYS



# ---------------------------------------------------------------------------
# Sistemas headless de gameplay - movidos para world_systems.py (problema F).
# Re-export para compatibilidade: todo codigo cliente continua fazendo
# `from systems import EnemyAISystem` etc. Codigo de SERVIDOR deve importar
# de world_systems diretamente (nao arrasta pygame).
# ---------------------------------------------------------------------------
from engine.world_systems import System, EnemyAISystem, StatusEffectSystem, get_tilemap


# ---------------------------------------------------------------------------
# SkillSystem
# ---------------------------------------------------------------------------
from engine.skill_handlers import SkillHandlers

class SkillSystem(System, SkillHandlers):
    """Gerencia habilidades ativas do jogador (teclas 1-7, incluindo talentos).

    Handlers de habilidades (_skill_* e _talent_*) vivem em skill_handlers.py
    via herança de SkillHandlers — edite lá para adicionar ou alterar skills.
    """

    SKILL_KEYS = [pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4,
                  pygame.K_5, pygame.K_6, pygame.K_7]

    def __init__(self, world: World, player_entity_id: int,
                 screen: "pygame.Surface | None" = None):
        self.world = world
        self.player_entity_id = player_entity_id
        self.world_surf = screen
        self.hud_surf   = screen
        self._net = None  # NetworkClient — injetado por game.py para enviar CAST_SKILL

    # _is_on_screen: movido para engine/skill_handlers.py::SkillHandlers
    # (débito B3, 10/08/2026) — SkillSystem herda de lá agora. Ver docstring
    # do método na nova localização.

    def update(self, events: list = None, dt: float = 0) -> None:
        player_skills = self.world.get_component(self.player_entity_id, PlayerSkills)
        if not player_skills:
            return

        # Atualiza GCD e cooldowns de habilidades
        if player_skills.gcd_timer > 0:
            player_skills.gcd_timer = max(0.0, player_skills.gcd_timer - dt)
        for skill in player_skills.skills:
            if skill is None:
                continue
            if skill.current_cooldown > 0:
                skill.current_cooldown = max(0.0, skill.current_cooldown - dt)
            if skill.max_charges > 0 and skill.charges > 0 and skill.charge_timeout > 0:
                skill.charge_timer = max(0.0, skill.charge_timer - dt)
                if skill.charge_timer <= 0:
                    skill.charges = 0
                    LOG.add(f"{skill.name}: carga expirou!", (200, 100, 50))

        # Fatiador de Corpos: só o TIMER conta aqui (drives a barra de
        # canalização na hotbar, client/hotbar_handlers.py) — dano é
        # exclusivamente servidor (server/world_server.py, mesmo tick loop).
        # Chamar _fatiador_aoe_tick aqui já foi removido (07/08/2026): sem
        # nenhum guard de self._net, mas inofensivo online porque mob remoto
        # não tem CombatStats local (mesmo motivo do achado da Canalização,
        # ver PROBLEMAS_ARQUITETURA.md §13) — nunca deveria ter existido.
        char_stats = self.world.get_component(self.player_entity_id, CharacterStats)
        if char_stats and char_stats.fatiador_timer > 0:
            char_stats.fatiador_timer = max(0.0, char_stats.fatiador_timer - dt)
            char_stats.fatiador_tick  = max(0.0, char_stats.fatiador_tick  - dt)
            if char_stats.fatiador_tick <= 0 and char_stats.fatiador_timer > 0:
                _fat_skill = player_skills.skill_by_id("fatiador_de_corpos")
                _fat_interval = _fat_skill.params.get("tick_interval", 1.0) if _fat_skill else 1.0
                char_stats.fatiador_tick = _fat_interval
            if char_stats.fatiador_timer <= 0:
                LOG.add("Fatiador de Corpos terminou.", (200, 160, 100))
                # Imunidade (StatusEffects "cc_immune") expira sozinha via
                # StatusEffectSystem — nada pra desligar manualmente aqui.

        if not events:
            return

        combat_state = self.world.get_component(self.player_entity_id, CombatState)
        # Canalização activa: tecla de skill cancela a canalização antes de processar
        if combat_state and combat_state.is_casting:
            from engine.components import Channeling, SpellCast as _SCGuard
            channeling = self.world.get_component(self.player_entity_id, Channeling)
            _sc_guard  = self.world.get_component(self.player_entity_id, _SCGuard)
            # Safety valve: is_casting preso sem SpellCast nem Channeling → libera input
            if not channeling and _sc_guard is None:
                combat_state.is_casting = False
            else:
                if channeling:
                    for event in events:
                        if event.type == pygame.KEYDOWN:
                            any_skill_key = any(
                                skill is not None and event.key == player_skills.keybinds[i]
                                for i, skill in enumerate(player_skills.skills)
                            )
                            if any_skill_key:
                                self.world.remove_component(self.player_entity_id, Channeling)
                                combat_state.is_casting = False
                                from ui.combat_log import LOG as _LOG
                                from ui.floating_text import WARN as _WARN
                                _WARN.add("Canalização interrompida!")
                                break
                return  # aguarda próximo frame para usar a nova skill

        if combat_state and not combat_state.can_act():
            return

        for event in events:
            if event.type != pygame.KEYDOWN:
                continue
            for i, skill in enumerate(player_skills.skills):
                if skill is None:
                    continue
                if event.key == player_skills.keybinds[i]:
                    if not self._use_skill(i, skill):
                        skill.fail_flash_timer = 0.2
                    break

    # ------------------------------------------------------------------
    def _use_skill(self, _idx: int, skill) -> bool:
        """Tenta usar a skill. Retorna True se executou, False se falhou.

        Servidor calcula dano e efeitos; cliente executa apenas
        cooldown/GCD/som e envia CAST_SKILL — ver `_use_skill_visual_only`.
        `_skill_<id>` (mixin `SkillHandlers`, `engine/skill_handlers.py`)
        permanecem a fonte única do efeito de cada skill: reusados pelo
        servidor via `HeadlessSkillHandler` (débito B3, fechado 10/08/2026)
        — não fazem parte deste caminho, que é só feedback local/predição."""
        return self._use_skill_visual_only(_idx, skill)

    # ------------------------------------------------------------------
    def _use_skill_visual_only(self, _idx: int, skill) -> bool:
        """Replica client-side as verificações que o servidor vai fazer de
        verdade (autoritativo), pra dar feedback local imediato (som,
        flash de botão, "aguardando confirmação") sem esperar round-trip
        de rede — nunca aplica dano/efeito, só manda CAST_SKILL e espera
        SKILL_RESULT confirmar.

        1. can_act()/is_action_locked() check
        2. GCD check
        3. skill.is_ready() check (cooldown/cargas)
        4. Para ofensivas: resolve alvo (local) + enter_combat/is_pursuing + range check
        5. Rage/mana/HP threshold (feedback local — servidor valida de novo)
        6. Marca _server_pending (bloqueia reuso até SKILL_RESULT confirmar)
        7. Envia CAST_SKILL

        Modo offline não existe mais (servidor é sempre o único caminho de
        gameplay, ver `PROBLEMAS_ARQUITETURA.md` §13) — removido daqui
        10/08/2026 (mecânico, `self._net` é sempre não-None neste ponto,
        setado em `game.py` antes do loop principal permitir qualquer input).
        """
        combat_state  = self.world.get_component(self.player_entity_id, CombatState)
        player_skills = self.world.get_component(self.player_entity_id, PlayerSkills)
        _tile_move_sk = self.world.get_component(self.player_entity_id,
                                                  TileMovement)

        # 0. Talent lock: skill requer talento que não está alocado
        if skill.skill_id and skill.skill_id in _TALENT_SKILL_REQ_SYS:
            _tl_tid, _tl_name, _tl_min = _TALENT_SKILL_REQ_SYS[skill.skill_id]
            from engine.components import TalentTree as _TTLock
            _tt_lk = self.world.get_component(self.player_entity_id, _TTLock)
            if _tt_lk is not None and _tt_lk.allocated.get(_tl_tid, 0) < _tl_min:
                WARN.add("Requer talento")
                return False

        # 1. can_act (igual offline)
        if combat_state and not combat_state.can_act():
            return False
        # 1b. Sleep/disoriented/polymorph: can_act() não cobre (StatusEffects, não
        # CombatState) — igual ao bloqueio de PlayerInputSystem para movimento/ações.
        from engine.utils import is_action_locked
        if is_action_locked(self.world, self.player_entity_id):
            return False
        # 2. GCD (igual offline)
        if player_skills and player_skills.gcd_timer > 0:
            return False
        # 2b. Fatiador de Corpos (canalização, 10/09/2026): nenhuma outra
        # skill enquanto o spin estiver ativo — servidor já rejeita de
        # verdade (skill_processor.py), isto é só o mesmo bloqueio
        # replicado aqui pra feedback local imediato (igual overlay de
        # cooldown na hotbar, que já existia mas não impedia o clique).
        _char_fat_ui = self.world.get_component(self.player_entity_id, CharacterStats)
        if _char_fat_ui is not None and _char_fat_ui.fatiador_timer > 0:
            WARN.add("Canalizando Fatiador de Corpos")
            return False
        # 3. Cooldown/cargas + pending server (igual offline + online)
        if getattr(skill, "_server_pending", False):
            return False  # aguardando confirmação do servidor
        if not skill.is_ready():
            if skill.current_cooldown > 0:
                WARN.add(f"Em recarga ({skill.current_cooldown:.1f}s)")
            elif skill.max_charges > 0:
                WARN.add("Sem cargas")
            return False

        _is_offensive = getattr(skill, "offensive", True)
        _has_cast     = getattr(skill, "cast_time", 0.0) > 0
        _is_aoe       = getattr(skill, "needs_aoe_target", False)

        # Skills AOE (Calamidade Flamejante): chama o handler diretamente para mostrar
        # a mira antes do clique — CAST_SKILL é enviado pelo AoeTargetingSystem ao clicar.
        if _is_aoe and skill.skill_id:
            _aoe_handler = getattr(self, f"_skill_{skill.skill_id}", None)
            if _aoe_handler:
                _cs_aoe   = self.world.get_component(self.player_entity_id,
                                                      CombatStats)
                _tm_aoe   = self.world.get_component(self.player_entity_id,
                                                      TileMovement)
                _cst_aoe  = self.world.get_component(self.player_entity_id,
                                                      CombatState)
                return bool(_aoe_handler(skill, _cs_aoe, _cst_aoe, _tm_aoe))
            return False
        _char = self.world.get_component(self.player_entity_id,
                                          CharacterStats)
        _cs   = self.world.get_component(self.player_entity_id,
                                          CombatStats)

        # 4. Para ofensivas: resolve alvo + inicia chase + verifica range
        _needs_target = getattr(skill, "needs_target", True)
        if _is_offensive and combat_state and _tile_move_sk:
            _target_local = combat_state.target_entity_id
            if _needs_target:
                # Limpa target inválido: removido do mundo OU fora da visão (fog/parede)
                if _target_local != -1:
                    _stale_pos = self.world.get_component(_target_local,
                                     Position)
                    _stale_vis = self.world.get_component(_target_local,
                                     Visible)
                    # Alvo morto (mob com HP<=0 ou player remoto cujo corpo
                    # ficou no chão): não é mais alvo válido — limpa igual
                    # entidade removida, evita tocar som/iniciar cast num corpo.
                    _stale_cs = self.world.get_component(_target_local,
                                     CombatStats)
                    _stale_rc = self.world.get_component(_target_local,
                                     RemoteControlled)
                    _is_dead_target = (
                        (_stale_cs is not None and _stale_cs.current_hp <= 0) or
                        (_stale_rc is not None and _stale_rc.hp <= 0)
                    )
                    if _stale_pos is None or _stale_vis is None or _is_dead_target:
                        _target_local = -1
                        combat_state.target_entity_id = -1
                        combat_state.pursuit_skill_range = 0
                if _target_local == -1:
                    # Auto-select: mesmo comportamento de _resolve_target — B6
                    _params_pre = getattr(skill, "params", {}) or {}
                    _auto_range = _params_pre.get("max_range") or getattr(skill, "cast_range", 6)
                    _target_local = self._resolve_target(
                        combat_state, _tile_move_sk, _max_range=_auto_range)
                if _target_local == -1:
                    WARN.add("Nenhum alvo")
                    return False

                # Alvo amigável: recusa AQUI, antes de tocar som/entrar em
                # perseguição. Servidor também recusa (server/
                # skill_processor.py — CAST_SKILL contra alvo amigável),
                # mas sem este gate client-side o arco "tensiona" (som
                # toca), o personagem entra em combate e persegue o alvo
                # pra sempre — o SKILL_RESULT failed que volta do
                # servidor não completava o cast, então nada limpava
                # is_pursuing (bug real relatado pelo usuário 17/07/2026,
                # arqueiro travado tentando alcançar o Guarda Real).
                from engine.faction_system import can_engage as _can_engage_skill
                if not _can_engage_skill(self.world, self.player_entity_id, _target_local):
                    WARN.add("Alvo amigável")
                    return False

            # enter_combat + is_pursuing ANTES do range check.
            # Garante que pressionar skill inicia o chase/auto-attack mesmo fora de alcance.
            # is_pursuing=True para todas as ofensivas — cast-time skills bloqueiam
            # auto-attack via is_casting=True em can_act() durante o cast.
            from engine.stat_fns import enter_combat as _ec_pre
            _ec_pre(combat_state)
            combat_state.is_pursuing = True
            # reengage_chase cancela clique de chão/"Seguir" pendente —
            # sem isso, usar a skill (ex: Interceptar) enquanto um clique
            # de chão ainda está em andamento reativava o auto-chase sem
            # cancelar o auto-move de chão, e os dois competiam pela
            # posição do player todo frame (zigue-zague entre o tile
            # clicado e o alvo — bug real relatado pelo usuário
            # 09/09/2026, ver PROBLEMAS_ARQUITETURA.md §127). Mesma
            # classe de bug já corrigida pra clique em NPC (§112).
            from engine.utils import reengage_chase
            reengage_chase(self.world, self.player_entity_id)

            # Range check — apenas para skills que exigem alvo explícito
            if _needs_target and _target_local != -1:
                _params          = getattr(skill, "params", {}) or {}
                # Mago usa cast_range no catálogo; guerreiro/arqueiro usam params.max_range
                _max_range_tiles = _params.get("max_range") or getattr(skill, "cast_range", 0)
                # cast_range=0 significa melee (sem range explícito no catálogo);
                # usa 1 tile para o check — equivale a MELEE_RANGE_PX (1t + tolerance)
                if _max_range_tiles == 0:
                    _max_range_tiles = 1
                # Alimenta CombatState.pursuit_skill_range (ver docstring lá) —
                # a perseguição genérica por-tick passa a mirar o alcance
                # DESTA skill específica, não mais o mínimo entre todas as
                # skills da hotbar. Grava mesmo se o check de distância
                # abaixo falhar agora ("Fora de alcance") — é exatamente
                # esse caso que precisa fazer o personagem ANDAR até aqui.
                combat_state.pursuit_skill_range = _max_range_tiles
                _min_range_tiles = _params.get("min_range", 0)
                _pl_pos  = self.world.get_component(self.player_entity_id,
                                                     Position)
                _tgt_pos = self.world.get_component(_target_local,
                                                     Position)
                # Mesma métrica do check autoritativo do servidor
                # (SkillHandlers._resolve_target, engine/skill_handlers.py):
                # in_range() (Euclidiana em tile, círculo de verdade) pra
                # alcance ranged de verdade (>1 tile); chebyshev() (8
                # direções) só pra adjacência melee (_max_range_tiles<=1) —
                # diagonal precisa contar igual a reto NESSE caso, senão
                # golpe corpo-a-corpo quebra em 4 das 8 direções. NUNCA
                # Euclidiana em PIXEL contínuo aqui (bug real relatado pelo
                # usuário, 14/09/2026, ver PROBLEMAS_ARQUITETURA.md §134/§135).
                _tgt_tm = self.world.get_component(_target_local,
                                                    TileMovement)
                if _tile_move_sk and _tgt_tm and _pl_pos and _tgt_pos:
                    if _max_range_tiles <= 1:
                        _atk_ok = chebyshev(_tile_move_sk.current_tile_x, _tile_move_sk.current_tile_y,
                                             _tgt_tm.current_tile_x, _tgt_tm.current_tile_y) <= _max_range_tiles
                    else:
                        _atk_ok = in_range(_tile_move_sk.current_tile_x, _tile_move_sk.current_tile_y,
                                            _tgt_tm.current_tile_x, _tgt_tm.current_tile_y, _max_range_tiles)
                    if not _atk_ok:
                        WARN.add("Fora de alcance")
                        return False
                    if _min_range_tiles > 0 and in_range(_tile_move_sk.current_tile_x, _tile_move_sk.current_tile_y,
                                                          _tgt_tm.current_tile_x, _tgt_tm.current_tile_y,
                                                          _min_range_tiles - 1):
                        WARN.add("Alvo muito próximo")
                        return False
                    # LOS check para skills com projétil: bloqueia se há parede no caminho.
                    # Evita deduzir mana/cooldown quando o projétil seria destruído na parede.
                    # Campo de catálogo (não hardcode de skill_id) — qualquer skill nova
                    # com projétil visual só precisa marcar has_projectile=True (10/08/2026).
                    from content.skill_config import SKILL_CATALOG as _SC_proj
                    _skill_has_proj = _SC_proj.get(skill.skill_id, {}).get("has_projectile", False)
                    if _skill_has_proj:
                        _tmap_los = get_tilemap()
                        if _tmap_los:
                            _ptx_los = int(_pl_pos.x / TILE_SIZE)
                            _pty_los = int(_pl_pos.y / TILE_SIZE)
                            _ttx_los = int(_tgt_pos.x / TILE_SIZE)
                            _tty_los = int(_tgt_pos.y / TILE_SIZE)
                            if not EnemyAISystem._has_line_of_sight(
                                    _tmap_los, _ptx_los, _pty_los, _ttx_los, _tty_los):
                                WARN.add("Há obstáculos no caminho")
                                return False

        # 5. Rage/mana/HP threshold — verifica com awareness de proc (igual offline)
        _rage_cost = 0
        _mana_cost = 0
        # Proc: free charge que ignora custo e restrição de HP (ex: Assassino → Executar)
        _proc_attr         = getattr(skill, "proc_attr",         "")
        _proc_ignores_cost = getattr(skill, "proc_ignores_cost", False)
        _is_procced = bool(
            _proc_attr and _char and getattr(_char, _proc_attr, 0) > 0
        )
        if skill.skill_id and _char and _cs:
            # Rage: usa custo base do skill, mas prefere custo modificado por talentos
            # (ex: Golpe Poderoso tem golpe_poderoso_rage_cost = 15 - pontos Veterano)
            _rage_cost = getattr(skill, "rage_cost", 0)
            _talent_cost = getattr(_cs, f"{skill.skill_id}_rage_cost", None)
            if _talent_cost is not None:
                _rage_cost = _talent_cost  # talento modificou o custo (ex: Veterano)
            if _rage_cost > 0 and not (_is_procced and _proc_ignores_cost):
                if _char.rage < _rage_cost:
                    WARN.add(f"Raiva insuficiente ({_rage_cost})")
                    return False
            # Mana custo fixo (ex: Bola de Fogo, Calcinar, Nova Congelante)
            _fixed_mana = getattr(skill, "mana_cost", 0)
            if _fixed_mana > 0 and not (_is_procced and _proc_ignores_cost):
                # Aplica descontos de mana por talento (mesma lógica dos handlers offline)
                from content.skill_config import SKILL_CATALOG as _SC_mc
                _skill_mc = _SC_mc.get(skill.skill_id, {})
                # Desconto flat (ex: Frieza → fire_mana_discount)
                _disc_attr = _skill_mc.get("mana_discount_attr", "")
                if _disc_attr and _cs:
                    _fixed_mana = max(0, _fixed_mana - int(getattr(_cs, _disc_attr, 0)))
                # Desconto percentual (ex: Piromaníaco → pyromania_bonus)
                _pct_attr = _skill_mc.get("mana_pct_discount_attr", "")
                if _pct_attr and _cs:
                    _pct = getattr(_cs, _pct_attr, 0.0)
                    if _pct > 0:
                        _fixed_mana = max(0, int(_fixed_mana * (1.0 - _pct)))
                # Chama Interna: proc ativo → cast grátis (não bloqueia por mana)
                _fire_free = _char and getattr(_char, "fire_instant_ready", False)
                if not _fire_free:
                    _char_mana = getattr(_char, "mana", 0) if _char else 0
                    if _char_mana < _fixed_mana:
                        WARN.add("Mana insuficiente")
                        return False
            # Mana custo percentual (ex: Polimorfia = 10% mana máxima)
            _mana_pct = getattr(skill, "mana_cost_pct", 0.0)
            if _mana_pct > 0 and hasattr(_cs, "max_mana"):
                _mana_cost = int(_cs.max_mana * _mana_pct)
                if _mana_cost > 0 and getattr(_char, "mana", 0) < _mana_cost:
                    WARN.add("Mana insuficiente")
                    return False
            # Concentração (arqueiro) — mesmo check do offline _check_concentration
            _conc_cost = (getattr(skill, "params", {}) or {}).get("concentration_cost", 0)
            if _conc_cost > 0 and _char:
                _conc_free = _cs and getattr(_cs, "concentration_free", False)
                if not _conc_free and getattr(_char, "concentration", 0) < _conc_cost:
                    WARN.add(f"Concentração insuficiente ({int(getattr(_char, 'concentration', 0))}/{_conc_cost})")
                    return False
        # HP threshold (ex: Executar exige alvo <30% HP) — verifica no cliente via RemoteEntityMeta
        if not (_is_procced and _proc_ignores_cost) and combat_state:
            _params_sk   = getattr(skill, "params", {}) or {}
            _hp_threshold = _params_sk.get("hp_threshold", 0.0) if isinstance(_params_sk, dict) else 0.0
            if _hp_threshold > 0:
                _tgt_local = combat_state.target_entity_id
                from engine.components import RemoteEntityMeta as _REM_sk
                _meta_sk = self.world.get_component(_tgt_local, _REM_sk)
                if _meta_sk and _meta_sk.hp_max > 0:
                    if _meta_sk.hp / _meta_sk.hp_max >= _hp_threshold:
                        WARN.add(f"Alvo precisa ter <{int(_hp_threshold * 100)}% HP")
                        return False

        # 6. Aplica efeitos locais — não aplica GCD nem cooldown — espera
        # confirmação do servidor. Mostra flash de "botão pressionado" (igual
        # ao de falha por recursos). GCD + cooldown + som são aplicados em
        # SKILL_RESULT quando confirmado.
        skill.fail_flash_timer          = 0.15   # flash escuro rápido = "registrado"
        skill._server_pending           = True   # bloqueia reuso até confirmação
        skill._server_pending_timeout   = 0.40   # fallback: libera após 400ms (dentro do GCD 0.8s)
        # Para skills baseadas em cargas: consome localmente (igual ao handler offline)
        # Servidor também consume a sua cópia; cargas são regrantadas via morte de mob.
        if skill.max_charges > 0 and skill.charges > 0:
            skill.charges     -= 1
            skill.charge_timer = 0.0

        # Cura NÃO é predita localmente — servidor confirma via STATS_UPDATE (heal_amount).
        # Isso garante que Vitória Iminente só cura se o dano for aplicado no servidor.

        # NÃO deduz rage/mana localmente — servidor é autoritativo
        # Motivo: se servidor falhar (range/target), rage não deve ser consumida.
        # Servidor envia STATS_UPDATE com rage real após processar a skill.
        # Cliente atualiza a partir desse valor (sem "dip" visual falso).
        _rage_pre  = getattr(_char, "rage",  0) if _char else 0
        _mana_pre  = getattr(_cs,   "mana",  0) if _cs   else 0
        # (dedução acontece no servidor via sync_player_resources + handler)

        # 7. enter_combat + is_pursuing — já feito no passo 4, para ofensivas
        # (não-ofensivas nunca entram em combate por aqui).

        # Envia CAST_SKILL com rage/mana PRÉ-dedução para o servidor validar corretamente
        if self._net:
            from shared.messages import MsgType as _MT
            _tid_local  = getattr(combat_state, "target_entity_id", -1) if combat_state else -1
            _tid_server = -1
            if _tid_local != -1:
                from engine.components import RemoteEntityMeta as _REM_cast
                _meta_cast = self.world.get_component(_tid_local, _REM_cast)
                if _meta_cast:
                    _tid_server = _meta_cast.server_eid
            # PvP: alvo pode ser player remoto (não está em RemoteEntityMeta)
            if _tid_server == -1 and _tid_local != -1:
                _rc_cast = self.world.get_component(_tid_local, RemoteControlled)
                if _rc_cast is not None:
                    _tid_server = _rc_cast.server_eid

            # Direção do player até o mouse (mundo) — capturada no instante do cast,
            # igual ao alvo (_tid_server) já é. Sem isso ficava hardcoded em (0,0):
            # skills de cone sem alvo travado (ex: Tiro Múltiplo) chegavam no
            # servidor com direção nula, _server_tiro_multiplo descartava
            # (dlen < 0.001) e a skill nunca acertava nada — mostrava a mira,
            # mas não disparava (ver arquitetura/PROBLEMAS_ARQUITETURA.md).
            # Pirofagia já calcula isso à parte (PirofagiaSystem, spell_system.py);
            # aqui cobre as demais skills de cone que passam pelo fluxo genérico.
            _dir_x, _dir_y = 0.0, 0.0
            _pos_cast = self.world.get_component(self.player_entity_id, Position)
            if _pos_cast:
                _lw_cast = self.world_surf.get_width()  if self.world_surf else 1280
                _lh_cast = self.world_surf.get_height() if self.world_surf else 720
                _cam_x_cast, _cam_y_cast = get_camera_offset(self.world, _lw_cast, _lh_cast)
                _scale_cast = (self.world_surf.get_width() / max(1, self.hud_surf.get_width())
                               if self.world_surf and self.hud_surf else 1.0)
                _msx_cast, _msy_cast = pygame.mouse.get_pos()
                _mx_cast = _msx_cast * _scale_cast
                _my_cast = _msy_cast * _scale_cast
                _ddx_cast = _mx_cast - (_pos_cast.x - _cam_x_cast)
                _ddy_cast = _my_cast - (_pos_cast.y - _cam_y_cast)
                _dlen_cast = math.hypot(_ddx_cast, _ddy_cast) or 1.0
                _dir_x, _dir_y = _ddx_cast / _dlen_cast, _ddy_cast / _dlen_cast

            self._net.send(_MT.CAST_SKILL, {
                "sid":   skill.skill_id,
                "tid":   _tid_server,
                "dir_x": _dir_x,
                "dir_y": _dir_y,
                "rage":  _rage_pre,
                "mana":  _mana_pre,
            })

        # Spells com cast_time: cria SpellCast visual_only para exibir a barra de cast.
        # Servidor processa o efeito real; cliente remove o SpellCast ao encher sem disparar.
        if _has_cast:
            from engine.components import SpellCast as _SCVis
            from content.skill_config import SKILL_CATALOG as _SC_int
            _sc_tid    = getattr(combat_state, "target_entity_id", -1) if combat_state else -1
            _sc_cast   = getattr(skill, "cast_time", 0.0)
            _skill_cat = _SC_int.get(skill.skill_id, {})
            _interruptible = _skill_cat.get("interruptible", True)
            # Aplica redução de cast_time ANTES de decidir criar SpellCast.
            # Se a redução zerar o tempo (ex: Nova Congelante com 5pts Precisão Elemental),
            # a skill vira instantânea: sem barra de cast e sem SpellCast interruptível.
            # Criar SpellCast com cast_time=0 + interruptible=True causava cancelamento
            # imediato quando o player estava em movimento, bloqueando som e efeito.
            _ct_red_attr = _skill_cat.get("cast_time_reduction_attr", "")
            if _ct_red_attr and _cs:
                _sc_cast = max(0.0, _sc_cast - getattr(_cs, _ct_red_attr, 0.0))
            # Chama Interna: proc ativo → BdF instantânea, sem barra de cast
            if skill.skill_id == "bola_de_fogo" and _char and getattr(_char, "fire_instant_ready", False):
                _sc_cast = 0.0
                _char.fire_instant_ready = False  # consome proc (servidor já consumiu a sua cópia)
            if _sc_cast > 0.0:
                self.world.add_component(self.player_entity_id, _SCVis(
                    spell_id     = skill.skill_id,
                    cast_time    = _sc_cast,
                    elapsed      = 0.0,
                    target_id    = _sc_tid,
                    mana_cost    = 0,
                    visual_only  = True,
                    interruptible= _interruptible,
                ))
                if combat_state:
                    combat_state.is_casting = True

        # Interceptar: NÃO prediz mais a animação de dash localmente (revisado
        # 22/07/2026, pedido do usuário — bug real: alvo em movimento causava
        # dessincronia client/server, personagem ficava em loop de "dash e
        # volta" a cada rejeição do servidor). A animação só toca quando
        # `_handle_msg_entity_move` recebe a correção confirmada do servidor
        # (`is_dash=True`, `skill_rejected=False`) — mesmo mecanismo já usado
        # pra qualquer deslocamento forçado por servidor (knockback etc.):
        # como `player_tm.is_moving` continua `False` aqui (nada predito),
        # esse handler cai no branch que INICIA a animação pela primeira vez,
        # em vez de reconciliar uma predição local. Se o servidor rejeitar
        # (`skill_rejected=True`), nenhuma animação chega a tocar — sem
        # snap-back visível, porque nunca houve nada pra desfazer.

        # Fatiador de Corpos: seta timer local para a animação de channeling (hotbar overlay).
        # Ticks reais são processados no servidor; o cliente só usa o timer para o visual.
        # _fatiador_aoe_tick local não causa dano (mobs online não têm CombatStats).
        if skill.skill_id == "fatiador_de_corpos" and _char:
            _fat_p = getattr(skill, "params", {}) or {}
            _char.fatiador_timer = _fat_p.get("duration",      5.0)
            _char.fatiador_tick  = _fat_p.get("tick_interval", 1.0)

        # Previne auto-attack no mesmo frame que a skill: se o timer já zerou, dá um
        # mínimo de 1 frame (0.05s) para que PlayerInputSystem não dispare o auto-attack
        # simultaneamente, evitando o som de ataque sobreposto com o da skill.
        if _cs and _cs.attack_cooldown_timer <= 0:
            _cs.attack_cooldown_timer = 0.05

        # Skills sem cast (instantâneas) já causaram dano de forma síncrona — conta
        # aqui. Skills com cast (ex: Bola de Fogo) só confirmam dano depois, via
        # SKILL_RESULT(is_proj_damage) — contadas em
        # network_handlers.py::_handle_msg_skill_result, não aqui (ativação ≠ dano).
        if skill.skill_id and not _has_cast:
            from engine.components import TrainingDummy as _TDsk_vo
            _qf_target_vo = getattr(combat_state, "target_entity_id", -1) if combat_state else -1
            _on_dummy_vo = (_qf_target_vo != -1
                           and self.world.get_component(_qf_target_vo, _TDsk_vo) is not None)
            quest_fire("use_skill", skill_id=skill.skill_id, on_dummy=_on_dummy_vo)

        return True

    # _resolve_target: movido para engine/skill_handlers.py::SkillHandlers
    # (débito B3, 10/08/2026) — SkillSystem herda de lá agora. Ver docstring
    # do método na nova localização.

    # Todos os handlers (_skill_* e _talent_*) estão em skill_handlers.py via SkillHandlers.

