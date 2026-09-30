import datetime as dt
import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.common import load_config

M2_PER_ACRE = 4046.8564224


@pytest.fixture
def cfg():
    return load_config()


def gen_row(pid, name, mw, year, month=6, dc=None, ba="ERCO", county="Pecos", state="TX", src="SUN", pm="PV", gid="PV1"):
    return {
        "Entity Name": "Op " + name, "Plant ID": pid, "Plant Name": name, "Plant State": state, "County": county,
        "Balancing Authority Code": ba, "Generator ID": gid, "Nameplate Capacity (MW)": mw, "DC Net Capacity (MW)": dc,
        "Operating Month": month, "Operating Year": year, "Energy Source Code": src, "Prime Mover Code": pm,
        "Latitude": 31.0, "Longitude": -103.0, "source_month": "2026-08",
    }


@pytest.fixture
def generators():
    return pd.DataFrame([
        gen_row(1, "Alpha", 50, 2017, 6, dc=65, gid="PV1"),     # 2 phases, 80 MW, DC 104 → ILR 1.30
        gen_row(1, "Alpha", 30, 2018, 3, dc=39, gid="PV2"),
        gen_row(1, "Alpha", 20, 2019, 1, src="MWH", pm="BA", gid="BESS"),  # storage, excluded from MW
        gen_row(2, "Bravo", 150, 2016, 9),                      # no EIA DC → USPVDB DC
        gen_row(3, "Charlie", 8, 2018),                         # < 10 MW
        gen_row(4, "Delta", 60, 2023, 2),                       # COD too late
        gen_row(5, "Echo", 30, 2019),                           # 100 acres → 3.3 ac/MW
        gen_row(6, "Foxtrot", 40, 2020),                        # no polygon → review
        gen_row(7, "Golf", 20, 2021, ba="EPE", county="El Paso"),  # non-ERCOT flag, still passes
        gen_row(8, "Hotel", 100, 2015, 1, dc=130),              # boundary: exactly 100 MW, planned load 74.75
        gen_row(9, "India", 200, 2019, state="NM", county="Lea"),  # not TX
    ])


def _square(acres, x0):
    side = math.sqrt(acres * M2_PER_ACRE)
    return box(x0, 800000, x0 + side, 800000 + side)


@pytest.fixture
def polygons():
    rows = [  # eia_id, acres, ac, dc, year, county
        (1, 640, 80, 104, 2017, "Pecos County"),
        (2, 1200, 150, 195, 2016, "Pecos County"),
        (3, 70, 8, 10, 2018, "Pecos County"),
        (4, 480, 60, 78, 2023, "Pecos County"),
        (5, 100, 30, 39, 2019, "Pecos County"),
        (7, 160, 20, 26, 2021, "El Paso County"),
        (8, 800, 100, 130, 2015, "Pecos County"),
        (99, 300, 40, 52, 2020, "Pecos County"),   # orphan: not in EIA-860M
        (None, 90, 12, 15, 2018, "Pecos County"),  # no eia_id
        (1, 0, 0, 0, 2017, "Lea County"),          # different state row ignored below
    ]
    geoms, recs = [], []
    for i, (eid, acres, ac, dc, yr, cty) in enumerate(rows):
        state = "NM" if cty == "Lea County" else "TX"
        geoms.append(_square(max(acres, 1), -600000 + i * 20000))
        recs.append({"case_id": i, "eia_id": eid, "p_state": state, "p_county": cty, "p_area": acres * M2_PER_ACRE,
                     "p_cap_ac": ac, "p_cap_dc": dc, "p_year": yr, "p_axis": "single-axis", "p_tech_pri": "crystalline"})
    return gpd.GeoDataFrame(recs, geometry=geoms, crs="EPSG:5070").to_crs("EPSG:4326")


@pytest.fixture
def as_of():
    return dt.date(2026, 9, 30)
