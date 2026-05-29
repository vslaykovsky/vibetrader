from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ParamsHyperopt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_space: dict[str, HyperoptSearchSpec]
    n_trials: int = 30
    timeout_seconds: int = 21600
    direction: Literal["maximize", "minimize"] = "maximize"
    objective_metric: HyperoptObjectiveMetric = "total_return"
    seed: int | None = None
    trial_timeout_seconds: int | None = 1800


class ParamsHyperoptOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_space: dict[str, HyperoptSearchSpec] | None = Field(
        default=None,
        description="Top-level params.json tunables to sample; keys must already exist in params.json.",
    )
    n_trials: int | None = None
    timeout_seconds: int | None = None
    direction: Literal["maximize", "minimize"] | None = None
    objective_metric: HyperoptObjectiveMetric | None = Field(
        default=None,
        description="Generated metrics.json key to optimize.",
    )
    seed: int | None = None
    trial_timeout_seconds: int | None = None


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
            "trial budgets, timeouts, direction, seed, and objective metric. Use parameters_json instead "
            "for ticker, dates, deposit, provider, scale, simulation_scale, metadata, run_mode, or other "
            "base simulation inputs. For lower drawdown, maximize max_drawdown because drawdowns are "
            "stored as negative percentages."
        ),
    )
