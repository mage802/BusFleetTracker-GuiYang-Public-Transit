"""Normalize a vehicle snapshot to the six-column operation-record schema."""
import datetime as dt
import math
import re

HEADER = ["车号", "运营线路", "运营方向", "下一站名称", "距离下站距离（米）", "采集时间"]


def display_line(line):
    match = re.fullmatch(r"([A-Za-z]*\d+)路", line)
    return match[1] if match else line


def extract_operations(obj, line, terminal, observed_at):
    records = []
    for bus in obj.get("list", []):
        vehicle = bus.get("busNumber")
        if vehicle is None or not str(vehicle).strip():
            continue
        status = str(bus.get("statusType", ""))
        station = str(bus.get("stationName") or "")
        distance = None
        quality = []
        if status == "0":
            # User convention: an arrived vehicle points to its current stop.
            distance = 0.0
        elif status == "2":
            try:
                value = float(bus["busToStationNiheDistance"])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid distance")
                distance = round(value, 2)
            except (KeyError, ValueError, TypeError, OverflowError):
                quality.append("missing_or_invalid_distance")
        else:
            station = ""
            quality.append("unverified_status_type")
        if not station:
            quality.append("missing_next_station")
        if not terminal:
            quality.append("missing_terminal")
        source_time = None
        try:
            value = bus.get("_recTime")
            if value is not None:
                source_time = dt.datetime.fromtimestamp(float(value) / 1000, dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="milliseconds")
        except (ValueError, TypeError, OverflowError, OSError):
            quality.append("invalid_source_time")
        records.append(dict(vehicle=str(vehicle).strip(), route=display_line(line),
                            terminal=terminal or "", next_station=station, distance_m=distance,
                            observed_at=observed_at, source_time=source_time, status_type=status,
                            quality=";".join(quality)))
    return records
