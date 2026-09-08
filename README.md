# Reporte de productividad de preparación

Aplicación Streamlit para medir la productividad del almacén, registrar jornadas y consultar históricos.

## Ejecutar localmente

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

La aplicación puede importar `PRUEBA GITHUB.xlsx` desde la interfaz o cualquier Excel que contenga, como mínimo, columnas equivalentes a:

- `Fecha confirmación`
- `Confirmado por` (operador)
- `Ctd.prev.proced.UMA` (unidades)
- `Fe.inicio`, `Hora inicio` y `Hora de confirmación` para calcular horas automáticamente

Las jornadas capturadas se guardan en `data/productivity_history.csv`. En Streamlit Community Cloud el almacenamiento local puede reiniciarse; usa **Descargar histórico CSV** para respaldar la información y volver a cargarla cuando sea necesario.
