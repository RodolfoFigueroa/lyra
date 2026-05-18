import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _extract_names(node: ast.AST | None) -> set[str]:
    """Finds all variable/type names referenced within an AST node."""
    if not node:
        return set()
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def generate_sdk_interface(source: str, dest: str) -> None:  # noqa: PLR0912
    source_path = Path(source)
    dest_path = Path(dest)

    # 1. Read the source code
    with source_path.open() as f:
        source_code = f.read()

    # 2. Parse it into an Abstract Syntax Tree
    tree = ast.parse(source_code)

    required_names = set()
    abstract_class_node = None

    # 3. Find and modify the concrete class, harvesting required types
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "LyraDBImplicit":
            abstract_class_node = node
            node.name = "LyraDB"
            node.bases = [ast.Name(id="ABC", ctx=ast.Load())]

            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    item.decorator_list.append(
                        ast.Name(id="abstractmethod", ctx=ast.Load())
                    )

                    # Harvest names from argument type hints and default values
                    for arg in item.args.args + item.args.kwonlyargs:
                        required_names.update(_extract_names(arg.annotation))
                    for default in item.args.defaults + item.args.kw_defaults:
                        required_names.update(_extract_names(default))

                    # Harvest names from the return type hint
                    required_names.update(_extract_names(item.returns))

                    # Preserve docstring, strip execution logic
                    if ast.get_docstring(item):
                        docstring_node = item.body[0]
                        item.body = [
                            docstring_node,
                            ast.Expr(value=ast.Constant(value=Ellipsis)),
                        ]
                    else:
                        item.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]

    if not abstract_class_node:
        msg = f"Class 'LyraDBImplicit' not found in {source_path}"
        raise ValueError(msg)

    # 4. Rebuild the module strictly with necessary imports and the abstract class
    new_body = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Filter aliases in the import statement
            new_aliases = []
            for alias in node.names:
                # Get the name as it appears in the file (handling `import X as Y`)
                imported_name = alias.asname or alias.name.split(".")[0]
                if imported_name in required_names:
                    new_aliases.append(alias)

            # Only keep the import if at least one required name survived
            if new_aliases:
                node.names = new_aliases
                new_body.append(node)

        elif node is abstract_class_node:
            new_body.append(node)

    tree.body = new_body

    # 5. Generate the new source code
    new_source = "from abc import ABC, abstractmethod\n\n" + ast.unparse(tree)

    # 6. Write it to the SDK file
    with dest_path.open("w") as f:
        f.write(new_source)

    msg = f"Generated SDK interface from {source_path} to {dest_path}"
    logger.info(msg)


if __name__ == "__main__":
    generate_sdk_interface(
        source="lyra_app/db/client.py",
        dest="packages/lyra_sdk/src/lyra/sdk/db.py",
    )
