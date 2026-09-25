import pandas as pd


def calculate_capacity(
    feature_ids: list[str], *, units_per_feature: int = 10
) -> pd.DataFrame:
    return pd.DataFrame(
        {"capacity": [units_per_feature] * len(feature_ids)},
        index=feature_ids,
    )
