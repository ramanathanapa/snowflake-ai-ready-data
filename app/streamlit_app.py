"""Fund Analyst: natural-language analysis of public SEC fund data for portfolio managers.
Runs in Snowflake (Streamlit in Snowflake) and locally (set FUND_ACCOUNT and FUND_USER; key at ~/.snowflake/rsa_key.p8).
"""
import json
import os
import pathlib

import altair as alt
import pandas as pd
import streamlit as st

SEMANTIC_FILE = "@FUND_AI.APP.SEMANTIC_STAGE/semantic_model.yaml"
SUGGESTIONS = [
    "Which funds have the lowest expense ratio?",
    "How much do IVV and VOO overlap?",
    "What are the top 10 holdings of VTI?",
    "Which bond fund is most sensitive to interest rates?",
    "What did ARKK buy or sell recently?",
    "Which funds hold the most NVIDIA?",
]

st.set_page_config(page_title="Fund Analyst", page_icon=None, layout="wide")


# ---------- connection (works in Snowflake and locally) ----------
@st.cache_resource
def get_session():
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session(), True
    except Exception:
        from cryptography.hazmat.primitives import serialization as s
        from snowflake.snowpark import Session
        key = s.load_pem_private_key((pathlib.Path.home() / ".snowflake" / "rsa_key.p8").read_bytes(), password=None)
        der = key.private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())
        return Session.builder.configs({
            "account": os.environ["FUND_ACCOUNT"], "user": os.environ["FUND_USER"], "private_key": der,
            "role": "FUND_ENGINEER", "warehouse": "FUND_WH", "database": "FUND_AI", "schema": "ANALYTICS",
        }).create(), False


SESSION, IN_SNOWFLAKE = get_session()


@st.cache_data(ttl=600, show_spinner=False)
def query(sql: str) -> pd.DataFrame:
    return SESSION.sql(sql).to_pandas()


def lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def call_analyst(messages: list) -> dict:
    body = {"messages": messages, "semantic_model_file": SEMANTIC_FILE}
    if IN_SNOWFLAKE:
        import _snowflake
        resp = _snowflake.send_snow_api_request("POST", "/api/v2/cortex/analyst/message", {}, {}, body, None, 60000)
        if resp["status"] >= 400:
            raise RuntimeError(f"Cortex Analyst error {resp['status']}: {resp['content'][:300]}")
        return json.loads(resp["content"])
    import urllib.request
    conn = SESSION._conn._conn
    req = urllib.request.Request(
        f"https://{conn.host}/api/v2/cortex/analyst/message", data=json.dumps(body).encode(),
        headers={"Authorization": f'Snowflake Token="{conn.rest.token}"', "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def auto_chart(df: pd.DataFrame):
    """Bar chart for the common 'label + number' result shape."""
    if not (2 <= len(df) <= 30):
        return
    nums = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    labels = [c for c in df.columns if df[c].dtype == object]
    if labels and nums:
        st.bar_chart(df.set_index(labels[0])[nums[0]])


# ---------- header ----------
fresh = query("SELECT MIN(AS_OF_DATE) AS OLDEST, MAX(AS_OF_DATE) AS NEWEST, COUNT(*) AS FUNDS, "
              "AVG(DAYS_SINCE_AS_OF) AS AVG_AGE FROM FUND_AI.ANALYTICS.FUND_SUMMARY").iloc[0]
st.title("Fund Analyst")
st.caption("Natural-language analysis of public SEC fund filings (N-PORT holdings, prospectus fees). "
           "Analysis from public regulatory filings, not investment advice.")
c1, c2, c3 = st.columns(3)
c1.metric("Funds covered", int(fresh["FUNDS"]))
c2.metric("Portfolio data as of", f"{fresh['OLDEST']} to {fresh['NEWEST']}")
c3.metric("Average age of holdings", f"{fresh['AVG_AGE']:.0f} days")
st.info("Holdings come from SEC N-PORT filings, public about 60 days after the portfolio date. "
        "Every fund has its own as-of date. There is no price, benchmark or sector data.")

tab_ask, tab_funds, tab_compare, tab_changes = st.tabs(["Ask", "Funds", "Compare", "What changed"])

# ---------- Ask ----------
with tab_ask:
    if "chat" not in st.session_state:
        st.session_state.chat = []  # list of {"role","text","raw","sql","df","error"}
    if st.session_state.chat and st.button("Clear conversation"):
        st.session_state.chat = []
        st.rerun()

    pending = None
    if not st.session_state.chat:
        st.write("Try a question:")
        cols = st.columns(3)
        for i, s in enumerate(SUGGESTIONS):
            if cols[i % 3].button(s, key=f"sug{i}", use_container_width=True):
                pending = s

    for m in st.session_state.chat:
        with st.chat_message("user" if m["role"] == "user" else "assistant"):
            if m["role"] == "user":
                st.write(m["text"])
                continue
            if m.get("error"):
                st.error(m["error"])
            if m.get("text"):
                st.write(m["text"])
            if m.get("df") is not None:
                st.dataframe(m["df"], use_container_width=True, hide_index=True)
                auto_chart(m["df"])
            if m.get("sql"):
                with st.expander("SQL used"):
                    st.code(m["sql"], language="sql")

    question = st.chat_input("Ask about the 24 funds: holdings, fees, overlap, duration, what changed...") or pending
    if question:
        st.session_state.chat.append({"role": "user", "text": question})
        history = []
        for m in st.session_state.chat:
            if m["role"] == "user":
                history.append({"role": "user", "content": [{"type": "text", "text": m["text"]}]})
            elif m.get("raw"):
                history.append({"role": "analyst", "content": m["raw"]})
        # Analyst requires the conversation to start with and alternate from a user turn
        reply = {"role": "analyst"}
        try:
            with st.spinner("Thinking..."):
                msg = call_analyst(history)["message"]["content"]
            reply["raw"] = msg
            reply["text"] = next((c["text"] for c in msg if c["type"] == "text"), "")
            sql = next((c["statement"] for c in msg if c["type"] == "sql"), None)
            if sql:
                reply["sql"] = sql
                # run the SQL as generated (it ends with a comment, so it must not be wrapped in a subquery)
                reply["df"] = SESSION.sql(sql).to_pandas().head(500)
            else:
                sug = next((c for c in msg if c["type"] == "suggestions"), None)
                if sug:
                    reply["text"] += "\n\nDid you mean:\n" + "\n".join(f"- {s}" for s in sug["suggestions"])
        except Exception as e:  # show the failure instead of a stack trace
            reply["error"] = str(e)[:500]
        st.session_state.chat.append(reply)
        st.rerun()

# ---------- Funds ----------
with tab_funds:
    summary = query("SELECT * FROM FUND_AI.ANALYTICS.FUND_SUMMARY ORDER BY NET_ASSETS DESC")
    types = st.multiselect("Fund type", sorted(summary["FUND_TYPE"].unique()), default=sorted(summary["FUND_TYPE"].unique()))
    view = summary[summary["FUND_TYPE"].isin(types)]
    st.dataframe(
        view[["FUND_TICKER", "FUND_NAME", "FUND_TYPE", "NET_ASSETS", "AS_OF_DATE", "POSITIONS",
              "TOP10_CONCENTRATION_PCT", "EXPENSE_RATIO_PCT", "TURNOVER_PCT", "LAST_FULL_YEAR_RETURN_PCT"]],
        use_container_width=True, hide_index=True,
        column_config={
            "NET_ASSETS": st.column_config.NumberColumn("Net assets ($)", format="%.2e"),
            "TOP10_CONCENTRATION_PCT": st.column_config.NumberColumn("Top 10 %", format="%.1f"),
            "EXPENSE_RATIO_PCT": st.column_config.NumberColumn("Expense %", format="%.2f"),
            "TURNOVER_PCT": st.column_config.NumberColumn("Turnover %", format="%.0f"),
            "LAST_FULL_YEAR_RETURN_PCT": st.column_config.NumberColumn("Last yr return %", format="%.1f"),
        })

    st.subheader("Fund detail")
    pick = st.selectbox("Fund", summary["FUND_TICKER"].tolist(), key="detail_fund")
    row = summary[summary["FUND_TICKER"] == pick].iloc[0]
    st.write(f"**{row['FUND_NAME']}** ({row['FUND_FAMILY']}). Other tickers: {row['ALL_TICKERS']}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net assets", f"${row['NET_ASSETS'] / 1e9:,.1f}B")
    m2.metric("As of", str(row["AS_OF_DATE"]), f"{int(row['DAYS_SINCE_AS_OF'])} days old", delta_color="off")
    m3.metric("Expense ratio", f"{row['EXPENSE_RATIO_PCT']:.2f}%")
    m4.metric("Top 10 holdings", f"{row['TOP10_CONCENTRATION_PCT']:.1f}%")

    left, right = st.columns(2)
    with left:
        st.write("Top holdings")
        top = query(f"SELECT ISSUER_NAME, SUM(PCT_NET_ASSETS) AS WEIGHT_PCT FROM FUND_AI.ANALYTICS.LATEST_HOLDING "
                    f"WHERE FUND_TICKER = {lit(pick)} GROUP BY ISSUER_NAME ORDER BY WEIGHT_PCT DESC LIMIT 15")
        st.dataframe(top, use_container_width=True, hide_index=True,
                     column_config={"WEIGHT_PCT": st.column_config.NumberColumn("Weight %", format="%.2f")})
    with right:
        st.write("Asset mix")
        mix = query(f"SELECT ASSET_CLASS, PCT_NET_ASSETS FROM FUND_AI.ANALYTICS.FUND_ASSET_MIX "
                    f"WHERE FUND_TICKER = {lit(pick)} AND PCT_NET_ASSETS > 0.05 ORDER BY PCT_NET_ASSETS DESC")
        st.bar_chart(mix.set_index("ASSET_CLASS"))
        st.write("Country mix (top 10)")
        ctry = query(f"SELECT COUNTRY, PCT_NET_ASSETS FROM FUND_AI.ANALYTICS.FUND_COUNTRY_MIX "
                     f"WHERE FUND_TICKER = {lit(pick)} ORDER BY PCT_NET_ASSETS DESC LIMIT 10")
        st.bar_chart(ctry.set_index("COUNTRY"))

    ret = query(f"SELECT CALENDAR_YEAR, TOTAL_RETURN_PCT FROM FUND_AI.ANALYTICS.ANNUAL_RETURNS "
                f"WHERE CLASS_TICKER = {lit(pick)} ORDER BY CALENDAR_YEAR")
    if len(ret):
        st.write("Calendar-year total return %")
        st.bar_chart(ret.set_index("CALENDAR_YEAR"))
    else:
        st.caption("No calendar-year returns in the prospectus data for this fund.")

    bond = query(f"SELECT * FROM FUND_AI.ANALYTICS.BOND_PROFILE WHERE FUND_TICKER = {lit(pick)}")
    if len(bond):
        b = bond.iloc[0]
        st.write("Bond profile")
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Duration (years)", f"{b['EFFECTIVE_DURATION_YEARS']:.1f}")
        b2.metric("Avg coupon", f"{b['WAVG_COUPON_PCT']:.2f}%")
        b3.metric("Avg maturity (years)", f"{b['WAVG_MATURITY_YEARS']:.1f}")
        b4.metric("Treasury / corporate / securitized",
                  f"{b['TREASURY_PCT']:.0f} / {b['CORPORATE_PCT']:.0f} / {b['SECURITIZED_PCT']:.0f} %")

# ---------- Compare ----------
with tab_compare:
    chosen = st.multiselect("Funds to compare", summary["FUND_TICKER"].tolist(), default=["VOO", "IVV", "VTI"])
    if len(chosen) >= 2:
        inlist = ", ".join(lit(c) for c in chosen)
        st.dataframe(
            summary[summary["FUND_TICKER"].isin(chosen)][
                ["FUND_TICKER", "NET_ASSETS", "EXPENSE_RATIO_PCT", "POSITIONS", "TOP10_CONCENTRATION_PCT",
                 "TURNOVER_PCT", "AS_OF_DATE"]],
            use_container_width=True, hide_index=True)
        ov = query(f"SELECT FUND_A, FUND_B, OVERLAP_PCT FROM FUND_AI.ANALYTICS.FUND_OVERLAP "
                   f"WHERE FUND_A IN ({inlist}) AND FUND_B IN ({inlist})")
        st.write("Portfolio overlap % (100 = identical holdings)")
        if len(ov):
            base = alt.Chart(ov).encode(x=alt.X("FUND_B:N", title=None), y=alt.Y("FUND_A:N", title=None))
            heat = base.mark_rect().encode(color=alt.Color("OVERLAP_PCT:Q", scale=alt.Scale(domain=[0, 100]), title="%"))
            txt = base.mark_text().encode(text=alt.Text("OVERLAP_PCT:Q", format=".0f"))
            st.altair_chart(heat + txt, use_container_width=True)
        else:
            st.caption("These funds share no securities.")
        w = query(f"SELECT FUND_TICKER, ISSUER_NAME, SUM(PCT_NET_ASSETS) AS W FROM FUND_AI.ANALYTICS.LATEST_HOLDING "
                  f"WHERE FUND_TICKER IN ({inlist}) GROUP BY FUND_TICKER, ISSUER_NAME")
        top_names = w.groupby("ISSUER_NAME")["W"].max().nlargest(15).index
        wide = w[w["ISSUER_NAME"].isin(top_names)].pivot(index="ISSUER_NAME", columns="FUND_TICKER", values="W")
        st.write("Largest positions across the selected funds (weight %)")
        st.dataframe(wide.fillna(0).round(2).sort_values(chosen[0], ascending=False), use_container_width=True)
    else:
        st.info("Select at least two funds.")

# ---------- What changed ----------
with tab_changes:
    fund = st.selectbox("Fund", summary["FUND_TICKER"].tolist(), key="chg_fund")
    chg = query(f"SELECT * FROM FUND_AI.ANALYTICS.HOLDING_CHANGES WHERE FUND_TICKER = {lit(fund)}")
    if len(chg):
        st.write(f"Snapshot {chg['PREVIOUS_AS_OF'].iloc[0]} to {chg['CURRENT_AS_OF'].iloc[0]}. "
                 f"Fund-wide share count moved {chg['FUND_FLOW_PCT'].dropna().iloc[0]:+.1f}% (creations/redemptions); "
                 "INCREASED/DECREASED are relative to that.")
        counts = chg["CHANGE_TYPE"].value_counts().reindex(["NEW", "SOLD", "INCREASED", "DECREASED", "UNCHANGED"]).fillna(0)
        st.bar_chart(counts)
        kinds = st.multiselect("Show", ["NEW", "SOLD", "INCREASED", "DECREASED", "UNCHANGED"],
                               default=["NEW", "SOLD", "INCREASED", "DECREASED"])
        show = chg[chg["CHANGE_TYPE"].isin(kinds)].copy()
        show["ABS"] = show["WEIGHT_CHANGE_PTS"].abs()
        st.dataframe(
            show.sort_values("ABS", ascending=False)[
                ["ISSUER_NAME", "ISSUER_TITLE", "CHANGE_TYPE", "PREVIOUS_PCT", "CURRENT_PCT",
                 "WEIGHT_CHANGE_PTS", "RELATIVE_BALANCE_CHANGE_PCT"]].head(200),
            use_container_width=True, hide_index=True,
            column_config={
                "PREVIOUS_PCT": st.column_config.NumberColumn("Prev weight %", format="%.2f"),
                "CURRENT_PCT": st.column_config.NumberColumn("Now weight %", format="%.2f"),
                "WEIGHT_CHANGE_PTS": st.column_config.NumberColumn("Weight chg (pts)", format="%.2f"),
                "RELATIVE_BALANCE_CHANGE_PCT": st.column_config.NumberColumn("Traded vs fund %", format="%.1f"),
            })
    else:
        st.info("This fund has only one snapshot loaded.")
