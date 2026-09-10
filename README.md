# Reporte de productividad de preparación

Aplicación Streamlit local para medir la productividad del almacén, registrar jornadas y consultar históricos.

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

Solo se consideran registros con `Tp.almacén destino` igual a `9025`. La actividad
se clasifica como `Picking` cuando `Cola` comienza con `PICK`, como `Extracciones`
cuando comienza con `SALIDA`, y como `Otros` para el resto de colas. El peso de
`Peso Carga` se convierte de kilogramos a toneladas; por eso el total mensual
incluye todas las actividades del destino 9025.

## Persistencia de esta versión de prueba

Esta versión no utiliza SQL ni Supabase. Los registros se guardan en
`data/productivity_history.csv`. Si se vuelve a cargar el mismo archivo, se
reemplaza su lote anterior para evitar duplicados. Usa **Descargar histórico CSV**
para respaldar la información y **Reiniciar todo el historial** para comenzar una
nueva prueba.

En Streamlit Community Cloud el almacenamiento local puede reiniciarse al
redeployar; esta versión es para validar el cálculo y la experiencia antes de
activar una base de datos persistente.
