"""Plugin definition for the minimal smoke-test example."""

from lyra.sdk import PluginDefinition

from smoke_plugin.metrics import run_cancel, run_file, run_table


def create_plugin() -> PluginDefinition:
    """Create the example plugin with its table, file, and cancellation metrics.

    Returns:
        The complete smoke-test plugin definition.
    """
    return PluginDefinition(metrics=[run_table, run_file, run_cancel])
