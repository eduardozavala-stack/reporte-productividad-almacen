from pathlib import Path
import hashlib
import io
import unicodedata

import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import Client, create_client


APP_DIR = Path(__file__).parent
HISTORY_PATH = APP_DIR / "data" / "productivity_history.csv"
HISTORY_COLUMNS = [
    "fecha",
    "operador",
    "turno",
    "actividad",
    "lineas_preparadas",
    "toneladas_preparadas",
    "horas_productivas",
    "incidencias",
    "meta_lineas_hora",
    "batch_id",
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


def supabase_client() -> Client | None:
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_SERVICE_KEY") or st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


def normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    history = history.copy()
    for column in HISTORY_COLUMNS:
        if column not in history:
            history[column] = "Sin asignar" if column in {"actividad", "batch_id"} else 0
    history["fecha"] = pd.to_datetime(history["fecha"], errors="coerce").dt.date
    numeric = ["lineas_preparadas", "toneladas_preparadas", "horas_productivas", "incidencias", "meta_lineas_hora"]
    for column in numeric:
        history[column] = pd.to_numeric(history[column], errors="coerce").fillna(0)
    history["actividad"] = history["actividad"].fillna("Sin clasificar").astype(str)
    history["turno"] = history["turno"].fillna("Sin clasificar").astype(str)
    history["operador"] = history["operador"].fillna("Sin asignar").astype(str)
    return history[HISTORY_COLUMNS].dropna(subset=["fecha"])


def load_history() -> pd.DataFrame:
    if not HISTORY_PATH.exists():
        return empty_history()
    return normalize_history(pd.read_csv(HISTORY_PATH))


def save_history(history: pd.DataFrame) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalize_history(history).to_csv(HISTORY_PATH, index=False, date_format="%Y-%m-%d")


def read_csv(file_content: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return pd.read_csv(
                io.BytesIO(file_content),
                sep=None,
                engine="python",
                encoding=encoding,
            )
        except UnicodeDecodeError:
            continue
    raise ValueError("No se pudo leer el CSV con las codificaciones UTF-8, CP1252 o Latin-1.")


def shift_from_datetime(values: pd.Series) -> pd.Series:
    hours = values.dt.hour
    return pd.Series(
        pd.NA,
        index=values.index,
        dtype="string",
    ).mask((hours >= 23) | (hours < 7), "Turno noche") \
        .mask((hours >= 7) & (hours < 15), "Turno día") \
        .mask((hours >= 15) & (hours < 23), "Turno tarde")


def classify_activity(source: pd.DataFrame, columns: list[str]) -> pd.Series:
    queue_col = find_column(columns, ["Cola", "Recurso de origen"])
    process_col = find_column(columns, ["Descr.tipo proceso almacén", "Tipo proceso almacén"])
    queue = source[queue_col].fillna("").astype(str).str.upper() if queue_col else pd.Series("", index=source.index)
    process = source[process_col].fillna("").astype(str).str.lower() if process_col else pd.Series("", index=source.index)
    return pd.Series("Otros", index=source.index).mask(
        queue.str.startswith("PICK"), "Picking"
    ).mask(
        process.str.contains("salida|extracci", regex=True), "Extracciones"
    )


def aggregate_source(source: pd.DataFrame, batch_id: str) -> tuple[pd.DataFrame, list[str]]:
    date_col = find_column(list(source.columns), ["Fecha confirmación", "Fecha"])
    operator_col = find_column(list(source.columns), ["Confirmado por", "Operador", "Usuario"])
    quantity_col = find_column(
        list(source.columns),
        ["Ctd.prev.proced.UMA", "Unidades preparadas", "Cantidad"],
    )
    weight_col = find_column(list(source.columns), ["Peso de carga", "Peso Carga", "Peso"])
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
    confirmation_datetime = pd.to_datetime(source[date_col], errors="coerce", format="mixed")
    prepared["fecha"] = confirmation_datetime.dt.date
    prepared["turno"] = shift_from_datetime(confirmation_datetime)
    prepared["actividad"] = classify_activity(source, list(source.columns))
    prepared["batch_id"] = batch_id
    prepared["operador"] = source[operator_col].fillna("Sin asignar").astype(str).replace("nan", "Sin asignar")
    prepared["lineas_preparadas"] = 1
    prepared["toneladas_preparadas"] = (
        pd.to_numeric(source[weight_col], errors="coerce").fillna(0).div(1000)
        if weight_col
        else 0
    )
    if not weight_col:
        warnings.append("No se encontró Peso Carga; las toneladas quedaron en cero.")

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
            format="mixed",
        )
        end = end.where(end >= start, end + pd.Timedelta(days=1))
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
        .groupby(["fecha", "operador", "turno", "actividad", "batch_id"], as_index=False)
        .agg(
            lineas_preparadas=("lineas_preparadas", "sum"),
            toneladas_preparadas=("toneladas_preparadas", "sum"),
            horas_productivas=("horas_productivas", "sum"),
        )
    )
    grouped["incidencias"] = 0
    grouped["meta_lineas_hora"] = 20.0
    return grouped[HISTORY_COLUMNS], warnings


def load_remote_history(client: Client) -> pd.DataFrame:
    response = client.table("productivity_records").select("*").execute()
    return normalize_history(pd.DataFrame(response.data)) if response.data else empty_history()


def append_remote_history(client: Client, records: pd.DataFrame) -> int:
    records = normalize_history(records)
    if records.empty:
        return 0
    payload = records.assign(fecha=records["fecha"].astype(str)).to_dict("records")
    response = client.table("productivity_records").upsert(
        payload,
        on_conflict="batch_id,fecha,operador,turno,actividad",
        ignore_duplicates=True,
    ).execute()
    return len(response.data or [])


def productivity_metrics(history: pd.DataFrame) -> dict[str, float]:
    hours = history["horas_productivas"].sum()
    lines = history["lineas_preparadas"].sum()
    return {
        "lines": lines,
        "tons": history["toneladas_preparadas"].sum(),
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
        remote = supabase_client()
        st.session_state.history = load_remote_history(remote) if remote else load_history()
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
    shifts = ["Turno noche", "Turno día", "Turno tarde"]
    selected_shifts = st.multiselect("Turno", shifts, default=shifts)
    activities = ["Picking", "Extracciones", "Otros"]
    selected_activities = st.multiselect("Actividad", activities, default=activities)

    st.divider()
    st.header("Cargar datos")
    uploaded = st.file_uploader("Archivo CSV histórico (.csv)", type=["csv"])
    if uploaded and st.button("Importar y guardar histórico", type="primary", use_container_width=True):
        try:
            batch_id = hashlib.sha256(uploaded.getvalue()).hexdigest()
            imported, warnings = aggregate_source(read_csv(uploaded.getvalue()), batch_id)
            remote = supabase_client()
            if remote:
                inserted = append_remote_history(remote, imported)
                st.session_state.history = load_remote_history(remote)
                st.success(f"Se guardaron {inserted:,} registros nuevos en Supabase.")
            else:
                st.session_state.history = pd.concat([history, imported], ignore_index=True)
                save_history(st.session_state.history)
                st.warning("Supabase no está configurado; se guardó localmente.")
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
        & filtered["turno"].isin(selected_shifts)
        & filtered["actividad"].isin(selected_activities)
    ]

metrics = productivity_metrics(filtered) if not filtered.empty else {
    "lines": 0, "tons": 0, "hours": 0, "rate": 0, "incidents": 0
}
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Toneladas preparadas", f"{metrics['tons']:,.2f}")
col2.metric("Líneas de referencia", f"{metrics['lines']:,.0f}")
col3.metric("Horas productivas", f"{metrics['hours']:,.1f}")
col4.metric("Líneas / hora", f"{metrics['rate']:,.1f}")
col5.metric("Incidencias", f"{metrics['incidents']:,.0f}")

tab_report, tab_capture, tab_data = st.tabs(["📊 Reporte", "➕ Registrar jornada", "🗃️ Histórico"])

with tab_report:
    if filtered.empty:
        st.info("Carga un CSV o registra una jornada para comenzar el reporte.")
    else:
        daily = (
            filtered.groupby("fecha", as_index=False)
            .agg(
                lineas_preparadas=("lineas_preparadas", "sum"),
                toneladas_preparadas=("toneladas_preparadas", "sum"),
                horas_productivas=("horas_productivas", "sum"),
            )
        )
        daily["toneladas_hora"] = daily["toneladas_preparadas"].div(daily["horas_productivas"].replace(0, pd.NA))
        chart = px.line(
            daily,
            x="fecha",
            y="toneladas_preparadas",
            markers=True,
            title="Evolución diaria de toneladas preparadas",
            labels={"fecha": "Fecha", "toneladas_preparadas": "Toneladas"},
        )
        chart.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(chart, use_container_width=True)

        weekly = filtered.assign(semana=filtered["fecha"].apply(lambda value: value.isocalendar().week)).groupby("semana", as_index=False)["toneladas_preparadas"].sum()
        monthly = filtered.assign(mes=filtered["fecha"].apply(lambda value: value.strftime("%Y-%m"))).groupby("mes", as_index=False)["toneladas_preparadas"].sum()
        by_shift = filtered.groupby("turno", as_index=False)["toneladas_preparadas"].sum()
        b1, b2, b3 = st.columns(3)
        b1.plotly_chart(px.bar(weekly, x="semana", y="toneladas_preparadas", title="Preparación por semana", labels={"semana": "Semana", "toneladas_preparadas": "Toneladas"}), use_container_width=True)
        b2.plotly_chart(px.bar(monthly, x="mes", y="toneladas_preparadas", title="Preparación por mes", labels={"mes": "Mes", "toneladas_preparadas": "Toneladas"}), use_container_width=True)
        b3.plotly_chart(px.bar(by_shift, x="turno", y="toneladas_preparadas", title="Preparación por turno", labels={"turno": "Turno", "toneladas_preparadas": "Toneladas"}), use_container_width=True)

        by_operator = (
            filtered.groupby("operador", as_index=False)
            .agg(
                lineas=("lineas_preparadas", "sum"),
                toneladas=("toneladas_preparadas", "sum"),
                horas=("horas_productivas", "sum"),
                incidencias=("incidencias", "sum"),
            )
        )
        by_operator["lineas_hora"] = by_operator["lineas"].div(by_operator["horas"].replace(0, pd.NA))
        st.subheader("Desempeño por operador")
        st.dataframe(
            by_operator.sort_values("lineas_hora", ascending=False).style.format(
                {"lineas": "{:,.0f}", "toneladas": "{:,.2f}", "horas": "{:,.1f}", "lineas_hora": "{:,.1f}"}
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
        shift = c3.selectbox("Turno", ["Turno noche", "Turno día", "Turno tarde"])
        c4, c5, c6 = st.columns(3)
        lines = c4.number_input("Líneas preparadas", min_value=0, step=1)
        tons = c5.number_input("Toneladas preparadas", min_value=0.0, step=0.01)
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
                "toneladas_preparadas": tons,
                "actividad": "Otros",
                "horas_productivas": hours,
                "incidencias": incidents,
                "meta_lineas_hora": target,
                "batch_id": f"manual-{record_date}-{operator.strip()}",
            }])
            remote = supabase_client()
            if remote:
                append_remote_history(remote, new_row)
                st.session_state.history = load_remote_history(remote)
            else:
                st.session_state.history = pd.concat([st.session_state.history, new_row], ignore_index=True)
                save_history(st.session_state.history)
                st.warning("Supabase no está configurado; se guardó localmente.")
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
