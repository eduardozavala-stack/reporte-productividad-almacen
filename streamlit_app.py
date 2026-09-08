from pathlib import Path
import io
import unicodedata

import pandas as pd
import plotly.express as px
import streamlit as st


APP_DIR = Path(__file__).parent
HISTORY_PATH = APP_DIR / "data" / "productivity_history.csv"
SOURCE_PATH = APP_DIR / "PRUEBA GITHUB.xlsx"
HISTORY_COLUMNS = [
    "fecha",
    "operador",
    "turno",
    "lineas_preparadas",
    "unidades_preparadas",
    "horas_productivas",
    "incidencias",
    "meta_lineas_hora",
]


def normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    return "".join(char for char in text if not unicodedata.combining(char)).lower().strip()


def find_column(columns: list[str], aliases: list[str]) -> str | None:
    normalized_columns = {normalized(column): column for column in columns}
    for alias in aliases:
        if normalized(alias) in normalized_columns:
            return normalized_columns[normalized(alias)]
    for column in columns:
        if any(normalized(alias) in normalized(column) for alias in aliases):
            return column
    return None


def empty_history() -> pd.DataFrame:
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def load_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        return empty_history()
    history = pd.read_csv(HISTORY_PATH)
    for column in HISTORY_COLUMNS:
        if column not in history:
            history[column] = 0
    history["fecha"] = pd.to_datetime(history["fecha"], errors="coerce").dt.date
    for column in [
        "lineas_preparadas",
        "unidades_preparadas",
        "horas_productivas",
        "incidencias",
        "meta_lineas_hora",
    ]:
        history[column] = pd.to_numeric(history[column], errors="coerce").fillna(0)
    return history[HISTORY_COLUMNS].dropna(subset=["fecha"])


def save_history(history: pd.DataFrame) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(HISTORY_PATH, index=False, date_format="%Y-%m-%d")


def load_initial_history() -> pd.DataFrame:
    history = load_history()
    if not history.empty or not SOURCE_PATH.exists():
        return history
    imported, _ = aggregate_source(read_excel(SOURCE_PATH.read_bytes()))
    if not imported.empty:
        save_history(imported)
    return imported


def read_excel(file_content: bytes) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(file_content))


def aggregate_source(source: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    date_col = find_column(list(source.columns), ["Fecha confirmación", "Fecha"])
    operator_col = find_column(list(source.columns), ["Confirmado por", "Operador", "Usuario"])
    quantity_col = find_column(
        list(source.columns),
        ["Ctd.prev.proced.UMA", "Unidades preparadas", "Cantidad"],
    )
    start_date_col = find_column(list(source.columns), ["Fe.inicio", "Fecha inicio"])
    start_time_col = find_column(list(source.columns), ["Hora inicio", "Hora de inicio"])
    end_time_col = find_column(
        list(source.columns),
        ["Hora de confirmación", "Hora confirmación", "Hora fin"],
    )
    if not date_col or not operator_col:
        raise ValueError("El archivo debe incluir columnas de fecha y operador.")

    warnings = []
    prepared = pd.DataFrame()
    prepared["fecha"] = pd.to_datetime(source[date_col], errors="coerce").dt.date
    prepared["operador"] = source[operator_col].fillna("Sin asignar").astype(str).replace("nan", "Sin asignar")
    prepared["lineas_preparadas"] = 1
    prepared["unidades_preparadas"] = (
        pd.to_numeric(source[quantity_col], errors="coerce").fillna(0)
        if quantity_col
        else 0
    )
    if not quantity_col:
        warnings.append("No se encontró cantidad; las unidades preparadas quedaron en cero.")

    if start_time_col and end_time_col:
        if start_date_col:
            start = pd.to_datetime(
                source[start_date_col].astype(str) + " " + source[start_time_col].astype(str),
                errors="coerce",
            )
        else:
            start = pd.to_datetime(source[start_time_col], errors="coerce")
        end = pd.to_datetime(
            source[date_col].astype(str) + " " + source[end_time_col].astype(str),
            errors="coerce",
        )
        minutes = (end - start).dt.total_seconds().div(60)
        minutes = minutes.where(minutes >= 0)
        prepared["horas_productivas"] = minutes.fillna(0).div(60)
        if prepared["horas_productivas"].sum() == 0:
            warnings.append("No se pudieron calcular horas con las columnas de inicio y confirmación.")
    else:
        prepared["horas_productivas"] = 0
        warnings.append("Faltan horas de inicio/confirmación; registre horas en el formulario diario.")

    grouped = (
        prepared.dropna(subset=["fecha"])
        .groupby(["fecha", "operador"], as_index=False)
        .agg(
            lineas_preparadas=("lineas_preparadas", "sum"),
            unidades_preparadas=("unidades_preparadas", "sum"),
            horas_productivas=("horas_productivas", "sum"),
        )
    )
    grouped["turno"] = "Importado"
    grouped["incidencias"] = 0
    grouped["meta_lineas_hora"] = 20.0
    return grouped[HISTORY_COLUMNS], warnings


def productivity_metrics(history: pd.DataFrame) -> dict[str, float]:
    hours = history["horas_productivas"].sum()
    lines = history["lineas_preparadas"].sum()
    return {
        "lines": lines,
        "units": history["unidades_preparadas"].sum(),
        "hours": hours,
        "rate": lines / hours if hours else 0,
        "incidents": history["incidencias"].sum(),
    }


st.set_page_config(
    page_title="Productividad de preparación",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
    .block-container {padding-top: 2rem; padding-bottom: 3rem;}
    [data-testid="stMetricValue"] {color: #0b6e4f;}
    .subtitle {color: #64748b; margin-top: -0.8rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📦 Productividad de preparación")
st.markdown(
    '<p class="subtitle">Reporte operativo para medir el desempeño del almacén y construir una historia diaria confiable.</p>',
    unsafe_allow_html=True,
)

if "history" not in st.session_state:
    try:
        st.session_state.history = load_initial_history()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        st.session_state.history = empty_history()
        st.warning(f"No se pudo cargar el archivo inicial: {exc}")

with st.sidebar:
    st.header("Filtros")
    history = st.session_state.history
    if history.empty:
        st.info("Aún no hay registros históricos.")
        min_date = max_date = pd.Timestamp.today().date()
    else:
        min_date = min(history["fecha"])
        max_date = max(history["fecha"])
    date_range = st.date_input("Periodo", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range
    operators = sorted(history["operador"].dropna().unique().tolist()) if not history.empty else []
    selected_operators = st.multiselect("Operador", operators, default=operators)

    st.divider()
    st.header("Cargar datos")
    uploaded = st.file_uploader("Excel histórico (.xlsx)", type=["xlsx"])
    if uploaded and st.button("Importar y guardar histórico", type="primary", use_container_width=True):
        try:
            imported, warnings = aggregate_source(read_excel(uploaded.getvalue()))
            st.session_state.history = pd.concat([history, imported], ignore_index=True)
            save_history(st.session_state.history)
            st.success(f"Se importaron {len(imported):,} registros agregados.")
            for warning in warnings:
                st.warning(warning)
        except (ValueError, KeyError, TypeError) as exc:
            st.error(f"No se pudo importar el archivo: {exc}")

    if HISTORY_PATH.exists():
        st.download_button(
            "Descargar histórico CSV",
            data=HISTORY_PATH.read_bytes(),
            file_name="productividad_historica.csv",
            mime="text/csv",
            use_container_width=True,
        )

filtered = st.session_state.history.copy()
if not filtered.empty:
    filtered = filtered[
        (filtered["fecha"] >= start_date)
        & (filtered["fecha"] <= end_date)
        & filtered["operador"].isin(selected_operators)
    ]

metrics = productivity_metrics(filtered) if not filtered.empty else {
    "lines": 0, "units": 0, "hours": 0, "rate": 0, "incidents": 0
}
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Líneas preparadas", f"{metrics['lines']:,.0f}")
col2.metric("Unidades", f"{metrics['units']:,.0f}")
col3.metric("Horas productivas", f"{metrics['hours']:,.1f}")
col4.metric("Líneas / hora", f"{metrics['rate']:,.1f}")
col5.metric("Incidencias", f"{metrics['incidents']:,.0f}")

tab_report, tab_capture, tab_data = st.tabs(["📊 Reporte", "➕ Registrar jornada", "🗃️ Histórico"])

with tab_report:
    if filtered.empty:
        st.info("Carga un Excel o registra una jornada para comenzar el reporte.")
    else:
        daily = (
            filtered.groupby("fecha", as_index=False)
            .agg(
                lineas_preparadas=("lineas_preparadas", "sum"),
                unidades_preparadas=("unidades_preparadas", "sum"),
                horas_productivas=("horas_productivas", "sum"),
            )
        )
        daily["lineas_hora"] = daily["lineas_preparadas"].div(daily["horas_productivas"].replace(0, pd.NA))
        chart = px.line(
            daily,
            x="fecha",
            y="lineas_hora",
            markers=True,
            title="Evolución de productividad",
            labels={"fecha": "Fecha", "lineas_hora": "Líneas / hora"},
        )
        chart.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(chart, use_container_width=True)

        by_operator = (
            filtered.groupby("operador", as_index=False)
            .agg(
                lineas=("lineas_preparadas", "sum"),
                unidades=("unidades_preparadas", "sum"),
                horas=("horas_productivas", "sum"),
                incidencias=("incidencias", "sum"),
            )
        )
        by_operator["lineas_hora"] = by_operator["lineas"].div(by_operator["horas"].replace(0, pd.NA))
        st.subheader("Desempeño por operador")
        st.dataframe(
            by_operator.sort_values("lineas_hora", ascending=False).style.format(
                {"lineas": "{:,.0f}", "unidades": "{:,.0f}", "horas": "{:,.1f}", "lineas_hora": "{:,.1f}"}
            ),
            use_container_width=True,
            hide_index=True,
        )

with tab_capture:
    st.subheader("Registrar jornada diaria")
    with st.form("daily_productivity_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        record_date = c1.date_input("Fecha", value=pd.Timestamp.today().date())
        operator = c2.text_input("Operador o equipo")
        shift = c3.selectbox("Turno", ["Mañana", "Tarde", "Noche", "Importado"])
        c4, c5, c6 = st.columns(3)
        lines = c4.number_input("Líneas preparadas", min_value=0, step=1)
        units = c5.number_input("Unidades preparadas", min_value=0, step=1)
        hours = c6.number_input("Horas productivas", min_value=0.0, step=0.25)
        c7, c8 = st.columns(2)
        incidents = c7.number_input("Incidencias", min_value=0, step=1)
        target = c8.number_input("Meta (líneas/hora)", min_value=0.0, value=20.0, step=1.0)
        submitted = st.form_submit_button("Guardar jornada", type="primary")
    if submitted:
        if not operator.strip():
            st.error("Ingresa un operador o equipo.")
        elif hours <= 0:
            st.error("Las horas productivas deben ser mayores a cero.")
        else:
            new_row = pd.DataFrame([{
                "fecha": record_date,
                "operador": operator.strip(),
                "turno": shift,
                "lineas_preparadas": lines,
                "unidades_preparadas": units,
                "horas_productivas": hours,
                "incidencias": incidents,
                "meta_lineas_hora": target,
            }])
            st.session_state.history = pd.concat([st.session_state.history, new_row], ignore_index=True)
            save_history(st.session_state.history)
            st.success("Jornada guardada en el histórico.")

with tab_data:
    st.subheader("Registros del periodo seleccionado")
    if filtered.empty:
        st.info("No hay registros para los filtros actuales.")
    else:
        st.dataframe(
            filtered.sort_values(["fecha", "operador"], ascending=[False, True]),
            use_container_width=True,
            hide_index=True,
        )
