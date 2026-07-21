"""Generate Starlight API reference pages from Python source metadata."""

from __future__ import annotations

import ast
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import griffe
from griffe import DocstringSectionKind

LOGGER = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = (
    ROOT / "docs" / "src" / "content" / "docs" / "reference" / "generated" / "python"
)
SEARCH_PATHS = (
    ROOT / "packages" / "lyra_sdk" / "src",
    ROOT / "packages" / "lyra_api" / "src",
    ROOT / "packages" / "lyra_utils" / "src",
)


@dataclass(frozen=True)
class SymbolRef:
    module: str
    name: str


@dataclass(frozen=True)
class PageSpec:
    slug: str
    title: str
    description: str
    sidebar_label: str
    order: int
    intro: str
    symbols: tuple[SymbolRef, ...]


def sym(module: str, name: str) -> SymbolRef:
    return SymbolRef(module=module, name=name)


PAGE_SPECS = (
    PageSpec(
        slug="lyra-sdk-runtime",
        title="lyra-sdk Runtime Contracts",
        description="Generated reference for Lyra runner runtime contracts.",
        sidebar_label="lyra-sdk Runtime",
        order=2,
        intro=(
            "`lyra-sdk` runtime contracts describe the objects runner plugins "
            "receive from the worker runtime."
        ),
        symbols=(
            sym("lyra.sdk.context", "RunContext"),
            sym("lyra.sdk.db", "LyraDB"),
        ),
    ),
    PageSpec(
        slug="lyra-sdk-job-models",
        title="lyra-sdk Job Models",
        description="Generated reference for Lyra job lifecycle models.",
        sidebar_label="lyra-sdk Jobs",
        order=3,
        intro="Job models define request, status, event, and result contracts.",
        symbols=(
            sym("lyra.sdk.models.job", "JobLifecycleStatus"),
            sym("lyra.sdk.models.job", "TerminalJobStatus"),
            sym("lyra.sdk.models.job", "JobEnvelope"),
            sym("lyra.sdk.models.job", "JobEvent"),
            sym("lyra.sdk.models.job", "JobEventRecord"),
            sym("lyra.sdk.models.job", "JobLifecycleEvent"),
            sym("lyra.sdk.models.job", "JobProgressEvent"),
            sym("lyra.sdk.models.job", "JobMessageEvent"),
            sym("lyra.sdk.models.job", "TableJobResult"),
            sym("lyra.sdk.models.job", "FileJobResult"),
            sym("lyra.sdk.models.job", "FailedJobResult"),
            sym("lyra.sdk.models.job", "CancelledJobResult"),
            sym("lyra.sdk.models.job", "TerminalJobResult"),
            sym("lyra.sdk.models.job", "parse_job_result"),
            sym("lyra.sdk.models.job", "JobCreateRequest"),
            sym("lyra.sdk.models.job", "JobLinks"),
            sym("lyra.sdk.models.job", "JobCreateResponse"),
            sym("lyra.sdk.models.job", "JobStatusInfo"),
        ),
    ),
    PageSpec(
        slug="lyra-sdk-plugin-models",
        title="lyra-sdk Plugin Models",
        description="Generated reference for Lyra plugin manifest models.",
        sidebar_label="lyra-sdk Plugins",
        order=4,
        intro=(
            "Plugin APIs define typed metric functions, generate schema v4 "
            "manifests, and compile them into Lyra runtime metadata."
        ),
        symbols=(
            sym("lyra.sdk.plugin", "PluginDefinition"),
            sym("lyra.sdk.plugin", "metric"),
            sym("lyra.sdk.plugin", "MetricDescription"),
            sym("lyra.sdk.plugin", "PluginDefinitionError"),
            sym("lyra.sdk.plugin", "Input"),
            sym("lyra.sdk.plugin", "BatchInput"),
            sym("lyra.sdk.plugin", "BatchItem"),
            sym("lyra.sdk.models.plugin_v4", "PluginInfoV4"),
            sym("lyra.sdk.models.plugin_v4", "PluginOwnedInputMetadataV4"),
            sym("lyra.sdk.models.plugin_v4", "SpatialInputKindV4"),
            sym("lyra.sdk.models.plugin_v4", "LocationInputV4"),
            sym("lyra.sdk.models.plugin_v4", "BoundsInputV4"),
            sym("lyra.sdk.models.plugin_v4", "StringInputV4"),
            sym("lyra.sdk.models.plugin_v4", "NumberInputV4"),
            sym("lyra.sdk.models.plugin_v4", "IntegerInputV4"),
            sym("lyra.sdk.models.plugin_v4", "BooleanInputV4"),
            sym("lyra.sdk.models.plugin_v4", "EnumInputV4"),
            sym("lyra.sdk.models.plugin_v4", "JsonSchemaInputV4"),
            sym("lyra.sdk.models.plugin_v4", "PluginOwnedInputSpecV4"),
            sym("lyra.sdk.models.plugin_v4", "BatchInputV4"),
            sym("lyra.sdk.models.plugin_v4", "InputSpecV4"),
            sym("lyra.sdk.models.plugin_v4", "OutputColumnTypeV4"),
            sym("lyra.sdk.models.plugin_v4", "TableOutputColumnV4"),
            sym("lyra.sdk.models.plugin_v4", "BatchedTableOutputColumnV4"),
            sym("lyra.sdk.models.plugin_v4", "TableOutputV4"),
            sym("lyra.sdk.models.plugin_v4", "FileOutputV4"),
            sym("lyra.sdk.models.plugin_v4", "OutputSpecV4"),
            sym("lyra.sdk.models.plugin_v4", "MetricManifestV4"),
            sym("lyra.sdk.models.plugin_v4", "PluginManifestV4"),
            sym("lyra.sdk.models.plugin_v4", "CompiledMetricManifestV4"),
            sym("lyra.sdk.models.plugin_v4", "CompiledPluginManifestV4"),
            sym("lyra.sdk.models.plugin_v4", "compile_plugin_manifest"),
        ),
    ),
    PageSpec(
        slug="lyra-sdk-catalog-models",
        title="lyra-sdk Catalog Models",
        description="Generated reference for metric catalog and data type models.",
        sidebar_label="lyra-sdk Catalog",
        order=5,
        intro="Catalog models describe metric discovery and spatial wrapper schemas.",
        symbols=(
            sym("lyra.sdk.models.data_types", "DataTypeSchemaInfo"),
            sym("lyra.sdk.models.data_types", "DataTypesResponse"),
            sym("lyra.sdk.models.metric", "MetricInfoV4"),
        ),
    ),
    PageSpec(
        slug="lyra-sdk-geometry",
        title="lyra-sdk Geometry Models",
        description="Generated reference for Lyra GeoJSON and spatial input types.",
        sidebar_label="lyra-sdk Geometry",
        order=6,
        intro="Geometry models represent resolved spatial inputs passed to plugins.",
        symbols=(
            sym("lyra.sdk.models.geometry", "CRSProperties"),
            sym("lyra.sdk.models.geometry", "CRS"),
            sym("lyra.sdk.models.geometry", "PointGeometry"),
            sym("lyra.sdk.models.geometry", "PolygonGeometry"),
            sym("lyra.sdk.models.geometry", "MultiPolygonGeometry"),
            sym("lyra.sdk.models.geometry", "Feature"),
            sym("lyra.sdk.models.geometry", "FeatureNoMultiPolygon"),
            sym("lyra.sdk.models.geometry", "GeoJSON"),
            sym("lyra.sdk.models.geometry", "SingleGeoJSON"),
            sym("lyra.sdk.types", "ExplicitLocationAPI"),
            sym("lyra.sdk.types", "ExplicitBoundsAPI"),
        ),
    ),
    PageSpec(
        slug="lyra-api",
        title="lyra-api Client Reference",
        description="Generated reference for the sync and async Lyra API clients.",
        sidebar_label="lyra-api",
        order=7,
        intro="`lyra-api` exposes sync and async clients for Lyra's HTTP job API.",
        symbols=(
            sym("lyra.api", "LyraAPIClient"),
            sym("lyra.api", "AsyncLyraAPIClient"),
            sym("lyra.api", "JobHandle"),
            sym("lyra.api", "AsyncJobHandle"),
            sym("lyra.api", "LyraAPIError"),
            sym("lyra.api", "DownloadError"),
            sym("lyra.api", "JobEventStreamError"),
            sym("lyra.api", "JobEventCursorGapError"),
            sym("lyra.api", "JobWaitTimeoutError"),
        ),
    ),
    PageSpec(
        slug="lyra-utils",
        title="lyra-utils Helper Reference",
        description="Generated reference for shared Lyra plugin utility helpers.",
        sidebar_label="lyra-utils",
        order=8,
        intro="`lyra-utils` contains optional helpers for plugin implementations.",
        symbols=(
            sym("lyra.utils", "get_date_range"),
            sym("lyra.utils", "get_season_date_range"),
            sym("lyra.utils", "convert_geojson_to_gdf"),
            sym("lyra.utils", "convert_polygon_to_ee"),
            sym("lyra.utils", "convert_gdf_to_ee"),
            sym("lyra.utils", "get_reducer_name"),
            sym("lyra.utils", "compute_gdf"),
            sym("lyra.utils", "chunk_gdf"),
            sym("lyra.utils", "reduce_ee_image_over_gdf_factory"),
        ),
    ),
)

_MODULE_CACHE: dict[str, griffe.Module] = {}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate_api_docs()
    LOGGER.info("Generated API reference pages in %s", OUTPUT_DIR.relative_to(ROOT))


def generate_api_docs() -> None:
    ensure_safe_output_dir()
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    write_page(
        OUTPUT_DIR / "index.md",
        render_landing_page(),
    )
    for page in PAGE_SPECS:
        write_page(
            OUTPUT_DIR / f"{page.slug}.md",
            render_reference_page(page),
        )


def ensure_safe_output_dir() -> None:
    docs_root = ROOT / "docs" / "src" / "content" / "docs"
    if docs_root not in OUTPUT_DIR.parents:
        msg = f"refusing to generate outside the docs content tree: {OUTPUT_DIR}"
        raise RuntimeError(msg)


def write_page(path: Path, content: str) -> None:
    path.write_text(f"{content.rstrip()}\n", encoding="utf-8")


def render_landing_page() -> str:
    lines = [
        frontmatter(
            title="API Reference",
            description=(
                "Generated Python API reference for lyra-sdk, lyra-api, and lyra-utils."
            ),
            sidebar_label="Overview",
            order=1,
        ),
        "These pages are generated from Python source signatures, type hints, "
        "docstrings, and Pydantic field metadata.",
        "",
        "Use the hand-written package guides for workflows and examples, and "
        "use this reference for exact symbols, fields, and method signatures.",
        "",
        "| Page | Contents |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| [{page.sidebar_label}](./{page.slug}/) | {page.description} |"
        for page in PAGE_SPECS
    )
    return "\n".join(lines)


def render_reference_page(page: PageSpec) -> str:
    lines = [
        frontmatter(
            title=page.title,
            description=page.description,
            sidebar_label=page.sidebar_label,
            order=page.order,
        ),
        page.intro,
        "",
        "> Generated from Python source. Edit the package docstrings, type hints, "
        "or Pydantic field metadata, then rerun the docs build.",
        "",
    ]

    for symbol in page.symbols:
        obj = resolve_symbol(symbol)
        lines.extend(render_symbol(obj))
        lines.append("")

    return "\n".join(lines)


def frontmatter(
    *,
    title: str,
    description: str,
    sidebar_label: str,
    order: int,
) -> str:
    return "\n".join(
        (
            "---",
            f"title: {json.dumps(title)}",
            f"description: {json.dumps(description)}",
            "editUrl: false",
            "sidebar:",
            f"  label: {json.dumps(sidebar_label)}",
            f"  order: {order}",
            "---",
            "",
        ),
    )


def resolve_symbol(symbol: SymbolRef) -> griffe.Object:
    module = load_module(symbol.module)
    member = module.members.get(symbol.name)
    if member is None:
        msg = f"configured API symbol not found: {symbol.module}.{symbol.name}"
        raise RuntimeError(msg)
    return resolve_alias(member)


def load_module(module_name: str) -> griffe.Module:
    if module_name not in _MODULE_CACHE:
        loaded = griffe.load(
            module_name,
            submodules=True,
            search_paths=SEARCH_PATHS,
            docstring_parser="google",
            docstring_options={
                "returns_multiple_items": False,
                "warn_unknown_params": False,
                "warnings": False,
            },
            allow_inspection=False,
            resolve_aliases=True,
        )
        if not isinstance(loaded, griffe.Module):
            msg = f"configured API module did not resolve to a module: {module_name}"
            raise RuntimeError(msg)
        _MODULE_CACHE[module_name] = loaded
    return _MODULE_CACHE[module_name]


def resolve_alias(obj: griffe.Object | griffe.Alias) -> griffe.Object:
    return obj.final_target if isinstance(obj, griffe.Alias) else obj


def render_symbol(obj: griffe.Object) -> list[str]:
    if isinstance(obj, griffe.Class):
        return render_class(obj)
    if isinstance(obj, griffe.Function):
        return render_function(obj=obj, level=2)
    if isinstance(obj, griffe.Attribute):
        return render_attribute(obj=obj, level=2)

    return [
        f"## `{obj.name}`",
        "",
        f"_Source: `{obj.path}`._",
    ]


def render_class(obj: griffe.Class) -> list[str]:
    bases = f"({', '.join(str(base) for base in obj.bases)})" if obj.bases else ""
    lines = [
        f"## `{obj.name}`",
        "",
        f"_Source: `{obj.path}`._",
        "",
        "```python",
        f"class {obj.name}{bases}: ...",
        "```",
        "",
    ]

    docstring = render_docstring_text(obj.docstring)
    if docstring:
        lines.extend([docstring, ""])

    attributes = public_attributes(obj)
    if attributes:
        lines.extend(render_attribute_table(attributes))
        lines.append("")

    methods = public_methods(obj)
    if methods:
        lines.extend(["### Methods", ""])
        for method in methods:
            lines.extend(render_function(obj=method, level=4))
            lines.append("")

    return lines


def render_function(*, obj: griffe.Function, level: int) -> list[str]:
    prefix = "#" * level
    lines = [
        f"{prefix} `{obj.name}`",
        "",
        f"_Source: `{obj.path}`._",
        "",
        "```python",
        format_function_signature(obj),
        "```",
        "",
    ]

    docstring = render_docstring_text(obj.docstring)
    if docstring:
        lines.extend([docstring, ""])

    parameter_rows = parameter_table_rows(obj)
    if parameter_rows:
        lines.extend(["**Parameters**", "", *parameter_rows, ""])

    return_rows = return_table_rows(obj)
    if return_rows:
        lines.extend(["**Returns**", "", *return_rows, ""])

    raises_rows = raises_table_rows(obj)
    if raises_rows:
        lines.extend(["**Raises**", "", *raises_rows, ""])

    return lines


def render_attribute(*, obj: griffe.Attribute, level: int) -> list[str]:
    prefix = "#" * level
    lines = [
        f"{prefix} `{obj.name}`",
        "",
        f"_Source: `{obj.path}`._",
        "",
        "```python",
        format_attribute_assignment(obj),
        "```",
        "",
    ]
    docstring = render_docstring_text(obj.docstring)
    if docstring:
        lines.extend([docstring, ""])
    return lines


def public_attributes(obj: griffe.Class) -> list[griffe.Attribute]:
    return [
        member
        for name, member in obj.members.items()
        if isinstance(member, griffe.Attribute) and not name.startswith("_")
    ]


def public_methods(obj: griffe.Class) -> list[griffe.Function]:
    return [
        member
        for name, member in obj.members.items()
        if isinstance(member, griffe.Function)
        and not name.startswith("_")
        and not is_pydantic_validator(member)
    ]


def is_pydantic_validator(obj: griffe.Function) -> bool:
    validators = ("field_validator(", "model_validator(")
    return any(
        any(marker in str(decorator.value) for marker in validators)
        for decorator in obj.decorators
    )


def render_attribute_table(attributes: list[griffe.Attribute]) -> list[str]:
    lines = [
        "**Attributes**",
        "",
        "| Name | Type | Default / constraints | Description |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        (
            "| "
            f"`{attribute.name}` | "
            f"{code_cell(attribute_annotation(attribute))} | "
            f"{attribute_default(attribute)} | "
            f"{table_cell(attribute_description(attribute))} |"
        )
        for attribute in attributes
    )
    return lines


def parameter_table_rows(obj: griffe.Function) -> list[str]:
    descriptions = docstring_parameter_descriptions(obj.docstring)
    rows = ["| Name | Type | Default | Description |", "| --- | --- | --- | --- |"]
    for parameter in obj.parameters:
        if parameter.name in {"self", "cls"}:
            continue
        rows.append(
            "| "
            f"`{parameter.name}` | "
            f"{code_cell(stringify(parameter.annotation))} | "
            f"{parameter_default(parameter)} | "
            f"{table_cell(descriptions.get(parameter.name, ''))} |",
        )
    return rows if len(rows) > 2 else []


def return_table_rows(obj: griffe.Function) -> list[str]:
    returns = stringify(obj.returns)
    description = docstring_return_description(obj.docstring)
    if not returns and not description:
        return []
    return [
        "| Type | Description |",
        "| --- | --- |",
        f"| {code_cell(returns or 'None')} | {table_cell(description)} |",
    ]


def raises_table_rows(obj: griffe.Function) -> list[str]:
    rows = ["| Type | Description |", "| --- | --- |"]
    for annotation, description in docstring_raises(obj.docstring):
        rows.append(f"| {code_cell(annotation)} | {table_cell(description)} |")
    return rows if len(rows) > 2 else []


def format_function_signature(obj: griffe.Function) -> str:
    qualifier = "async def" if "async" in obj.labels else "def"
    return f"{qualifier} {obj.signature()}"


def format_attribute_assignment(obj: griffe.Attribute) -> str:
    annotation = attribute_annotation(obj)
    value = stringify(obj.value)
    if annotation and value:
        return f"{obj.name}: {annotation} = {value}"
    if annotation:
        return f"{obj.name}: {annotation}"
    if value:
        return f"{obj.name} = {value}"
    return obj.name


def attribute_annotation(obj: griffe.Attribute) -> str:
    return stringify(obj.annotation)


def attribute_default(obj: griffe.Attribute) -> str:
    value = obj.value
    if value is None:
        return "Required"

    field_args = field_call_arguments(value)
    if field_args is not None:
        visible_args = [
            argument
            for argument in field_args
            if not argument.startswith("description=")
        ]
        if visible_args:
            return code_cell(f"Field({', '.join(visible_args)})")
        return "Required"

    return code_cell(stringify(value))


def attribute_description(obj: griffe.Attribute) -> str:
    docstring = render_docstring_text(obj.docstring)
    if docstring:
        return docstring
    return field_description(obj.value)


def field_call_arguments(value: str | griffe.Expr | None) -> list[str] | None:
    if not isinstance(value, griffe.ExprCall) or stringify(value.function) != "Field":
        return None
    return [
        (
            f"{argument.name}={stringify(argument.value)}"
            if isinstance(argument, griffe.ExprKeyword)
            else stringify(argument)
        )
        for argument in value.arguments
    ]


def field_description(value: str | griffe.Expr | None) -> str:
    if not isinstance(value, griffe.ExprCall) or stringify(value.function) != "Field":
        return ""

    for argument in value.arguments:
        if not isinstance(argument, griffe.ExprKeyword):
            continue
        if argument.name != "description":
            continue
        raw_value = stringify(argument.value)
        try:
            return str(ast.literal_eval(raw_value))
        except (SyntaxError, ValueError):
            return raw_value.strip("\"'")

    return ""


def render_docstring_text(docstring: griffe.Docstring | None) -> str:
    if docstring is None:
        return ""
    sections = docstring.parse()
    return "\n\n".join(
        str(section.value).strip()
        for section in sections
        if section.kind == DocstringSectionKind.text and str(section.value).strip()
    )


def docstring_parameter_descriptions(
    docstring: griffe.Docstring | None,
) -> dict[str, str]:
    if docstring is None:
        return {}

    descriptions: dict[str, str] = {}
    for section in docstring.parse():
        if section.kind != DocstringSectionKind.parameters:
            continue
        for parameter in section.value:
            descriptions[parameter.name] = parameter.description
    return descriptions


def docstring_return_description(docstring: griffe.Docstring | None) -> str:
    if docstring is None:
        return ""

    descriptions: list[str] = []
    for section in docstring.parse():
        if section.kind != DocstringSectionKind.returns:
            continue
        descriptions.extend(
            item.description for item in section.value if item.description
        )
    return " ".join(descriptions)


def docstring_raises(docstring: griffe.Docstring | None) -> list[tuple[str, str]]:
    if docstring is None:
        return []

    rows: list[tuple[str, str]] = []
    for section in docstring.parse():
        if section.kind != DocstringSectionKind.raises:
            continue
        rows.extend(
            (stringify(item.annotation), item.description)
            for item in section.value
            if item.annotation or item.description
        )
    return rows


def parameter_default(parameter: griffe.Parameter) -> str:
    if parameter.default is None:
        return "Required"
    return code_cell(stringify(parameter.default))


def stringify(value: str | griffe.Expr | None) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ")


def code_cell(value: str) -> str:
    if not value:
        return ""
    return f"`{table_cell(value)}`"


def table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    main()
