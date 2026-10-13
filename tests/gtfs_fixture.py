"""A tiny synthetic GTFS feed for the transit tests.

    A ──bus 1── B ──bus 1── C   (Sun–Thu 08:00/08:30/09:00, Fri 08:00; 5 min per hop)
                            C2 ──bus 2── D ──bus 2── E   (C2 is ~100 m from C; Sun–Thu 08:20/08:50/09:20)
    A ─────────── night bus 3 ──────────── E   (Tuesdays only, 24:10 → 24:40, i.e. after midnight)
    F ──rail── G ──rail── H   (Sun–Thu 08:10; headsign is a train number, like the MoT feed)

Stops are ~1 km apart so each one is only reachable on foot from its own
neighbourhood. Weekday services are removed on Monday 2026-10-19 ("holiday").
"""

from __future__ import annotations

import zipfile
from pathlib import Path

STOPS = {  # id: (name, lat, lon, desc)
    "A": ("אלף/הרצל", 32.0000, 34.8000, "רחוב: הרצל 1 עיר: תל אביב יפו רציף:  קומה: "),
    "B": ("בית/בוגרשוב", 32.0000, 34.8100, "רחוב: בוגרשוב עיר: תל אביב יפו רציף:  קומה: "),
    "C": ("גימל/מרכז", 32.0000, 34.8200, "רחוב:  עיר: תל אביב יפו רציף:  קומה: "),
    "C2": ("גימל 2/מסוף", 32.0009, 34.8200, "רחוב:  עיר: תל אביב יפו רציף:  קומה: "),
    "D": ("דלת/ויצמן", 32.0000, 34.8300, "רחוב:  עיר: רמת גן רציף:  קומה: "),
    "E": ("הא/קניון", 32.0100, 34.8300, "רחוב:  עיר: רמת גן רציף:  קומה: "),
    "F": ("תחנת פא", 32.0500, 34.8000, "רחוב:  עיר: הרצליה רציף: 1 קומה: "),
    "G": ("תחנת גימל רכבת", 32.1000, 34.8000, "רחוב:  עיר: נתניה רציף: 2 קומה: "),
    "H": ("תחנת חית", 32.1500, 34.8000, "רחוב:  עיר: חדרה רציף:  קומה: "),
}

FEED_START, FEED_END = "20261001", "20261130"
WEDNESDAY = "2026-10-14"
TUESDAY = "2026-10-13"
HOLIDAY = "2026-10-19"


def _hms(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}:00"


def write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    files: dict[str, list[str]] = {}
    files["agency.txt"] = ["agency_id,agency_name,agency_url,agency_timezone",
                           "3,אגד,http://x,Asia/Jerusalem", "2,רכבת ישראל,http://y,Asia/Jerusalem"]
    files["routes.txt"] = ["route_id,agency_id,route_short_name,route_long_name,route_desc,route_type,route_color",
                           "r1,3,1,אלף-תל אביב יפו<->גימל-תל אביב יפו-1#,x,3,",
                           "r2,3,2,גימל 2-תל אביב יפו<->הא-רמת גן-1#,x,3,",
                           "r3,3,3,אלף-תל אביב יפו<->הא-רמת גן-1#,x,3,",
                           "r9,2,,תחנת פא-הרצליה<->תחנת חית-חדרה,x,2,",
                           "rx,3,99,קו בהזמנה,x,715,"]
    files["stops.txt"] = ["stop_id,stop_code,stop_name,stop_desc,stop_lat,stop_lon,location_type,parent_station"]
    for i, (sid, (name, lat, lon, desc)) in enumerate(STOPS.items()):
        files["stops.txt"].append(f"{sid},{1000 + i},{name},{desc},{lat},{lon},0,")
    files["calendar.txt"] = ["service_id,sunday,monday,tuesday,wednesday,thursday,friday,saturday,start_date,end_date",
                             f"WD,1,1,1,1,1,0,0,{FEED_START},{FEED_END}",
                             f"FRI,0,0,0,0,0,1,0,{FEED_START},{FEED_END}",
                             f"TUE,0,0,1,0,0,0,0,{FEED_START},{FEED_END}"]
    files["calendar_dates.txt"] = ["service_id,date,exception_type", "WD,20261019,2"]
    trips = ["route_id,service_id,trip_id,trip_headsign,direction_id,shape_id"]
    st = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type,shape_dist_traveled"]

    def add(route, svc, tid, head, shape, stops, start_min, hop=5):
        trips.append(f"{route},{svc},{tid},{head},0,{shape}")
        for k, s in enumerate(stops):
            t = _hms(start_min + k * hop)
            st.append(f"{tid},{t},{t},{s},{k + 1},0,0,{k * 950}")

    for h in (8 * 60, 8 * 60 + 30, 9 * 60):
        add("r1", "WD", f"1_{h}", "גימל", "S1", ["A", "B", "C"], h)
    add("r1", "FRI", "1_fri", "גימל", "S1", ["A", "B", "C"], 8 * 60)
    for h in (8 * 60 + 20, 8 * 60 + 50, 9 * 60 + 20):
        add("r2", "WD", f"2_{h}", "הא", "", ["C2", "D", "E"], h)
    add("r3", "TUE", "3_night", "הא", "", ["A", "E"], 24 * 60 + 10, hop=30)
    add("r9", "WD", "rail_1", "123", "", ["F", "G", "H"], 8 * 60 + 10, hop=10)
    add("rx", "WD", "x_1", "", "", ["A", "B"], 8 * 60)
    files["trips.txt"] = trips
    files["stop_times.txt"] = st
    files["shapes.txt"] = ["shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence"]
    for k, lon in enumerate([34.8000, 34.8050, 34.8100, 34.8150, 34.8200]):
        files["shapes.txt"].append(f"S1,{32.0002 if k % 2 else 32.0},{lon},{k + 1}")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, lines in files.items():
            z.writestr(name, "﻿" + "\r\n".join(lines) + "\r\n")
    return path
