"""How much of the Daily Assessment the pipeline filled, and how much is left for manual chart abstraction.

Unit = one REDCap question on one Available participant-day (a lab value and its "not collected" flag are one
question; a checkbox and its options are one question). Each question-day is
  auto     answered by the pipeline; auto_absence is the subset answered only from the absence of charting
           (lab "not collected", GCS "not documented", "no PaO2", room air with no device documented in 12 h)
  partial  answered in part (nutrition: oral / TPN filled, tube feeding left for the abstractor)
  manual   shown by the form's branching but left blank: chart abstraction needed
  pending  hidden only because a parent question is still manual (may become manual once the parent is answered)
  n/a      hidden by branching given the answers
Output is aggregate only (counts 1-10 shown as "<11"; percentages only when the denominator is >= 11).
"""
import re

import pandas as pd

from redcap_branching import Branching

PARTIAL_ITEMS = {"daily_nutrition_8a_{d}"}
COUNTS = ["auto", "auto_absence", "partial", "manual", "pending"]
SMALL = 11


def _suppress(n):
    return "<11" if 0 < n < SMALL else str(int(n))


def _pct(num, den):
    return f"{100 * num / den:.1f}" if den >= SMALL else ""


def item_map(spec, mapping):
    """field_template -> question (item) it belongs to. A "not collected" companion (branching "[value] = ''")
    belongs to its value field."""
    companion = re.compile(r"^\[([a-z0-9_]+)\] = ''$")
    nc_to_value = {}
    for t, row in spec.iterrows():
        m = companion.match(row["branching_logic"].replace("{d}", "dsuffix"))
        if m:
            nc_to_value[t] = m.group(1).replace("dsuffix", "{d}")
    out = {}
    for t, row in spec.iterrows():
        if row["field_type"] == "checkbox":
            continue
        out[t] = row["parent"] or nc_to_value.get(t, t)
    return out


def classify(long, day_df, spec, mapping):
    """Per item: counts of auto / partial / manual / pending over Available participant-days."""
    items = item_map(spec, mapping)
    order = list(dict.fromkeys(items[t] for t in spec.index if t in items))
    fields_of = {i: [t for t in spec.index if items.get(t) == i] for i in order}
    gate = mapping["gate_field"]
    rules = {}
    counts = {i: dict(auto=0, auto_absence=0, partial=0, manual=0, pending=0) for i in order}
    values = {(r, d): {} for r, d in zip(long["record_id"], long["day"])}
    basis = {}
    for r, d, s, t, v, b in zip(long["record_id"], long["day"], long["suffix"], long["field_template"], long["value"], long["basis"]):
        values[(r, d)][t.replace("{d}", s)] = v
        basis[(r, d, t.replace("{d}", s))] = b
    avail = day_df[day_df["available"]]
    for r, d, s in zip(avail["record_id"], avail["day"], avail["suffix"]):
        vals = values.get((r, d), {})
        status = {}
        for i in order:
            if i == gate:
                status[i] = "auto"
                counts[i]["auto"] += 1
                continue
            names = [t.replace("{d}", s) for t in fields_of[i]]
            answered = any(vals.get(n, "") == "1" for n in names) if spec.loc[fields_of[i][0], "field_type"] == "checkbox_option" \
                else any(vals.get(n, "") != "" for n in names)
            shown, refs = False, []
            for t in fields_of[i]:
                key = (t, s)
                if key not in rules:
                    rules[key] = Branching(spec.loc[t, "branching_logic"].replace("{d}", s))
                shown |= rules[key].shown(vals)
                refs += rules[key].refs
            if answered:
                st = "partial" if i in PARTIAL_ITEMS else "auto"
            elif shown:
                st = "manual"
            else:
                parent_items = {items.get(_template(n, s)) for n in refs if vals.get(n, "") == ""}
                st = "pending" if any(status.get(p) in ("manual", "pending") for p in parent_items) else "na"
            status[i] = st
            if st != "na":
                counts[i][st] += 1
            if st == "auto" and all(basis.get((r, d, n), "data") == "absence" for n in names if vals.get(n, "") != ""):
                counts[i]["auto_absence"] += 1
    return counts, order, len(avail)


def _template(name, suffix):
    base, sep, opt = name.partition("___")
    t = base[: -len(suffix)] + "{d}" if base.endswith("_" + suffix) else base.replace(f"_{suffix}_", "_{d}_")
    return t + (sep + opt if sep else "")


def write_coverage(long, day_df, spec, mapping, days, path):
    counts, order, n_days = classify(long, day_df, spec, mapping)
    section = {i: spec.loc[i, "section"] if i in spec.index else "" for i in order}
    rows = []
    for i in order:
        c = counts[i]
        rows.append({"level": "question", "section": section[i], "question": i, **c})
    df = pd.DataFrame(rows)
    roll = []
    for sec, g in df.groupby("section", sort=False):
        roll.append({"level": "section", "section": sec, "question": f"{len(g)} questions", **g[COUNTS].sum().to_dict()})
    roll.append({"level": "total", "section": "Daily Assessment", "question": f"{len(df)} questions", **df[COUNTS].sum().to_dict()})
    out = pd.concat([pd.DataFrame(roll), df], ignore_index=True)
    out["applicable"] = out["auto"] + out["partial"] + out["manual"]
    out["pct_auto"] = [_pct(a, n) for a, n in zip(out["auto"], out["applicable"])]
    out["pct_auto_or_partial"] = [_pct(a + p, n) for a, p, n in zip(out["auto"], out["partial"], out["applicable"])]
    out["participant_days"] = n_days
    rep = out[["level", "section", "question", "participant_days", "applicable", *COUNTS, "pct_auto", "pct_auto_or_partial"]].copy()
    for c in ["participant_days", "applicable", *COUNTS]:
        rep[c] = rep[c].map(_suppress)
    path.parent.mkdir(parents=True, exist_ok=True)
    rep.to_csv(path, index=False)
    return rep
