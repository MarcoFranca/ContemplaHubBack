from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal, Optional

from fastapi import HTTPException, status
from supabase import Client

from app.routers.carteira import ensure_carteira_cliente
from app.schemas.carteira_import import (
    CarteiraImportConfirmResponse,
    CarteiraImportPlannedEntities,
    CarteiraImportPreviewResponse,
    CarteiraImportPreviewSummary,
    CarteiraImportRowPreview,
    CarteiraImportRowResult,
    ImportProduto,
    ParsedImportRow,
)
from app.security.auth import CurrentProfile
from app.services.cota_finance_service import normalize_cota_financial_payload
from app.services.lead_address_service import apply_lead_address_rules


HEADER_ALIASES = {
    "sistema": "sistema",
    "lance feito": "lance_feito",
    "contemplada": "contemplada",
    "optin": "optin",
    "cliente": "cliente",
    "tipo de lance": "tipo_lance",
    "empresa": "empresa",
    "valor da cota": "valor_cota",
    "grupo": "grupo",
    "cota": "cota",
    "prazo": "prazo",
    "forma de pagamento": "forma_pagamento",
    "indice de corecao": "indice_correcao",
    "indice de correcao": "indice_correcao",
    "furo": "furo",
    "objetivo": "objetivo",
    "estrategia / obs": "estrategia_obs",
    "estrategia/obs": "estrategia_obs",
    "parcela reduzida": "parcela_reduzida",
    "data ultimo lance": "data_ultimo_lance",
    "detalhes lance": "detalhes_lance",
    "aporte": "aporte",
    "valor final da carta": "valor_final_carta",
    "valor da parcela": "valor_parcela",
}

PERCENT_REGEX = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
MONEY_REGEX = re.compile(r"r\$\s*([\d\.\,]+)", re.IGNORECASE)
SEPARATOR_ONLY_REGEX = re.compile(r"^[\-\_=|/\\\.\*]+$")


@dataclass
class PreviewContext:
    parsed: ParsedImportRow
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    planned: CarteiraImportPlannedEntities = field(default_factory=CarteiraImportPlannedEntities)
    existing_lead: dict[str, Any] | None = None
    existing_administradora: dict[str, Any] | None = None
    existing_grupo: dict[str, Any] | None = None
    existing_cota: dict[str, Any] | None = None
    existing_contrato: dict[str, Any] | None = None
    existing_contemplacao: dict[str, Any] | None = None


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    collapsed = re.sub(r"\s+", " ", value.replace("\r", " ").replace("\n", " ")).strip()
    return collapsed


def _normalize_lookup(value: str | None) -> str:
    text = _normalize_text(value).lower()
    if not text:
        return ""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _normalize_header(value: str) -> str:
    return _normalize_lookup(value)


def _parse_bool(value: str | None) -> bool | None:
    normalized = _normalize_lookup(value)
    if not normalized:
        return None
    if normalized in {"true", "1", "sim", "yes", "y", "x"}:
        return True
    if normalized in {"false", "0", "nao", "no", "n"}:
        return False
    return None


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    raw = raw.replace("R$", "").replace("r$", "").replace(" ", "")
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parse_percent(value: str | None) -> Decimal | None:
    raw = _normalize_text(value)
    if not raw:
        return None
    cleaned = raw.replace("%", "").strip()
    parsed = _parse_decimal(cleaned)
    return parsed


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return int(Decimal(raw.replace(".", "").replace(",", ".")))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: str | None) -> str | None:
    raw = _normalize_text(value)
    if not raw:
        return None

    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_money_or_percent(text: str | None) -> tuple[Decimal | None, Decimal | None]:
    raw = _normalize_text(text)
    if not raw:
        return None, None

    money_match = MONEY_REGEX.search(raw)
    percent_match = PERCENT_REGEX.search(raw)

    money = _parse_decimal(money_match.group(1)) if money_match else None
    percent = _parse_percent(percent_match.group(1)) if percent_match else None
    return money, percent


def _split_tsv(raw_text: str) -> tuple[list[str], list[list[str]]]:
    lines = [line for line in raw_text.splitlines()]
    if not lines:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cole um conteúdo tabulado com cabeçalho.")

    header_line = next((line for line in lines if _normalize_text(line)), "")
    if not header_line:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nenhuma linha com dados foi encontrada.")

    headers = header_line.split("\t")
    header_index = lines.index(header_line)
    rows = [line.split("\t") for line in lines[header_index + 1 :]]
    return headers, rows


def _map_row(headers: list[str], values: list[str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for index, header in enumerate(headers):
        canonical = HEADER_ALIASES.get(_normalize_header(header))
        if not canonical:
            continue
        mapped[canonical] = values[index].strip() if index < len(values) else ""
    return mapped


def _infer_produto(raw: str | None, default: ImportProduto) -> ImportProduto:
    normalized = _normalize_lookup(raw)
    if any(token in normalized for token in ("auto", "veiculo", "carro", "moto")):
        return "auto"
    if any(token in normalized for token in ("imovel", "imobiliario", "casa", "apartamento")):
        return "imobiliario"
    return default


def _infer_lance_tipo(raw: str | None) -> Literal["livre", "fixo", "sorteio"] | None:
    normalized = _normalize_lookup(raw)
    if not normalized:
        return None
    if "fixo" in normalized:
        return "fixo"
    if "livre" in normalized:
        return "livre"
    if "sorteio" in normalized:
        return "sorteio"
    return None


def _build_import_notes(mapped: dict[str, str]) -> list[str]:
    notes: list[str] = []
    if mapped.get("optin"):
        notes.append(f"optin={mapped['optin']}")
    if mapped.get("sistema"):
        notes.append(f"sistema={mapped['sistema']}")
    if mapped.get("valor_final_carta"):
        notes.append(f"valor_final_carta={mapped['valor_final_carta']}")
    return notes


def _is_separator_like_row(mapped: dict[str, str]) -> bool:
    values = [_normalize_text(value) for value in mapped.values() if _normalize_text(value)]
    if not values:
        return False
    return all(SEPARATOR_ONLY_REGEX.fullmatch(value) for value in values)


def parse_import_rows(raw_text: str, *, produto_padrao: ImportProduto) -> list[ParsedImportRow]:
    headers, row_values = _split_tsv(raw_text)
    parsed_rows: list[ParsedImportRow] = []

    for index, values in enumerate(row_values, start=2):
        mapped = _map_row(headers, values)
        if not any(_normalize_text(value) for value in mapped.values()):
            parsed_rows.append(
                ParsedImportRow(
                    row_number=index,
                    produto=produto_padrao,
                    observacoes_importacao=[],
                )
            )
            continue

        if _is_separator_like_row(mapped):
            parsed_rows.append(
                ParsedImportRow(
                    row_number=index,
                    produto=produto_padrao,
                    observacoes_importacao=[],
                )
            )
            continue

        detalhes_lance = _normalize_text(mapped.get("detalhes_lance"))
        percentual_lance = _parse_percent(detalhes_lance)
        valor_lance, percentual_from_money = _extract_money_or_percent(detalhes_lance)
        if percentual_lance is None:
            percentual_lance = percentual_from_money

        parsed = ParsedImportRow(
            row_number=index,
            cliente_nome=_normalize_text(mapped.get("cliente")) or None,
            optin=_parse_bool(mapped.get("optin")),
            contemplada=bool(_parse_bool(mapped.get("contemplada"))),
            lance_feito=bool(_parse_bool(mapped.get("lance_feito"))),
            lance_tipo=_infer_lance_tipo(mapped.get("tipo_lance")),
            administradora_nome=_normalize_text(mapped.get("empresa")) or None,
            grupo_codigo=_normalize_text(mapped.get("grupo")) or None,
            numero_cota=_normalize_text(mapped.get("cota")) or None,
            produto=_infer_produto(mapped.get("sistema"), produto_padrao),
            valor_carta=_parse_decimal(mapped.get("valor_cota")),
            prazo=_parse_int(mapped.get("prazo")),
            forma_pagamento=_normalize_text(mapped.get("forma_pagamento")) or None,
            indice_correcao=_normalize_text(mapped.get("indice_correcao")) or None,
            furo_meses=_parse_int(mapped.get("furo")),
            objetivo=_normalize_text(mapped.get("objetivo")) or None,
            estrategia=_normalize_text(mapped.get("estrategia_obs")) or None,
            parcela_reduzida=_parse_bool(mapped.get("parcela_reduzida")),
            data_ultimo_lance=_parse_date(mapped.get("data_ultimo_lance")),
            detalhes_lance=detalhes_lance or None,
            aporte=_parse_decimal(mapped.get("aporte")),
            valor_final_carta=_parse_decimal(mapped.get("valor_final_carta")),
            valor_parcela=_parse_decimal(mapped.get("valor_parcela")),
            percentual_lance=percentual_lance,
            valor_lance=valor_lance,
            numero_contrato=None,
            data_adesao=None,
            data_assinatura=None,
            contemplacao_motivo="sorteio"
            if _infer_lance_tipo(mapped.get("tipo_lance")) == "sorteio"
            else "lance",
            observacoes_importacao=_build_import_notes(mapped),
        )
        parsed_rows.append(parsed)

    return parsed_rows


def _find_single_by_normalized_name(
    rows: Iterable[dict[str, Any]],
    *,
    field_name: str,
    expected: str,
) -> dict[str, Any] | None:
    expected_normalized = _normalize_lookup(expected)
    for row in rows:
        if _normalize_lookup(str(row.get(field_name) or "")) == expected_normalized:
            return row
    return None


def _query_lead_by_name(sb: Client, *, org_id: str, nome: str) -> dict[str, Any] | None:
    token = nome.split(" ")[0] if nome else ""
    if not token:
        return None
    response = (
        sb.table("leads")
        .select("id, org_id, nome")
        .eq("org_id", org_id)
        .ilike("nome", f"%{token}%")
        .limit(50)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return _find_single_by_normalized_name(rows, field_name="nome", expected=nome)


def _query_administradora_by_name(sb: Client, *, org_id: str, nome: str) -> dict[str, Any] | None:
    token = nome.split(" ")[0] if nome else ""
    if not token:
        return None
    response = (
        sb.table("administradoras")
        .select("id, org_id, nome")
        .or_(f"org_id.eq.{org_id},org_id.is.null")
        .ilike("nome", f"%{token}%")
        .limit(50)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return _find_single_by_normalized_name(rows, field_name="nome", expected=nome)


def _query_grupo(
    sb: Client,
    *,
    org_id: str,
    administradora_id: str,
    codigo: str,
) -> dict[str, Any] | None:
    response = (
        sb.table("grupos")
        .select("id, org_id, administradora_id, codigo, produto, assembleia_dia")
        .eq("org_id", org_id)
        .eq("administradora_id", administradora_id)
        .eq("codigo", codigo)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def _query_cota(
    sb: Client,
    *,
    org_id: str,
    administradora_id: str,
    grupo_codigo: str,
    numero_cota: str,
) -> dict[str, Any] | None:
    response = (
        sb.table("cotas")
        .select("id, org_id, lead_id, administradora_id, grupo_codigo, numero_cota, status")
        .eq("org_id", org_id)
        .eq("administradora_id", administradora_id)
        .eq("grupo_codigo", grupo_codigo)
        .eq("numero_cota", numero_cota)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def _query_contract_by_number(sb: Client, *, org_id: str, numero: str) -> dict[str, Any] | None:
    response = (
        sb.table("contratos")
        .select("id, org_id, cota_id, numero, status")
        .eq("org_id", org_id)
        .eq("numero", numero)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def _query_contemplacao(sb: Client, *, org_id: str, cota_id: str) -> dict[str, Any] | None:
    response = (
        sb.table("contemplacoes")
        .select("id, cota_id, data")
        .eq("org_id", org_id)
        .eq("cota_id", cota_id)
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def _requires_lance(parsed: ParsedImportRow) -> bool:
    return bool(
        parsed.lance_feito
        or parsed.percentual_lance is not None
        or parsed.valor_lance is not None
        or parsed.data_ultimo_lance is not None
        or parsed.lance_tipo is not None
        or parsed.detalhes_lance
    )


def _can_create_contract(parsed: ParsedImportRow) -> bool:
    return bool(
        parsed.numero_contrato
        and parsed.valor_parcela is not None
        and parsed.prazo is not None
        and parsed.data_assinatura is not None
    )


def _build_preview_context(sb: Client, profile: CurrentProfile, parsed: ParsedImportRow) -> PreviewContext:
    if not any(
        [
            parsed.cliente_nome,
            parsed.administradora_nome,
            parsed.grupo_codigo,
            parsed.numero_cota,
            parsed.valor_carta,
        ]
    ):
        return PreviewContext(parsed=parsed, status="ignorada")

    ctx = PreviewContext(parsed=parsed, status="pronta")

    if not parsed.cliente_nome:
        ctx.errors.append("Cliente é obrigatório.")
    if not parsed.administradora_nome:
        ctx.errors.append("Administradora/empresa é obrigatória.")
    if not parsed.grupo_codigo:
        ctx.errors.append("Grupo é obrigatório.")
    if not parsed.numero_cota:
        ctx.errors.append("Número da cota é obrigatório.")
    if parsed.valor_carta is None:
        ctx.errors.append("Valor da cota é obrigatório.")

    if ctx.errors:
        ctx.status = "erro"
        return ctx

    existing_lead = _query_lead_by_name(sb, org_id=profile.org_id, nome=parsed.cliente_nome or "")
    ctx.existing_lead = existing_lead
    ctx.planned.cliente_encontrado = existing_lead is not None
    ctx.planned.cliente_criar = existing_lead is None

    existing_adm = _query_administradora_by_name(
        sb,
        org_id=profile.org_id,
        nome=parsed.administradora_nome or "",
    )
    ctx.existing_administradora = existing_adm
    ctx.planned.administradora_criar = existing_adm is None
    if parsed.grupo_codigo:
        ctx.planned.grupo_criar = True
    if parsed.numero_cota:
        ctx.planned.cota_criar = True

    if existing_adm and parsed.grupo_codigo:
        existing_grupo = _query_grupo(
            sb,
            org_id=profile.org_id,
            administradora_id=str(existing_adm["id"]),
            codigo=parsed.grupo_codigo,
        )
        ctx.existing_grupo = existing_grupo
        ctx.planned.grupo_criar = existing_grupo is None

        existing_cota = _query_cota(
            sb,
            org_id=profile.org_id,
            administradora_id=str(existing_adm["id"]),
            grupo_codigo=parsed.grupo_codigo,
            numero_cota=parsed.numero_cota or "",
        )
        ctx.existing_cota = existing_cota
        ctx.planned.cota_criar = existing_cota is None
        if existing_cota:
            if existing_lead and existing_cota.get("lead_id") != existing_lead.get("id"):
                ctx.errors.append(
                    "Já existe cota nesta organização com a mesma administradora, grupo e número vinculada a outro cliente."
                )
            else:
                ctx.warnings.append("A cota já existe e não será duplicada.")

            ctx.existing_contemplacao = _query_contemplacao(
                sb,
                org_id=profile.org_id,
                cota_id=str(existing_cota["id"]),
            )

    if parsed.numero_contrato:
        existing_contract = _query_contract_by_number(sb, org_id=profile.org_id, numero=parsed.numero_contrato)
        ctx.existing_contrato = existing_contract
        if existing_contract:
            ctx.warnings.append("Já existe contrato com este número na organização.")
        elif _can_create_contract(parsed):
            ctx.planned.contrato_criar = True
        else:
            ctx.warnings.append(
                "Dados insuficientes para criar contrato existente. Mínimo: número, valor da parcela, prazo e data de assinatura."
            )
    elif parsed.valor_parcela is not None:
        ctx.warnings.append("Valor da parcela informado sem número de contrato. O contrato não será criado.")

    if parsed.lance_tipo == "sorteio":
        ctx.warnings.append("TIPO DE LANCE=SORTEIO não cria lance; a informação será preservada em estratégia/observações.")
    elif _requires_lance(parsed):
        if parsed.lance_tipo not in {"livre", "fixo"}:
            ctx.warnings.append("Dados de lance detectados, mas o tipo não foi reconhecido como FIXO/LIVRE.")
        elif parsed.data_ultimo_lance is None:
            ctx.warnings.append("Dados de lance detectados sem data do último lance. O lance não será criado.")
        elif parsed.percentual_lance is None and parsed.valor_lance is None:
            ctx.warnings.append("Dados de lance detectados sem percentual ou valor. O lance não será criado.")
        else:
            ctx.planned.lance_criar = True

    if parsed.contemplada:
        if ctx.existing_contemplacao:
            ctx.warnings.append("A cota já possui contemplação registrada.")
        elif parsed.data_ultimo_lance is None:
            ctx.warnings.append("CONTEMPLADA=TRUE sem data de referência. A contemplação não será criada.")
        else:
            ctx.planned.contemplacao_criar = True

    if parsed.parcela_reduzida is None:
        ctx.warnings.append("Parcela reduzida não informada; o campo será tratado como falso.")

    if parsed.valor_final_carta is not None:
        ctx.warnings.append("Valor final da carta será preservado apenas em observações do cadastro.")

    if parsed.optin is not None:
        ctx.warnings.append("Opt-in não possui coluna operacional dedicada neste fluxo e será preservado apenas em observações.")

    if ctx.errors:
        ctx.status = "erro"
    elif ctx.warnings:
        ctx.status = "aviso"

    return ctx


def _apply_in_batch_duplicate_rules(contexts: list[PreviewContext]) -> None:
    seen_cotas: dict[str, int] = {}
    seen_contratos: dict[str, int] = {}

    for ctx in contexts:
        if ctx.status == "ignorada":
            continue

        if ctx.parsed.administradora_nome and ctx.parsed.grupo_codigo and ctx.parsed.numero_cota:
            cota_key = "::".join(
                [
                    _normalize_lookup(ctx.parsed.administradora_nome),
                    ctx.parsed.grupo_codigo,
                    ctx.parsed.numero_cota,
                ]
            )
            first_row = seen_cotas.get(cota_key)
            if first_row is None:
                seen_cotas[cota_key] = ctx.parsed.row_number
            else:
                ctx.errors.append(
                    f"Esta planilha repete a cota da linha {first_row} para a mesma administradora/grupo/número."
                )

        if ctx.parsed.numero_contrato:
            contrato_key = ctx.parsed.numero_contrato
            first_contract_row = seen_contratos.get(contrato_key)
            if first_contract_row is None:
                seen_contratos[contrato_key] = ctx.parsed.row_number
            else:
                ctx.errors.append(f"Esta planilha repete o contrato da linha {first_contract_row}.")

        if ctx.errors:
            ctx.status = "erro"
        elif ctx.warnings:
            ctx.status = "aviso"


def _build_preview_summary(contexts: list[PreviewContext]) -> CarteiraImportPreviewSummary:
    summary = CarteiraImportPreviewSummary(total_rows=len(contexts))
    clientes_encontrados = set[str]()
    clientes_criar = set[str]()
    administradoras_criar = set[str]()
    grupos_criar = set[str]()
    cotas_criar = set[str]()
    contratos_criar = set[str]()
    lances_criar = set[str]()
    contemplacoes_criar = set[str]()

    for ctx in contexts:
        if ctx.status == "pronta":
            summary.prontas += 1
        elif ctx.status == "aviso":
            summary.avisos += 1
        elif ctx.status == "erro":
            summary.erros += 1
        elif ctx.status == "ignorada":
            summary.ignoradas += 1

        if ctx.status in {"erro", "ignorada"}:
            continue

        if ctx.planned.cliente_encontrado and ctx.existing_lead:
            clientes_encontrados.add(str(ctx.existing_lead["id"]))
        if ctx.planned.cliente_criar and ctx.parsed.cliente_nome:
            clientes_criar.add(_normalize_lookup(ctx.parsed.cliente_nome))
        if ctx.planned.administradora_criar and ctx.parsed.administradora_nome:
            administradoras_criar.add(_normalize_lookup(ctx.parsed.administradora_nome))
        if ctx.planned.grupo_criar and ctx.parsed.grupo_codigo and ctx.existing_administradora:
            grupos_criar.add(f"{ctx.existing_administradora['id']}::{ctx.parsed.grupo_codigo}")
        if ctx.planned.grupo_criar and ctx.parsed.grupo_codigo and not ctx.existing_administradora:
            grupos_criar.add(f"new::{_normalize_lookup(ctx.parsed.administradora_nome)}::{ctx.parsed.grupo_codigo}")
        if ctx.planned.cota_criar and ctx.parsed.administradora_nome and ctx.parsed.grupo_codigo and ctx.parsed.numero_cota:
            cotas_criar.add(
                f"{_normalize_lookup(ctx.parsed.administradora_nome)}::{ctx.parsed.grupo_codigo}::{ctx.parsed.numero_cota}"
            )
        if ctx.planned.contrato_criar and ctx.parsed.numero_contrato:
            contratos_criar.add(ctx.parsed.numero_contrato)
        if ctx.planned.lance_criar and ctx.parsed.numero_cota:
            lances_criar.add(f"{ctx.parsed.numero_cota}::{ctx.parsed.data_ultimo_lance}")
        if ctx.planned.contemplacao_criar and ctx.parsed.numero_cota:
            contemplacoes_criar.add(f"{ctx.parsed.numero_cota}::{ctx.parsed.data_ultimo_lance}")

    summary.clientes_encontrados = len(clientes_encontrados)
    summary.clientes_a_criar = len(clientes_criar)
    summary.administradoras_a_criar = len(administradoras_criar)
    summary.grupos_a_criar = len(grupos_criar)
    summary.cotas_a_criar = len(cotas_criar)
    summary.contratos_a_criar = len(contratos_criar)
    summary.lances_a_criar = len(lances_criar)
    summary.contemplacoes_a_criar = len(contemplacoes_criar)
    return summary


def _resolve_lead_for_confirm(
    sb: Client,
    profile: CurrentProfile,
    parsed: ParsedImportRow,
    cache: dict[str, dict[str, Any]],
    existing_lead: dict[str, Any] | None,
) -> dict[str, Any]:
    if existing_lead:
        return existing_lead

    key = _normalize_lookup(parsed.cliente_nome)
    if key in cache:
        return cache[key]

    found = _query_lead_by_name(sb, org_id=profile.org_id, nome=parsed.cliente_nome or "")
    if found:
        cache[key] = found
        return found

    created = _create_lead(sb, profile, parsed)
    cache[key] = created
    return created


def _resolve_administradora_for_confirm(
    sb: Client,
    profile: CurrentProfile,
    parsed: ParsedImportRow,
    cache: dict[str, dict[str, Any]],
    existing_administradora: dict[str, Any] | None,
) -> dict[str, Any]:
    if existing_administradora:
        return existing_administradora

    key = _normalize_lookup(parsed.administradora_nome)
    if key in cache:
        return cache[key]

    found = _query_administradora_by_name(sb, org_id=profile.org_id, nome=parsed.administradora_nome or "")
    if found:
        cache[key] = found
        return found

    created = _create_administradora(sb, profile, parsed.administradora_nome or "")
    cache[key] = created
    return created


def _resolve_grupo_for_confirm(
    sb: Client,
    profile: CurrentProfile,
    parsed: ParsedImportRow,
    administradora_id: str,
    cache: dict[str, dict[str, Any]],
    existing_grupo: dict[str, Any] | None,
) -> dict[str, Any]:
    if existing_grupo:
        return existing_grupo

    key = f"{administradora_id}::{parsed.grupo_codigo}"
    if key in cache:
        return cache[key]

    found = _query_grupo(
        sb,
        org_id=profile.org_id,
        administradora_id=administradora_id,
        codigo=parsed.grupo_codigo or "",
    )
    if found:
        cache[key] = found
        return found

    created = _create_grupo(
        sb,
        profile,
        administradora_id=administradora_id,
        codigo=parsed.grupo_codigo or "",
        produto=parsed.produto,
        assembleia_dia=None,
        observacoes=_build_cota_observacoes(parsed),
    )
    cache[key] = created
    return created


def build_import_preview(
    *,
    sb: Client,
    profile: CurrentProfile,
    raw_text: str,
    produto_padrao: ImportProduto,
) -> tuple[list[PreviewContext], CarteiraImportPreviewResponse]:
    parsed_rows = parse_import_rows(raw_text, produto_padrao=produto_padrao)
    contexts = [_build_preview_context(sb, profile, parsed) for parsed in parsed_rows]
    _apply_in_batch_duplicate_rules(contexts)
    rows = [
        CarteiraImportRowPreview(
            row_number=ctx.parsed.row_number,
            status=ctx.status,  # type: ignore[arg-type]
            cliente_nome=ctx.parsed.cliente_nome,
            administradora_nome=ctx.parsed.administradora_nome,
            grupo_codigo=ctx.parsed.grupo_codigo,
            numero_cota=ctx.parsed.numero_cota,
            contrato_numero=ctx.parsed.numero_contrato,
            lance_tipo=ctx.parsed.lance_tipo if ctx.parsed.lance_tipo in {"livre", "fixo"} else None,
            contemplada=ctx.parsed.contemplada,
            errors=ctx.errors,
            warnings=ctx.warnings,
            planned=ctx.planned,
        )
        for ctx in contexts
        if ctx.status != "ignorada"
    ]
    summary = _build_preview_summary(contexts)
    return contexts, CarteiraImportPreviewResponse(rows=rows, summary=summary)


def _create_lead(sb: Client, profile: CurrentProfile, parsed: ParsedImportRow) -> dict[str, Any]:
    payload = apply_lead_address_rules(
        {
            "org_id": profile.org_id,
            "nome": parsed.cliente_nome,
            "telefone": None,
            "email": None,
            "owner_id": profile.user_id,
            "etapa": "pos_venda",
        }
    )
    response = sb.table("leads").insert(payload, returning="representation").execute()
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao criar lead da carteira na importação.")
    return rows[0]


def _create_administradora(sb: Client, profile: CurrentProfile, nome: str) -> dict[str, Any]:
    response = (
        sb.table("administradoras")
        .insert({"org_id": profile.org_id, "nome": nome}, returning="representation")
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao criar administradora na importação.")
    return rows[0]


def _create_grupo(
    sb: Client,
    profile: CurrentProfile,
    *,
    administradora_id: str,
    codigo: str,
    produto: ImportProduto,
    assembleia_dia: Optional[int],
    observacoes: Optional[str],
) -> dict[str, Any]:
    response = (
        sb.table("grupos")
        .insert(
            {
                "org_id": profile.org_id,
                "administradora_id": administradora_id,
                "codigo": codigo,
                "produto": produto,
                "assembleia_dia": assembleia_dia,
                "observacoes": observacoes,
            },
            returning="representation",
        )
        .execute()
    )
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao criar grupo na importação.")
    return rows[0]


def _build_cota_observacoes(parsed: ParsedImportRow) -> str | None:
    notes = list(parsed.observacoes_importacao)
    if parsed.detalhes_lance:
        notes.append(f"detalhes_lance={parsed.detalhes_lance}")
    if parsed.lance_tipo == "sorteio":
        notes.append("tipo_lance=sorteio")
    if not notes:
        return None
    return " | ".join(notes)


def _create_cota(
    sb: Client,
    profile: CurrentProfile,
    *,
    lead_id: str,
    administradora_id: str,
    parsed: ParsedImportRow,
) -> dict[str, Any]:
    payload = normalize_cota_financial_payload(
        {
            "org_id": profile.org_id,
            "lead_id": lead_id,
            "administradora_id": administradora_id,
            "numero_cota": parsed.numero_cota,
            "grupo_codigo": parsed.grupo_codigo,
            "produto": parsed.produto,
            "valor_carta": float(parsed.valor_carta) if parsed.valor_carta is not None else None,
            "valor_parcela": float(parsed.valor_parcela) if parsed.valor_parcela is not None else None,
            "prazo": parsed.prazo,
            "forma_pagamento": parsed.forma_pagamento,
            "indice_correcao": parsed.indice_correcao,
            "parcela_reduzida": bool(parsed.parcela_reduzida),
            "data_adesao": parsed.data_adesao,
            "status": "contemplada" if parsed.contemplada else "ativa",
            "furo_meses": parsed.furo_meses,
            "aporte": float(parsed.aporte) if parsed.aporte is not None else None,
            "objetivo": parsed.objetivo,
            "estrategia": parsed.estrategia,
            "observacoes": _build_cota_observacoes(parsed),
        }
    )
    response = sb.table("cotas").insert(payload, returning="representation").execute()
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao criar cota na importação.")
    return rows[0]


def _create_contract(
    sb: Client,
    profile: CurrentProfile,
    *,
    cota_id: str,
    parsed: ParsedImportRow,
) -> dict[str, Any]:
    payload = {
        "org_id": profile.org_id,
        "deal_id": None,
        "cota_id": cota_id,
        "numero": parsed.numero_contrato,
        "data_assinatura": parsed.data_assinatura,
        "status": "contemplado" if parsed.contemplada else "alocado",
    }
    response = sb.table("contratos").insert(payload, returning="representation").execute()
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao criar contrato na importação.")
    return rows[0]


def _create_lance(
    sb: Client,
    profile: CurrentProfile,
    *,
    cota_id: str,
    parsed: ParsedImportRow,
) -> dict[str, Any]:
    payload = {
        "org_id": profile.org_id,
        "cota_id": cota_id,
        "tipo": parsed.lance_tipo,
        "percentual": float(parsed.percentual_lance) if parsed.percentual_lance is not None else None,
        "valor": float(parsed.valor_lance) if parsed.valor_lance is not None else None,
        "origem": "importacao_planilha",
        "assembleia_data": parsed.data_ultimo_lance,
        "base_calculo": "valor_carta",
        "pagamento": None,
        "resultado": "pendente",
        "created_by": profile.user_id,
    }
    response = sb.table("lances").insert(payload, returning="representation").execute()
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao criar lance na importação.")
    return rows[0]


def _create_contemplacao(
    sb: Client,
    profile: CurrentProfile,
    *,
    cota_id: str,
    parsed: ParsedImportRow,
) -> dict[str, Any]:
    payload = {
        "org_id": profile.org_id,
        "cota_id": cota_id,
        "motivo": parsed.contemplacao_motivo,
        "lance_percentual": float(parsed.percentual_lance) if parsed.percentual_lance is not None else None,
        "data": parsed.data_ultimo_lance,
    }
    response = sb.table("contemplacoes").insert(payload, returning="representation").execute()
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(500, "Falha ao criar contemplação na importação.")
    return rows[0]


def confirm_import(
    *,
    sb: Client,
    profile: CurrentProfile,
    raw_text: str,
    produto_padrao: ImportProduto,
) -> CarteiraImportConfirmResponse:
    contexts, preview = build_import_preview(
        sb=sb,
        profile=profile,
        raw_text=raw_text,
        produto_padrao=produto_padrao,
    )

    results: list[CarteiraImportRowResult] = []
    imported_rows = 0
    failed_rows = 0
    ignored_rows = 0
    lead_cache: dict[str, dict[str, Any]] = {}
    administradora_cache: dict[str, dict[str, Any]] = {}
    grupo_cache: dict[str, dict[str, Any]] = {}

    for ctx in contexts:
        if ctx.status == "ignorada":
            ignored_rows += 1
            results.append(
                CarteiraImportRowResult(
                    row_number=ctx.parsed.row_number,
                    status="ignorada",
                    cliente_nome=ctx.parsed.cliente_nome,
                    warnings=ctx.warnings,
                )
            )
            continue

        if ctx.status == "erro":
            failed_rows += 1
            results.append(
                CarteiraImportRowResult(
                    row_number=ctx.parsed.row_number,
                    status="erro",
                    cliente_nome=ctx.parsed.cliente_nome,
                    errors=ctx.errors,
                    warnings=ctx.warnings,
                )
            )
            continue

        try:
            lead = _resolve_lead_for_confirm(
                sb,
                profile,
                ctx.parsed,
                lead_cache,
                ctx.existing_lead,
            )
            ensure_carteira_cliente(
                supa=sb,
                org_id=profile.org_id,
                lead_id=str(lead["id"]),
                origem_entrada="importacao_planilha",
                observacoes="Cliente importado por colagem de planilha",
            )

            administradora = _resolve_administradora_for_confirm(
                sb,
                profile,
                ctx.parsed,
                administradora_cache,
                ctx.existing_administradora,
            )
            grupo = _resolve_grupo_for_confirm(
                sb,
                profile,
                administradora_id=str(administradora["id"]),
                parsed=ctx.parsed,
                cache=grupo_cache,
                existing_grupo=ctx.existing_grupo,
            )
            cota = ctx.existing_cota or _create_cota(
                sb,
                profile,
                lead_id=str(lead["id"]),
                administradora_id=str(administradora["id"]),
                parsed=ctx.parsed,
            )

            contrato = ctx.existing_contrato
            if ctx.planned.contrato_criar:
                contrato = _create_contract(sb, profile, cota_id=str(cota["id"]), parsed=ctx.parsed)

            lance = None
            if ctx.planned.lance_criar:
                existing_lance_resp = (
                    sb.table("lances")
                    .select("id")
                    .eq("org_id", profile.org_id)
                    .eq("cota_id", str(cota["id"]))
                    .eq("assembleia_data", ctx.parsed.data_ultimo_lance)
                    .limit(1)
                    .execute()
                )
                existing_lances = getattr(existing_lance_resp, "data", None) or []
                lance = existing_lances[0] if existing_lances else _create_lance(
                    sb,
                    profile,
                    cota_id=str(cota["id"]),
                    parsed=ctx.parsed,
                )

            contemplacao = ctx.existing_contemplacao
            if ctx.planned.contemplacao_criar:
                contemplacao = _create_contemplacao(
                    sb,
                    profile,
                    cota_id=str(cota["id"]),
                    parsed=ctx.parsed,
                )
                sb.table("cotas").update({"status": "contemplada"}).eq("org_id", profile.org_id).eq(
                    "id", str(cota["id"])
                ).execute()

            imported_rows += 1
            results.append(
                CarteiraImportRowResult(
                    row_number=ctx.parsed.row_number,
                    status="aviso" if ctx.warnings else "pronta",
                    cliente_nome=ctx.parsed.cliente_nome,
                    lead_id=str(lead["id"]),
                    administradora_id=str(administradora["id"]),
                    grupo_id=str(grupo["id"]),
                    cota_id=str(cota["id"]),
                    contrato_id=str(contrato["id"]) if contrato else None,
                    lance_id=str(lance["id"]) if lance else None,
                    contemplacao_id=str(contemplacao["id"]) if contemplacao else None,
                    warnings=ctx.warnings,
                )
            )
        except HTTPException as exc:
            failed_rows += 1
            results.append(
                CarteiraImportRowResult(
                    row_number=ctx.parsed.row_number,
                    status="erro",
                    cliente_nome=ctx.parsed.cliente_nome,
                    errors=[str(exc.detail)],
                    warnings=ctx.warnings,
                )
            )
        except Exception as exc:
            failed_rows += 1
            results.append(
                CarteiraImportRowResult(
                    row_number=ctx.parsed.row_number,
                    status="erro",
                    cliente_nome=ctx.parsed.cliente_nome,
                    errors=[str(exc)],
                    warnings=ctx.warnings,
                )
            )

    return CarteiraImportConfirmResponse(
        imported_rows=imported_rows,
        failed_rows=failed_rows,
        ignored_rows=ignored_rows,
        rows=results,
        summary=preview.summary,
    )
