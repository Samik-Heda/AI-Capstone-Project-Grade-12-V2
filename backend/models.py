"""Pydantic request models (spec §11). Validation errors surface as HTTP 422 via main.py."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import scoring


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requires_industrial_land: bool = True
    min_norm: dict[str, Any] = Field(default_factory=dict)
    max_port_km: Any = None

    @model_validator(mode="after")
    def _check(self):
        scoring.normalize_constraints(self.model_dump())
        return self


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: dict[str, Any]
    constraints: Constraints | None = None

    @field_validator("weights")
    @classmethod
    def _weights(cls, v: dict[str, Any]) -> dict[str, Any]:
        scoring.normalize_weights(v)  # raises on negative / NaN / unknown / all-zero
        return v


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    text: str = Field(min_length=1, max_length=2000)


class ProfileSelect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str


class WeightsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overrides: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] | None = None

    @field_validator("overrides")
    @classmethod
    def _overrides(cls, v: dict[str, Any]) -> dict[str, Any]:
        keys = set(scoring.dataset().keys)
        for k, w in v.items():
            if k not in keys:
                raise ValueError(f"unknown field: {k}")
            if w is None or isinstance(w, bool) or not isinstance(w, (int, float)) or not (0 <= w <= 1):
                raise ValueError(f"override for {k} must be a number in [0, 1]")
        return v

    @field_validator("constraints")
    @classmethod
    def _constraints(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            scoring.normalize_constraints(v)
        return v


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    district_id: int


class SaveProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class BriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    turn_id: str


class NarrateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    turn_id: str
    quality: bool = False  # use the slower quality model (UI setting, "slow on this laptop")


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    weights: dict[str, Any]
    constraints: Constraints | None = None
    rationale: str = ""
    description: str | None = None
    base_profile_id: str | None = None
    provenance: dict[str, Any] | None = None

    @field_validator("weights")
    @classmethod
    def _weights(cls, v: dict[str, Any]) -> dict[str, Any]:
        scoring.normalize_weights(v)
        return v


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    weights: dict[str, Any] | None = None
    constraints: Constraints | None = None
    rationale: str | None = None
    description: str | None = None
    base_profile_id: str | None = None
    provenance: dict[str, Any] | None = None

    @field_validator("weights")
    @classmethod
    def _weights(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            scoring.normalize_weights(v)
        return v


class ProfileDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500)
    base_preset_id: str | None = None


class ClustersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    k: int | None = Field(default=None, ge=3, le=6)
