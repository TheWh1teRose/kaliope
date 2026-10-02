"""The model catalogue: every registered model and what it accepts.

The experiment pages build their model settings from this, so a control the
model rejects is disabled before a request is ever sent.
"""

from __future__ import annotations

from typing import get_args

from fastapi import APIRouter, Depends

from app.llm import registry as llm_registry
from app.models import User
from app.schemas.model_catalogue import ModelCatalogueOut, ModelOut, ProviderOut
from app.security import current_user

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=ModelCatalogueOut)
def get_models(_user: User = Depends(current_user)) -> ModelCatalogueOut:
    return ModelCatalogueOut(
        models=[ModelOut.model_validate(entry) for entry in llm_registry.catalogue()],
        providers=[ProviderOut(name=name) for name in get_args(llm_registry.ProviderName)],
    )
