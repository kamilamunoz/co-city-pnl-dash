"""Orquestador: lee data/raw_apartment_co.parquet, agrega P&L por (mes, region)
para las dos vistas (ACC y Sintético), y escribe site/data/kpi_pnl.json.

Uso:
    make refresh

Opcional:
    MES_CUTOFF=YYYY-MM   Excluye meses posteriores al cutoff (inclusive el mes
                         siguiente). Sirve para no publicar meses parciales
                         cuando el tracker CO trae días del mes en curso.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import db_dtypes  # noqa: F401  registra tipos dbdate/dbtime del parquet
import pandas as pd

from scripts._pnl import (
    LABEL_OTROS,
    MIN_ROWS_PER_REGION,
    PNL_STRUCTURE,
    PNL_STRUCTURE_CONSOLIDATED,
    REGION_ALIASES,
    aggregate_all_regions,
    build_consolidated_long,
    line_values_per_nid,
    prepare,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = REPO_ROOT / "data" / "raw_apartment_co.parquet"
OUT_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl.json"
OUT_FACTS_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl_facts.json"
OUT_CONSOLIDATED_PATH = REPO_ROOT / "site" / "data" / "kpi_pnl_consolidated.json"

# JSONs de las otras 2 líneas de negocio (repos hermanos).
INMO_JSON_PATH = Path.home() / "Finanzas-Habi" / "co-inmo-pnl-dash" / "site" / "data" / "kpi_pnl.json"
HABICREDIT_JSON_PATH = Path.home() / "Finanzas-Habi" / "habicredit-co-pnl-dash" / "site" / "data" / "kpi_pnl.json"

# Fuentes externas Local OpEx (mismo repo blt-dashboard que MX).
BLT_DASHBOARD_DATA = Path.home() / "Finanzas-Habi" / "blt-dashboard" / "data"
FX_COP_PER_USD = 3900.0  # el mismo escalar exacto que usa Danibot en Seguimiento Terceros para CO

# Rent CO — mapeo vendor → destino (según opex_terceros y rent_by_city_co de Danibot):
# - "EDIFICIO FIJAR CENTRO 93B" (~12% Rent CO) → Bogotá (única ancla clara)
# - "PATRIMONIOS AUTONOMOS FIDUCIARIA CORFICOLOMBIANA S A 800256769" (~66%) → master-lease
# - todo lo demás → rent_nacional
RENT_CO_VENDOR_TO_REGION = {
    "EDIFICIO FIJAR CENTRO 93B- PROPIEDAD HORIZONTAL": "Bogotá",
}
RENT_CO_MASTERLEASE_VENDORS = {
    "PATRIMONIOS AUTONOMOS FIDUCIARIA CORFICOLOMBIANA S A 800256769",
}


def _alias_region(region: str) -> str:
    """Aplica REGION_ALIASES a un string (Valle de Aburrá → Valle De Aburrá)."""
    return REGION_ALIASES.get(region, region)


def _region_labels(df_prepared: pd.DataFrame) -> list[dict]:
    """Devuelve lista de regiones con conteo, ordenadas: real > Otros > Total."""
    counts = df_prepared["region_norm"].value_counts()
    real = [r for r in counts.index if r != LABEL_OTROS]
    real_sorted = sorted(real, key=lambda r: -int(counts[r]))
    ordered = real_sorted
    if LABEL_OTROS in counts.index:
        ordered.append(LABEL_OTROS)
    ordered.append("Total")
    return [
        {"key": r, "label": r, "filas": int(counts.get(r, 0)) if r != "Total" else int(counts.sum())}
        for r in ordered
    ]


def _long_to_nested(long_df: pd.DataFrame) -> dict:
    """{region → {mes → {key → valor}}} con floats redondeados a 2."""
    out: dict = {}
    for (region, mes), sub in long_df.groupby(["region", "mes"], sort=False):
        d = {row.key: round(float(row.valor), 2) for row in sub.itertuples()}
        out.setdefault(region, {})[mes] = d
    return out


def _load_local_opex_co() -> tuple[pd.DataFrame | None, dict]:
    """Lee payroll (Lis) y rent (Danibot) del repo blt-dashboard para CO.

    Reglas:
    - Payroll: COP absoluto ya por región canónica CO.
    - Rent: USD_K en la fuente; se multiplica por FX_COP_PER_USD × 1000 → COP.
      Solo EDIFICIO FIJAR 93B mapea a región (Bogotá). Master-lease Patrimonios
      Autónomos y resto → rent_masterlease / rent_nacional en Total.
    - Marketing: pendiente (no hay query CO todavía).
    """
    payroll_path = BLT_DASHBOARD_DATA / "payroll_by_city.json"
    opex_path = BLT_DASHBOARD_DATA / "opex_terceros.json"

    if not payroll_path.exists() or not opex_path.exists():
        log.warning("blt-dashboard data no disponible — omitiendo Local OpEx CO")
        return None, {}

    meta: dict = {}
    rows: list[dict] = []

    # ── Payroll CO (Lis) ─────────────────────────────────────────────
    payroll = json.loads(payroll_path.read_text(encoding="utf-8"))
    meta["payroll_generado_en"] = payroll["_meta"].get("generated")

    payroll_by_region_mes: dict[tuple[str, str], float] = {}
    sedes_by_region: dict[str, list[str]] = {}
    payroll_max_mes: str | None = None
    for block in payroll.get("co", []):
        region_final = _alias_region(block["city_pnl"])
        sedes_by_region.setdefault(region_final, []).extend(block.get("sedes") or [])
        for mes, val in block.get("costo_empresa_cop_mensual", {}).items():
            payroll_by_region_mes[(region_final, mes)] = (
                payroll_by_region_mes.get((region_final, mes), 0.0) + float(val)
            )
            if payroll_max_mes is None or mes > payroll_max_mes:
                payroll_max_mes = mes

    total_payroll_by_mes: dict[str, float] = {}
    regiones_sin_sede = [r for r, sedes in sedes_by_region.items() if not sedes]
    for (region, mes), val in payroll_by_region_mes.items():
        no_sedes = not sedes_by_region.get(region)
        valor_signado = 0.0 if no_sedes else -val
        rows.append({"region": region, "mes": mes, "key": "payroll_local", "valor": valor_signado})
        if not no_sedes:
            total_payroll_by_mes[mes] = total_payroll_by_mes.get(mes, 0.0) + val

    # co_sin_ciudad (personas no atribuibles) → solo al Total
    sin_ciudad = payroll.get("co_sin_ciudad", {}).get("costo_empresa_cop_mensual", {})
    for mes, val in sin_ciudad.items():
        total_payroll_by_mes[mes] = total_payroll_by_mes.get(mes, 0.0) + float(val)

    for mes, val in total_payroll_by_mes.items():
        rows.append({"region": "Total", "mes": mes, "key": "payroll_local", "valor": -val})

    meta["payroll_cobertura_hasta"] = payroll_max_mes
    meta["payroll_regiones_sin_sede"] = regiones_sin_sede

    # ── Rent CO (Danibot, opex_terceros.data.Colombia.Rent) ──────────
    opex = json.loads(opex_path.read_text(encoding="utf-8"))
    meta["rent_generado_en"] = opex["meta"].get("generated_at")
    ytd_month = int(opex["meta"].get("ytd_month_2026", 12))
    vendors = opex["data"]["Colombia"]["Rent"]["vendors"]

    rent_acum: dict[tuple[str, str, str], float] = {}
    for v in vendors:
        name = v["name"]
        if name in RENT_CO_MASTERLEASE_VENDORS:
            dest_region, fact_key = "Total", "rent_masterlease"
        elif name in RENT_CO_VENDOR_TO_REGION:
            dest_region = _alias_region(RENT_CO_VENDOR_TO_REGION[name])
            fact_key = "rent_atribuible"
        else:
            dest_region, fact_key = "Total", "rent_nacional"

        for series_key, months in v.items():
            if not (isinstance(series_key, str) and series_key.startswith("a") and len(series_key) == 5):
                continue
            try:
                year = int(series_key[1:])
            except ValueError:
                continue
            if not isinstance(months, list):
                continue
            for i, val_usdk in enumerate(months):
                if val_usdk == 0.0:
                    continue
                if year == 2026 and i >= ytd_month:
                    continue
                mes = f"{year}-{i + 1:02d}"
                key = (dest_region, mes, fact_key)
                rent_acum[key] = rent_acum.get(key, 0.0) + float(val_usdk)

    total_atrib_by_mes: dict[str, float] = {}
    rent_max_mes: str | None = None
    for (region, mes, fact_key), val_usdk in rent_acum.items():
        val_cop = val_usdk * FX_COP_PER_USD * 1000.0
        rows.append({"region": region, "mes": mes, "key": fact_key, "valor": val_cop})
        if fact_key == "rent_atribuible" and region != "Total":
            total_atrib_by_mes[mes] = total_atrib_by_mes.get(mes, 0.0) + val_usdk
        if rent_max_mes is None or mes > rent_max_mes:
            rent_max_mes = mes

    for mes, val_usdk in total_atrib_by_mes.items():
        rows.append({
            "region": "Total", "mes": mes, "key": "rent_atribuible",
            "valor": val_usdk * FX_COP_PER_USD * 1000.0,
        })

    meta["rent_cobertura_hasta"] = rent_max_mes
    meta["fx_cop_per_usd"] = FX_COP_PER_USD

    # Backfill: emitir rent_atribuible=0 en (region, mes) con payroll pero sin rent
    regions_with_rent_atr = {(r, m) for (r, m, fk) in rent_acum.keys() if fk == "rent_atribuible"}
    regions_with_rent_atr |= {("Total", m) for m in total_atrib_by_mes.keys()}
    regions_with_payroll = {
        (row["region"], row["mes"]) for row in rows if row["key"] == "payroll_local"
    }
    for (r, m) in regions_with_payroll - regions_with_rent_atr:
        rows.append({"region": r, "mes": m, "key": "rent_atribuible", "valor": 0.0})

    # Marketing CO: pendiente
    meta["marketing_cobertura_hasta"] = None
    meta["marketing_pendiente"] = "Query CO por ciudad pendiente"

    if not rows:
        return None, meta
    return pd.DataFrame(rows), meta


def _load_inmo_co() -> tuple[dict[str, pd.DataFrame] | None, dict]:
    """Lee JSON del co-inmo-pnl-dash. Devuelve {vista → long_df} con las keys
    necesarias para el consolidado: contribution_margin, gmv_inmobiliaria, properties.
    """
    if not INMO_JSON_PATH.exists():
        log.warning("co-inmo-pnl-dash JSON no encontrado — consolidado sin Inmo")
        return None, {}

    inmo = json.loads(INMO_JSON_PATH.read_text(encoding="utf-8"))
    meta = {"inmo_generado_en": inmo.get("meta", {}).get("generado_en")}

    keys_of_interest = {"contribution_margin", "gmv_inmobiliaria", "properties"}
    out: dict[str, pd.DataFrame] = {}
    for vista, data_region in inmo["vistas"].items():
        acc: dict[tuple[str, str, str], float] = {}
        for region_orig, months in data_region.items():
            region_final = _alias_region(region_orig)
            for mes, row in months.items():
                for k, v in row.items():
                    if k not in keys_of_interest:
                        continue
                    key = (region_final, mes, k)
                    acc[key] = acc.get(key, 0.0) + float(v)
        rows = [{"region": r, "mes": m, "key": k, "valor": v} for (r, m, k), v in acc.items()]
        out[vista] = pd.DataFrame(rows)
    return out, meta


def _load_habicredit_co() -> tuple[pd.DataFrame | None, dict]:
    """Lee JSON del habicredit-co-pnl-dash. Schema distinto (data[ciudad][mes][kpi]).

    Devuelve long_df con [region, mes, key, valor] normalizado con alias
    ('Valle de Aburrá' → 'Valle De Aburrá'). HC no tiene vista ACC/Sintético —
    la misma serie aplica a ambas vistas del consolidado.
    """
    if not HABICREDIT_JSON_PATH.exists():
        log.warning("habicredit-co-pnl-dash JSON no encontrado — consolidado sin HC")
        return None, {}

    hc = json.loads(HABICREDIT_JSON_PATH.read_text(encoding="utf-8"))
    meta = {"hc_generado_en": hc.get("meta", {}).get("generated_at")}

    keys_of_interest = {"cant_desembolsos", "valor_desembolsado", "margen_neto"}
    rows: list[dict] = []
    # data structure: {ciudad: {mes: {kpi: valor}}}
    for ciudad_orig, months in hc.get("data", {}).items():
        region_final = _alias_region(ciudad_orig)
        for mes, kpi_map in months.items():
            for k, v in kpi_map.items():
                if k not in keys_of_interest or v is None:
                    continue
                rows.append({"region": region_final, "mes": mes, "key": k, "valor": float(v)})

    return pd.DataFrame(rows) if rows else None, meta


def _write_consolidated(
    long_by_vista_mm: dict[str, pd.DataFrame],
    inmo_by_vista: dict[str, pd.DataFrame] | None,
    hc_long: pd.DataFrame | None,
    local_opex_df: pd.DataFrame | None,
    regiones: list[dict],
    meses_mm: list[str],
    local_opex_meta: dict,
    inmo_meta: dict,
    hc_meta: dict,
) -> None:
    """Construye kpi_pnl_consolidated.json (MM + Inmo + HabiCredit + Local OpEx)."""
    vistas_out: dict[str, dict] = {}
    all_meses: set[str] = set(meses_mm)

    for vista, mm_long in long_by_vista_mm.items():
        inmo_long = None if inmo_by_vista is None else inmo_by_vista.get(vista)
        cons_long = build_consolidated_long(mm_long, inmo_long, hc_long, local_opex_df)
        vistas_out[vista] = _long_to_nested(cons_long)
        if inmo_long is not None and len(inmo_long) > 0:
            all_meses.update(inmo_long["mes"].astype(str).unique().tolist())

    if hc_long is not None and len(hc_long) > 0:
        all_meses.update(hc_long["mes"].astype(str).unique().tolist())

    meses_ordenados = sorted(all_meses)

    payload = {
        "meta": {
            "generado_en": datetime.now().isoformat(timespec="seconds"),
            "descripcion": (
                "Waterfall consolidado MM + Inmo + HabiCredit por región×mes. El "
                "Local OpEx (payroll+rent+marketing) se aplica UNA sola vez sobre la "
                "Contribution Total porque sirve a las 3 líneas de negocio."
            ),
            "local_opex": local_opex_meta,
            "inmo": inmo_meta,
            "habicredit": hc_meta,
            "currency": "COP",
            "unidad": "unidades absolutas (el frontend divide por 1_000_000 para mostrar en millones)",
        },
        "estructura": PNL_STRUCTURE_CONSOLIDATED,
        "regiones": regiones,
        "meses": meses_ordenados,
        "vistas": vistas_out,
    }

    OUT_CONSOLIDATED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CONSOLIDATED_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log.info("Escrito → %s (%.1f KB)", OUT_CONSOLIDATED_PATH, OUT_CONSOLIDATED_PATH.stat().st_size / 1024)


def main() -> None:
    if not RAW_PATH.exists():
        raise SystemExit(f"No existe {RAW_PATH}. Corre `make raw` primero.")

    log.info("Leyendo %s ...", RAW_PATH)
    raw = pd.read_parquet(RAW_PATH)
    log.info("Raw: %d filas", len(raw))

    log.info("Preparando (mes + region_norm) ...")
    df = prepare(raw)
    log.info("Después de prepare: %d filas (excluidas %d por fecha nula)", len(df), len(raw) - len(df))

    mes_cutoff = os.environ.get("MES_CUTOFF", "").strip()
    if mes_cutoff:
        antes = len(df)
        df = df.loc[df["mes"] <= mes_cutoff].copy()
        log.info("Cutoff %s aplicado: %d filas (excluidas %d de meses posteriores)",
                 mes_cutoff, len(df), antes - len(df))

    regiones = _region_labels(df)
    log.info("Regiones finales: %s", [r["key"] for r in regiones])

    log.info("Agregando vista ACC ...")
    long_acc = aggregate_all_regions(df, "acc")
    log.info("Agregando vista Sintético ...")
    long_sint = aggregate_all_regions(df, "sintetico")

    meses = sorted(df["mes"].unique().tolist())

    payload = {
        "meta": {
            "generado_en": datetime.now().isoformat(timespec="seconds"),
            "tabla_fuente": "clients-domain-data-master.finance_wh_bi.finance_apartment_tracker_co",
            "cohorte": "c_fecha_factura (mes de facturación de venta, canónica CO)",
            "currency": "COP",
            "unidad": "unidades absolutas (el frontend divide por 1_000_000 para mostrar en millones)",
            "min_rows_per_region": MIN_ROWS_PER_REGION,
            "filas_raw": int(len(raw)),
            "filas_incluidas": int(len(df)),
            "filas_excluidas_por_fecha_nula": int(len(raw) - len(df)),
            "rango_fechas": {
                "min": pd.to_datetime(df["c_fecha_factura"]).min().strftime("%Y-%m-%d"),
                "max": pd.to_datetime(df["c_fecha_factura"]).max().strftime("%Y-%m-%d"),
            },
        },
        "estructura": PNL_STRUCTURE,
        "regiones": regiones,
        "meses": meses,
        "vistas": {
            "acc": _long_to_nested(long_acc),
            "sintetico": _long_to_nested(long_sint),
        },
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    log.info("Escrito → %s (%.1f KB)", OUT_PATH, OUT_PATH.stat().st_size / 1024)

    # ── kpi_pnl_facts.json: valores por-NID (para drill-down) ──
    log.info("Construyendo facts por-NID ...")
    line_keys = [r["key"] for r in PNL_STRUCTURE]

    facts_payload = {}
    for vista in ("acc", "sintetico"):
        per_nid = line_values_per_nid(df, vista)
        # arrays paralelos + matriz de valores (round a 2)
        nids = per_nid["nid"].astype(str).tolist()
        regs = per_nid["region"].astype(str).tolist()
        meses_ = per_nid["mes"].astype(str).tolist()
        matriz = []
        for k in line_keys:
            if k in per_nid.columns:
                # redondear a 2 decimales; convertir a floats python nativos
                col = per_nid[k].round(2).astype(float).tolist()
                matriz.append(col)
            else:
                matriz.append([0.0] * len(per_nid))
        facts_payload[vista] = {
            "columnas": line_keys,
            "nid": nids,
            "region": regs,
            "mes": meses_,
            # matriz [linea][nid_idx] → val
            "valores": matriz,
        }

    with open(OUT_FACTS_PATH, "w", encoding="utf-8") as f:
        json.dump(facts_payload, f, ensure_ascii=False, separators=(",", ":"))
    log.info("Escrito → %s (%.1f KB)", OUT_FACTS_PATH, OUT_FACTS_PATH.stat().st_size / 1024)

    # ── kpi_pnl_consolidated.json: waterfall MM + Inmo + HabiCredit + Local OpEx ──
    log.info("Cargando Local OpEx CO (payroll Lis + rent Danibot; marketing pendiente) ...")
    local_opex_df, local_opex_meta = _load_local_opex_co()
    if local_opex_df is not None:
        log.info("Local OpEx CO: %d filas, payroll hasta %s, rent hasta %s",
                 len(local_opex_df),
                 local_opex_meta.get("payroll_cobertura_hasta"),
                 local_opex_meta.get("rent_cobertura_hasta"))

    log.info("Cargando Inmo CO (JSON de co-inmo-pnl-dash) ...")
    inmo_by_vista, inmo_meta = _load_inmo_co()
    if inmo_by_vista is not None:
        log.info("Inmo CO: generado_en=%s", inmo_meta.get("inmo_generado_en"))

    log.info("Cargando HabiCredit CO (JSON de habicredit-co-pnl-dash) ...")
    hc_long, hc_meta = _load_habicredit_co()
    if hc_long is not None:
        log.info("HabiCredit CO: %d filas, generado_en=%s", len(hc_long), hc_meta.get("hc_generado_en"))

    log.info("Construyendo consolidado MM + Inmo + HabiCredit ...")
    _write_consolidated(
        long_by_vista_mm={"acc": long_acc, "sintetico": long_sint},
        inmo_by_vista=inmo_by_vista,
        hc_long=hc_long,
        local_opex_df=local_opex_df,
        regiones=regiones,
        meses_mm=meses,
        local_opex_meta=local_opex_meta,
        inmo_meta=inmo_meta,
        hc_meta=hc_meta,
    )


if __name__ == "__main__":
    main()
