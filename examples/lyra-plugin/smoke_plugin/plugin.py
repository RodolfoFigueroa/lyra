from lyra.sdk import PluginDefinition

from smoke_plugin.metrics import run_cancel, run_file, run_table


def create_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[run_table, run_file, run_cancel])
