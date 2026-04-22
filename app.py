import re
import io
import csv
from pathlib import Path

import streamlit as st
import pandas as pd


DATA_FILE = Path("private_sales_latest.csv")
BROKER_MAP_FILE = Path("broker_map.csv")


def to_number(series):
    s = series.fillna("").astype(str).str.strip()
    s = s.str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    s = s.str.replace("$", "", regex=False)
    s = s.str.replace(",", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def get_paid_amount(event_name):
    match = re.search(r"PV\s*(\d+)", str(event_name).upper())
    if match:
        return float(match.group(1))
    return 0.0


def get_broker_code(event_name):
    """
    Rule:
    Use the FIRST 2 letters after PV#.
    Example:
    PV3 SHCL -> SH
    PV5 TTSEB -> TT
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


def df_to_csv_download(df):
    return df.to_csv(index=False).encode("utf-8")


def read_uploaded_text(path_or_buffer):
    if hasattr(path_or_buffer, "read"):
        raw = path_or_buffer.read()
    else:
        with open(path_or_buffer, "rb") as f:
            raw = f.read()

    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    raise ValueError("Could not decode CSV file.")


def find_detail_header_line(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        first_cell = next(csv.reader([line], skipinitialspace=False), [])
        if not first_cell:
            continue
        first_val = str(first_cell[0]).strip().strip('"')
        if first_val == "Id":
            return i
    return None


def load_full_csv(path_or_buffer):
    text = read_uploaded_text(path_or_buffer)
    header_line_idx = find_detail_header_line(text)
    if header_line_idx is None:
        raise ValueError("Could not find the detail section. Expected a row starting with 'Id'.")

    lines = text.splitlines()
    detail_text = "\n".join(lines[header_line_idx:])

    try:
        df = pd.read_csv(io.StringIO(detail_text))
    except Exception as e:
        raise ValueError(f"Could not read detail section: {e}")

    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed:")]
    return df, header_line_idx + 1


def filter_private_sales(df):
    if "Event Name" not in df.columns:
        raise ValueError("CSV is missing the 'Event Name' column.")

    working = df.copy()
    working["Event Name"] = working["Event Name"].fillna("").astype(str).str.strip()

    pv_df = working[
        working["Event Name"].str.upper().str.startswith("PV", na=False)
    ].copy()

    return pv_df


def load_broker_map():
    if not BROKER_MAP_FILE.exists():
        return {}

    broker_map = pd.read_csv(BROKER_MAP_FILE, dtype=str).fillna("")
    broker_map.columns = [str(c).strip() for c in broker_map.columns]

    if "Broker Company" not in broker_map.columns or "Broker Code" not in broker_map.columns:
        return {}

    broker_map["Broker Company"] = broker_map["Broker Company"].astype(str).str.strip()
    broker_map["Broker Code"] = broker_map["Broker Code"].astype(str).str.strip().str.upper()

    broker_map = broker_map[broker_map["Broker Code"] != ""].copy()
    broker_map = broker_map.drop_duplicates(subset=["Broker Code"], keep="first")

    return dict(zip(broker_map["Broker Code"], broker_map["Broker Company"]))


def calc_profit_pct(total_profit, total_boxoffice):
    if total_boxoffice == 0:
        return 0.0
    return total_profit / total_boxoffice


def render():
    st.set_page_config(page_title="Private Sales App", layout="wide")

    st.title("Private Sales App")
    st.write("This app auto-loads the latest Private Sales CSV from GitHub and keeps only rows whose Event Name starts with PV.")

    if not DATA_FILE.exists():
        st.error("private_sales_latest.csv was not found in the GitHub repo.")
        st.stop()

    try:
        df, start_line = load_full_csv(DATA_FILE)
        total_detail_rows = len(df)
        df = filter_private_sales(df)
    except Exception as e:
        st.error(str(e))
        st.stop()

    st.success(f"Latest file loaded successfully. Detail section found starting at line {start_line}.")
    st.write(f"Total detail rows found: {total_detail_rows:,}")
    st.write(f"PV rows kept: {len(df):,}")

    if df.empty:
        st.warning("No rows were found where Event Name starts with PV.")
        st.stop()

    broker_lookup = load_broker_map()

    st.subheader("Private Sales Dashboard")

    event_list = sorted(df["Event Name"].dropna().astype(str).unique().tolist())

    if "event_ref_data" not in st.session_state:
        defaults = []
        for event_name in event_list:
            broker_code = get_broker_code(event_name)
            broker_company = broker_lookup.get(broker_code, "UNKNOWN")
            defaults.append({
                "Event Name": event_name,
                "Broker Code": broker_code,
                "Broker Company": broker_company,
                "Broker Fee %": 5.0,
                "Account": "Flipper",
                "Sales Date": "",
            })
        st.session_state.event_ref_data = pd.DataFrame(defaults)
    else:
        existing = st.session_state.event_ref_data.copy()
        if "Event Name" not in existing.columns:
            existing = pd.DataFrame(columns=["Event Name", "Broker Code", "Broker Company", "Broker Fee %", "Account", "Sales Date"])

        existing_events = set(existing["Event Name"].astype(str))
        missing_events = [e for e in event_list if e not in existing_events]

        if missing_events:
            add_rows = []
            for event_name in missing_events:
                broker_code = get_broker_code(event_name)
                broker_company = broker_lookup.get(broker_code, "UNKNOWN")
                add_rows.append({
                    "Event Name": event_name,
                    "Broker Code": broker_code,
                    "Broker Company": broker_company,
                    "Broker Fee %": 5.0,
                    "Account": "Flipper",
                    "Sales Date": "",
                })
            add_df = pd.DataFrame(add_rows)
            st.session_state.event_ref_data = pd.concat([existing, add_df], ignore_index=True)

        st.session_state.event_ref_data = st.session_state.event_ref_data[
            st.session_state.event_ref_data["Event Name"].astype(str).isin(set(event_list))
        ].copy()

    # update broker code/company defaults from current map without touching manual fee/account
    refreshed_rows = []
    for _, row in st.session_state.event_ref_data.iterrows():
        event_name = str(row["Event Name"])
        broker_code = get_broker_code(event_name)
        current_company = str(row.get("Broker Company", "")).strip()
        mapped_company = broker_lookup.get(broker_code, "UNKNOWN")
        if current_company == "" or current_company == "UNKNOWN":
            current_company = mapped_company

        refreshed_rows.append({
            "Event Name": event_name,
            "Broker Code": broker_code,
            "Broker Company": current_company,
            "Broker Fee %": row.get("Broker Fee %", 5.0),
            "Account": row.get("Account", "Flipper"),
            "Sales Date": row.get("Sales Date", ""),
        })

    st.session_state.event_ref_data = pd.DataFrame(refreshed_rows)

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

    working = df.copy()

    for col in ["Quantity", "BoxOffice", "Overs", "Flipper Fee", "UF Ticket Fee"]:
        if col in working.columns:
            working[col] = to_number(working[col])
        else:
            working[col] = 0.0

    if "Order No" not in working.columns:
        working["Order No"] = ""

    if "Flipper Name" not in working.columns:
        working["Flipper Name"] = ""

    if "Purchased" not in working.columns:
        working["Purchased"] = ""

    working["Broker Code"] = working["Event Name"].apply(get_broker_code)
    working["Broker Company"] = working["Broker Code"].map(broker_lookup).fillna("UNKNOWN")
    working["Paid Per Ticket"] = working["Event Name"].apply(get_paid_amount)
    working["Total Paid to Flipper"] = working["Quantity"] * working["Paid Per Ticket"]

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
            st.write("Enter broker company names for the missing codes below, then download the CSV snippet and add those rows to broker_map.csv in GitHub.")

            missing_rows = []
            for code in unknown_codes:
                company_name = st.text_input(f"Broker company for code {code}", key=f"missing_broker_{code}")
                missing_rows.append({
                    "Broker Company": company_name.strip(),
                    "Broker Code": code
                })

            missing_df = pd.DataFrame(missing_rows)
            st.dataframe(missing_df, use_container_width=True)

            st.download_button(
                "Download Missing Broker CSV",
                data=df_to_csv_download(missing_df),
                file_name="missing_broker_codes.csv",
                mime="text/csv"
            )

    # merge control table
    working = working.merge(
        st.session_state.event_ref_data,
        on="Event Name",
        how="left",
        suffixes=("", "_control")
    )

    # fill date from Purchased if Sales Date blank
    if "Sales Date" in working.columns and "Purchased" in working.columns:
        working["Sales Date"] = working["Sales Date"].fillna("").astype(str)
        working["Purchased"] = working["Purchased"].fillna("").astype(str)
        working["Sales Date"] = working.apply(
            lambda r: r["Purchased"] if str(r["Sales Date"]).strip() == "" else r["Sales Date"],
            axis=1
        )

    event_summary = (
        working.groupby("Event Name", dropna=False)
        .agg(
            Broker_Code=("Broker Code_control", "first"),
            Broker_Company=("Broker Company_control", "first"),
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
        event_summary["Total_BoxOffice"] * event_summary["Broker_Fee_Pct"] / 100
    )

    event_summary["Total Company Profit"] = (
        event_summary["Broker Fees"]
        + event_summary["Overs"]
        - event_summary["Flipper_Fees"]
    )

    event_summary["Profit %"] = event_summary.apply(
        lambda r: calc_profit_pct(r["Total Company Profit"], r["Total_BoxOffice"]), axis=1
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
    grand_profit_pct = calc_profit_pct(total_profit, total_boxoffice)

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

    flipper_summary = (
        working.groupby(["Flipper Name", "Event Name"], dropna=False)
        .agg(
            Broker_Code=("Broker Code_control", "first"),
            Broker_Company=("Broker Company_control", "first"),
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
                Broker_Code=("Broker Code_control", "first"),
                Broker_Company=("Broker Company_control", "first"),
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

    broker_summary_base = (
        working.groupby(["Broker Code_control", "Broker Company_control"], dropna=False)
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
            "Broker Code_control": "Broker Code",
            "Broker Company_control": "Broker Company",
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

    broker_summary["Profit %"] = broker_summary.apply(
        lambda r: calc_profit_pct(r["Total Company Profit"], r["Total BoxOffice"]), axis=1
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

    detail_columns = []
    for col in [
        "Event Name",
        "Purchased",
        "Quantity",
        "BoxOffice",
        "Overs",
        "Flipper Fee",
        "UF Ticket Fee",
        "Flipper Name",
        "Order No",
        "Paid Per Ticket",
        "Total Paid to Flipper",
        "Broker Code_control",
        "Broker Company_control",
        "Broker Fee %",
        "Account",
        "Sales Date",
    ]:
        if col in working.columns:
            detail_columns.append(col)

    clean_detail = working[detail_columns].copy()

    clean_detail = clean_detail.rename(columns={
        "Broker Code_control": "Broker Code",
        "Broker Company_control": "Broker Company",
    })

    if purchase_id_col is not None and purchase_id_col in working.columns:
        clean_detail[purchase_id_col] = working[purchase_id_col]

    clean_detail_columns_order = [
        "Event Name",
        "Broker Code",
        "Broker Company",
        "Broker Fee %",
        "Account",
        "Sales Date",
        "Purchased",
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
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    col1.metric("Total BoxOffice", f"{total_boxoffice:,.2f}")
    col2.metric("Broker Fees", f"{event_summary_display['Broker Fees'].sum():,.2f}")
    col3.metric("Overs", f"{event_summary_display['Overs'].sum():,.2f}")
    col4.metric("Flipper Fees", f"{event_summary_display['Flipper Fees'].sum():,.2f}")
    col5.metric("Company Profit", f"{total_profit:,.2f}")
    col6.metric("Profit %", f"{grand_profit_pct:.2%}")

    st.markdown("### Download Files")
    d1, d2, d3, d4, d5 = st.columns(5)

    with d1:
        st.download_button(
            label="Download Event Control",
            data=df_to_csv_download(st.session_state.event_ref_data),
            file_name="event_control.csv",
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
                label="Download PO Summary",
                data=df_to_csv_download(po_summary),
                file_name="po_summary.csv",
                mime="text/csv"
            )

    with d5:
        st.download_button(
            label="Download Broker Summary",
            data=df_to_csv_download(broker_summary),
            file_name="broker_summary.csv",
            mime="text/csv"
        )

    st.download_button(
        label="Download Clean Detail File",
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
        st.dataframe(
            po_summary[
                [
                    "Purchase ID",
                    "Event Name",
                    "Broker Code",
                    "Broker Company",
                    "Flipper Name",
                    "Amount Paid",
                ]
            ],
            use_container_width=True
        )
    else:
        st.warning("No Purchase ID column was found in this CSV.")

    st.markdown("### Broker Summary")
    st.dataframe(broker_summary, use_container_width=True)

    show_raw = st.checkbox("Show Raw PV Data Only", value=False, key="private_sales_show_raw")

    if show_raw:
        st.markdown("### Raw PV Data Only")
        st.dataframe(df, use_container_width=True)


if __name__ == "__main__":
    render()
