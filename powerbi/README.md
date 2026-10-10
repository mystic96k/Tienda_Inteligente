# Integración con Power BI

| # | Patrón | Cuándo | Actualización |
|---|--------|--------|---------------|
| 1 | **Conector Web → `/api/export/*.csv`** | Arranque rápido, sin acceso a la BD | Programada (la API debe ser alcanzable) |
| 2 | **PostgreSQL / Cloud SQL → esquema `rpt`** (vistas de solo lectura) | Modelo gobernado, Import o DirectQuery | Programada / DirectQuery |
| 3 | **BigQuery** | Mucho histórico y muchos consumidores | Ver `cloud/` |

Para incrustar un reporte publicado en el tablero define `POWERBI_EMBED_URL` (Publish-to-web solo para demos; en producción usa Power BI Embedded).

## 1. Conector Web (Power Query M)
```m
let
    Key  = "change-me",                                  // EXPORT_API_KEY
    Base = "https://TU-API/api/export/",
    Get  = (name as text) => Table.PromoteHeaders(
             Csv.Document(Web.Contents(Base & name & ".csv", [Headers = [#"X-API-Key" = Key]]),
                          [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
             [PromoteAllScalars = true]),
    Forecast = Get("store_forecast")
in  Forecast
```
Datasets: `stores`, `store_daily_kpis`, `store_forecast`, `demand_forecast`, `staffing_plan`, `recommendations`, `promo_performance`, `supplier_performance`, `dq_issues`.
En el Servicio: credencial anónima y *Omitir prueba de conexión* (la autenticación va en el encabezado).

## 2. Base de datos directa (recomendado)
```sql
CREATE ROLE powerbi LOGIN PASSWORD '...';
GRANT USAGE ON SCHEMA rpt TO powerbi;
GRANT SELECT ON ALL TABLES IN SCHEMA rpt TO powerbi;
```
Power BI Desktop → Obtener datos → PostgreSQL → base `retail` → `rpt.vw_*`.
Cloud SQL no es público por defecto: usa un gateway de datos en una VM de la VPC o el Cloud SQL Auth Proxy.

Vistas: `vw_sales_fact`, `vw_inventory_fact`, `vw_store_kpis`, `vw_store_forecast`, `vw_demand_forecast`, `vw_staffing_plan`, `vw_recommendations`, `vw_promo_performance`, `vw_supplier_performance`, `vw_data_quality`.

## Modelo
Hechos: `vw_sales_fact`, `vw_inventory_fact`, `vw_store_kpis`, `vw_store_forecast`, `vw_staffing_plan`. Dimensiones: tiendas/sitios, producto, `Dim Fecha` (`CALENDARAUTO()`). Relaciones por tienda + fecha.

```dax
Ventas netas = SUM ( vw_store_kpis[net_sales] )
Ventas 30d vs previos % =
VAR f = MAX ( vw_store_kpis[kpi_date] )
VAR a = CALCULATE ( [Ventas netas], DATESINPERIOD ( 'Dim Fecha'[Date], f, -30, DAY ) )
VAR b = CALCULATE ( [Ventas netas], DATESINPERIOD ( 'Dim Fecha'[Date], f - 30, -30, DAY ) )
RETURN DIVIDE ( a - b, b )
```
(Ajusta los nombres de columna a lo que muestre `db/02_reporting_views.sql`.)
