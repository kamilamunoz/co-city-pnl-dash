# co-city-pnl-dash

Dashboard estático de **P&L por ciudad · Colombia** (Market Maker · Habi).

- **Fuente**: `clients-domain-data-master.finance_wh_bi.finance_apartment_tracker_co`
- **Cohorte**: `c_fecha_factura` (mes de facturación de venta — canónica CO)
- **Región**: columna `apartment_specifications_region` (Bogotá, Cali, Barranquilla, Valle De Aburrá)
- **Currency**: COP millones (000,000's)
- **Alcance**: hasta Contribution Margin (CM)
- **Dos vistas**: ACC (columnas `_accounting`) · Sintético (COALESCE `_accounting`>0 con `_model` fila por fila)

## Comandos

```bash
make install     # una vez
make raw         # trae raw de BQ → data/raw_apartment_co.parquet
make refresh     # raw + agrega P&L → site/data/kpi_pnl.json
make serve       # http://localhost:8002/site/
```

## Prerequisitos

```bash
gcloud auth application-default login
```

## Deploy

GitHub Pages sobre `main`. Workflow en `.github/workflows/pages.yml` publica solo `site/`.

## Diferencias con mx-city-pnl-dash

| Aspecto | MX | CO |
|---|---|---|
| Cohorte | `fecha_facturacion_venta` | `c_fecha_factura` |
| Currency | MXN 000's | COP 000,000's (millones) |
| Convención buyers/sellers | Invertida | Natural (Sellers = compra Habi) |
| HC100 | Se omite (double-count) | No aplica |
| Sufijos | `_ue`, `_financial` | `_accounting`, `_model`, `_total` |
| Bugs conocidos | Okol/KARDEX | MCN reversiones (flag informativo) |
