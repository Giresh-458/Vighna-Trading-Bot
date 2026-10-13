import os
import time
import json
import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from app.config import INITIAL_CAPITAL, MAX_TICK_AGE_MS
from app.market_data_cache import is_fresh_persisted_tick

# Page Configuration
st.set_page_config(
    page_title="KB4Trade Portfolio & PnL",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .metric-card {
        background-color: #1a1c23;
        border: 1px solid #2d3139;
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 15px;
    }
    .metric-title {
        color: #8b949e;
        font-size: 13px;
        font-weight: 500;
        margin-bottom: 6px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-value {
        color: #f0f6fc;
        font-size: 26px;
        font-weight: 700;
    }
    .metric-sub {
        font-size: 12px;
        margin-top: 4px;
    }
    .positive { color: #3fb950 !important; }
    .negative { color: #f85149 !important; }
    .neutral { color: #58a6ff !important; }
</style>
""", unsafe_allow_html=True)

DB_PATH = "paper_trading.db"
IST = timezone(timedelta(hours=5, minutes=30))

def to_ist(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%d %b %H:%M:%S")
    except Exception:
        return iso_str[:19].replace("T", " ")

def get_date_str(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%Y-%m-%d")
    except:
        return "Unknown"

def is_today(iso_str: str) -> bool:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).date() == datetime.now(IST).date()
    except:
        return False

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    conn.row_factory = sqlite3.Row
    return conn

def load_data():
    if not os.path.exists(DB_PATH):
        return {}, [], [], [], {}
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM system_state")
        state_dict = {row["key"]: row["value"] for row in cursor.fetchall()}
        cursor.execute("SELECT * FROM positions ORDER BY entry_time DESC")
        positions = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM trades ORDER BY timestamp DESC")
        trades = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM signals ORDER BY received_at DESC")
        signals = [dict(row) for row in cursor.fetchall()]
        try:
            cursor.execute("SELECT token, ltp, updated_at FROM live_ticks")
            live_ticks = {
                str(row["token"]): {"ltp": row["ltp"], "updated_at": row["updated_at"]}
                for row in cursor.fetchall()
            }
        except Exception:
            live_ticks = {}
    finally:
        conn.close()
    return state_dict, positions, trades, signals, live_ticks

@st.cache_resource
def get_angel_session():
    try:
        from app.angel_auth import AngelAuth
        auth = AngelAuth()
        if auth.login(): return auth
    except: pass
    return None

@st.cache_data(ttl=1)
def _cached_api_ltp(symbol: str, token: str, exch: str) -> Optional[float]:
    auth = get_angel_session()
    if not auth or not auth.api: return None
    try:
        res = auth.api.ltpData(exch, symbol, str(token))
        if res and res.get("status") and res.get("data"):
            return float(res["data"]["ltp"])
    except: pass
    return None

def fetch_live_ltp(auth, symbol: str, token: str, segment: str, live_ticks: Optional[dict] = None) -> Optional[float]:
    # A persisted WebSocket value without a timestamp cannot be proven current.
    tick = live_ticks.get(str(token)) if live_ticks else None
    if isinstance(tick, dict) and tick.get("ltp") is not None:
        if is_fresh_persisted_tick(tick.get("updated_at"), MAX_TICK_AGE_MS):
            try:
                value = float(tick["ltp"])
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass

    exch = "MCX" if "commodity" in segment else ("BFO" if symbol.startswith("SENSEX") or symbol.startswith("BANKEX") else "NFO")
    return _cached_api_ltp(symbol, str(token), exch)

state_dict, positions, trades, signals, live_ticks = load_data()
angel_auth = get_angel_session()

# Sidebar
st.sidebar.title("⚡ Control Panel")
auto_refresh = st.sidebar.checkbox("Auto Refresh", value=True)
refresh_interval = st.sidebar.selectbox("Refresh Interval", [3, 5, 10, 30], index=1)
if st.sidebar.button("🔄 Refresh Data Now"): st.rerun()

st.sidebar.divider()
st.sidebar.subheader("ℹ️ System Info")
st.sidebar.write("**Broker:** Angel One")
st.sidebar.write("**Execution:** Paper Trading")
st.sidebar.caption(f"Updated: {datetime.now(IST).strftime('%H:%M:%S IST')}")

# Group Data by Segment
segments = ["equity", "commodity_futures", "commodity_options", "hero_zero"]
seg_names = {"equity": "📈 Equity", "commodity_futures": "🛢️ Commodity Futures", "commodity_options": "🛢️ Commodity Options", "hero_zero": "🚀 Hero-Zero"}

data = {seg: {"positions": [], "trades_today": [], "trades_history": []} for seg in segments}
sig_map = {s["id"]: s for s in signals}

for p in positions:
    sig = sig_map.get(p["signal_id"], {})
    seg = sig.get("segment", "equity")
    data[seg]["positions"].append(p)

all_historical_trades = []
all_trades_processed = []

for t in trades:
    pos = next((p for p in positions if p["id"] == t["position_id"]), {})
    if not pos: continue
    sig = sig_map.get(pos.get("signal_id"), {})
    seg = sig.get("segment", "equity")

    t["segment"] = seg
    t["symbol"] = pos["symbol"]
    all_trades_processed.append(t)

    if is_today(t["timestamp"]):
        data[seg]["trades_today"].append(t)
    else:
        data[seg]["trades_history"].append(t)
        all_historical_trades.append(t)

initial_cap = float(INITIAL_CAPITAL)
shared_available_margin = float(state_dict.get("available_margin_total", initial_cap))
global_blocked_margin = 0.0
global_unrealized_pnl = 0.0
missing_quote_count = 0

# Calculate portfolio equity once across every segment, using only fresh quotes.
for p in positions:
    if p.get("status") != "OPEN":
        continue
    sig = sig_map.get(p.get("signal_id"), {})
    seg = sig.get("segment", "equity")
    stored_margin = p.get("margin_used")
    if stored_margin is not None:
        margin_used = max(0.0, float(stored_margin))
    else:
        is_futures = seg == "commodity_futures" or "FUT" in str(p.get("symbol", "")).upper()
        margin_used = float(p["entry_price"]) * int(p["quantity"]) * (0.10 if is_futures else 1.0)
    p["_dashboard_margin_used"] = margin_used
    global_blocked_margin += margin_used

    live_ltp = fetch_live_ltp(angel_auth, p["symbol"], p["token"], seg, live_ticks)
    if live_ltp is not None and live_ltp > 0:
        p["current_ltp"] = live_ltp
        entry = float(p["entry_price"])
        qty = int(p["quantity"])
        p["unrealized_pnl"] = (
            (live_ltp - entry) * qty
            if p.get("trade_type", "LONG") == "LONG"
            else (entry - live_ltp) * qty
        )
        global_unrealized_pnl += p["unrealized_pnl"]
    else:
        p["current_ltp"] = None
        p["unrealized_pnl"] = None
        missing_quote_count += 1

portfolio_equity = shared_available_margin + global_blocked_margin + global_unrealized_pnl

st.title("📊 KB4Trade Portfolio Monitor")
if missing_quote_count:
    st.warning(
        f"{missing_quote_count} open position(s) have no fresh market quote. "
        "Their unrealized P&L is excluded, so displayed portfolio equity is partial until quotes recover."
    )
tabs = st.tabs(["📈 Equity", "🛢️ Commodity Futures", "🛢️ Commodity Options", "🚀 Hero-Zero", "📆 History & Daily Summary", "👥 Subscribers"])

initial_cap = float(INITIAL_CAPITAL)

# Render Today's Segment Dashboards
for i, seg in enumerate(segments):
    with tabs[i]:
        st.subheader(f"{seg_names[seg]} - Today's Live Dashboard")
        
        # This is one shared cash pool; the balance is intentionally common to all tabs.
        avail_margin = shared_available_margin

        open_pos = [p for p in data[seg]["positions"] if p["status"] == "OPEN"]
        closed_trades_today = data[seg]["trades_today"]
        total_blocked = sum(float(p.get("_dashboard_margin_used", 0.0)) for p in open_pos)
        total_unrealized = sum(
            float(p["unrealized_pnl"]) for p in open_pos if p.get("unrealized_pnl") is not None
        )

        today_realized = sum(t["net_pnl"] for t in closed_trades_today)
        port_val = portfolio_equity

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f"""<div class="metric-card"><div class="metric-title">Total Portfolio Equity</div><div class="metric-value">₹{port_val:,.2f}</div><div class="metric-sub {'positive' if port_val >= initial_cap else 'negative'}">{((port_val - initial_cap)/initial_cap)*100:+.2f}% Lifetime</div></div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="metric-card"><div class="metric-title">Shared Available Cash</div><div class="metric-value neutral">₹{avail_margin:,.2f}</div><div class="metric-sub">Blocked in this segment: ₹{total_blocked:,.2f}</div></div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="metric-card"><div class="metric-title">Today's Realized P&L</div><div class="metric-value {'positive' if today_realized >= 0 else 'negative'}">{'+' if today_realized >= 0 else ''}₹{today_realized:,.2f}</div><div class="metric-sub">{len(closed_trades_today)} trades closed today</div></div>""", unsafe_allow_html=True)
        with col4:
            st.markdown(f"""<div class="metric-card"><div class="metric-title">Live P&L</div><div class="metric-value {'positive' if total_unrealized >= 0 else 'negative'}">{'+' if total_unrealized >= 0 else ''}₹{total_unrealized:,.2f}</div><div class="metric-sub">{len(open_pos)} open position(s)</div></div>""", unsafe_allow_html=True)
            
        st.write("#### Active Positions")
        if not open_pos:
            st.info("No active positions.")
        else:
            header_cols = st.columns([2, 1, 1, 1, 1, 2, 1])
            header_cols[0].markdown("**Symbol**")
            header_cols[1].markdown("**Type**")
            header_cols[2].markdown("**Qty**")
            header_cols[3].markdown("**Entry**")
            header_cols[4].markdown("**LTP**")
            header_cols[5].markdown("**Unrealized P&L**")
            header_cols[6].markdown("**Action**")
            
            for p in open_pos:
                cols = st.columns([2, 1, 1, 1, 1, 2, 1])
                cols[0].write(p["symbol"])
                cols[1].write(p.get("trade_type", "LONG"))
                cols[2].write(str(p["quantity"]))
                cols[3].write(f"₹{p['entry_price']:.2f}")
                cols[4].write(f"₹{p['current_ltp']:.2f}" if p.get("current_ltp") is not None else "STALE / N/A")

                if p.get("unrealized_pnl") is None:
                    cols[5].write("Unavailable — quote missing")
                else:
                    pnl_color = "green" if p["unrealized_pnl"] >= 0 else "red"
                    cols[5].markdown(
                        f"<span style='color:{pnl_color}'>₹{p['unrealized_pnl']:,.2f}</span>",
                        unsafe_allow_html=True,
                    )
                
                if cols[6].button("Close", key=f"close_{p['id']}"):
                    conn = get_db_connection()
                    try:
                        cur = conn.cursor()
                        payload_data = json.dumps({"pos_id": p["id"]})
                        cur.execute("INSERT INTO commands (command, payload, status, created_at) VALUES (?, ?, 'PENDING', ?)", 
                                    ("CLOSE_POSITION", payload_data, datetime.now(timezone.utc).isoformat()))
                        conn.commit()
                        st.info(
                            f"Manual close request queued for {p['symbol']}. "
                            "The bot will close it only after a fresh REST/WebSocket quote is available."
                        )
                        time.sleep(1)
                        st.rerun()
                    finally:
                        conn.close()
            
        st.write("#### Today's Closed Trades")
        if not closed_trades_today:
            st.info("No trades closed today.")
        else:
            df = pd.DataFrame([{
                "Symbol": t["symbol"], "Qty": t["quantity"], "Entry": f"{t['entry_price']:.2f}", "Exit": f"{t['exit_price']:.2f}",
                "Gross P&L": f"₹{t['gross_pnl']:,.2f}", "Net P&L": f"₹{t['net_pnl']:,.2f}", "Time": to_ist(t["timestamp"])
            } for t in closed_trades_today])
            st.dataframe(df, width="stretch", hide_index=True)

# History Tab
with tabs[4]:
    st.subheader("📆 Daily P&L Summary")
    
    daily_summary = {}
    for t in all_trades_processed:
        d_str = get_date_str(t["timestamp"])
        if d_str not in daily_summary:
            daily_summary[d_str] = {"Date": d_str, "Equity": 0.0, "Futures": 0.0, "Options": 0.0, "Hero-Zero": 0.0, "Total": 0.0}
        
        pnl = t["net_pnl"]
        if t["segment"] == "equity": daily_summary[d_str]["Equity"] += pnl
        elif t["segment"] == "commodity_futures": daily_summary[d_str]["Futures"] += pnl
        elif t["segment"] == "commodity_options": daily_summary[d_str]["Options"] += pnl
        elif t["segment"] == "hero_zero": daily_summary[d_str]["Hero-Zero"] += pnl
        daily_summary[d_str]["Total"] += pnl
        
    if not daily_summary:
        st.info("No trading history yet.")
    else:
        summary_df = pd.DataFrame(list(daily_summary.values())).sort_values(by="Date", ascending=False)
        for col in ["Equity", "Futures", "Options", "Hero-Zero", "Total"]:
            summary_df[col] = summary_df[col].apply(lambda x: f"₹{x:,.2f}")
        st.dataframe(summary_df, width="stretch", hide_index=True)
        
    st.write("---")
    st.subheader("🕰️ Historical Trades (Before Today)")
    if not all_historical_trades:
        st.info("No historical trades to display.")
    else:
        hist_df = pd.DataFrame([{
            "Date": get_date_str(t["timestamp"]),
            "Segment": " ".join(seg_names[t["segment"]].split(" ")[1:]),
            "Symbol": t["symbol"],
            "Qty": t["quantity"],
            "Entry (₹)": f"{t['entry_price']:.2f}",
            "Exit (₹)": f"{t['exit_price']:.2f}",
            "Net P&L": f"₹{t['net_pnl']:,.2f}"
        } for t in sorted(all_historical_trades, key=lambda x: x["timestamp"], reverse=True)])
        st.dataframe(hist_df, width="stretch", hide_index=True)

# Subscribers Tab
with tabs[5]:
    st.subheader("Manage Broadcast Subscriptions")
    with st.expander("➕ Add / Extend Subscription", expanded=True):
        col_s1, col_s2 = st.columns([2, 1])
        with col_s1:
            sub_id_input = st.text_input("Telegram User ID")
            segments_sel = st.multiselect("Allowed Call Types", ["Equity", "Commodity Futures", "Commodity Options", "Hero-Zero"], default=["Equity", "Commodity Futures", "Commodity Options", "Hero-Zero"])
        with col_s2:
            days_input = st.number_input("Days of Access", min_value=1, value=7)
            st.write("")
            if st.button("Grant Access", type="primary", use_container_width=True):
                if not sub_id_input or not segments_sel: st.error("Fill all fields.")
                else:
                    try:
                        seg_map = {"Equity": "equity", "Commodity Futures": "commodity_futures", "Commodity Options": "commodity_options", "Hero-Zero": "hero_zero"}
                        seg_str = ",".join([seg_map[s] for s in segments_sel])
                        uid_int = int(sub_id_input.strip())
                        conn = get_db_connection()
                        try:
                            cur = conn.cursor()
                            row = cur.execute("SELECT expiry_date FROM subscribers WHERE user_id=?", (uid_int,)).fetchone()
                            now_utc = datetime.now(timezone.utc)
                            if not row: st.error("User not found in database. Ask them to send /start to bot.")
                            else:
                                try: exp = datetime.fromisoformat(row["expiry_date"])
                                except: exp = now_utc
                                new_exp = (now_utc if exp < now_utc else exp) + timedelta(days=days_input)
                                cur.execute("UPDATE subscribers SET expiry_date=?, allowed_segments=? WHERE user_id=?", (new_exp.isoformat(), seg_str, uid_int))
                                conn.commit()
                                st.success(f"Access granted! Expiry: {to_ist(new_exp.isoformat())}")
                        finally:
                            conn.close()
                    except Exception as e: st.error(f"Error: {e}")

    with st.expander("❌ Delete Subscriber", expanded=False):
        st.error("⚠️ This action is permanent and cannot be undone.")
        col_d1, col_d2 = st.columns([2, 1])
        with col_d1:
            del_id_input = st.text_input("Enter Telegram User ID to verify deletion", key="del_sub_id")
        with col_d2:
            st.write("")
            st.write("")
            if st.button("Delete Subscriber", type="primary", use_container_width=True):
                if not del_id_input:
                    st.error("Enter a User ID.")
                else:
                    try:
                        uid_int = int(del_id_input.strip())
                        conn = get_db_connection()
                        try:
                            cur = conn.cursor()
                            if not cur.execute("SELECT user_id FROM subscribers WHERE user_id=?", (uid_int,)).fetchone():
                                st.error("User not found.")
                            else:
                                cur.execute("DELETE FROM subscribers WHERE user_id=?", (uid_int,))
                                conn.commit()
                                st.success("Subscriber deleted!")
                                time.sleep(1)
                                st.rerun()
                        finally:
                            conn.close()
                    except ValueError:
                        st.error("Invalid ID format.")

    with st.expander("⏭ Skip Today & Extend", expanded=False):
        st.info("Skip signals for today and automatically extend subscription by 1 day.")
        col_sk1, col_sk2 = st.columns([2, 1])
        with col_sk1:
            skip_id_input = st.text_input("Telegram User ID", key="skip_sub_id")
        with col_sk2:
            st.write("")
            st.write("")
            if st.button("Skip & Extend", use_container_width=True):
                if not skip_id_input:
                    st.error("Enter a User ID.")
                else:
                    try:
                        uid_int = int(skip_id_input.strip())
                        conn = get_db_connection()
                        try:
                            cur = conn.cursor()
                            row = cur.execute("SELECT expiry_date, skip_date FROM subscribers WHERE user_id=?", (uid_int,)).fetchone()
                            if not row:
                                st.error("User not found.")
                            else:
                                today_str = datetime.now(IST).date().isoformat()
                                if row["skip_date"] == today_str:
                                    st.warning("User has already been skipped for today.")
                                else:
                                    try: 
                                        exp = datetime.fromisoformat(row["expiry_date"])
                                        if exp.tzinfo is None:
                                            exp = exp.replace(tzinfo=timezone.utc)
                                    except: 
                                        exp = datetime.now(timezone.utc)
                                    new_exp = exp + timedelta(days=1)
                                    cur.execute("UPDATE subscribers SET skip_date=?, expiry_date=? WHERE user_id=?", (today_str, new_exp.isoformat(), uid_int))
                                    conn.commit()
                                    st.success(f"Done! Expiry extended to {to_ist(new_exp.isoformat())}")
                                    time.sleep(1)
                                    st.rerun()
                        finally:
                            conn.close()
                    except ValueError:
                        st.error("Invalid ID format.")

    st.write("---")
    try:
        conn = get_db_connection()
        try:
            subs = [dict(r) for r in conn.execute("SELECT * FROM subscribers ORDER BY expiry_date DESC").fetchall()]
        finally:
            conn.close()
            
        if not subs: st.info("No subscribers found.")
        else:
            active, expired = [], []
            now_utc = datetime.now(timezone.utc)
            today_str = datetime.now(IST).date().isoformat()
            for s in subs:
                try: 
                    exp_dt = datetime.fromisoformat(s["expiry_date"])
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    is_active = exp_dt > now_utc
                except: 
                    is_active = False
                row_data = {
                    "User ID": str(s["user_id"]), "Name": s["first_name"], "Username": f"@{s['username']}" if s.get("username") else "N/A",
                    "Segments": s.get("allowed_segments", "equity,commodity_futures,commodity_options,hero_zero").replace("_", " ").title(),
                    "Expiry (IST)": to_ist(s["expiry_date"]),
                    "Skipped Today": "Yes" if s.get("skip_date") == today_str else "No"
                }
                if is_active:
                    row_data["Status"] = "🟢 Active"
                    active.append(row_data)
                else:
                    row_data["Status"] = "🔴 Expired"
                    expired.append(row_data)
            st.markdown("### 🟢 Active Members")
            if active: st.dataframe(pd.DataFrame(active), width="stretch", hide_index=True)
            else: st.info("None")
            st.markdown("### 🔴 Pending & Expired")
            if expired: st.dataframe(pd.DataFrame(expired), width="stretch", hide_index=True)
            else: st.info("None")
    except Exception as e: st.warning(f"Error: {e}")

if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()

