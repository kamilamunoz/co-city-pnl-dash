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
    if is_sint:
        lines["financing_costs"] = -pick("financing_costs_ue", "financing_costs_accounting")
    else:
        lines["financing_costs"] = -_num(df["financing_costs_accounting"])
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


def aggregate(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Devuelve DataFrame long: columnas [region, mes, key, valor]."""
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide["region"] = df_prepared["region_norm"].values
    wide["mes"] = df_prepared["mes"].values
    grouped = wide.groupby(["region", "mes"], as_index=False).sum(numeric_only=True)
    long = grouped.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return long


def aggregate_all_regions(df_prepared: pd.DataFrame, vista: str) -> pd.DataFrame:
    """Igual a aggregate pero también añade fila 'Total' (todas las regiones)."""
    by_region = aggregate(df_prepared, vista)
    lines = _line_values(df_prepared, vista)
    wide = pd.DataFrame(lines)
    wide["mes"] = df_prepared["mes"].values
    total = wide.groupby("mes", as_index=False).sum(numeric_only=True)
    total["region"] = "Total"
    total_long = total.melt(id_vars=["region", "mes"], var_name="key", value_name="valor")
    return pd.concat([by_region, total_long], ignore_index=True)
