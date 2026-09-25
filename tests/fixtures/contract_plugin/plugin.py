from lyra.sdk import PluginDefinition

from .metrics import (
    bounded_features,
    capacity,
    feature_report,
    interval_width,
    reused_capacity,
    selection_size,
)


def create_plugin() -> PluginDefinition:
    return PluginDefinition(
        metrics=[
            capacity,
            reused_capacity,
            selection_size,
            bounded_features,
            feature_report,
            interval_width,
        ]
    )


def create_capacity_plugin() -> PluginDefinition:
    return PluginDefinition(metrics=[capacity])
