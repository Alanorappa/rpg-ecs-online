"""
check_surfaces.py — verifica superfícies de render nos sistemas de UI modal.

Regra: sistemas cujos render() desenham painéis/modais de interface
devem usar self.hud_surf para draw calls, nunca self.world_surf.

Sistemas verificados: LootSystem, ShopSystem, QuestDialogSystem,
                      BlacksmithSystem (crafting), TrainerSystem, QuestJournalSystem

Uso:
  python check_surfaces.py
"""
import ast, re, sys, os

# Sistemas de UI modal: (arquivo, nome_da_classe, metodo_problemático)
# Cada entrada define quais métodos NÃO devem usar world_surf para draw calls
UI_MODAL_METHODS = {
    "systems.py": {
        "LootSystem":       ["render"],
        "ShopSystem":       ["render"],
    },
    "quest_system.py": {
        "QuestDialogSystem":  ["render"],
        "QuestJournalSystem": ["render"],
    },
    "crafting_system.py": {
        "BlacksmithSystem": ["render"],
    },
    "trainer_system.py": {
        "TrainerSystem": ["render"],
    },
}

# Padrões de draw call que indicam escrita em uma superfície
DRAW_CALLS = {"blit", "fill", "draw"}

def uses_world_surf_for_drawing(method_node: ast.FunctionDef) -> list[str]:
    """Retorna linhas onde world_surf é usada para draw calls no método."""
    violations = []
    src_lines = ast.unparse(method_node).splitlines() if hasattr(ast, "unparse") else []

    for node in ast.walk(method_node):
        # Procura chamadas do tipo: self.world_surf.blit(...), pygame.draw.rect(self.world_surf, ...)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # self.world_surf.blit(...) / self.world_surf.fill(...)
        if (isinstance(func, ast.Attribute)
                and func.attr in ("blit", "fill")
                and isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "self"
                and func.value.attr == "world_surf"):
            violations.append(
                f"  linha ~{getattr(node, 'lineno', '?')}: self.world_surf.{func.attr}()"
            )
        # pygame.draw.*(self.world_surf, ...)
        if (isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "draw"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "pygame"
                and node.args):
            first_arg = node.args[0]
            if (isinstance(first_arg, ast.Attribute)
                    and first_arg.attr == "world_surf"
                    and isinstance(first_arg.value, ast.Name)
                    and first_arg.value.id == "self"):
                violations.append(
                    f"  linha ~{getattr(node, 'lineno', '?')}: pygame.draw.{func.attr}(self.world_surf, ...)"
                )
    return violations


def check_file(path: str, class_methods: dict) -> list[str]:
    errors = []
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"{path}: SyntaxError — {e}"]

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in class_methods:
            continue
        methods_to_check = class_methods[node.name]
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            if item.name not in methods_to_check:
                continue
            violations = uses_world_surf_for_drawing(item)
            for v in violations:
                errors.append(f"{path}  {node.name}.{item.name}() usa world_surf para draw:")
                errors.append(v)
    return errors


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    all_errors = []
    files_checked = 0

    for fname, class_methods in UI_MODAL_METHODS.items():
        path = os.path.join(base, fname)
        if not os.path.exists(path):
            print(f"AVISO: {fname} não encontrado, pulando.")
            continue
        errs = check_file(path, class_methods)
        all_errors.extend(errs)
        files_checked += 1

    if all_errors:
        print(f"\n{len([e for e in all_errors if 'usa world_surf' in e])} violação(ões) encontrada(s):\n")
        for e in all_errors:
            print(e)
        print()
        print("Regra: métodos render() de sistemas de UI devem usar self.hud_surf,")
        print("       nunca self.world_surf — o modal ficaria invisível após o scale.")
        sys.exit(1)
    else:
        print(f"OK — {files_checked} arquivo(s) verificados, nenhuma violação de superfície encontrada.")


if __name__ == "__main__":
    main()
