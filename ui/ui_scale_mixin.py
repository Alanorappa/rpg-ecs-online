"""ui_scale_mixin.py — Mixin para Systems de UI renderizados pelo cliente que
vivem fora de GameEngine (BlacksmithSystem, TrainerSystem, ShopSystem,
QuestDialogSystem, QuestJournalSystem, LootSystem, TalentSystem, MapOverlay,
god_mode) e por isso não tinham acesso a self._u()/self._ui_scale da engine.

Cada um desses sistemas criava suas próprias fontes uma única vez no
__init__ (`self._font_sm = _font(20)`) e nunca mais as atualizava — diferente
de GameEngine, que recarrega font_xs/sm/md/lg via _reload_ui_fonts() sempre
que o usuário muda a "Escala da UI" no menu de pausa. Resultado: esses
painéis nunca reagiam à escala, e a geometria (larguras/alturas/paddings) era
puro pixel fixo — texto vazando de caixa, botão encavalando.

GameEngine chama set_ui_scale(self._ui_scale) nesses sistemas depois de
construí-los e de novo sempre que o usuário muda a escala (ver
game.py:_set_ui_scale). Ver arquitetura/PROBLEMAS_ARQUITETURA.md item IU3
(seção de responsividade) para o levantamento completo.
"""
from ui.fonts import make as _font
from ui.ui_sizes import UI


class UIScaleMixin:
    """Subclasses declaram `_FONT_BASES = {"_font_sm": 20, "_font_md": 26, ...}`
    (nome do atributo -> tamanho base em px, mesmo valor que antes ia direto
    pra `_font(N)`) como atributo de classe. set_ui_scale() recria essas
    fontes escaladas e fica disponível via self._u(px) para a geometria."""

    _FONT_BASES: dict = {}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ui_scale: float = 1.0
        self._ui_scale_applied: "float | None" = None
        self._u_scale_override: "float | None" = None
        self.set_ui_scale(1.0)

    def _u(self, px: int) -> int:
        """Converte pixels base (desenhados como se ui_scale=1.0) pra pixels
        escalados pela escala de UI atual (ou pelo override de painel ativo
        — ver _set_panel_scale)."""
        s = self._u_scale_override if self._u_scale_override is not None else self.ui_scale
        return max(1, round(px * s))

    def _set_panel_scale(self, design_w: int, design_h: int, margin: int = 20,
                         margin_h: "int | None" = None) -> None:
        """Mesmo contrato de GameEngine._set_panel_scale — chamar no início de
        cada draw/click-handler de painel, antes de qualquer self._u(), com o
        tamanho base (escala 1.0) do painel. Garante que o painel nunca fique
        maior que a tela atual mesmo com ui_scale alto + janela pequena.

        margin = reserva horizontal; margin_h = reserva vertical (default
        None = usa margin também). Ver GameEngine._set_panel_scale pro bug
        real que motivou separar os dois (reserva de largura aplicada por
        engano na altura colapsava a escala pra negativa numa tela 720
        de altura)."""
        mh = margin if margin_h is None else margin_h
        screen = getattr(self, "hud_surf", None) or self.screen
        self._u_scale_override = max(0.15, min(
            self.ui_scale,
            (screen.get_width()  - margin) / design_w,
            (screen.get_height() - mh)     / design_h,
        ))

    # Mesmas reservas de GameEngine._HUD_SAFE_W/_MINIMAP_SAFE_W — ver lá pro
    # raciocínio completo.
    _HUD_SAFE_W     = UI.HUD_SAFE_W
    _MINIMAP_SAFE_W = UI.MINIMAP_SAFE_W

    def _safe_panel_origin(self, design_w: int, design_h: int, margin: int = 20) -> "tuple[int, int]":
        """Mesmo contrato de GameEngine._safe_panel_origin — centraliza o
        painel na área livre da tela, excluindo as zonas reservadas pro
        HUD/minimapa, em vez da tela inteira."""
        reserved_w = self._HUD_SAFE_W + self._MINIMAP_SAFE_W + margin
        self._set_panel_scale(design_w, design_h,
                              margin=round(reserved_w * self.ui_scale),
                              margin_h=round(margin * self.ui_scale))
        screen = getattr(self, "hud_surf", None) or self.screen
        SW, SH = screen.get_width(), screen.get_height()
        pw, ph = self._u(design_w), self._u(design_h)
        left   = self._u(self._HUD_SAFE_W)
        right  = self._u(self._MINIMAP_SAFE_W)
        safe_w = max(pw, SW - left - right)
        x0 = left + (safe_w - pw) // 2
        y0 = (SH - ph) // 2
        return x0, y0

    def set_ui_scale(self, scale: float) -> None:
        """Atualiza ui_scale e recarrega as fontes declaradas em _FONT_BASES.
        Idempotente — não recria fontes se a escala não mudou."""
        if scale == self._ui_scale_applied:
            return
        self.ui_scale = scale
        self._ui_scale_applied = scale
        for attr, base_px in self._FONT_BASES.items():
            setattr(self, attr, _font(round(base_px * scale)))
