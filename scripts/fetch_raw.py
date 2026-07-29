"""Trae el raw de finance_apartment_tracker_co y lo guarda como parquet.

Mapeo MX → CO:
- `fecha_facturacion_venta` → `c_fecha_factura` (canónica CO)
- `region` → `apartment_specifications_region` (4 grupos: Bogotá, Cali, Barranquilla, Valle De Aburrá)
- `sell_price_financial`/`buy_price_financial` → `sell_price`/`buy_price` (sin _financial)
- Sin HC100 ni Fee HC100 (no aplica CO)
- Sin `_ue` explícito en muchas columnas: CO usa `_accounting` + `_model` con `holding_costs_total` como _ue implícito
- Convención buyers/sellers CO natural: `buying_*` = compra Habi (Sellers), `selling_*` = venta Habi (Buyers)

Uso:
    make raw
"""

from __future__ import annotations

import logging
from pathlib import Path

from scripts._bq import BILLING_PROJECT, TABLE_APT_CO, run_query

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "data" / "raw_apartment_co.parquet"

QUERY = f"""
select
    -- id + territorio
    nid,
    apartment_specifications_region as region,
    apartment_specifications_city as city,
    area_metropolitana,
    stratum,

    -- fechas
    c_fecha_factura,
    fecha_facturacion_venta,
    date_of_purchase_promise,
    date_of_purchase_real___deed as date_of_purchase_real_deed,
    date_of_sell_real___deed as date_of_sell_real_deed,
    mes_venta,
    mes_cierre,
    dates_start_remo,
    dates_end_remo,

    -- precio
    sell_price,
    buy_price,
    margen_bruto,
    margen_bruto_postremo,

    -- remodeling (detalle sintético: mejoras / pintura / reparaciones)
    r_valor_real_mejoras,
    r_valor_real_pintura,
    r_valor_real_reparcion as r_valor_real_reparaciones,
    r_valor_alistamiento,
    r_valor_final_obra,
    remodeling__model,
    remodeling_accounting,
    remodeling__ue,

    -- trámites sellers (compra Habi CO — buying_*)
    notarial_fees_buying__model,
    notarial_fees_buying_accounting,
    notarial_fees_buying__ue,
    title_registration__model,
    title_registration_accounting,
    title_registration__ue,
    buying_appraisal__model,
    buying_appraisal_accounting,
    legal_fee__model,
    legal_fee_accouting as legal_fee_accounting,
    operational__model,
    operational_accouting as operational_accounting,
    operational__ue,
    total_buying_transaction__costs as total_buying_transaction_costs,

    -- trámites buyers (venta Habi CO — selling_*)
    notarial_fee_selling__model,
    notarial_fee_selling_accounting,
    notarial_fee_selling__ue,
    transaction_costs_total_accounting,
    transaction_costs__total as transaction_costs_total,

    -- holding CO — 4 conceptos + total
    utilities__model,
    utilities_accouting as utilities_accounting,
    utilities__ue,
    maintenance__model,
    maintenance_accouting as maintenance_accounting,
    maintenance__ue,
    property_taxes__model,
    property_taxes_accouting as property_taxes_accounting,
    property_taxes__ue,
    property_fees__model,
    property_fees_accounting,
    property_fees__ue,
    holding_costs_total_accounting,
    holding_costs_total,

    -- comisiones (sellers=compra Habi CO / buyers=venta Habi CO)
    buying_broker_comissions_model,
    buying_broker_comissions_accounting,
    buying_broker_comissions_ue,
    buying_internal_comissions_model,
    buying_internal_comissions_ue,
    selling_broker_comissions_accounting,
    selling_internal_comissions_model,
    selling_internal_comissions_ue,
    commercial_costs_total,

    -- financing
    financing_costs_model,
    financing_costs_accounting,
    financing_costs_ue,
    financing_costs_fiduciary_selling_comission,
    financing___pp___sp_estimate_financing as financing_estimate,
    financing_rate,

    -- metadata operativo
    tipo_compra_sellers,
    buyer_client_type_financing as buyer_client_type,
    days_on_inventory,
    inventory_status,

    -- fecha derivada
    extract(month from c_fecha_factura) as MES,
    extract(year from c_fecha_factura) as ANO
from `{TABLE_APT_CO}`
where c_fecha_factura is not null
"""


def main() -> None:
    log.info("Trayendo raw de %s (billing=%s) ...", TABLE_APT_CO, BILLING_PROJECT)
    df = run_query(QUERY, label="apartment_co_raw")
    log.info("Total filas: %d", len(df))
    log.info(
        "Rango c_fecha_factura: %s → %s",
        df["c_fecha_factura"].min(), df["c_fecha_factura"].max(),
    )
    log.info("Regiones únicas: %d", df["region"].nunique(dropna=True))
    log.info("Filas con region NULL: %d", df["region"].isna().sum())
    log.info("Ciudades únicas: %d", df["city"].nunique(dropna=True))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PATH, index=False)
    log.info("Escrito → %s (%.1f MB)", OUT_PATH, OUT_PATH.stat().st_size / 1024**2)


if __name__ == "__main__":
    main()
