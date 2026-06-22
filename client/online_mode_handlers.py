"""
online_mode_handlers.py — Mixin com o ciclo de vida da conexão online:
abre/mantém a conexão com o servidor, processa login, drena mensagens
recebidas (_process_network), resolve dano diferido de Bola de Fogo
(_process_bdf_pending), envia movimento do jogador e desenha o HUD
minimalista de status de conexão. Separado de game.py para manter
GameEngine conciso. Esta classe NÃO deve ser instanciada diretamente —
ela é herdada por GameEngine, que fornece self._net, self.world,
self.player_entity, self.screen, self.font_xs e os demais atributos
referenciados aqui.
"""
import pygame


class OnlineModeHandlers:

    # ── Modo Online ───────────────────────────────────────────────────────────

    def _connect_online(self) -> None:
        """Inicia conexão com o servidor. Se net_client já foi passado no __init__,
        apenas ativa o online_mode (login + spawn já foram feitos pelo login_screen)."""
        self._death_respawn_system.online_mode = True
        if self._net is not None:
            # Já conectado via login_screen — LOGIN_OK + WORLD_STATE estão na fila
            return
        import config as _cfg
        from client.network import NetworkClient
        data = _cfg.load()
        host = data.get("server_host", "localhost")
        port = int(data.get("server_port", 8765))
        self._net = NetworkClient(host=host, port=port)
        self._net.connect()
        import threading
        threading.Timer(0.5, self._do_login).start()

    def _do_login(self) -> None:
        if self._net and self._net.connected:
            # Lê stats reais do personagem para o servidor usar (evita fallbacks)
            from components import CombatStats
            cs  = self.world.get_component(self.player_entity, CombatStats)
            ap      = float(cs.attack_power) if cs else 0.0
            max_hp  = int(cs.max_hp)         if cs else 0
            self._net.login(self._net_user, self._net_pass, ap=ap, max_hp=max_hp)
        else:
            import threading
            threading.Timer(1.0, self._do_login).start()

    def _process_network(self) -> None:
        """
        Processa todas as mensagens recebidas do servidor neste frame.
        Chamado uma vez por frame no game loop, antes dos sistemas.
        """
        if not self._net:
            return
        for msg_type, payload, seq, ts in self._net.poll():
            self._handle_net_message(msg_type, payload)

    def _process_bdf_pending(self) -> None:
        """Associa dano diferido de BdF ao projétil visual após o SpellCastSystem rodar.

        Chamado logo após o loop de sistemas — neste ponto o SpellCastSystem já criou
        o projétil (se o cast completou neste frame). Tenta setar deferred_result no
        projétil voando para que _on_hit exiba o dano no impacto visual.
        Fallback após 200ms: exibe imediatamente via _apply_combat_result.
        """
        import time as _t_pend
        from components import PlayerProjectile as _PPpend, Position as _PPpendPos
        now = _t_pend.time()
        remaining: list[dict] = []
        for entry in self._bdf_pending:
            t_loc = entry["t_loc"]
            found = False
            for _peid, _pp, _ in self.world.get_entities_with(_PPpend, _PPpendPos):
                if (_pp.attacker_id == self.player_entity
                        and _pp.target_id == t_loc
                        and not _pp.deferred_result):
                    _pp.deferred_result = {
                        "damage":  entry["dmg"],
                        "outcome": entry["outcome"],
                    }
                    found = True
                    break
            if not found:
                if now > entry["deadline"]:
                    # Projétil sumiu ou nunca foi criado — mostra dano agora
                    self._apply_combat_result({
                        "attacker": self._my_eid,
                        "target":   entry["t_srv"],
                        "damage":   entry["dmg"],
                        "outcome":  entry["outcome"],
                        "hp_after": entry["hp_after"],
                        "source":   "skill",
                        "sid":      "bola_de_fogo",
                    })
                else:
                    remaining.append(entry)
        self._bdf_pending = remaining

    def _send_player_move(self) -> None:
        """
        Envia MOVE ao servidor se o jogador se moveu desde o último envio.
        Chamado após sistemas.update() no game loop.
        """
        if not self._net or not self._net.connected or self._my_eid == -1:
            return
        from components import TileMovement
        tm = self.world.get_component(self.player_entity, TileMovement)
        if not tm:
            return
        # Dash (Interceptar predito localmente, correção de knockback, etc.) muda
        # target_tile_x/y por conta própria — não é input do jogador. Mandar isso
        # como MOVE normal cria um pedido paralelo/concorrente com a resolução da
        # própria skill (validação dx<=1 às vezes aceita, às vezes rejeita,
        # dependendo da distância do dash) — gera correções fora de ordem e
        # dessincroniza a posição. Nenhum dash deveria nunca virar um MOVE normal:
        # a posição final do dash já chega ao servidor pelo canal da skill.
        if getattr(tm, "is_dash", False):
            return
        # Usa target (início do movimento) em vez de current (fim da animação)
        # → outro jogador vê o movimento começar junto com a animação local
        tx, ty = tm.target_tile_x, tm.target_tile_y
        if tx != self._net_last_tx or ty != self._net_last_ty:
            self._net.move(tx, ty)
            self._net_last_tx = tx
            self._net_last_ty = ty

    def _draw_online_hud(self) -> None:
        """HUD minimalista de conexão — canto superior direito da tela."""
        if not self._net:
            return
        connected = self._net.connected
        latency   = self._net.latency_ms
        n_players = len(self._remote_players)

        # Status de conexão
        if connected and self._my_eid != -1:
            status_txt = f"Online  {latency}ms  |  {n_players} jogador(es) próximo(s)"
            status_col = (80, 220, 80)
        elif connected:
            status_txt = "Conectado — aguardando login..."
            status_col = (220, 220, 80)
        else:
            status_txt = "Desconectado"
            status_col = (220, 80, 80)

        surf = self.font_xs.render(status_txt, True, status_col)
        x = self.screen.get_width() - surf.get_width() - 8
        y = 6
        bg = pygame.Surface((surf.get_width() + 6, surf.get_height() + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 140))
        self.screen.blit(bg, (x - 3, y - 2))
        self.screen.blit(surf, (x, y))

        # Posição local (debug)
        from components import TileMovement
        tm = self.world.get_component(self.player_entity, TileMovement)
        if tm:
            pos_txt = f"tile ({tm.current_tile_x}, {tm.current_tile_y})"
            ps = self.font_xs.render(pos_txt, True, (160, 160, 160))
            self.screen.blit(ps, (self.screen.get_width() - ps.get_width() - 8, y + surf.get_height() + 2))
