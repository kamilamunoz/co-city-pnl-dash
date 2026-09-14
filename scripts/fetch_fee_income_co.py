"""Trae el fee "INGRESOS POR BONO DDC" (cuenta 41010122) del auxiliar contable
CO agregado por NID, y lo guarda en data/raw_fee_income_co.parquet.

Fuente: `clients-domain-data-master.finance_wh_bi.finance_auxiliar_contable_co`.
Subsidiaria: 'HABI'. Convención: monto = credito - debito (positivo = ingreso).

El join con el tracker MM se hace por `nid` (INT64 en tracker CO → cast a STRING
en el auxiliar).

Uso:
    make raw_fee
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
OUT_PATH = REPO_ROOT / "data" / "raw_fee_income_co.parquet"

QUERY = """
SELECT
    SAFE_CAST(nid AS INT64) AS nid,
    SUM(credito - debito) AS fee_income
FROM `clients-domain-data-master.finance_wh_bi.finance_auxiliar_contable_co`
WHERE numero_de_cuenta = '41010122'
  AND nid IS NOT NULL AND nid != ''
  AND SAFE_CAST(nid AS INT64) IS NOT NULL
GROUP BY nid
HAVING fee_income != 0
"""


def main() -> None:
    log.info("Trayendo fee income CO (cuenta 41010122) de auxiliar_contable_co (billing=%s) ...",
             BILLING_PROJECT)
    df = run_query(QUERY, label="fee_income_co")
    log.info("Total NIDs con fee: %d · total fee COP: %.0f", len(df), df["fee_income"].sum())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False)
    log.info("Escrito → %s (%.1f KB)", OUT_PATH, OUT_PATH.stat().st_size / 1024)


if __name__ == "__main__":
    main()
