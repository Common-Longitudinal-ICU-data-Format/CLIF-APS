"""Time-anchored derivations for the APS daily assessment, used by run_aps.py (rules in config/redcap/daily_mapping.yaml).

All inputs are site-local, tz-aware DataFrames with a hospitalization_id. A "query" frame has one row per question
asked: columns key (any hashable), hospitalization_id, t (the anchor time).
"""
import re

import pandas as pd

RESP_SETTINGS = ["fio2_set", "lpm_set", "peep_set", "peep_obs", "pressure_support_set", "pressure_control_set",
                 "tidal_volume_set", "tidal_volume_obs", "resp_rate_obs", "mean_airway_pressure_obs",
                 "peak_inspiratory_pressure_obs", "peak_inspiratory_pressure_set", "plateau_pressure_obs", "minute_vent_obs"]


def norm_unit(u):
    if u is None or pd.isna(u):
        return None
    return re.sub(r"\s+", "", str(u)).lower().replace("µ", "mc").replace("μ", "mc").replace("ug", "mcg").replace("hour", "hr")


def _asof(q, events, time_col, tolerance=None, direction="backward"):
    """merge_asof by hospitalization_id; q needs key, hospitalization_id, t."""
    left = q.assign(t=pd.to_datetime(q["t"]).dt.as_unit("ns")).sort_values("t")
    right = events.assign(**{time_col: pd.to_datetime(events[time_col]).dt.as_unit("ns")}).sort_values(time_col)
    left["hospitalization_id"] = left["hospitalization_id"].astype(str)
    right["hospitalization_id"] = right["hospitalization_id"].astype(str)
    kw = {"tolerance": tolerance} if tolerance is not None else {}
    return pd.merge_asof(left, right, left_on="t", right_on=time_col, by="hospitalization_id", direction=direction, **kw)


# ------------------------------------------------------------------------------------------- respiratory support
def resp_timeline(resp):
    """One row per (hospitalization, recorded_dttm); device carried to rows without one; settings and mode carried
    forward within a device episode, each with the time it was last charted."""
    cols = ["hospitalization_id", "recorded_dttm", "device_category", "mode_category"] + RESP_SETTINGS
    r = resp.reindex(columns=cols).copy()
    # same timestamp charted twice: first non-missing per column after sorting by device (deterministic)
    r = (r.sort_values(["hospitalization_id", "recorded_dttm", "device_category"], na_position="last")
         .groupby(["hospitalization_id", "recorded_dttm"], as_index=False, sort=True).first())
    r["device"] = r.groupby("hospitalization_id")["device_category"].ffill()
    dev = r["device"].fillna("__none__")
    r["episode"] = ((r["hospitalization_id"] != r["hospitalization_id"].shift()) | (dev != dev.shift())).cumsum()
    for col in ["mode_category"] + RESP_SETTINGS:
        r[col + "__t"] = r["recorded_dttm"].where(r[col].notna()).groupby(r["episode"]).ffill()
        r[col] = r.groupby("episode")[col].ffill()
    return r


def resp_at(timeline, q, carry_h, room_air_h, ecmo_times=None):
    """Respiratory support in effect at each query time.
    Returns q columns + device, status (documented | room_air_inferred | stale | none), ecmo (bool), settings."""
    carry = pd.Timedelta(hours=carry_h)
    m = _asof(q, timeline, "recorded_dttm")
    age = m["t"] - m["recorded_dttm"]
    documented = m["recorded_dttm"].notna() & (age <= carry) & m["device"].notna()
    none_recent = m["recorded_dttm"].isna() | (age > pd.Timedelta(hours=room_air_h))
    m["status"] = "stale"
    m.loc[documented, "status"] = "documented"
    m.loc[~documented & none_recent, "status"] = "room_air_inferred"
    m.loc[m["status"] == "room_air_inferred", "device"] = "room_air"
    m.loc[m["status"] == "stale", "device"] = None
    for col in ["mode_category"] + RESP_SETTINGS:
        fresh = documented & ((m["t"] - m[col + "__t"]) <= carry)
        m[col] = m[col].where(fresh)
    m["ecmo"] = False
    if ecmo_times is not None and not ecmo_times.empty:
        e = _asof(q[["key", "hospitalization_id", "t"]], ecmo_times.rename(columns={"recorded_dttm": "ecmo_t"}), "ecmo_t",
                  tolerance=carry)
        m = m.merge(e[["key", "ecmo_t"]], on="key", how="left")
        m["ecmo"] = m["ecmo_t"].notna()
    return m.drop(columns=[c for c in m.columns if c.endswith("__t") or c in ("episode", "device_category")])


def support_code(device, ecmo, codes):
    if device is None or pd.isna(device):
        return ""
    if ecmo:
        return codes["ecmo_with_imv"] if device == "imv" else codes["ecmo_without_imv"]
    return codes.get(device, codes["other"])


def nearest_value(events, q, window_min):
    """events: hospitalization_id, time, value (duplicates at one time collapsed to the lowest value).
    Nearest event to t within +-window; ties go to the earlier event. Returns Series indexed like q.key."""
    ev = (events.dropna(subset=["value"]).sort_values(["hospitalization_id", "time", "value"])
          .drop_duplicates(["hospitalization_id", "time"]))
    tol = pd.Timedelta(minutes=window_min)
    b = _asof(q, ev, "time", tolerance=tol, direction="backward").set_index("key")
    f = _asof(q, ev, "time", tolerance=tol, direction="forward").set_index("key")
    db = (b["t"] - b["time"]).abs()
    df = (f["time"] - f["t"]).abs()
    use_f = b["time"].isna() | (f["time"].notna() & (df < db))
    return b["value"].where(~use_f, f["value"])


# ------------------------------------------------------------------------------------------------- infusions
def infusion_at(meds, q, carry_h):
    """Last row of one medication at or before t within carry_h. A stop or not_administered row at the same time
    wins over other rows. Returns q + running (bool), med_dose, med_dose_unit."""
    m = meds.copy()
    m["_stop"] = (m["mar_action_category"].astype(str).str.lower().eq("stop")
                  | m.get("mar_action_group", pd.Series("", index=m.index)).astype(str).str.lower().eq("not_administered"))
    m = (m.sort_values(["hospitalization_id", "admin_dttm", "_stop", "med_dose"])
         .drop_duplicates(["hospitalization_id", "admin_dttm"], keep="last"))
    r = _asof(q, m[["hospitalization_id", "admin_dttm", "_stop", "med_dose", "med_dose_unit"]], "admin_dttm",
              tolerance=pd.Timedelta(hours=carry_h))
    r["running"] = r["admin_dttm"].notna() & ~r["_stop"].fillna(True).astype(bool) & (r["med_dose"].fillna(0) > 0)
    return r
