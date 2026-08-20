"""Trae el raw de marketing spend CO por área metropolitana y lo guarda como
parquet en data/raw_marketing_co.parquet.

Fuente: `sellers-main-prod.bi_co.resumen_inversiones_regiones_colombia`.
El mapeo `area_metropolitana → region` es el que Kamila definió: las 4 ciudades
canónicas CO (Bogotá, Valle de Aburrá, Cali, Barranquilla) + Otros para el resto.

Valores en USD. La conversión a COP (FX 3900) se aplica en refresh_data.py.

Uso:
    make raw_mkt
"""

from __future__ import annotations

import logging
from pathlib import Path

from scripts._bq import BILLING_PROJECT, run_query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "raw_marketing_co.parquet"

QUERY = """
SELECT
    ir.mes as ir_mes_inversion,
    CASE
        WHEN ir.area_metropolitana NOT IN ('Valle de Aburrá', 'Barranquilla', 'Bogotá', 'Cali') THEN 'Otros'
        ELSE ir.area_metropolitana
    END as ir_area_metropolitana,
    SUM(ir.spend) as ir_spend
FROM `sellers-main-prod.bi_co.resumen_inversiones_regiones_colombia` as ir
GROUP BY 1, 2
ORDER BY 1 DESC, 2 ASC
"""


def main() -> None:
    log.info("Trayendo marketing CO de sellers-main-prod.bi_co.resumen_inversiones_regiones_colombia (billing=%s) ...",
             BILLING_PROJECT)
    df = run_query(QUERY, label="marketing_co_raw")
    log.info("Total filas: %d", len(df))
    if not df.empty:
        log.info("Rango mes: %s → %s", df["ir_mes_inversion"].min(), df["ir_mes_inversion"].max())
        log.info("Regiones únicas: %s", sorted(df["ir_area_metropolitana"].unique().tolist()))
        log.info("Spend total USD: %.1f", df["ir_spend"].sum())

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False)
    log.info("Escrito → %s (%.1f KB)", OUT_PATH, OUT_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
