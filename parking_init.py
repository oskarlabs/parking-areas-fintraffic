# app.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timezone
import pytz

st.set_page_config(page_title="Pysäköintialueen täyttöaste", layout="centered")

API_BASE = "https://parking.fintraffic.fi/api/v1"
HEADERS = {"Accept": "application/json"}
TZ = pytz.timezone("Europe/Helsinki")

@st.cache_data(ttl=60)
def fetch_facilities_df() -> pd.DataFrame:
    url = f"{API_BASE}/facilities"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    fac = pd.json_normalize(data if isinstance(data, list) else data.get("results", data))
    # Vain tarpeelliset sarakkeet
    cols = [c for c in ["id", "name.fi", "name.en", "name.sv", "address.fi", "address.en", "address.sv"] if c in fac.columns]
    return fac[cols].rename(columns={"name.fi": "name_fi"})

@st.cache_data(ttl=30)
def fetch_utilizations_df() -> pd.DataFrame:
    url = f"{API_BASE}/utilizations"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    u = resp.json()
    util = pd.DataFrame(u)
    if util.empty:
        return util
    # Laske täyttöasteet
    util["occupancy"] = (util["capacity"] - util["spacesAvailable"]) / util["capacity"]
    util["occupancy"] = util["occupancy"].clip(lower=0, upper=1)
    util["occupancy_pct"] = (util["occupancy"] * 100).round(1)
    # Aikaleima datetimeksi
    util["timestamp"] = pd.to_datetime(util["timestamp"], utc=True, errors="coerce")
    return util

def helsinki_time(dt_utc: pd.Timestamp) -> str:
    if pd.isna(dt_utc):
        return "-"
    local_dt = dt_utc.tz_convert(TZ)
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")

st.title("🚗 Pysäköintialueen täyttöaste")
st.caption("Tieto lähde: Fintraffic LIIPI API")

# --- Data ---
try:
    with st.spinner("Haetaan kohdelista..."):
        fac = fetch_facilities_df()
    with st.spinner("Haetaan täyttöasteet..."):
        util = fetch_utilizations_df()
except Exception as e:
    st.error(f"Datassa tapahtui virhe: {e}")
    st.stop()

if fac.empty:
    st.warning("Kohdelista on tyhjä.")
    st.stop()

# Rakennetaan valikon vaihtoehdot: nimi (id)
name_map = (
    fac.assign(display=lambda d: d.apply(lambda r: f"{r['name_fi'] or r.get('name.en') or 'Nimetön'} (id {r['id']})", axis=1))
    .set_index("display")["id"]
    .to_dict()
)

selection = st.selectbox("Valitse pysäköintilaitos", options=list(name_map.keys()))

show = st.button("näytä p-alue")

if show:
    facility_id = name_map[selection]

    if util.empty or "facilityId" not in util.columns:
        st.warning("Täyttöastetietoja ei saatavilla juuri nyt.")
        st.stop()

    df = util.merge(fac[["id", "name_fi"]], left_on="facilityId", right_on="id", how="left")

    # Viimeisin rivi per (facilityId, usage, capacityType)
    latest = (
        df.sort_values("timestamp")
          .groupby(["facilityId", "usage", "capacityType"], as_index=False, dropna=False)
          .tail(1)
    )
    latest = latest[latest["facilityId"] == facility_id].copy()

    if latest.empty:
        st.info("Tälle kohteelle ei löytynyt käyttöastetietoja.")
        st.stop()

    # Otsikko ja perustiedot
    area_name = latest["name_fi"].iloc[0] if "name_fi" in latest.columns else selection
    st.subheader(area_name)

    # Yhteenveto (jos useita rivejä, näytetään kooste)
    # Lasketaan kokonaissumma kapasiteetista ja vapaista paikoista capacityType/usage yli
    summary = latest.agg(
        capacity=("capacity", "sum"),
        spacesAvailable=("spacesAvailable", "sum")
    )
    if summary["capacity"] and summary["capacity"] > 0:
        occ_pct_total = round((1 - summary["spacesAvailable"] / summary["capacity"]) * 100, 1)
    else:
        occ_pct_total = None

    cols = st.columns(3)
    cols[0].metric("Kapasiteetti (yht.)", int(summary["capacity"]))
    cols[1].metric("Vapaat paikat (yht.)", int(summary["spacesAvailable"]))
    cols[2].metric("Täyttöaste (yht.)", f"{occ_pct_total:.1f} %" if occ_pct_total is not None else "-")

    # Yksityiskohtainen taulukko per käyttötyyppi/kapasiteettityyppi
    pretty = latest[[
        "usage", "capacityType", "capacity", "spacesAvailable", "occupancy_pct", "timestamp"
    ]].copy()

    pretty.rename(columns={
        "usage": "Käyttö",
        "capacityType": "Tyyppi",
        "capacity": "Kapasiteetti",
        "spacesAvailable": "Vapaita",
        "occupancy_pct": "Täyttöaste (%)",
        "timestamp": "Aikaleima"
    }, inplace=True)

    # Muutetaan aikaleimat Helsingin aikaan stringeiksi
    pretty["Aikaleima"] = pretty["Aikaleima"].apply(lambda t: helsinki_time(t))

    st.markdown("**Rivit per käyttötyyppi / kapasiteettityyppi (uusin havainto):**")
    st.dataframe(pretty.reset_index(drop=True), use_container_width=True)

    # Viimeisin havaintoaika (max timestamp)
    last_ts = latest["timestamp"].max()
    st.caption(f"Viimeisin havainto: {helsinki_time(last_ts)}")
else:
    st.info("Valitse pysäköintialue ja paina **näytä p-alue** nähdäksesi tilanteen.")
