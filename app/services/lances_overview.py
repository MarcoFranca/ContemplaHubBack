from typing import Any


def build_operacao_overview(
    cotas: list[dict[str, Any]],
    controles: list[dict[str, Any]],
) -> dict[str, int]:
    status_por_cota = {
        str(controle["cota_id"]): controle.get("status_mes", "pendente")
        for controle in controles
    }
    statuses = [status_por_cota.get(str(cota["id"]), "pendente") for cota in cotas]

    return {
        "pendentes": sum(status not in {"planejado", "feito", "sem_lance"} for status in statuses),
        "planejados": statuses.count("planejado"),
        "baixados": statuses.count("feito"),
        "sem_lance": statuses.count("sem_lance"),
        "contempladas": sum(cota.get("status") == "contemplada" for cota in cotas),
        "total": len(cotas),
    }
