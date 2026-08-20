"""Trae el raw de OpEx corporativo CO por métrica y ubicación desde bet_data_p2.

Alcance: `04. Opex` en `m_pais = '1. Colombia'`, excluyendo las 3 líneas ya
cubiertas por el Local OpEx dedicado (`01. Payroll`, `02. Marketing`, `05. Rent`).
Se excluyen también las filas etiquetadas como `HABICREDIT` (doble-conteo con la
línea de negocio) y `BOGOTA - HABICAPITAL` (entidad distinta).

Uso:
    make raw_corp
"""

from __future__ import annotations

import logging
from pathlib import Path

from scripts._bq import run_query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "raw_corp_opex_co.parquet"

QUERY = """
SELECT
    mes,
    m_metrica,
    c_ubicacion,
    SUM(actuals_accounting) AS actuals_cop
FROM `papyrus-delivery-data.corp_gov_global.bet_data_p2`
WHERE m_categoria = '04. Opex'
  AND m_pais = '1. Colombia'
  AND m_metrica NOT IN ('01. Payroll', '02. Marketing', '05. Rent')
  AND (dummie_eliminaciones NOT IN (1, -1) OR dummie_eliminaciones IS NULL)
  AND c_ubicacion NOT IN ('HABICREDIT', 'BOGOTA - HABICAPITAL')
  AND actuals_accounting IS NOT NULL
  AND actuals_accounting != 0
GROUP BY 1, 2, 3
HAVING actuals_cop != 0
ORDER BY mes DESC, m_metrica, c_ubicacion
"""


def main() -> None:
    log.info("Trayendo OpEx corporativo CO de bet_data_p2 ...")
    df = run_query(QUERY, label="corp_opex_co_raw")
    log.info("Total filas: %d", len(df))
    if not df.empty:
        log.info("Rango mes: %s → %s", df["mes"].min(), df["mes"].max())
        log.info("Métricas: %s", sorted(df["m_metrica"].unique().tolist()))
        log.info("Ubicaciones: %s", sorted(df["c_ubicacion"].dropna().unique().tolist()))
        log.info("Total actuals COP MM: %.1f", df["actuals_cop"].sum() / 1e6)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False)
    log.info("Escrito → %s (%.1f KB)", OUT_PATH, OUT_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
