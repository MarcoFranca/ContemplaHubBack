from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AzosPerfilCotacaoIn(BaseModel):
    data_nascimento: date
    sexo: Literal["m", "f"]
    altura_m: float = Field(gt=0.3, lt=3.0)
    peso_kg: float = Field(gt=1, lt=500)
    fumante: bool
    renda_mensal: float = Field(ge=0)
    profissao_id: str = Field(min_length=1)
    consentimento_confirmado: Literal[True]

    @field_validator("profissao_id")
    @classmethod
    def normalize_profissao_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("profissao_id é obrigatório")
        return cleaned

    def to_azos(self) -> dict[str, Any]:
        return {
            "birth_date": self.data_nascimento.strftime("%d/%m/%Y"),
            "gender": self.sexo,
            "height": self.altura_m,
            "weight": self.peso_kg,
            "is_smoker": self.fumante,
            "salary": self.renda_mensal,
            "profession_id": self.profissao_id,
        }


class AzosCoberturaSelecionadaIn(BaseModel):
    code: str = Field(min_length=1)
    capital: float = Field(gt=0)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("code é obrigatório")
        return cleaned


class AzosCoberturasIn(BaseModel):
    perfil: AzosPerfilCotacaoIn


class AzosCotacaoIn(BaseModel):
    perfil: AzosPerfilCotacaoIn
    coberturas: list[AzosCoberturaSelecionadaIn] = Field(min_length=1)


class AzosSyncIn(BaseModel):
    recurso: Literal["propostas", "apolices"]
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AzosCarteiraSyncIn(BaseModel):
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AzosPublicInterestIn(BaseModel):
    origem: str = Field(default="proposta_publica", max_length=80)
