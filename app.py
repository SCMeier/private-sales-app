import re
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Private Sales App", layout="wide")

st.title("Private Sales App")
st.write("Upload your Private Sales CSV file below, or use the built-in demo report.")

uploaded_file = st.file_uploader("Choose CSV file", type=["csv"])


def to_number(series):
    return pd.to_numeric(series, errors="coerce").fillna(0)


def get_paid_amount(event_name):
    match = re.search(r"PV\s*(\d+)", str(event_name).upper())
    if match:
        return float(match.group(1))
    return 0.0


def find_purchase_id_column(df):
    possible_cols = [
        "Purchase Id",
        "Purchase ID",
        "PurchaseID",
        "Purchase_Id",
        "PurchaseID#",
        "Purchase ID#",
        "Purchase Id #",
        "Purchase No",
        "Purchase Number",
        "Purchase",
        "Id",
        "ID",
    ]
    for col in possible_cols:
        if col in df.columns:
            return col
    return None


def df_to_csv_download(df):
    return df.to_csv(index=False).encode("utf-8")


def load_data():
    if uploaded_file is not None:
        df_local = pd.read_csv(uploaded_file)
        source_name = uploaded_file.name
        source_type = "uploaded"
        return df_local, source_name, source_type

    demo_path = Path("demo.csv")
    if demo_path.exists():
        df_local = pd.read_csv(demo_path)
        source_name = "demo.csv"
        source_type = "demo"
        return df_local, source_name, source_type

    return None, None, None


df, source_name, source_type = load_data()

if df is not None:
    if source_type == "uploaded":
        st.success(f"File loaded successfully: {source_name}")
    else:
        st.info(f"Showing default report from {source_name}. Upload a CSV above to replace it.")

    st.subheader("Private Sales Dashboard")

    if "Event Name" not in df.columns:
        st.error("Your CSV is missing the 'Event Name' column.")
        st.stop()

    event_list = sorted(df["Event Name"].dropna().astype(str).unique().tolist())

    default_sales_date = ""
    if "Purchase Date" in df.columns:
        purchase_dates = (
            df.groupby("Event Name", dropna=False)["Purchase Date"]
            .agg(lambda s: str(s.dropna().iloc[0]) if len(s.dropna()) > 0 else "")
            .reset_index()
        )
        purchase_date_map = dict(zip(purchase_dates["Event Name"], purchase_dates["Purchase Date"]))
    else:
        purchase_date_map = {}

    default_event_ref = pd.DataFrame({
        "Event Name": event_list,
        "Broker": ["" for _ in event_list],
        "Broker Fee %": [5.0 for _ in event_list],
        "Account": ["Flipper" for _ in event_list],
        "Sales Date": [purchase_date_map.get(event, default_sales_date) for event in event_list],
    })

    if "event_ref_data" not in st.session_state:
        st.session_state.event_ref_data = default_event_ref.copy()
    else:
        existing = st.session_state.event_ref_data.copy()
        existing_events = set(existing["Event Name"].astype(str))
        missing_events = [e for e in event_list if e not in existing_events]

        if missing_events:
            add_df = pd.DataFrame({
                "Event Name": missing_events,
                "Broker": ["" for _ in missing_events],
                "Broker Fee %": [5.0 for _ in missing_events],
                "Account": ["Flipper" for _ in missing_events],
                "Sales Date": [purchase_date_map.get(event, default_sales_date) for event in missing_events],
            })
            st.session_state.event_ref_data = pd.concat(
                [existing, add_df],
                ignore_index=True
            )

        current_events = set(event_list)
        st.session_state.event_ref_data = st.session_state.event_ref_data[
            st.session_state.event_ref_data["Event Name"].astype(str).isin(current_events)
        ].reset_index(drop=True)

    st.markdown("### Event Reference")

    event_ref_for_edit = st.session_state.event_ref_data.copy()
    event_ref_for_edit["Broker Fee %"] = pd.to_numeric(
        event_ref_for_edit["Broker Fee %"], errors="coerce"
    ).fillna(5.0)

    edited_event_ref = st.data_editor(
        event_ref_for_edit,
        use_container_width=True,
        num_rows="fixed",
        key="event_ref_editor",
        column_config={
            "Event Name": st.column_config.TextColumn(
                "Event Name",
                disabled=True
            ),
            "Broker": st.column_config.TextColumn("Broker"),
            "Broker Fee %": st.column_config.NumberColumn(
                "Broker Fee %",
                min_value=0.0,
                max_value=100.0,
                step=0.25,
                format="%.2f"
            ),
            "Account": st.column_config.SelectboxColumn(
                "Account",
                options=["Flipper", "Broker"]
            ),
            "Sales Date": st.column_config.TextColumn("Sales Date"),
        }
    )

    st.session_state.event_ref_data = edited_event_ref.copy()

    working = df.copy()

    for col in ["Quantity", "BoxOffice", "Overs", "Flipper Fee", "UF Ticket Fee"]:
        if col in working.columns:
            working[col] = to_number(working[col])
        else:
            working[col] = 0

    if "Order No" not in working.columns:
        working["Order No"] = ""

    if "Flipper Name" not in working.columns:
        working["Flipper Name"] = ""

    working["Paid Per Ticket"] = working["Event Name"].apply(get_paid_amount)
    working["Total Paid to Flipper"] = working["Quantity"] * working["Paid Per Ticket"]

    event_summary = (
        working.groupby("Event Name", dropna=False)
        .agg(
            Orders=("Order No", "nunique"),
            Quantity=("Quantity", "sum"),
            Total_BoxOffice=("BoxOffice", "sum"),
            Overs=("Overs", "sum"),
            Flipper_Fees=("Flipper Fee", "sum"),
            UF_Ticket_Fee=("UF Ticket Fee", "sum"),
        )
        .reset_index()
    )

    event_summary = event_summary.merge(
        st.session_state.event_ref_data,
        on="Event Name",
        how="left"
    )

    event_summary["Broker Fee %"] = pd.to_numeric(
        event_summary["Broker Fee %"], errors="coerce"
    ).fillna(5.0)

    event_summary["Broker Fees"] = (
        event_summary["Total_BoxOffice"] * event_summary["Broker Fee %"] / 100
    )

    event_summary["Total Company Profit"] = (
        event_summary["Broker Fees"]
        + event_summary["Overs"]
        - event_summary["Flipper_Fees"]
    )

    event_summary = event_summary.rename(columns={
        "Total_BoxOffice": "Total BoxOffice",
        "Flipper_Fees": "Flipper Fees",
        "UF_Ticket_Fee": "UF Ticket Fee",
    })

    event_summary_display = event_summary[
        [
            "Event Name",
            "Broker",
            "Broker Fee %",
            "Account",
            "Sales Date",
            "Orders",
            "Quantity",
            "Total BoxOffice",
            "Broker Fees",
            "Overs",
            "Flipper Fees",
            "UF Ticket Fee",
            "Total Company Profit",
        ]
    ].copy()

    totals_row = pd.DataFrame([{
        "Event Name": "GRAND TOTAL",
        "Broker": "",
        "Broker Fee %": "",
        "Account": "",
        "Sales Date": "",
        "Orders": event_summary_display["Orders"].sum(),
        "Quantity": event_summary_display["Quantity"].sum(),
        "Total BoxOffice": event_summary_display["Total BoxOffice"].sum(),
        "Broker Fees": event_summary_display["Broker Fees"].sum(),
        "Overs": event_summary_display["Overs"].sum(),
        "Flipper Fees": event_summary_display["Flipper Fees"].sum(),
        "UF Ticket Fee": event_summary_display["UF Ticket Fee"].sum(),
        "Total Company Profit": event_summary_display["Total Company Profit"].sum(),
    }])

    event_summary_download = pd.concat(
        [event_summary_display, totals_row],
        ignore_index=True
    )

    flipper_summary = (
        working.groupby(["Flipper Name", "Event Name"], dropna=False)
        .agg(
            Quantity=("Quantity", "sum"),
            Paid_Per_Ticket=("Paid Per Ticket", "max"),
            Total_Paid_To_Flipper=("Total Paid to Flipper", "sum"),
        )
        .reset_index()
    )

    flipper_summary = flipper_summary.rename(columns={
        "Paid_Per_Ticket": "Paid Per Ticket",
        "Total_Paid_To_Flipper": "Total Paid to Flipper",
    })

    purchase_id_col = find_purchase_id_column(working)
    po_summary = pd.DataFrame()

    if purchase_id_col is not None:
        po_working = working.copy()
        po_working[purchase_id_col] = po_working[purchase_id_col].fillna("").astype(str).str.strip()
        po_working = po_working[po_working[purchase_id_col] != ""]

        po_summary = (
            po_working.groupby(purchase_id_col, dropna=False)
            .agg(
                Event_Name=("Event Name", "first"),
                Flipper_Name=("Flipper Name", "first"),
                Amount_Paid=("Paid Per Ticket", "first"),
            )
            .reset_index()
        )

        po_summary = po_summary.rename(columns={
            purchase_id_col: "Purchase ID",
            "Event_Name": "Event Name",
            "Flipper_Name": "Flipper Name",
            "Amount_Paid": "Amount Paid",
        })

    detail_columns = []
    for col in [
        "Event Name",
        "Quantity",
        "BoxOffice",
        "Overs",
        "Flipper Fee",
        "UF Ticket Fee",
        "Flipper Name",
        "Order No",
        "Paid Per Ticket",
        "Total Paid to Flipper",
    ]:
        if col in working.columns:
            detail_columns.append(col)

    clean_detail = working[detail_columns].copy()

    clean_detail = clean_detail.merge(
        st.session_state.event_ref_data,
        on="Event Name",
        how="left"
    )

    if purchase_id_col is not None and purchase_id_col in working.columns:
        clean_detail[purchase_id_col] = working[purchase_id_col]

    clean_detail_columns_order = [
        "Event Name",
        "Broker",
        "Broker Fee %",
        "Account",
        "Sales Date",
        "Flipper Name",
        "Quantity",
        "Paid Per Ticket",
        "Total Paid to Flipper",
        "BoxOffice",
        "Overs",
        "Flipper Fee",
        "UF Ticket Fee",
        "Order No",
    ]

    if purchase_id_col is not None and purchase_id_col in clean_detail.columns:
        clean_detail_columns_order.append(purchase_id_col)

    clean_detail = clean_detail[
        [col for col in clean_detail_columns_order if col in clean_detail.columns]
    ].copy()

    st.markdown("### Dashboard Totals")
    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Total BoxOffice", f"{event_summary_display['Total BoxOffice'].sum():,.2f}")
    col2.metric("Broker Fees", f"{event_summary_display['Broker Fees'].sum():,.2f}")
    col3.metric("Overs", f"{event_summary_display['Overs'].sum():,.2f}")
    col4.metric("Flipper Fees", f"{event_summary_display['Flipper Fees'].sum():,.2f}")
    col5.metric("Company Profit", f"{event_summary_display['Total Company Profit'].sum():,.2f}")

    st.markdown("### Download Files")
    d1, d2, d3, d4 = st.columns(4)

    with d1:
        st.download_button(
            label="Download Event Reference",
            data=df_to_csv_download(st.session_state.event_ref_data),
            file_name="event_reference.csv",
            mime="text/csv"
        )

    with d2:
        st.download_button(
            label="Download Event Summary",
            data=df_to_csv_download(event_summary_download),
            file_name="event_summary.csv",
            mime="text/csv"
        )

    with d3:
        st.download_button(
            label="Download Flipper Summary",
            data=df_to_csv_download(flipper_summary),
            file_name="flipper_summary.csv",
            mime="text/csv"
        )

    with d4:
        if not po_summary.empty:
            st.download_button(
                label="Download PO Fee Summary",
                data=df_to_csv_download(po_summary),
                file_name="po_fee_summary.csv",
                mime="text/csv"
            )

    st.download_button(
        label="Download Clean Detail File",
        data=df_to_csv_download(clean_detail),
        file_name="detail_clean.csv",
        mime="text/csv"
    )

    st.markdown("### Event Summary")
    st.dataframe(
        event_summary_download,
        use_container_width=True
    )

    st.markdown("### Flipper Summary")
    st.dataframe(
        flipper_summary[
            [
                "Flipper Name",
                "Event Name",
                "Quantity",
                "Paid Per Ticket",
                "Total Paid to Flipper",
            ]
        ],
        use_container_width=True
    )

    st.markdown("### PO Fee Summary")
    if not po_summary.empty:
        st.dataframe(
            po_summary[
                [
                    "Purchase ID",
                    "Event Name",
                    "Flipper Name",
                    "Amount Paid",
                ]
            ],
            use_container_width=True
        )
    else:
        st.warning("No Purchase ID column was found in this CSV.")

    show_raw = st.checkbox("Show Raw Data", value=False)

    if show_raw:
        st.markdown("### Raw Data")
        st.dataframe(df, use_container_width=True)

else:
    st.warning("No uploaded file or demo.csv was found.")
