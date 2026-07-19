from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


HyperoptObjectiveMetric = Literal[
    "total_return",
    "sharpe_ratio",
    "max_drawdown",
    "win_rate",
    "num_trades",
    "final_equity",
]


class HyperoptIntSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["int"] = "int"
    low: int
    high: int


class HyperoptFloatSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["float"] = "float"
    low: float
    high: float


class HyperoptCategoricalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["categorical"] = "categorical"
    choices: list[Any]


HyperoptSearchSpec = Annotated[
    HyperoptIntSpec | HyperoptFloatSpec | HyperoptCategoricalSpec,
    Field(discriminator="type"),
]


class WalkForwardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    train_window_days: int = Field(gt=0)
    test_window_days: int = Field(gt=0)
    oos_total_days: int = Field(gt=0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_step_days(cls, value):
        if not isinstance(value, dict) or "step_days" not in value:
            return value
        migrated = dict(value)
        step_days = migrated.pop("step_days")
        if step_days != migrated.get("test_window_days"):
            raise ValueError("legacy step_days must equal test_window_days")
        return migrated


class ParamsHyperopt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_space: dict[str, HyperoptSearchSpec]
    included_parameters: list[str] | None = None
    excluded_parameters: list[str] | None = None
    n_trials: int = 30
    timeout_seconds: int = 21600
    direction: Literal["maximize", "minimize"] = "maximize"
    objective_metric: HyperoptObjectiveMetric = "total_return"
    seed: int | None = None
    trial_timeout_seconds: int | None = 1800
    mode: Literal["single", "walk_forward"] = "single"
    walk_forward: WalkForwardConfig | None = None


class ParamsHyperoptOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_space: dict[str, HyperoptSearchSpec] | None = Field(
        default=None,
        description="Top-level params.json tunables to sample; keys must already exist in params.json and use stable semantic names.",
    )
    included_parameters: list[str] | None = Field(
        default=None,
        description="Optional whitelist of search_space keys to optimize.",
    )
    excluded_parameters: list[str] | None = Field(
        default=None,
        description="Optional blacklist of search_space keys to skip.",
    )
    n_trials: int | None = None
    timeout_seconds: int | None = Field(
        default=None,
        description=(
            "Wall-clock budget for one hyperopt study. In walk_forward mode this is applied separately to each fold. "
            "Do not use very low values such as 180 seconds for 40-trial walk-forward folds unless the user explicitly "
            "requested it or previous timing proves it is enough; prefer at least 600 seconds for 40 trials and "
            "900-1800 seconds for slower strategies."
        ),
    )
    direction: Literal["maximize", "minimize"] | None = None
    objective_metric: HyperoptObjectiveMetric | None = Field(
        default=None,
        description="Generated metrics.json key to optimize.",
    )
    seed: int | None = None
    trial_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "Hard timeout for one simulator trial, mainly to stop a hanging or non-responsive strategy. "
            "This is not the hyperopt study budget."
        ),
    )
    mode: Literal["single", "walk_forward"] | None = Field(
        default=None,
        description="Use 'walk_forward' to optimize on rolling train windows and stitch out-of-sample test windows.",
    )
    walk_forward: WalkForwardConfig | None = Field(
        default=None,
        description=(
            "Walk-forward study configuration. Required when mode is 'walk_forward'. "
            "The runner restores params.json after completion and writes stitched OOS outputs."
        ),
    )


class RunHyperoptToolParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameters_json: str | None = Field(
        default=None,
        description="Optional valid JSON merged into params.json before hyperopt, using run_backtest merge rules.",
    )
    parameters_hyperopt_json: ParamsHyperoptOverrides | None = Field(
        default=None,
        description=(
            "Optional structured object merged into params-hyperopt.json. Use for search space, ranges, "
            "included/excluded parameter filters, trial budgets, timeouts, direction, seed, and objective metric. "
            "Use parameters_json instead for ticker, dates, deposit, provider, scale, simulation_scale, metadata, "
            "run_mode, or other base simulation inputs. For lower drawdown, maximize max_drawdown because "
            "drawdowns are stored as negative percentages."
        ),
    )
