from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Assessment(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    surge_score: int = Field(ge=0, le=100)
    stage: Literal["early", "developing", "already_moved", "noise", "insufficient_data"]
    catalyst_strength: int = Field(ge=0, le=10)
    manipulation_risk: int = Field(ge=0, le=10)
    confidence: float = Field(ge=0, le=1)
    thesis: str = Field(max_length=350)
    risks: str = Field(max_length=350)
