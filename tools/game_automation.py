"""
tools/game_automation.py — ferramenta de DEV (01/09/2026, pedido do usuário)
pra Claude jogar/inspecionar visualmente o cliente rodando (janela pygame
nativa) sem depender de screenshot manual. Fora do jogo de propósito — não
importa nada de engine/client/server, não faz parte do runtime real, só
control a janela do SO de fora.

Requer (já instalados neste ambiente): pyautogui (mouse/screenshot),
pydirectinput (teclado — via scan code, ÚNICO jeito que este jogo
SDL2/pygame-ce reconhece tecla sintética; pyautogui.keyDown/press NÃO
funciona aqui, testado ao vivo 01/09/2026), pygetwindow, Pillow.

Uso via linha de comando (1 ação por chamada — Claude roda, olha o
screenshot resultante, decide a próxima ação; não é um loop automático):

    py -3.10 tools/game_automation.py screenshot out.png
    py -3.10 tools/game_automation.py screenshot out.png --window "RPG ECS"
    py -3.10 tools/game_automation.py click 640 400
    py -3.10 tools/game_automation.py move 640 400
    py -3.10 tools/game_automation.py key space
    py -3.10 tools/game_automation.py keydown w
    py -3.10 tools/game_automation.py keyup w
    py -3.10 tools/game_automation.py type "texto de chat"
    py -3.10 tools/game_automation.py activate

Coordenadas de click/move são RELATIVAS à janela do jogo (0,0 = canto
superior esquerdo da área do jogo), não da tela inteira — convertidas
internamente via `_find_window()`.

Failsafe do pyautogui continua LIGADO de propósito (mover o mouse pro
canto superior esquerdo da tela aborta qualquer ação em andamento) — é
o kill-switch manual do usuário, nunca desligar.
"""
from __future__ import annotations

import sys
import time


def _make_dpi_aware() -> None:
    """Mesma função de `main.py::_make_dpi_aware` — este script roda como
    processo SEPARADO do jogo, então precisa da PRÓPRIA declaração de
    DPI-awareness. Sem isso, `pygetwindow`/`GetWindowRect` devolvem
    coordenadas VIRTUALIZADAS pelo Windows (escaladas pela config de DPI
    do monitor), enquanto o clique de fato acontece em pixel físico —
    os dois nunca batem, clique sempre erra o alvo (bug real achado
    01/09/2026 testando pela 1ª vez: clique em "Jogar" não fazia nada,
    coordenada calculada parecia certa no screenshot mas nunca acertava
    o botão de verdade)."""
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

DEFAULT_WINDOW_TITLE = "RPG ECS"


def _find_window(title_substring: str = DEFAULT_WINDOW_TITLE):
    import pygetwindow as gw

    matches = [w for w in gw.getAllWindows() if title_substring.lower() in w.title.lower()]
    if not matches:
        titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
        raise RuntimeError(
            f"Nenhuma janela com título contendo {title_substring!r} encontrada. "
            f"Janelas abertas: {titles}"
        )
    # Prefere a primeira visível/não-minimizada
    for w in matches:
        if w.visible and not w.isMinimized:
            return w
    return matches[0]


def activate(title_substring: str = DEFAULT_WINDOW_TITLE) -> None:
    win = _find_window(title_substring)
    if win.isMinimized:
        win.restore()
    win.activate()
    time.sleep(0.15)  # dá tempo do SO trocar o foco de verdade


def screenshot(out_path: str, title_substring: str = DEFAULT_WINDOW_TITLE) -> None:
    from PIL import ImageGrab

    win = _find_window(title_substring)
    # Precisa ATIVAR (trazer pra frente) antes de capturar — screenshot de
    # região pega o que está COMPOSITADO na tela naquele retângulo, não o
    # buffer da janela em si; se outra janela (ex: VSCode) estiver por
    # cima no mesmo espaço de tela, a captura sai dela, não do jogo (bug
    # real achado 01/09/2026 testando pela 1ª vez — captura saiu do
    # VSCode). `all_screens=True` (Pillow >=9.2) é necessário pra
    # funcionar em monitor secundário — sem isso, ImageGrab só enxerga o
    # monitor primário e a região fica preta.
    activate(title_substring)
    bbox = (win.left, win.top, win.left + win.width, win.top + win.height)
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    img.save(out_path)
    print(f"Screenshot salvo em {out_path} (janela {win.title!r}, bbox {bbox})")


def click(x: int, y: int, title_substring: str = DEFAULT_WINDOW_TITLE,
         button: str = "left") -> None:
    """mouseDown/mouseUp EXPLÍCITOS com pausa entre os 3 passos (mover →
    descer → subir) — `pyautogui.click()` puro (down+up quase instantâneo)
    não registrava no jogo de jeito nenhum na 1ª tentativa (01/09/2026,
    testado ao vivo: mouse ficava exatamente em cima do botão — hover
    highlight aparecia certo no screenshot — mas o clique em si nunca
    "pegava"). SDL/pygame parece precisar de uma janela de tempo mínima
    entre down e up pra reconhecer como clique de verdade, não só
    ruído. Confirmado funcionando: clique real em "Jogar" na tela de
    seleção de personagem carregou o mundo."""
    import pyautogui

    win = _find_window(title_substring)
    activate(title_substring)
    sx, sy = win.left + int(x), win.top + int(y)
    pyautogui.moveTo(sx, sy, duration=0.2)
    time.sleep(0.3)
    pyautogui.mouseDown(sx, sy, button=button)
    time.sleep(0.15)
    pyautogui.mouseUp(sx, sy, button=button)
    print(f"Clique ({button}) em janela-relativo ({x},{y}) = tela ({sx},{sy})")


def move(x: int, y: int, title_substring: str = DEFAULT_WINDOW_TITLE) -> None:
    import pyautogui

    win = _find_window(title_substring)
    sx, sy = win.left + int(x), win.top + int(y)
    pyautogui.moveTo(sx, sy)
    print(f"Mouse movido pra janela-relativo ({x},{y}) = tela ({sx},{sy})")


def key(key_name: str, title_substring: str = DEFAULT_WINDOW_TITLE) -> None:
    import pydirectinput

    activate(title_substring)
    pydirectinput.press(key_name)
    print(f"Tecla '{key_name}' pressionada")


def keydown(key_name: str, title_substring: str = DEFAULT_WINDOW_TITLE) -> None:
    """`pydirectinput`, NÃO `pyautogui.keyDown` (01/09/2026, achado
    testando ao vivo) — este jogo (pygame-ce/SDL2) lê o teclado por
    SCAN CODE, não por virtual-key code. `pyautogui.keyDown('d')` manda
    um evento de teclado válido pro Windows (a janela recebe foco
    certo, confirmado via GetForegroundWindow), mas o SDL nunca via
    isso como "d pressionado" — `pygame.key.get_pressed()` continuava
    tudo False, personagem nunca andava. `pydirectinput` manda via
    SendInput com o SCAN CODE físico da tecla (mesmo mecanismo de
    hardware real) — testado e confirmado: segurar 'a' 1.5s moveu o
    personagem 6 tiles pra oeste de verdade."""
    import pydirectinput

    activate(title_substring)
    pydirectinput.keyDown(key_name)
    print(f"Tecla '{key_name}' segurada (keyDown) — lembre de mandar keyup depois")


def keyup(key_name: str) -> None:
    import pydirectinput

    pydirectinput.keyUp(key_name)
    print(f"Tecla '{key_name}' solta (keyUp)")


def type_text(text: str, title_substring: str = DEFAULT_WINDOW_TITLE, interval: float = 0.03) -> None:
    import pydirectinput

    activate(title_substring)
    pydirectinput.typewrite(text, interval=interval)
    print(f"Texto digitado: {text!r}")


def _main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    # --window "Título" opcional em qualquer comando, no fim da linha
    title = DEFAULT_WINDOW_TITLE
    if "--window" in args:
        i = args.index("--window")
        title = args[i + 1]
        args = args[:i] + args[i + 2:]

    if cmd == "screenshot":
        screenshot(args[0], title_substring=title)
    elif cmd == "click":
        button = args[2] if len(args) > 2 else "left"
        click(int(args[0]), int(args[1]), title_substring=title, button=button)
    elif cmd == "move":
        move(int(args[0]), int(args[1]), title_substring=title)
    elif cmd == "key":
        key(args[0], title_substring=title)
    elif cmd == "keydown":
        keydown(args[0], title_substring=title)
    elif cmd == "keyup":
        keyup(args[0])
    elif cmd == "type":
        type_text(args[0], title_substring=title)
    elif cmd == "activate":
        activate(title)
        print("Janela ativada/focada")
    else:
        print(f"Comando desconhecido: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    _main()
