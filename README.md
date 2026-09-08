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

La actividad se clasifica como `Picking` cuando `Cola` comienza con `PICK`, y como
`Extracciones` cuando el tipo de proceso contiene salida o extracción. El peso de
`Peso Carga` se convierte de kilogramos a toneladas.

## Configurar Supabase

1. Ejecuta `supabase_schema.sql` en el SQL Editor de Supabase.
2. En Streamlit Cloud agrega estos secrets:

```toml
SUPABASE_URL = "https://tu-proyecto.supabase.co"
SUPABASE_SERVICE_KEY = "tu-service-role-key"
```

Con los secrets configurados, cada CSV se guarda en Supabase. El hash del archivo
evita insertar dos veces el mismo CSV. La service-role key debe mantenerse
únicamente en Streamlit Secrets y nunca publicarse en GitHub.

Las jornadas capturadas se guardan en `data/productivity_history.csv`. En Streamlit Community Cloud el almacenamiento local puede reiniciarse; usa **Descargar histórico CSV** para respaldar la información y volver a cargarla cuando sea necesario.
