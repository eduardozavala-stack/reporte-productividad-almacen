from pathlib import Path
import io
import unicodedata

import pandas as pd
import plotly.express as px
import streamlit as st


APP_DIR = Path(__file__).parent
HISTORY_PATH = APP_DIR / "data" / "productivity_history.csv"
HISTORY_COLUMNS = [
    "fecha",
    "operador",
    "turno",
    "actividad",
    "hora",
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


def normalize_history(history: pd.DataFrame) -> pd.DataFrame:
    history = history.copy()
    for column in HISTORY_COLUMNS:
        if column not in history:
            history[column] = "Sin asignar" if column in {"actividad", "batch_id"} else 0
    history["fecha"] = pd.to_datetime(history["fecha"], errors="coerce").dt.date
    numeric = ["hora", "lineas_preparadas", "toneladas_preparadas", "horas_productivas", "incidencias", "meta_lineas_hora"]
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
            parsed = pd.read_csv(
                io.BytesIO(file_content),
                sep=None,
                engine="python",
                encoding=encoding,
            )
            if any("\ufffd" in str(column) for column in parsed.columns):
                continue
            return parsed
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
    queue_col = find_column(columns, ["Cola"])
    queue = source[queue_col].fillna("").astype(str).str.upper() if queue_col else pd.Series("", index=source.index)
    return pd.Series("Otros", index=source.index).mask(
        queue.str.startswith("PICK"), "Picking"
    ).mask(
        queue.str.startswith("SALIDA"), "Extracciones"
    )


def aggregate_source(source: pd.DataFrame, batch_id: str) -> tuple[pd.DataFrame, list[str]]:
    date_col = find_column(list(source.columns), ["Fecha confirmación", "Fecha"])
    operator_col = find_column(list(source.columns), ["Confirmado por", "Operador", "Usuario"])
    weight_col = find_column(list(source.columns), ["Peso de carga", "Peso Carga", "Peso"])
    destination_col = find_column(
        list(source.columns),
        ["Tp.almacén destino", "Tipo almacén destino", "Tp almacen destino"],
    )
    start_date_col = find_column(list(source.columns), ["Fe.inicio", "Fecha inicio"])
    start_time_col = find_column(list(source.columns), ["Hora inicio", "Hora de inicio"])
    end_time_col = find_column(
        list(source.columns),
        ["Hora de confirmación", "Hora confirmación", "Hora fin"],
    )
    if not date_col or not operator_col or not weight_col or not destination_col:
        raise ValueError(
            "El archivo debe incluir Fecha confirmación, Confirmado por, "
            "Peso de carga y Tp.almacén destino."
        )

    warnings = []
    prepared = pd.DataFrame()
    confirmation_date = pd.to_datetime(source[date_col], errors="coerce", format="mixed")
    confirmation_time = (
        pd.to_datetime(source[end_time_col], errors="coerce", format="mixed")
        if end_time_col
        else confirmation_date
    )
    prepared["fecha"] = confirmation_date.dt.date
    prepared["hora"] = confirmation_time.dt.hour
    prepared["turno"] = shift_from_datetime(confirmation_time)
    prepared["actividad"] = classify_activity(source, list(source.columns))
    prepared["batch_id"] = batch_id
    prepared["operador"] = source[operator_col].fillna("Sin asignar").astype(str).replace("nan", "Sin asignar")
    prepared["lineas_preparadas"] = 1
    prepared["toneladas_preparadas"] = (
        pd.to_numeric(source[weight_col], errors="coerce").fillna(0).div(1000)
        if weight_col
        else 0
    )
    destination = pd.to_numeric(source[destination_col], errors="coerce")
    prepared = prepared[destination.eq(9025).to_numpy()]

    if start_time_col and end_time_col:
        if start_date_col:
            start = pd.to_datetime(
                source[start_date_col].astype(str) + " " + source[start_time_col].astype(str),
                errors="coerce",
            )
        else:
            start = pd.to_datetime(source[start_time_col], errors="coerce")
        end = pd.to_datetime(
            confirmation_date.dt.strftime("%Y-%m-%d") + " " + source[end_time_col].astype(str),
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
        .groupby(["fecha", "operador", "turno", "actividad", "hora", "batch_id"], as_index=False)
        .agg(
            lineas_preparadas=("lineas_preparadas", "sum"),
            toneladas_preparadas=("toneladas_preparadas", "sum"),
            horas_productivas=("horas_productivas", "sum"),
        )
    )
    grouped["incidencias"] = 0
    grouped["meta_lineas_hora"] = grouped["actividad"].map(
        {"Picking": 1.2, "Extracciones": 10.0}
    ).fillna(0)
    return grouped[HISTORY_COLUMNS], warnings


def productivity_metrics(history: pd.DataFrame) -> dict[str, float]:
    hours = history["horas_productivas"].sum()
    lines = history["lineas_preparadas"].sum()
    return {
        "lines": lines,
        "tons": history["toneladas_preparadas"].sum(),
        "hours": hours,
        "rate": lines / hours if hours else 0,
        "tons_rate": history["toneladas_preparadas"].sum() / hours if hours else 0,
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
    .block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 1500px;}
    [data-testid="stMetricValue"] {color: #172033; font-size: 1.55rem;}
    [data-testid="stMetric"] {background: #ffffff; border: 1px solid #e4e8ef; border-radius: 12px; padding: .7rem .9rem;}
    .subtitle {color: #64748b; margin-top: -0.8rem;}
    .dashboard-header {background: linear-gradient(90deg, #9f1017, #d52b2f); color: white; padding: .7rem 1rem; border-radius: 10px; margin-bottom: 1rem;}
    .dashboard-header h1 {font-size: 1.15rem; margin: 0; color: white;}
    .section-title {font-weight: 700; color: #243047; margin: .8rem 0 .2rem;}
    .small-note {font-size: .78rem; color: #64748b;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="dashboard-header"><h1>📦 REPORTE DE PRODUCTIVIDAD · PREPARACIÓN DE ALMACÉN</h1></div>',
    unsafe_allow_html=True,
)
st.markdown('<p class="subtitle">Control operativo de toneladas preparadas, picking y extracciones.</p>', unsafe_allow_html=True)

if "history" not in st.session_state:
    try:
        st.session_state.history = load_history()
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
            import hashlib
            batch_id = hashlib.sha256(uploaded.getvalue()).hexdigest()
            imported, warnings = aggregate_source(read_csv(uploaded.getvalue()), batch_id)
            existing = history[history["batch_id"].ne(batch_id)]
            st.session_state.history = pd.concat([existing, imported], ignore_index=True)
            save_history(st.session_state.history)
            st.success(f"Se guardaron {len(imported):,} registros en el histórico local.")
            for warning in warnings:
                st.warning(warning)
        except (ValueError, KeyError, TypeError) as exc:
            st.error(f"No se pudo importar el archivo: {exc}")

    st.divider()
    st.header("Administración")
    reset_confirmed = st.checkbox("Confirmo que deseo borrar todo el histórico", key="reset_confirmed")
    if st.button("Reiniciar todo el historial", use_container_width=True, disabled=not reset_confirmed):
        try:
            save_history(empty_history())
            st.session_state.history = empty_history()
            st.success("Histórico local eliminado correctamente.")
            st.session_state.reset_confirmed = False
            st.rerun()
        except (ValueError, KeyError, TypeError, OSError) as exc:
            st.error(f"No se pudo reiniciar el histórico: {exc}")

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
    "lines": 0, "tons": 0, "hours": 0, "rate": 0, "tons_rate": 0, "incidents": 0
}
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Toneladas preparadas", f"{metrics['tons']:,.2f} TN")
col2.metric("Picking", f"{filtered.loc[filtered['actividad'].eq('Picking'), 'toneladas_preparadas'].sum():,.2f} TN" if not filtered.empty else "0.00 TN")
col3.metric("Extracciones", f"{filtered.loc[filtered['actividad'].eq('Extracciones'), 'toneladas_preparadas'].sum():,.2f} TN" if not filtered.empty else "0.00 TN")
col4.metric("Toneladas / hora", f"{metrics['tons_rate']:,.2f}")
col5.metric("Horas productivas", f"{metrics['hours']:,.1f}")

tab_report, tab_capture, tab_data = st.tabs(["📊 Reporte", "➕ Registrar jornada", "🗃️ Histórico"])

with tab_report:
    if filtered.empty:
        st.info("Carga un CSV o registra una jornada para comenzar el reporte.")
    else:
        st.markdown('<div class="section-title">Resumen ejecutivo</div>', unsafe_allow_html=True)
        st.caption("El total considera todas las filas con Tp. almacén destino igual a 9025.")
        st.metric("Preparación acumulada", f"{metrics['tons']:,.2f} TN")

        daily = filtered.groupby(["fecha", "actividad"], as_index=False)["toneladas_preparadas"].sum()
        chart = px.bar(daily, x="fecha", y="toneladas_preparadas", color="actividad", barmode="stack",
                       title="Preparación diaria por actividad", labels={"fecha": "Fecha", "toneladas_preparadas": "Toneladas", "actividad": "Actividad"},
                       color_discrete_map={"Picking": "#2ca25f", "Extracciones": "#d52b2f", "Otros": "#68748a"})
        chart.update_layout(height=330, margin=dict(l=10, r=10, t=50, b=10), legend_title_text="")
        st.plotly_chart(chart, use_container_width=True)

        weekly = filtered.assign(semana=filtered["fecha"].apply(lambda value: f"{value.isocalendar().year}-S{value.isocalendar().week:02d}")).groupby(["semana", "actividad"], as_index=False)["toneladas_preparadas"].sum()
        monthly = filtered.assign(mes=filtered["fecha"].apply(lambda value: value.strftime("%Y-%m"))).groupby(["mes", "actividad"], as_index=False)["toneladas_preparadas"].sum()
        by_shift = filtered.groupby(["turno", "actividad"], as_index=False)["toneladas_preparadas"].sum()
        b1, b2 = st.columns(2)
        for container, data, x, title in [(b1, weekly, "semana", "Preparación por semana"), (b2, by_shift, "turno", "Preparación por turno")]:
            fig = px.bar(data, x=x, y="toneladas_preparadas", color="actividad", barmode="stack", title=title,
                         labels={x: x.capitalize(), "toneladas_preparadas": "TN", "actividad": ""},
                         color_discrete_map={"Picking": "#2ca25f", "Extracciones": "#d52b2f", "Otros": "#68748a"})
            fig.update_layout(height=300, margin=dict(l=5, r=5, t=45, b=5), legend_title_text="")
            container.plotly_chart(fig, use_container_width=True)
        monthly_total = monthly.groupby("mes", as_index=False)["toneladas_preparadas"].sum()
        monthly_chart = px.bar(
            monthly_total,
            x="mes",
            y="toneladas_preparadas",
            text_auto=".2f",
            title="Preparación mensual · destino 9025",
            labels={"mes": "Mes", "toneladas_preparadas": "Toneladas"},
            color_discrete_sequence=["#b71520"],
        )
        monthly_chart.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10), showlegend=False)
        st.plotly_chart(monthly_chart, use_container_width=True)

        st.markdown('<div class="section-title">Productividad Picking por hora de confirmación</div>', unsafe_allow_html=True)
        hourly = (
            filtered[filtered["actividad"].eq("Picking")]
            .groupby("hora", as_index=False)["toneladas_preparadas"]
            .sum()
        )
        hourly_activity = (
            hourly.set_index("hora")["toneladas_preparadas"]
            .reindex(range(24), fill_value=0)
            .rename_axis("hora")
            .reset_index()
        )
        hourly_table = hourly_activity.set_index("hora").T
        hourly_table.columns = [f"{hour:02d}" for hour in hourly_table.columns]
        hourly_table.index = ["Picking"]
        hourly_table["Total"] = hourly_table.sum(axis=1)
        figure = px.bar(
            hourly_activity,
            x="hora",
            y="toneladas_preparadas",
            title="Picking · meta 1.2 TN/h",
            labels={"hora": "Hora de confirmación", "toneladas_preparadas": "Toneladas"},
            color_discrete_sequence=["#2ca25f"],
        )
        figure.add_hline(y=1.2, line_dash="dash", line_color="#172033", annotation_text="Meta 1.2 TN/h")
        figure.update_layout(height=330, xaxis={"tickmode": "linear", "dtick": 1, "range": [-0.5, 23.5]}, margin=dict(l=5, r=5, t=50, b=5))
        st.plotly_chart(figure, use_container_width=True)
        st.dataframe(
            hourly_table.style.format("{:,.2f}"),
            use_container_width=True,
        )

        by_operator = (
            filtered.groupby("operador", as_index=False)
            .agg(
                lineas=("lineas_preparadas", "sum"),
                toneladas=("toneladas_preparadas", "sum"),
                horas=("horas_productivas", "sum"),
                incidencias=("incidencias", "sum"),
            )
        )
        by_operator["toneladas_hora"] = by_operator["toneladas"].div(by_operator["horas"].replace(0, pd.NA))
        st.markdown('<div class="section-title">Top operadores por toneladas</div>', unsafe_allow_html=True)
        ranking = by_operator.sort_values("toneladas", ascending=False).head(10)
        st.plotly_chart(px.bar(ranking, x="toneladas", y="operador", orientation="h", text_auto=".2f",
                               title="Ranking de preparación", labels={"toneladas": "Toneladas", "operador": ""}, color_discrete_sequence=["#b71520"]),
                        use_container_width=True)
        st.dataframe(
            by_operator.sort_values("toneladas", ascending=False).style.format(
                {"lineas": "{:,.0f}", "toneladas": "{:,.2f}", "horas": "{:,.1f}", "toneladas_hora": "{:,.2f}"}
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
