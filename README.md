# Reporte de productividad de preparación

Aplicación Streamlit para medir la productividad del almacén, registrar jornadas y consultar históricos.

## Ejecutar localmente

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

La aplicación importa archivos CSV delimitados por coma, punto y coma o tabulador. El archivo debe contener, como mínimo, columnas equivalentes a:

- `Fecha confirmación`
- `Confirmado por` (operador)
- `Ctd.prev.proced.UMA` (unidades)
- `Fe.inicio`, `Hora inicio` y `Hora de confirmación` para calcular horas automáticamente

El turno se calcula automáticamente desde `Fecha confirmación`:

- `Turno noche`: 23:00 a 07:00
- `Turno día`: 07:00 a 15:00
- `Turno tarde`: 15:00 a 23:00

Las jornadas capturadas se guardan en `data/productivity_history.csv`. En Streamlit Community Cloud el almacenamiento local puede reiniciarse; usa **Descargar histórico CSV** para respaldar la información y volver a cargarla cuando sea necesario.
