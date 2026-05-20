import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _extract_names(node: ast.AST | None) -> set[str]:
    """Finds all variable/type names referenced within an AST node.

    Args:
        node: The AST node to inspect, or None.

    Returns:
        A set of identifier strings found in the node.
    """
    if not node:
        return set()
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _make_method_abstract(item: ast.FunctionDef, required_names: set[str]) -> None:
    """Makes a public method abstract and harvests type names from its signature.

    Adds the ``@abstractmethod`` decorator, collects all type names used in
    argument annotations and the return type, and replaces the method body
    with an ellipsis (preserving any existing docstring).

    Args:
        item: The function definition node to transform in-place.
        required_names: Mutable set that is updated with every type name
            found in the method signature.
    """
    item.decorator_list.append(ast.Name(id="abstractmethod", ctx=ast.Load()))

    for arg in item.args.args + item.args.kwonlyargs:
        required_names.update(_extract_names(arg.annotation))
    for default in item.args.defaults + item.args.kw_defaults:
        required_names.update(_extract_names(default))
    required_names.update(_extract_names(item.returns))

    # Preserve docstring, strip execution logic
    if ast.get_docstring(item):
        item.body = [item.body[0], ast.Expr(value=ast.Constant(value=Ellipsis))]
    else:
        item.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]


def _transform_class(tree: ast.Module) -> tuple[ast.ClassDef | None, set[str]]:
    """Finds ``LyraDBImplicit``, renames it to ``LyraDB``, and abstracts public methods.

    Args:
        tree: The parsed AST module to search and modify in-place.

    Returns:
        A tuple of ``(class_node, required_names)`` where ``class_node`` is the
        transformed ``ClassDef`` node (or ``None`` if the class was not found)
        and ``required_names`` is the set of type names referenced in the
        public method signatures.
    """
    required_names: set[str] = set()
    abstract_class_node = None

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "LyraDBImplicit":
            abstract_class_node = node
            node.name = "LyraDB"
            node.bases = [ast.Name(id="ABC", ctx=ast.Load())]

            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    _make_method_abstract(item, required_names)

    return abstract_class_node, required_names


def _filter_import(
    node: ast.Import | ast.ImportFrom, required_names: set[str]
) -> ast.Import | ast.ImportFrom | None:
    """Returns the import with only required aliases, or ``None`` if none are needed.

    Args:
        node: An import statement node whose aliases will be filtered in-place.
        required_names: The set of names that must be retained.

    Returns:
        The same node with its ``names`` list narrowed to required aliases, or
        ``None`` if no alias in the statement is required.
    """
    new_aliases = [
        alias
        for alias in node.names
        if (alias.asname or alias.name.split(".")[0]) in required_names
    ]
    if new_aliases:
        node.names = new_aliases
        return node
    return None


def _build_module_body(
    tree: ast.Module,
    abstract_class_node: ast.ClassDef,
    required_names: set[str],
) -> list[ast.stmt]:
    """Rebuilds the module body with only required imports and the abstract class.

    Args:
        tree: The parsed AST module whose body is used as the source of nodes.
        abstract_class_node: The transformed class node that must be included.
        required_names: Type names used to decide which import aliases to keep.

    Returns:
        A list of AST statements containing the filtered imports followed by
        the abstract class definition.
    """
    new_body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            filtered = _filter_import(node, required_names)
            if filtered:
                new_body.append(filtered)
        elif node is abstract_class_node:
            new_body.append(node)
    return new_body


def generate_sdk_interface(source: str, dest: str) -> None:
    """Generates an abstract SDK interface from a concrete implementation file.

    Reads the source file, locates the ``LyraDBImplicit`` class, converts it
    into an abstract base class named ``LyraDB``, strips all unrelated code,
    and writes the result to the destination file.

    Args:
        source: Path to the source Python file containing ``LyraDBImplicit``.
        dest: Path where the generated SDK interface file will be written.

    Raises:
        ValueError: If ``LyraDBImplicit`` is not found in the source file.
    """
    source_path = Path(source)
    dest_path = Path(dest)

    with source_path.open() as f:
        source_code = f.read()

    tree = ast.parse(source_code)

    abstract_class_node, required_names = _transform_class(tree)
    if not abstract_class_node:
        msg = f"Class 'LyraDBImplicit' not found in {source_path}"
        raise ValueError(msg)

    tree.body = _build_module_body(tree, abstract_class_node, required_names)

    header = (
        f"# This file is automatically generated from {source_path}.\n"
        "# Do not edit it directly, make changes in the source file instead.\n"
        "\n"
    )
    new_source = header + "from abc import ABC, abstractmethod\n\n" + ast.unparse(tree)

    with dest_path.open("w") as f:
        f.write(new_source)

    msg = f"Generated SDK interface from {source_path} to {dest_path}"
    logger.info(msg)


if __name__ == "__main__":
    generate_sdk_interface(
        source="lyra_app/db/client.py",
        dest="packages/lyra_sdk/src/lyra/sdk/db.py",
    )
