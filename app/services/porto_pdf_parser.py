"""Extrai dados de cartas de consórcio a partir de PDFs da Porto Seguro.

Suporta dois documentos:
- Extrato do Consorciado (cota ativa) — mais completo (data de adesão, taxas, parcela).
- Proposta / Contrato de Participação (momento da venda).

Saída: dicionário de SUGESTÃO para pré-preencher o formulário. O usuário sempre revisa
antes de salvar — extração é best-effort sobre o layout fixo da Porto.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

try:
    import pypdf
except Exception:  # pragma: no cover
    pypdf = None


def _extract_text(content: bytes) -> str:
    if pypdf is None:
        raise RuntimeError("Biblioteca pypdf não instalada no backend.")
    import io

    reader = pypdf.PdfReader(io.BytesIO(content))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _money(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    s = raw.strip().replace(".", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _percent(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    s = raw.strip().replace(".", "").replace(",", ".")
    try:
        return round(float(s), 4)
    except ValueError:
        return None


def _date_iso(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def _search(pattern: str, text: str, flags=re.IGNORECASE) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _produto_from(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    t = token.upper()
    if "IMV" in t or "IMOV" in t or "IMÓV" in t or "IMÓVEL" in t or "IMOVEL" in t:
        return "imobiliario"
    if "AUTO" in t or "VEIC" in t or "CARRO" in t:
        return "auto"
    return None


def detectar_tipo(text: str) -> Optional[str]:
    up = text.upper()
    if "EXTRATO DO CONSORCIADO" in up:
        return "extrato"
    if "PROPOSTA" in up and ("DADOS DA PROPOSTA" in up or "GRUPO DE CONSÓRCIO" in up or "GRUPO DE CONSORCIO" in up):
        return "proposta"
    return None


def _parse_extrato(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    out["grupo_codigo"] = _search(r"Grupo:\s*([A-Z]?\d+)", text)
    out["numero_cota"] = _search(r"Cota:\s*(\d+)", text)
    out["numero_contrato"] = _search(r"Contrato:\s*(\d+)", text)

    # Nome do cliente: vem após o número do contrato na mesma linha
    nome = _search(r"Contrato:\s*\d+\s+([A-ZÀ-Ú][A-ZÀ-Ú \.]+?)\s*(?:\n|F[íi]sica|Jur[íi]dica|Pessoa)", text)
    out["cliente_nome"] = re.sub(r"\s{2,}", " ", nome).strip() if nome else None
    out["cliente_cpf"] = _search(r"CPF/CNPJ:\s*(\d{11,14})", text)
    out["cliente_nascimento"] = _date_iso(_search(r"Nascimento:\s*(\d{2}/\d{2}/\d{4})", text))

    # Bloco "Dados do Plano": valores aparecem concatenados em ordem fixa após "Valor Cr�dito:"
    # Ex.: "Valor Cr�dito:19,5000 13/03/2026 20/03/2026 200 16/03/2026 45,0000 2,0000 IMV 700.000,00 008"
    out["taxa_admin_percentual"] = _percent(
        _search(r"Valor\s*Cr[éeê�]?dito:\s*([\d.,]+?)\d{2}/\d{2}/\d{4}", text)
    )
    out["data_adesao"] = _date_iso(
        _search(r"Valor\s*Cr[éeê�]?dito:\s*[\d.,]+(\d{2}/\d{2}/\d{4})", text)
    )
    # 1ª assembleia (segunda data do bloco) → dia da assembleia
    asm = _search(r"Valor\s*Cr[éeê�]?dito:\s*[\d.,]+\d{2}/\d{2}/\d{4}\s*(\d{2})/\d{2}/\d{4}", text)
    out["assembleia_dia"] = int(asm) if asm else None

    # "...45,0000 2,0000 IMV" → fundo é o número logo antes do produto (2,0000)
    out["fundo_reserva_percentual"] = _percent(_search(r"(\d{1,2},\d{3,4})(?:IMV|AUTO|IMOVEL)", text))
    out["produto"] = _produto_from(_search(r"\d(IMV|AUTO|IMOVEL|IMÓVEL)", text))

    out["prazo"] = int(_search(r"Prazo do grupo:\s*(\d+)\s*meses", text) or 0) or None
    out["valor_parcela"] = _money(_search(r"Valor\s*Contrib\.\s*Mensal:\s*([\d.,]+)", text))

    # Valor do crédito: vem logo após o produto no bloco "Dados do Plano"
    out["valor_carta"] = _money(
        _search(r"(?:IMV|AUTO|IMOVEL|IMÓVEL)\s*\n?\s*(\d{1,3}(?:\.\d{3})+,\d{2})", text)
    )

    return out


def _parse_proposta(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    out["numero_contrato"] = _search(r"N[ÚU]MERO\s*\n\s*(\d{6,})", text)
    out["valor_carta"] = _money(_search(r"VALOR DO CR[ÉE]DITO R\$\s*\n\s*([\d.,]+)", text))
    out["grupo_codigo"] = _search(r"\nGRUPO\s*\n\s*([A-Z]?\d+)", text)
    out["numero_cota"] = _search(r"COTA\s*\n\s*(\d{3,})", text)
    out["cliente_nome"] = _search(r"\nNOME\s*\n\s*([A-ZÀ-Ú][A-ZÀ-Ú \.]+)", text)
    out["cliente_cpf"] = _search(r"CPF\s*\n\s*(\d{3}\.\d{3}\.\d{3}-\d{2})", text)
    out["cliente_nascimento"] = _date_iso(_search(r"NASCIMENTO\s*\n\s*(\d{2}/\d{2}/\d{4})", text))

    # Produto a partir do código do bem (ex.: "IMV_CRA+_08"), não do corpo do texto
    # (evita casar "AUTO" dentro de "AUTORIZADO").
    out["produto"] = (
        _produto_from(_search(r"BEM OBJETO[^\n]*\n\s*([A-Z]{3})", text))
        or _produto_from(_search(r"COD\.\s*BEM\s*\n\s*([A-Z]{3})", text))
    )

    out["prazo"] = int(_search(r"\n(\d{2,3})\s*\n?PRAZO DO", text) or 0) or None
    out["taxa_admin_percentual"] = _percent(
        _search(r"([\d.,]+)%\s*TX\s+DE\s*\n\s*ADM\.\s+TOTAL", text)
    )
    out["fundo_reserva_percentual"] = _percent(_search(r"([\d.,]+)%\s*FUNDO", text))
    embutido = _percent(_search(r"([\d.,]+)%\s*LANCE EMBUTIDO", text))
    out["embutido_max_percent"] = embutido
    out["embutido_permitido"] = bool(embutido and embutido > 0)

    return out


def parse_porto_pdf(content: bytes) -> dict[str, Any]:
    text = _extract_text(content)
    tipo = detectar_tipo(text)

    if tipo == "extrato":
        dados = _parse_extrato(text)
    elif tipo == "proposta":
        dados = _parse_proposta(text)
    else:
        return {
            "tipo_documento": None,
            "dados": {},
            "avisos": ["Não foi possível identificar o documento como extrato ou proposta da Porto."],
        }

    # Remove chaves nulas e gera avisos para campos não encontrados
    dados_limpos = {k: v for k, v in dados.items() if v is not None and v != ""}
    avisos: list[str] = []
    essenciais = ["grupo_codigo", "numero_cota", "valor_carta", "prazo"]
    faltando = [c for c in essenciais if c not in dados_limpos]
    if faltando:
        avisos.append(
            "Confira os campos: alguns não foram lidos automaticamente (" + ", ".join(faltando) + ")."
        )

    return {
        "tipo_documento": tipo,
        "dados": dados_limpos,
        "avisos": avisos,
    }
