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
    """
    PV3 SHCL -> 3
    PV5 TTABC-Event -> 5
    """
    match = re.search(r"^PV\s*(\d+)", str(event_name).upper().strip())
    if match:
        return float(match.group(1))
    return 0.0


def get_broker_code(event_name):
    """
    Rule:
    Use the FIRST 2 letters after PV#.

    Examples:
    PV3 SHCL -> SH
    PV5 TTABC-Event -> TT
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
    Uflip export may have summary/junk rows before the real table.
    Find the header row that contains Event Name and read from there.
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
    return sorted(SAVED_MONTHS_DIR.glob("*.csv"))


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


def calc_profit_pct(profit_series, boxoffice_series):
    profit = pd.to_numeric(profit_series, errors="coerce").fillna(0.0)
    box = pd.to_numeric(boxoffice_series, errors="coerce").fillna(0.0)
    result = pd.Series(0.0, index=box.index if hasattr(box, "index") else None)
    nonzero = box != 0
    result.loc[nonzero] = profit.loc[nonzero] / box.loc[nonzero]
    return result


# =========================================================
# INITIAL LOAD
# =========================================================
ensure_saved_months_dir()
broker_map_df = load_broker_map()

source_choice = st.radio(
    "Choose source",
    ["Upload new monthly file", "Use a stored month"],
    horizontal=True
)

raw_df = None
raw_source_name = None

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

        col_save_1, col_save_2 = st.columns([1, 4])
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


with st.expander("Stored Months", expanded=False):
    saved_files = list_saved_month_files()
    if not saved_files:
        st.write("No stored month files yet.")
    else:
        stored_df = pd.DataFrame({"Saved File": [f.name for f in saved_files]})
        st.dataframe(stored_df, use_container_width=True)


# =========================================================
# MAIN PROCESSING
# =========================================================
if raw_df is not None:
    st.info(f"Source file: {raw_source_name}")

    pv_df = filter_private_sales(raw_df)

    if pv_df.empty:
        st.warning("No rows found where Event Name starts with PV.")
        st.stop()

    working = pv_df.copy()

    # Normalize numeric columns
    for col in ["Quantity", "BoxOffice", "Overs", "Flipper Fee", "UF Ticket Fee"]:
        if col in working.columns:
            working[col] = to_number(working[col])
        else:
            working[col] = 0.0

    # Ensure required columns exist
    if "Flipper Name" not in working.columns:
        working["Flipper Name"] = ""

    if "Order No" not in working.columns:
        working["Order No"] = ""

    if "Purchased" not in working.columns:
        working["Purchased"] = ""

    working["Event Name"] = working["Event Name"].fillna("").astype(str).str.strip()
    working["Purchased"] = working["Purchased"].fillna("").astype(str).str.strip()

    # Parse PV info
    working["Paid Per Ticket"] = working["Event Name"].apply(get_paid_amount)
    working["Broker Code"] = working["Event Name"].apply(get_broker_code)
    working["Total Paid to Flipper"] = working["Quantity"] * working["Paid Per Ticket"]

    # Broker lookup
    broker_lookup = {}
    if not broker_map_df.empty:
        broker_lookup = dict(
            zip(
                broker_map_df["Broker Code"].astype(str).str.upper(),
                broker_map_df["Broker Company"].astype(str).str.strip()
            )
        )

    working["Broker Company"] = working["Broker Code"].map(broker_lookup)
    working["Broker Company"] = working["Broker Company"].fillna("UNKNOWN")

    # Unknown broker warning
    unknown_codes = sorted(
        set(
            working.loc[working["Broker Company"] == "UNKNOWN", "Broker Code"]
            .fillna("")
            .astype(str)
            .str.strip()
            .tolist()
        ) - {""}
    )

    if unknown_codes:
        st.warning(f"Unknown Broker Codes Found: {', '.join(unknown_codes)}")

        with st.expander("Add Missing Broker Codes", expanded=False):
            st.write("Enter broker company names for the missing codes below. Then download the CSV snippet and add those rows to `broker_map.csv` in GitHub.")

            missing_broker_rows = []
            for code in unknown_codes:
                company_name = st.text_input(f"Broker company for code {code}", key=f"missing_broker_{code}")
                missing_broker_rows.append({
                    "Broker Company": company_name.strip(),
                    "Broker Code": code
                })

            missing_broker_df = pd.DataFrame(missing_broker_rows)

            st.dataframe(missing_broker_df, use_container_width=True)

            st.download_button(
                "Download Missing Broker CSV",
                data=df_to_csv_download(missing_broker_df),
                file_name="missing_broker_codes.csv",
                mime="text/csv"
            )

    # Event defaults
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

    # Reset event control when file changes
    current_signature = f"{raw_source_name}|{len(working)}|{working['Event Name'].nunique()}"
    if st.session_state.get("source_signature") != current_signature:
        st.session_state.source_signature = current_signature
        st.session_state.event_ref_data = default_event_ref.copy()

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

    # Merge event controls back
    working = working.merge(
        st.session_state.event_ref_data,
        on="Event Name",
        how="left",
        suffixes=("", "_event")
    )

    # =========================================================
    # EVENT SUMMARY
    # =========================================================
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

    event_summary["Profit %"] = calc_profit_pct(
        event_summary["Total Company Profit"],
        event_summary["Total_BoxOffice"]
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
            "Profit %",
        ]
    ].copy()

    total_boxoffice = event_summary_display["Total BoxOffice"].sum()
    total_profit = event_summary_display["Total Company Profit"].sum()
    grand_profit_pct = (total_profit / total_boxoffice) if total_boxoffice != 0 else 0.0

    totals_row = pd.DataFrame([{
        "Event Name": "GRAND TOTAL",
        "Broker Code": "",
        "Broker Company": "",
        "Broker Fee %": "",
        "Account": "",
        "Sales Date": "",
        "Orders": event_summary_display["Orders"].sum(),
        "Quantity": event_summary_display["Quantity"].sum(),
        "Total BoxOffice": total_boxoffice,
        "Broker Fees": event_summary_display["Broker Fees"].sum(),
        "Overs": event_summary_display["Overs"].sum(),
        "Flipper Fees": event_summary_display["Flipper Fees"].sum(),
        "UF Ticket Fee": event_summary_display["UF Ticket Fee"].sum(),
        "Total Company Profit": total_profit,
        "Profit %": grand_profit_pct,
    }])

    event_summary_download = pd.concat(
        [event_summary_display, totals_row],
        ignore_index=True
    )

    # =========================================================
    # FLIPPER SUMMARY
    # =========================================================
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

    # =========================================================
    # PO SUMMARY
    # =========================================================
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
    # BROKER SUMMARY
    # =========================================================
    broker_summary_base = (
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
        .rename(columns={
            "Broker Code_event": "Broker Code",
            "Broker Company_event": "Broker Company",
            "Total_BoxOffice": "Total BoxOffice",
            "Flipper_Fees": "Flipper Fees",
            "UF_Ticket_Fee": "UF Ticket Fee",
        })
    )

    broker_fees_grouped = (
        event_summary_display.groupby(["Broker Code", "Broker Company"], dropna=False)
        .agg(
            Broker_Fees=("Broker Fees", "sum"),
            Total_Company_Profit=("Total Company Profit", "sum"),
        )
        .reset_index()
        .rename(columns={
            "Broker_Fees": "Broker Fees",
            "Total_Company_Profit": "Total Company Profit",
        })
    )

    broker_summary = broker_summary_base.merge(
        broker_fees_grouped,
        on=["Broker Code", "Broker Company"],
        how="left"
    )

    broker_summary["Profit %"] = calc_profit_pct(
        broker_summary["Total Company Profit"],
        broker_summary["Total BoxOffice"]
    )

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
            "Profit %",
        ]
    ].copy()

    broker_summary = broker_summary.fillna("")

    # =========================================================
    # CLEAN DETAIL
    # =========================================================
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

    # =========================================================
    # DASHBOARD
    # =========================================================
    st.markdown("### Dashboard Totals")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total BoxOffice", f"{total_boxoffice:,.2f}")
    c2.metric("Broker Fees", f"{event_summary_display['Broker Fees'].sum():,.2f}")
    c3.metric("Overs", f"{event_summary_display['Overs'].sum():,.2f}")
    c4.metric("Flipper Fees", f"{event_summary_display['Flipper Fees'].sum():,.2f}")
    c5.metric("Company Profit", f"{total_profit:,.2f}")
    c6.metric("Profit %", f"{grand_profit_pct:.2%}")

    # =========================================================
    # DOWNLOADS
    # =========================================================
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

    # =========================================================
    # TABLES
    # =========================================================
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
