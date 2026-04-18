import io
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Private Sales App", layout="wide")

APP_TITLE = "Private Sales App"
SAVED_MONTHS_DIR = Path("saved_months")
BROKER_MAP_FILE = Path("broker_map.csv")

st.title(APP_TITLE)
st.write("Upload the full Uflip purchases CSV. The app will automatically keep only rows whose Event Name starts with PV.")


# =========================================================
# HELPERS
# =========================================================
def ensure_saved_months_dir():
    SAVED_MONTHS_DIR.mkdir(exist_ok=True)


def to_number(series):
    s = series.fillna("").astype(str).str.strip()
    s = s.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    s = s.str.replace("$", "", regex=False)
    s = s.str.replace(",", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def df_to_csv_download(df):
    return df.to_csv(index=False).encode("utf-8")


def sanitize_filename(text):
    text = str(text).strip()
    text = re.sub(r"[^\w\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "saved_file"


def get_paid_amount(event_name):
    match = re.search(r"PV\s*(\d+)", str(event_name).upper())
    if match:
        return float(match.group(1))
    return 0.0


def get_broker_code(event_name):
    """
    From values like:
    PV3 SHCL
    PV5 TTSEB-SEC Baseball Tournament

    Use the first 2 letters after PV# as broker code.
    """
    text = str(event_name).upper().strip()
    match = re.search(r"^PV\s*\d+\s*([A-Z]{2})", text)
    if match:
        return match.group(1)
    return ""


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


def load_broker_map():
    if not BROKER_MAP_FILE.exists():
        return pd.DataFrame(columns=["Broker Company", "Broker Code"])

    broker_map = pd.read_csv(BROKER_MAP_FILE, dtype=str).fillna("")
    broker_map.columns = [str(c).strip() for c in broker_map.columns]

    expected = {"Broker Company", "Broker Code"}
    if not expected.issubset(set(broker_map.columns)):
        return pd.DataFrame(columns=["Broker Company", "Broker Code"])

    broker_map["Broker Company"] = broker_map["Broker Company"].astype(str).str.strip()
    broker_map["Broker Code"] = broker_map["Broker Code"].astype(str).str.strip().str.upper()

    broker_map = broker_map[broker_map["Broker Code"] != ""].copy()
    broker_map = broker_map.drop_duplicates(subset=["Broker Code"], keep="first").reset_index(drop=True)
    return broker_map


def read_full_uplift_csv(file_obj):
    """
    The Uflip export can contain summary lines before the real detail table.
    We find the header row by looking for a row that contains Event Name.
    """
    raw = file_obj.read()

    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = str(raw)

    lines = text.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if "Event Name" in line and "," in line:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find the detail table header row containing 'Event Name'.")

    csv_text = "\n".join(lines[header_idx:])
    df = pd.read_csv(io.StringIO(csv_text), dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def filter_private_sales(df):
    if "Event Name" not in df.columns:
        raise ValueError("CSV is missing the 'Event Name' column.")

    working = df.copy()
    working["Event Name"] = working["Event Name"].fillna("").astype(str).str.strip()

    pv_df = working[working["Event Name"].str.upper().str.startswith("PV")].copy()
    return pv_df


def list_saved_month_files():
    ensure_saved_months_dir()
    files = sorted(SAVED_MONTHS_DIR.glob("*.csv"))
    return files


def save_month_file(uploaded_name, file_bytes, month_label):
    ensure_saved_months_dir()
    safe_month = sanitize_filename(month_label)
    safe_orig = sanitize_filename(Path(uploaded_name).stem)
    filename = f"{safe_month}__{safe_orig}.csv"
    save_path = SAVED_MONTHS_DIR / filename

    with open(save_path, "wb") as f:
        f.write(file_bytes)

    return save_path


def month_label_from_df(df):
    if "Purchased" in df.columns:
        purchased = df["Purchased"].fillna("").astype(str).str.strip()
        parsed = pd.to_datetime(purchased, errors="coerce")
        parsed = parsed.dropna()
        if not parsed.empty:
            return parsed.iloc[0].strftime("%Y-%m")
    return ""


# =========================================================
# LOAD DATA SOURCE
# =========================================================
ensure_saved_months_dir()
broker_map_df = load_broker_map()

source_choice = st.radio(
    "Choose source",
    ["Upload new monthly file", "Use a stored month"],
    horizontal=True
)

uploaded_file = None
selected_saved_path = None
raw_df = None
raw_source_name = None
raw_file_bytes = None

if source_choice == "Upload new monthly file":
    uploaded_file = st.file_uploader("Choose full purchases CSV", type=["csv"])

    if uploaded_file is not None:
        raw_file_bytes = uploaded_file.getvalue()
        raw_df = read_full_uplift_csv(io.BytesIO(raw_file_bytes))
        raw_source_name = uploaded_file.name

        default_month = month_label_from_df(raw_df)
        month_to_save = st.text_input(
            "Month label for saving this file",
            value=default_month if default_month else ""
        )

        col_save_1, col_save_2 = st.columns([1, 3])
        with col_save_1:
            if st.button("Save This Month File"):
                if month_to_save.strip() == "":
                    st.error("Enter a month label like 2026-04 before saving.")
                else:
                    saved_path = save_month_file(uploaded_file.name, raw_file_bytes, month_to_save.strip())
                    st.success(f"Saved monthly file: {saved_path.name}")

elif source_choice == "Use a stored month":
    saved_files = list_saved_month_files()

    if not saved_files:
        st.info("No saved month files found yet.")
    else:
        saved_labels = [f.name for f in saved_files]
        chosen_file = st.selectbox("Choose a stored month file", saved_labels)

        if chosen_file:
            selected_saved_path = SAVED_MONTHS_DIR / chosen_file
            with open(selected_saved_path, "rb") as f:
                raw_df = read_full_uplift_csv(f)
            raw_source_name = selected_saved_path.name

            col_load, col_delete = st.columns([3, 1])

            with col_load:
                st.success(f"Loaded stored month: {chosen_file}")

            with col_delete:
                delete_key = f"delete_{chosen_file}"
                if st.button("Delete This Month", key=delete_key):
                    try:
                        os.remove(selected_saved_path)
                        st.success(f"Deleted {chosen_file}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not delete file: {e}")


# =========================================================
# SHOW STORED MONTHS LIST
# =========================================================
with st.expander("Stored Months", expanded=False):
    saved_files = list_saved_month_files()
    if not saved_files:
        st.write("No stored month files yet.")
    else:
        stored_df = pd.DataFrame({
            "Saved File": [f.name for f in saved_files]
        })
        st.dataframe(stored_df, use_container_width=True)


# =========================================================
# PROCESS PRIVATE SALES
# =========================================================
if raw_df is not None:
    st.info(f"Source file: {raw_source_name}")

    pv_df = filter_private_sales(raw_df)

    if pv_df.empty:
        st.warning("No rows found where Event Name starts with PV.")
        st.stop()

    working = pv_df.copy()

    for col in ["Quantity", "BoxOffice", "Overs", "Flipper Fee", "UF Ticket Fee"]:
        if col in working.columns:
            working[col] = to_number(working[col])
        else:
            working[col] = 0.0

    if "Flipper Name" not in working.columns:
        working["Flipper Name"] = ""

    if "Order No" not in working.columns:
        working["Order No"] = ""

    if "Purchased" not in working.columns:
        working["Purchased"] = ""

    working["Event Name"] = working["Event Name"].fillna("").astype(str).str.strip()
    working["Purchased"] = working["Purchased"].fillna("").astype(str).str.strip()

    working["Paid Per Ticket"] = working["Event Name"].apply(get_paid_amount)
    working["Broker Code"] = working["Event Name"].apply(get_broker_code)
    working["Total Paid to Flipper"] = working["Quantity"] * working["Paid Per Ticket"]

    broker_lookup = {}
    if not broker_map_df.empty:
        broker_lookup = dict(
            zip(
                broker_map_df["Broker Code"].astype(str).str.upper(),
                broker_map_df["Broker Company"].astype(str).str.strip()
            )
        )

    working["Broker Company"] = working["Broker Code"].map(broker_lookup).fillna("")

    event_defaults = (
        working.groupby("Event Name", dropna=False)
        .agg(
            Broker_Code=("Broker Code", "first"),
            Broker_Company=("Broker Company", "first"),
            Sales_Date=("Purchased", "first"),
        )
        .reset_index()
        .rename(columns={
            "Broker_Code": "Broker Code",
            "Broker_Company": "Broker Company",
            "Sales_Date": "Sales Date",
        })
    )

    default_event_ref = event_defaults.copy()
    default_event_ref["Broker Fee %"] = 5.0
    default_event_ref["Account"] = "Flipper"

    default_event_ref = default_event_ref[
        [
            "Event Name",
            "Broker Code",
            "Broker Company",
            "Broker Fee %",
            "Account",
            "Sales Date",
        ]
    ].copy()

    event_names_now = set(default_event_ref["Event Name"].astype(str))

    if "event_ref_data" not in st.session_state:
        st.session_state.event_ref_data = default_event_ref.copy()
    else:
        current_ref = st.session_state.event_ref_data.copy()
        if "Event Name" in current_ref.columns:
            current_ref = current_ref[current_ref["Event Name"].astype(str).isin(event_names_now)].copy()
        else:
            current_ref = pd.DataFrame(columns=default_event_ref.columns)

        existing_events = set(current_ref["Event Name"].astype(str)) if not current_ref.empty else set()
        missing_events = [e for e in default_event_ref["Event Name"].astype(str) if e not in existing_events]

        if missing_events:
            add_rows = default_event_ref[default_event_ref["Event Name"].astype(str).isin(missing_events)].copy()
            current_ref = pd.concat([current_ref, add_rows], ignore_index=True)

        current_ref = current_ref.merge(
            default_event_ref[["Event Name", "Broker Code", "Broker Company", "Sales Date"]],
            on="Event Name",
            how="left",
            suffixes=("", "_new")
        )

        for col in ["Broker Code", "Broker Company", "Sales Date"]:
            new_col = f"{col}_new"
            if new_col in current_ref.columns:
                current_ref[col] = current_ref[col].replace("", pd.NA)
                current_ref[col] = current_ref[col].fillna(current_ref[new_col])
                current_ref[col] = current_ref[col].fillna("")
                current_ref.drop(columns=[new_col], inplace=True)

        if "Broker Fee %" not in current_ref.columns:
            current_ref["Broker Fee %"] = 5.0
        if "Account" not in current_ref.columns:
            current_ref["Account"] = "Flipper"

        st.session_state.event_ref_data = current_ref[
            [
                "Event Name",
                "Broker Code",
                "Broker Company",
                "Broker Fee %",
                "Account",
                "Sales Date",
            ]
        ].copy()

    st.subheader("Private Sales Dashboard")

    st.markdown("### Event Control")
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
            "Event Name": st.column_config.TextColumn("Event Name", disabled=True),
            "Broker Code": st.column_config.TextColumn("Broker Code", disabled=True),
            "Broker Company": st.column_config.TextColumn("Broker Company"),
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

    working = working.merge(
        st.session_state.event_ref_data,
        on="Event Name",
        how="left",
        suffixes=("", "_event")
    )

    event_summary = (
        working.groupby("Event Name", dropna=False)
        .agg(
            Broker_Code=("Broker Code_event", "first"),
            Broker_Company=("Broker Company_event", "first"),
            Broker_Fee_Pct=("Broker Fee %", "first"),
            Account=("Account", "first"),
            Sales_Date=("Sales Date", "first"),
            Orders=("Order No", "nunique"),
            Quantity=("Quantity", "sum"),
            Total_BoxOffice=("BoxOffice", "sum"),
            Overs=("Overs", "sum"),
            Flipper_Fees=("Flipper Fee", "sum"),
            UF_Ticket_Fee=("UF Ticket Fee", "sum"),
        )
        .reset_index()
    )

    event_summary["Broker_Fee_Pct"] = pd.to_numeric(
        event_summary["Broker_Fee_Pct"], errors="coerce"
    ).fillna(5.0)

    event_summary["Broker Fees"] = (
        event_summary["Total_BoxOffice"] * event_summary["Broker_Fee_Pct"] / 100.0
    )

    event_summary["Total Company Profit"] = (
        event_summary["Broker Fees"]
        + event_summary["Overs"]
        - event_summary["Flipper_Fees"]
    )

    event_summary = event_summary.rename(columns={
        "Broker_Code": "Broker Code",
        "Broker_Company": "Broker Company",
        "Broker_Fee_Pct": "Broker Fee %",
        "Sales_Date": "Sales Date",
        "Total_BoxOffice": "Total BoxOffice",
        "Flipper_Fees": "Flipper Fees",
        "UF_Ticket_Fee": "UF Ticket Fee",
    })

    event_summary_display = event_summary[
        [
            "Event Name",
            "Broker Code",
            "Broker Company",
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
        "Broker Code": "",
        "Broker Company": "",
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
            Broker_Code=("Broker Code_event", "first"),
            Broker_Company=("Broker Company_event", "first"),
            Quantity=("Quantity", "sum"),
            Paid_Per_Ticket=("Paid Per Ticket", "max"),
            Total_Paid_To_Flipper=("Total Paid to Flipper", "sum"),
        )
        .reset_index()
    )

    flipper_summary = flipper_summary.rename(columns={
        "Broker_Code": "Broker Code",
        "Broker_Company": "Broker Company",
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
                Broker_Code=("Broker Code_event", "first"),
                Broker_Company=("Broker Company_event", "first"),
                Flipper_Name=("Flipper Name", "first"),
                Amount_Paid=("Paid Per Ticket", "first"),
            )
            .reset_index()
        )

        po_summary = po_summary.rename(columns={
            purchase_id_col: "Purchase ID",
            "Event_Name": "Event Name",
            "Broker_Code": "Broker Code",
            "Broker_Company": "Broker Company",
            "Flipper_Name": "Flipper Name",
            "Amount_Paid": "Amount Paid",
        })

    # =========================================================
    # BROKER SUMMARY (FIXED)
    # =========================================================
    broker_summary = (
        working.groupby(["Broker Code_event", "Broker Company_event"], dropna=False)
        .agg(
            Events=("Event Name", "nunique"),
            Orders=("Order No", "nunique"),
            Quantity=("Quantity", "sum"),
            Total_BoxOffice=("BoxOffice", "sum"),
            Overs=("Overs", "sum"),
            Flipper_Fees=("Flipper Fee", "sum"),
            UF_Ticket_Fee=("UF Ticket Fee", "sum"),
        )
        .reset_index()
    )

    broker_fees_grouped = (
        event_summary_display.groupby(["Broker Code", "Broker Company"], dropna=False)
        .agg(
            Broker_Fees=("Broker Fees", "sum"),
            Total_Company_Profit=("Total Company Profit", "sum"),
        )
        .reset_index()
    )

    broker_summary = broker_summary.merge(
        broker_fees_grouped,
        left_on=["Broker Code_event", "Broker Company_event"],
        right_on=["Broker Code", "Broker Company"],
        how="left"
    )

    broker_summary = broker_summary.drop(columns=["Broker Code", "Broker Company"])

    broker_summary = broker_summary.rename(columns={
        "Broker Code_event": "Broker Code",
        "Broker Company_event": "Broker Company",
        "Total_BoxOffice": "Total BoxOffice",
        "Flipper_Fees": "Flipper Fees",
        "UF_Ticket_Fee": "UF Ticket Fee",
        "Broker_Fees": "Broker Fees",
        "Total_Company_Profit": "Total Company Profit",
    })

    broker_summary = broker_summary[
        [
            "Broker Code",
            "Broker Company",
            "Events",
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

    broker_summary = broker_summary.fillna("")

    detail_cols = []
    for col in [
        "Event Name",
        "Purchased",
        "Flipper Name",
        "Quantity",
        "BoxOffice",
        "Overs",
        "Flipper Fee",
        "UF Ticket Fee",
        "Order No",
        "Paid Per Ticket",
        "Total Paid to Flipper",
        "Broker Code_event",
        "Broker Company_event",
        "Broker Fee %",
        "Account",
        "Sales Date",
    ]:
        if col in working.columns:
            detail_cols.append(col)

    clean_detail = working[detail_cols].copy()
    clean_detail = clean_detail.rename(columns={
        "Broker Code_event": "Broker Code",
        "Broker Company_event": "Broker Company",
    })

    if purchase_id_col is not None and purchase_id_col in working.columns:
        clean_detail["Purchase ID"] = working[purchase_id_col]

    ordered_cols = [
        "Event Name",
        "Purchased",
        "Sales Date",
        "Broker Code",
        "Broker Company",
        "Broker Fee %",
        "Account",
        "Flipper Name",
        "Quantity",
        "Paid Per Ticket",
        "Total Paid to Flipper",
        "BoxOffice",
        "Overs",
        "Flipper Fee",
        "UF Ticket Fee",
        "Order No",
        "Purchase ID",
    ]
    clean_detail = clean_detail[[c for c in ordered_cols if c in clean_detail.columns]].copy()

    st.markdown("### Dashboard Totals")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total BoxOffice", f"{event_summary_display['Total BoxOffice'].sum():,.2f}")
    c2.metric("Broker Fees", f"{event_summary_display['Broker Fees'].sum():,.2f}")
    c3.metric("Overs", f"{event_summary_display['Overs'].sum():,.2f}")
    c4.metric("Flipper Fees", f"{event_summary_display['Flipper Fees'].sum():,.2f}")
    c5.metric("Company Profit", f"{event_summary_display['Total Company Profit'].sum():,.2f}")

    st.markdown("### Download Files")
    d1, d2, d3, d4, d5 = st.columns(5)

    with d1:
        st.download_button(
            "Download Event Control",
            data=df_to_csv_download(st.session_state.event_ref_data),
            file_name="event_control.csv",
            mime="text/csv"
        )

    with d2:
        st.download_button(
            "Download Event Summary",
            data=df_to_csv_download(event_summary_download),
            file_name="event_summary.csv",
            mime="text/csv"
        )

    with d3:
        st.download_button(
            "Download Flipper Summary",
            data=df_to_csv_download(flipper_summary),
            file_name="flipper_summary.csv",
            mime="text/csv"
        )

    with d4:
        if not po_summary.empty:
            st.download_button(
                "Download PO Summary",
                data=df_to_csv_download(po_summary),
                file_name="po_summary.csv",
                mime="text/csv"
            )

    with d5:
        st.download_button(
            "Download Broker Summary",
            data=df_to_csv_download(broker_summary),
            file_name="broker_summary.csv",
            mime="text/csv"
        )

    st.download_button(
        "Download Clean Detail File",
        data=df_to_csv_download(clean_detail),
        file_name="detail_clean.csv",
        mime="text/csv"
    )

    st.markdown("### Event Summary")
    st.dataframe(event_summary_download, use_container_width=True)

    st.markdown("### Flipper Summary")
    st.dataframe(
        flipper_summary[
            [
                "Flipper Name",
                "Event Name",
                "Broker Code",
                "Broker Company",
                "Quantity",
                "Paid Per Ticket",
                "Total Paid to Flipper",
            ]
        ],
        use_container_width=True
    )

    st.markdown("### PO Summary")
    if not po_summary.empty:
        po_cols = [c for c in ["Purchase ID", "Event Name", "Broker Code", "Broker Company", "Flipper Name", "Amount Paid"] if c in po_summary.columns]
        st.dataframe(po_summary[po_cols], use_container_width=True)
    else:
        st.warning("No Purchase ID column was found in this CSV.")

    st.markdown("### Broker Summary")
    st.dataframe(broker_summary, use_container_width=True)

    show_raw = st.checkbox("Show Raw PV Rows", value=False)
    if show_raw:
        st.markdown("### Raw PV Rows")
        st.dataframe(working, use_container_width=True)

else:
    st.info("Upload a full monthly purchases CSV or choose a stored month to begin.")
