import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_sdk_interface(source: str, dest: str) -> None:
    source_path = Path(source)
    dest_path = Path(dest)

    # 1. Read the source code
    with source_path.open() as f:
        source_code = f.read()

    # 2. Parse it into an Abstract Syntax Tree
    tree = ast.parse(source_code)

    # 3. Modify the tree
    for node in tree.body:
        # Find our concrete class
        if isinstance(node, ast.ClassDef) and node.name == "MyDBClientImplicit":
            # Rename it for the SDK
            node.name = "MyDBClient"

            # Make it inherit from ABC
            node.bases = [ast.Name(id="ABC", ctx=ast.Load())]

            # Iterate through the methods inside the class
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    # 1. Add the @abstractmethod decorator
                    item.decorator_list.append(
                        ast.Name(id="abstractmethod", ctx=ast.Load())
                    )

                    # 2. Check for and preserve the docstring
                    if ast.get_docstring(item):
                        # The docstring is always the first node in the body
                        docstring_node = item.body[0]
                        item.body = [
                            docstring_node,
                            ast.Expr(value=ast.Constant(value=Ellipsis)),
                        ]
                    else:
                        # No docstring, just add the Ellipsis
                        item.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]

    # 4. Generate the new source code (requires Python 3.9+)
    new_source = "from abc import ABC, abstractmethod\n\n" + ast.unparse(tree)

    # 5. Write it to the SDK file
    with dest_path.open("w") as f:
        f.write(new_source)

    msg = f"Generated SDK interface from {source_path} to {dest_path}"
    logger.info(msg)


if __name__ == "__main__":
    generate_sdk_interface(
        source="lyra_app/db/client.py",
        dest="packages/lyra_sdk/src/lyra/sdk/db.py",
    )
