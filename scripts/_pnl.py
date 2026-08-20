"""Motor de agregación P&L por (mes_venta, region) — Colombia.

Adaptado de mx-city-pnl-dash/_pnl.py.

Diferencias vs MX:
- Cohorte: c_fecha_factura (canónica CO, memoria feedback_c_fecha_factura_canonica.md)
- Sin HC100 ni Fee HC100 (no aplica CO)
- Sin IVA (CO no tiene régimen equivalente al mexicano sobre el margen)
- Holding CO tiene 4 buckets en el tracker (Admin/Utilities/Maintenance/Predial),
  agregando Aseo+Arreglos en maintenance según memoria apartment_tracker_co_maintenance_rule.md
- Sin seguridad/alarmas (no existe columna en tracker CO — vive en holding para MX)
- Nomenclatura CO natural en tracker: `buying_*` = compra Habi (Sellers Habi en display),
  `selling_*` = venta Habi (Buyers Habi en display)

- Vista ACC       → usa columnas *_accounting
- Vista Sintético → usa *_ue con fallback a *_accounting fila por fila
                    (fila = un NID, no un mes). Además:
                    · Remodeling se detalla en Mejoras/Pinturas/Reparaciones (r_valor_real_*)
                    · Alistamiento se toma de r_valor_alistamiento

Todos los valores en COP.
"""

from __future__ import annotations

import pandas as pd

# Umbral de filas totales para colapsar en 'Otros'
MIN_ROWS_PER_REGION = 50
# Los NIDs con region NULL — en CO son casi cero, pero por consistencia con MX
DEFAULT_REGION_FOR_NULLS = "Sin región"
LABEL_OTROS = "Otros"
# Alias de región cross-source. En CO no hay fusiones pendientes (a diferencia de
# MX donde CDMX → EDO MEX), pero sí normalizamos el naming de HabiCredit que usa
# "Valle de Aburrá" (D minúscula) vs "Valle De Aburrá" del tracker MM/Inmo.
REGION_ALIASES = {"Valle de Aburrá": "Valle De Aburrá"}


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _num(series: pd.Series) -> pd.Series:
    """Convierte a float y trata NaN como 0 para sumas."""
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def _coalesce_ue_acc(df: pd.DataFrame, ue_col: str, acc_col: str) -> pd.Series:
    """Vista Sintético: usa _ue si no es NaN, si no _accounting. Fila por fila."""
    ue = pd.to_numeric(df[ue_col], errors="coerce")
    acc = pd.to_numeric(df[acc_col], errors="coerce")
    return ue.where(ue.notna(), acc).fillna(0.0)


def _normalize_region(region: pd.Series, counts: pd.Series) -> pd.Series:
    """NaN → 'Sin región'. Regiones con <MIN_ROWS_PER_REGION → 'Otros'."""
    below = counts[counts < MIN_ROWS_PER_REGION].index.tolist()
    out = region.where(region.notna(), DEFAULT_REGION_FOR_NULLS)
    out = out.where(~out.isin(below), LABEL_OTROS)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# preparación
# ─────────────────────────────────────────────────────────────────────────────

def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columna `mes` (YYYY-MM string) y `region_norm`.

    Excluye filas con `c_fecha_factura` nula (NIDs sin facturar todavía).
    """
    out = df.copy()
    fecha = pd.to_datetime(out["c_fecha_factura"])
    out = out.loc[fecha.notna()].copy()
    out["mes"] = pd.to_datetime(out["c_fecha_factura"]).dt.to_period("M").astype(str)
    counts_by_region = out["region"].value_counts(dropna=False)
    out["region_norm"] = _normalize_region(out["region"], counts_by_region)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# estructura declarativa del P&L
# ─────────────────────────────────────────────────────────────────────────────

PNL_STRUCTURE = [
    # ── ingresos ──
    {"key": "invoiced_sales", "label": "# Invoiced Sales", "parent": None, "type": "kpi", "sign": "count"},
    {"key": "gmv_habi", "label": "(+) GMV Precio de Venta", "parent": None, "type": "kpi", "sign": "income"},
    {"key": "purchase_price", "label": "(-) GMV Purchase Price", "parent": None, "type": "kpi", "sign": "cost"},
    {"key": "gross_profit", "label": "(=) Gross Profit", "parent": None, "type": "total", "sign": "net"},

    # ── remodeling ──
    {"key": "rem_mejoras", "label": "Mejoras", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "sintetico"},
    {"key": "rem_pinturas", "label": "Pinturas", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "sintetico"},
    {"key": "rem_reparaciones", "label": "Reparaciones", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "sintetico"},
    {"key": "rem_remodeling_acc", "label": "Remodeling Accounting", "parent": "remodeling", "type": "subcuenta", "sign": "cost", "vista": "acc"},
    {"key": "rem_alistamiento", "label": "Alistamiento", "parent": "remodeling", "type": "subcuenta", "sign": "cost"},
    {"key": "remodeling", "label": "Remodeling Costs", "parent": None, "type": "rubro", "sign": "cost"},

    # ── transaction costs · sellers (compra Habi CO) ──
    {"key": "txs_notariales", "label": "Notariales Compra", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_registro", "label": "Registro (BYR)", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_avaluo", "label": "Avalúo Compra", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_estudios", "label": "Estudios de Títulos / Legal", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "txs_operacional", "label": "Operacionales", "parent": "tramites_sellers", "type": "subcuenta", "sign": "cost"},
    {"key": "tramites_sellers", "label": "Trámites Sellers (Compra Habi)", "parent": "transaction_costs", "type": "grupo", "sign": "cost"},

    # ── transaction costs · buyers (venta Habi CO) ──
    {"key": "txb_notariales", "label": "Notariales Venta", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "txb_otros", "label": "Otros Trámites Venta", "parent": "tramites_buyers", "type": "subcuenta", "sign": "cost"},
    {"key": "tramites_buyers", "label": "Trámites Buyers (Venta Habi)", "parent": "transaction_costs", "type": "grupo", "sign": "cost"},

    {"key": "transaction_costs", "label": "Transaction Costs", "parent": None, "type": "rubro", "sign": "cost"},

    # ── holding costs (4 buckets CO) ──
    {"key": "hol_admin", "label": "Administración (Property Fees)", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "hol_utilities", "label": "Servicios Públicos (Utilities)", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "hol_maintenance", "label": "Mantenimiento (Aseo+Arreglos)", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "hol_predial", "label": "Predial (Property Taxes)", "parent": "holding", "type": "subcuenta", "sign": "cost"},
    {"key": "holding", "label": "Holding Costs", "parent": None, "type": "rubro", "sign": "cost"},
    {"key": "holding_days", "label": "Días promedio en inventario", "parent": None, "type": "kpi", "sign": "days_avg"},

    # ── commercial · external ──
    {"key": "com_ext_sellers", "label": "Comisiones externas sellers (Compra)", "parent": "external_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "com_ext_buyers", "label": "Comisiones externas buyers (Venta)", "parent": "external_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "external_commissions", "label": "External Commissions", "parent": "commercial", "type": "grupo", "sign": "cost"},

    # ── commercial · internal ──
    {"key": "com_int_sellers", "label": "Internal sellers (Compra)", "parent": "internal_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "com_int_buyers", "label": "Internal buyers (Venta)", "parent": "internal_commissions", "type": "subcuenta", "sign": "cost"},
    {"key": "internal_commissions", "label": "Internal Commissions", "parent": "commercial", "type": "grupo", "sign": "cost"},

    {"key": "commercial", "label": "Commercial Costs", "parent": None, "type": "rubro", "sign": "cost"},

    # ── totales ──
    {"key": "direct_costs", "label": "(-) Direct Costs", "parent": None, "type": "rubro", "sign": "cost"},
    {"key": "unlevered_profit", "label": "(=) Unlevered Profit", "parent": None, "type": "total", "sign": "net"},
    {"key": "financing_costs", "label": "(-) Financing Costs", "parent": None, "type": "kpi", "sign": "cost"},
    {"key": "contribution_margin", "label": "(=) Contribution Margin", "parent": None, "type": "total", "sign": "net"},
]


# ─────────────────────────────────────────────────────────────────────────────
# cálculo por vista
# ─────────────────────────────────────────────────────────────────────────────

def _line_values(df: pd.DataFrame, vista: str) -> dict[str, pd.Series]:
    """Devuelve dict {key → serie indexada por df.index} con el valor por-fila
    de cada línea (antes de agrupar por mes/region).

    `vista` ∈ {'acc', 'sintetico'}.
    """
    is_sint = vista == "sintetico"

    def pick(ue_col: str | None, acc_col: str) -> pd.Series:
        """Sintético: coalesce(_ue, _accounting). ACC: solo _accounting."""
        if is_sint and ue_col and ue_col in df.columns:
            return _coalesce_ue_acc(df, ue_col, acc_col)
        return _num(df[acc_col])

    lines: dict[str, pd.Series] = {}

    # ── ingresos ──
    lines["invoiced_sales"] = pd.Series(1, index=df.index, dtype=float)  # count
    lines["gmv_habi"] = _num(df["sell_price"])
    lines["purchase_price"] = -_num(df["buy_price"])
    lines["gross_profit"] = lines["gmv_habi"] + lines["purchase_price"]

    # ── remodeling ──
    #  ACC: Remodeling Accounting + Alistamiento
    #  Sint: Mejoras + Pinturas + Reparaciones + Alistamiento
    lines["rem_mejoras"] = -_num(df["r_valor_real_mejoras"])
    lines["rem_pinturas"] = -_num(df["r_valor_real_pintura"])
    lines["rem_reparaciones"] = -_num(df["r_valor_real_reparaciones"])
    lines["rem_remodeling_acc"] = -_num(df["remodeling_accounting"])
    lines["rem_alistamiento"] = -_num(df["r_valor_alistamiento"])
    if is_sint:
        lines["remodeling"] = (
            lines["rem_mejoras"] + lines["rem_pinturas"] + lines["rem_reparaciones"]
            + lines["rem_alistamiento"]
        )
    else:
        lines["remodeling"] = lines["rem_remodeling_acc"] + lines["rem_alistamiento"]

    # ── transaction · sellers (compra Habi CO — columnas buying_*) ──
    lines["txs_notariales"] = -pick("notarial_fees_buying__ue", "notarial_fees_buying_accounting")
    lines["txs_registro"] = -pick("title_registration__ue", "title_registration_accounting")
    # avalúo compra y estudios no tienen _ue explícito, solo _model + _accounting
    if is_sint:
        lines["txs_avaluo"] = -_coalesce_ue_acc(df, "buying_appraisal__model", "buying_appraisal_accounting")
        lines["txs_estudios"] = -_coalesce_ue_acc(df, "legal_fee__model", "legal_fee_accounting")
    else:
        lines["txs_avaluo"] = -_num(df["buying_appraisal_accounting"])
        lines["txs_estudios"] = -_num(df["legal_fee_accounting"])
    lines["txs_operacional"] = -pick("operational__ue", "operational_accounting")
    lines["tramites_sellers"] = (
        lines["txs_notariales"] + lines["txs_registro"] + lines["txs_avaluo"]
        + lines["txs_estudios"] + lines["txs_operacional"]
    )

    # ── transaction · buyers (venta Habi CO — columnas selling_*) ──
    lines["txb_notariales"] = -pick("notarial_fee_selling__ue", "notarial_fee_selling_accounting")
    # el residual del total buyers (transaction_costs_total - notarial_fee_selling - sellers)
    # captura ICA, BYR venta, certificaciones venta, etc. que no están explícitos en tracker CO
    tc_total = _num(df["transaction_costs_total_accounting"]) if not is_sint else _num(df["transaction_costs_total"])
    buying_total = _num(df["total_buying_transaction_costs"])
    notarial_selling = -lines["txb_notariales"]  # positivo
    lines["txb_otros"] = -(tc_total - buying_total - notarial_selling).clip(lower=0)
    lines["tramites_buyers"] = lines["txb_notariales"] + lines["txb_otros"]

    lines["transaction_costs"] = lines["tramites_sellers"] + lines["tramites_buyers"]

    # ── holding (4 buckets CO) ──
    lines["hol_admin"] = -pick("property_fees__ue", "property_fees_accounting")
    lines["hol_utilities"] = -pick("utilities__ue", "utilities_accounting")
    lines["hol_maintenance"] = -pick("maintenance__ue", "maintenance_accounting")
    lines["hol_predial"] = -pick("property_taxes__ue", "property_taxes_accounting")
    lines["holding"] = (
        lines["hol_admin"] + lines["hol_utilities"]
        + lines["hol_maintenance"] + lines["hol_predial"]
    )
    # Días en inventario por-NID. La agregación por (region, mes) se convierte
    # a promedio en aggregate() dividiendo por el conteo de NIDs con valor no nulo.
    lines["holding_days"] = pd.to_numeric(df["days_on_inventory"], errors="coerce").fillna(0.0)
    lines["_holding_days_count"] = pd.to_numeric(df["days_on_inventory"], errors="coerce").notna().astype(float)

    # ── commercial · external ──
    lines["com_ext_sellers"] = -pick("buying_broker_comissions_ue", "buying_broker_comissions_accounting")
    lines["com_ext_buyers"] = -_num(df["selling_broker_comissions_accounting"])
    lines["external_commissions"] = lines["com_ext_sellers"] + lines["com_ext_buyers"]

    # ── commercial · internal ──
    if is_sint:
        lines["com_int_sellers"] = -_num(df["buying_internal_comissions_ue"])
        lines["com_int_buyers"] = -_num(df["selling_internal_comissions_ue"])
    else:
        # fallback a model si no hay accounting explícito
        lines["com_int_sellers"] = -_num(df["buying_internal_comissions_model"])
        lines["com_int_buyers"] = -_num(df["selling_internal_comissions_model"])
    lines["internal_commissions"] = lines["com_int_sellers"] + lines["com_int_buyers"]

    lines["commercial"] = lines["external_commissions"] + lines["internal_commissions"]

    # ── totales ──
    lines["direct_costs"] = (
        lines["remodeling"] + lines["transaction_costs"] + lines["holding"]
        + lines["commercial"]
    )
    lines["unlevered_profit"] = lines["gross_profit"] + lines["direct_costs"]
    # Financing: suma explícita fiduciary_selling_comission + model (aplicada a ambas vistas).
    # `financing_costs_accounting` está vacío desde 2024 y `_ue` coincide 1:1 con fiduciary_selling_comission.
    lines["financing_costs"] = -(
        _num(df["financing_costs_fiduciary_selling_comission"])
        + _num(df["financing_costs_model"])
    )
    lines["contribution_margin"] = lines["unlevered_profit"] + lines["financing_costs"]

    return lines


def line_values_per_nid(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve un DataFrame por-NID con columnas [nid, region, mes, <key1>, <key2>, ...].

    Cada columna key es el valor de esa línea del P&L para ese NID en esa vista.
    Se usa para el drill-down desde el frontend.
    """
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide.insert(0, "mes", df_prepared["mes"].values)
    wide.insert(0, "region", df_prepared["region_norm"].values)
    wide.insert(0, "nid", df_prepared["nid"].values)
    return wide


# Columnas cuyo agregado es promedio (no suma). Cada una tiene su columna
# hermana `_{key}_count` con el conteo de filas donde el valor original era no nulo.
AVG_COLUMNS = {"holding_days": "_holding_days_count"}


def _post_avg(grouped: pd.DataFrame) -> pd.DataFrame:
    """Convierte SUM(col) / SUM(count) para las columnas de promedio, luego
    elimina las columnas técnicas `_{key}_count`."""
    for col, count_col in AVG_COLUMNS.items():
        if col in grouped.columns and count_col in grouped.columns:
            denom = grouped[count_col].where(grouped[count_col] > 0, other=pd.NA)
            grouped[col] = (grouped[col] / denom).fillna(0.0)
            grouped.drop(columns=[count_col], inplace=True)
    return grouped


def aggregate(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve DataFrame long: columnas [region, mes, key, valor]."""
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes"].values
    grouped = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    grouped = _post_avg(grouped)
    long = grouped.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return long


def aggregate_all_regions(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Igual a aggregate pero también añade fila 'Total' (todas las regiones)."""
    by_region = aggregate(df_prepared, vista)
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide["mes"] = df_prepared["mes"].values
    total = wide.groupby("mes", as_index=False).sum(numeric_only=True)
    total = _post_avg(total)
    total["region"] = "Total"
    total_long = total.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return pd.concat([by_region, total_long], ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# estructura del waterfall CONSOLIDADO (tab MM + Inmo + HabiCredit)
# ─────────────────────────────────────────────────────────────────────────────
# Suma las 3 líneas de negocio CO por región×mes y aplica Local OpEx UNA sola
# vez al final (payroll/rent sirven a las 3 líneas, no solo a MM).
# "Volumen Intermediado" = GMV MM + GMV Inmo + Valor Desembolsado HC
# (conceptos heterogéneos — es un headline number, no un revenue estricto).

_RENT_ONLY_TOTAL_CO = ("rent_nacional",)

# Sub-métricas del OpEx corporativo (bet_data_p2) — se suman al total `corp_opex`
_CORP_OPEX_SUBS_CO = (
    "corp_opex_sales_ops",
    "corp_opex_tech",
    "corp_opex_prof_fees",
    "corp_opex_courier",
    "corp_opex_travel",
    "corp_opex_empl_rel",
    "corp_opex_other",
)
_CORP_OPEX_ONLY_TOTAL_CO = ("corp_opex_nacional",)

PNL_STRUCTURE_CONSOLIDATED = [
    # ── conteos ──
    {"key": "cons_props_mm", "label": "# Properties MM", "parent": "cons_props_total", "type": "kpi", "sign": "count"},
    {"key": "cons_props_inmo", "label": "# Properties Inmo", "parent": "cons_props_total", "type": "kpi", "sign": "count"},
    {"key": "cons_props_hc", "label": "# Desembolsos HabiCredit", "parent": "cons_props_total", "type": "kpi", "sign": "count"},
    {"key": "cons_props_total", "label": "# Transacciones Total", "parent": None, "type": "total", "sign": "count"},

    # ── volumen intermediado (heterogéneo: precio venta + comisión + préstamo) ──
    {"key": "cons_gmv_mm", "label": "GMV MM (precio de venta)", "parent": "cons_gmv_total", "type": "kpi", "sign": "income"},
    {"key": "cons_gmv_inmo", "label": "GMV Inmo (comisión bruta)", "parent": "cons_gmv_total", "type": "kpi", "sign": "income"},
    {"key": "cons_gmv_hc", "label": "Valor Desembolsado HabiCredit", "parent": "cons_gmv_total", "type": "kpi", "sign": "income",
     "note": "Valor total de préstamos originados en el mes. No es venta ni comisión — se suma al 'Volumen Intermediado' como headline number, pero conceptualmente es heterogéneo respecto a GMV MM (precio de venta) y GMV Inmo (comisión de brokerage)."},
    {"key": "cons_gmv_total", "label": "(=) Volumen Intermediado", "parent": None, "type": "total", "sign": "income",
     "note": "Suma de conceptos heterogéneos (precio de venta MM + comisión Inmo + valor de préstamos HC). Útil como headline; no interpretar como revenue comparable línea a línea."},

    # ── contribution por línea ──
    {"key": "cons_cm_mm", "label": "Contribution Margin MM", "parent": "cons_cm_total", "type": "kpi", "sign": "net"},
    {"key": "cons_cm_inmo", "label": "Contribution Margin Inmo", "parent": "cons_cm_total", "type": "kpi", "sign": "net"},
    {"key": "cons_cm_hc", "label": "Margen Neto HabiCredit", "parent": "cons_cm_total", "type": "kpi", "sign": "net"},
    {"key": "cons_cm_total", "label": "(=) Contribution Margin Total", "parent": None, "type": "total", "sign": "net"},

    # ── local OpEx ──
    {"key": "payroll_local", "label": "Payroll local", "parent": "local_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "rent_atribuible", "label": "Rent (atribuible por ciudad)", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True,
     "note": "Anclas vendor→ciudad de Danibot (docs/agrupaciones_por_ciudad.md): PATRIMONIOS AUTONOMOS FIDUCIARIA CORFICOLOMBIANA y EDIFICIO FIJAR 93B → Bogotá; INVERASTORGA → Valle De Aburrá; MUÑOZ ZEIGEN + EMPRESAS MUNICIPALES CALI → Cali; CUBICUS → Barranquilla. Cobertura ~87% del Rent CO."},
    {"key": "rent_nacional", "label": "Rent Nacional / no atribuible", "parent": "rent", "type": "subcuenta", "sign": "cost", "extern": True, "only_total": True,
     "note": "Servicios compartidos sin sede específica (Casalimpia -aseo-, Comcel, Digital Corp, Prosegur -vigilancia-, Supplies, y cola menor). Cubre ~13% del Rent CO. Solo visible en el consolidado porque no se puede atribuir a una ciudad."},
    {"key": "rent", "label": "Rent", "parent": "local_opex", "type": "grupo", "sign": "cost", "extern": True},
    {"key": "marketing_city", "label": "Marketing (ciudad)", "parent": "local_opex", "type": "subcuenta", "sign": "cost", "extern": True,
     "note": "Marketing digital atribuido por área metropolitana (query BQ sobre sellers-main-prod.bi_co). Sirve a MM, Inmo y HabiCredit — por eso se resta solo en el consolidado."},

    # ── OpEx corporativo (bet_data_p2, excluyendo payroll/mkt/rent y filas HABICREDIT/HABICAPITAL) ──
    {"key": "corp_opex_sales_ops", "label": "Sales & Ops", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_tech", "label": "Tech", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_prof_fees", "label": "Professional Fees", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_courier", "label": "Courier & Transportation", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_travel", "label": "Travel Expenses", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_empl_rel", "label": "Employee Relations", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_other", "label": "Other - Local Expenses", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True},
    {"key": "corp_opex_nacional", "label": "OpEx Corp Nacional / no atribuible", "parent": "corp_opex", "type": "subcuenta", "sign": "cost", "extern": True, "only_total": True,
     "note": "Filas de bet_data_p2 con c_ubicacion = 'COLOMBIA' o 'Global COL' (sin ciudad). Se muestra solo en Total. Fuente: `bet_data_p2` categoría '04. Opex', excluye Payroll/Marketing/Rent y filas etiquetadas HABICREDIT/HABICAPITAL."},
    {"key": "corp_opex", "label": "OpEx Corporativo", "parent": "local_opex", "type": "grupo", "sign": "cost", "extern": True,
     "note": "Tech + Professional Fees + Travel + Employee Relations + Courier + Sales&Ops + Other Local. Excluye HABICREDIT (doble-conteo con línea HC) y HABICAPITAL (otra entidad). Cobertura por ciudad ~40-60% del total; el resto va al bucket Nacional."},

    {"key": "local_opex", "label": "(-) Local OpEx", "parent": None, "type": "rubro", "sign": "cost", "extern": True,
     "note": "Payroll + Rent + Marketing + OpEx Corporativo. Sirve a MM, Inmo y HabiCredit simultáneamente, por eso se aplica UNA sola vez sobre la Contribution Total."},
    {"key": "net_city_contribution", "label": "(=) Net City Contribution", "parent": None, "type": "total", "sign": "net", "extern": True},
]


def build_consolidated_long(
    mm_long: pd.DataFrame,
    inmo_long: pd.DataFrame | None,
    hc_long: pd.DataFrame | None,
    opex_long: pd.DataFrame | None,
) -> pd.DataFrame:
    """Construye el waterfall consolidado MM + Inmo + HabiCredit + Local OpEx.

    - `mm_long` es la salida de `aggregate_all_regions(df_mm, vista)`.
    - `inmo_long` [region, mes, key, valor] con keys de Inmo (contribution_margin,
      gmv_inmobiliaria, properties).
    - `hc_long` [region, mes, key, valor] con keys de HC (cant_desembolsos,
      valor_desembolsado, margen_neto). Naming ya normalizado por REGION_ALIASES.
    - `opex_long` [region, mes, key, valor] con payroll_local, rent_atribuible,
      rent_masterlease, rent_nacional.

    Reglas:
    - Contribution Total = CM MM + CM Inmo + Margen Neto HC.
    - Volumen Intermediado = GMV MM + GMV Inmo + Valor Desembolsado HC.
    - # Total = # MM + # Inmo + # HC.
    - Rent = rent_atribuible + master-lease (0 si falta) + nacional (0 si falta).
    - local_opex = payroll + rent + marketing (marketing=0 mientras pendiente).
      Requiere payroll y rent_atribuible presentes.
    - net_city_contribution = cons_cm_total + local_opex (emite null si falta local_opex).
    """
    mm_by_cell: dict[tuple[str, str], dict[str, float]] = {}
    mm_keys = {"invoiced_sales", "gmv_habi", "contribution_margin"}
    for row in mm_long.itertuples():
        if row.key in mm_keys:
            mm_by_cell.setdefault((row.region, row.mes), {})[row.key] = float(row.valor)

    inmo_by_cell: dict[tuple[str, str], dict[str, float]] = {}
    if inmo_long is not None and len(inmo_long) > 0:
        for row in inmo_long.itertuples():
            inmo_by_cell.setdefault((row.region, row.mes), {})[row.key] = float(row.valor)

    hc_by_cell: dict[tuple[str, str], dict[str, float]] = {}
    if hc_long is not None and len(hc_long) > 0:
        for row in hc_long.itertuples():
            hc_by_cell.setdefault((row.region, row.mes), {})[row.key] = float(row.valor)

    opex_by_cell: dict[tuple[str, str], dict[str, float]] = {}
    if opex_long is not None and len(opex_long) > 0:
        for row in opex_long.itertuples():
            opex_by_cell.setdefault((row.region, row.mes), {})[row.key] = float(row.valor)

    all_cells = set(mm_by_cell.keys()) | set(inmo_by_cell.keys()) | set(hc_by_cell.keys())
    new_rows: list[dict] = []
    for (region, mes) in all_cells:
        mm = mm_by_cell.get((region, mes), {})
        inmo = inmo_by_cell.get((region, mes), {})
        hc = hc_by_cell.get((region, mes), {})

        props_mm = mm.get("invoiced_sales", 0.0)
        props_inmo = inmo.get("properties", 0.0)
        props_hc = hc.get("cant_desembolsos", 0.0)
        gmv_mm = mm.get("gmv_habi", 0.0)
        gmv_inmo = inmo.get("gmv_inmobiliaria", 0.0)
        gmv_hc = hc.get("valor_desembolsado", 0.0)
        cm_mm = mm.get("contribution_margin", 0.0)
        cm_inmo = inmo.get("contribution_margin", 0.0)
        cm_hc = hc.get("margen_neto", 0.0)

        new_rows.extend([
            {"region": region, "mes": mes, "key": "cons_props_mm", "valor": props_mm},
            {"region": region, "mes": mes, "key": "cons_props_inmo", "valor": props_inmo},
            {"region": region, "mes": mes, "key": "cons_props_hc", "valor": props_hc},
            {"region": region, "mes": mes, "key": "cons_props_total", "valor": props_mm + props_inmo + props_hc},
            {"region": region, "mes": mes, "key": "cons_gmv_mm", "valor": gmv_mm},
            {"region": region, "mes": mes, "key": "cons_gmv_inmo", "valor": gmv_inmo},
            {"region": region, "mes": mes, "key": "cons_gmv_hc", "valor": gmv_hc},
            {"region": region, "mes": mes, "key": "cons_gmv_total", "valor": gmv_mm + gmv_inmo + gmv_hc},
            {"region": region, "mes": mes, "key": "cons_cm_mm", "valor": cm_mm},
            {"region": region, "mes": mes, "key": "cons_cm_inmo", "valor": cm_inmo},
            {"region": region, "mes": mes, "key": "cons_cm_hc", "valor": cm_hc},
            {"region": region, "mes": mes, "key": "cons_cm_total", "valor": cm_mm + cm_inmo + cm_hc},
        ])

        cells = opex_by_cell.get((region, mes), {})
        for k, v in cells.items():
            new_rows.append({"region": region, "mes": mes, "key": k, "valor": v})

        if "rent_atribuible" in cells:
            rent_val = cells["rent_atribuible"] + sum(cells.get(k, 0.0) for k in _RENT_ONLY_TOTAL_CO)
            new_rows.append({"region": region, "mes": mes, "key": "rent", "valor": rent_val})

        # corp_opex (grupo) = suma de sub-métricas + nacional (si aplica)
        has_any_corp = any(k in cells for k in _CORP_OPEX_SUBS_CO + _CORP_OPEX_ONLY_TOTAL_CO)
        corp_val = 0.0
        if has_any_corp:
            corp_val = (
                sum(cells.get(k, 0.0) for k in _CORP_OPEX_SUBS_CO)
                + sum(cells.get(k, 0.0) for k in _CORP_OPEX_ONLY_TOTAL_CO)
            )
            new_rows.append({"region": region, "mes": mes, "key": "corp_opex", "valor": corp_val})

        if "payroll_local" in cells and "rent_atribuible" in cells:
            local_opex_val = (
                cells["payroll_local"]
                + cells["rent_atribuible"]
                + sum(cells.get(k, 0.0) for k in _RENT_ONLY_TOTAL_CO)
                + cells.get("marketing_city", 0.0)
                + corp_val
            )
            new_rows.append({"region": region, "mes": mes, "key": "local_opex", "valor": local_opex_val})
            new_rows.append({
                "region": region, "mes": mes,
                "key": "net_city_contribution",
                "valor": (cm_mm + cm_inmo + cm_hc) + local_opex_val,
            })

    return pd.DataFrame(new_rows) if new_rows else pd.DataFrame(columns=["region", "mes", "key", "valor"])
