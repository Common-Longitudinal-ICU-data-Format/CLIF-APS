"""Evaluate REDCap branching logic ("Show the field ONLY if") as it appears in config/redcap/daily_fields.csv.

Supports what the APS daily instruments use: [field], [checkbox(code)], = and <> against quoted strings, AND / OR.
Values are REDCap import strings; a blank checkbox option counts as unchecked ('0'), as in REDCap.

    rule = Branching("[daily_resp_8a_0] = '1' OR [daily_resp_8a_0] = '3'")
    rule.shown({"daily_resp_8a_0": "3"})   # True
    rule.refs                              # ['daily_resp_8a_0']
"""
import re

TOKEN = re.compile(r"\[([a-z0-9_]+)(?:\(([a-z0-9_]+)\))?\]")
STRING = re.compile(r"'[^']*'|\"[^\"]*\"")


class Branching:
    def __init__(self, expr: str):
        self.expr = (expr or "").strip()
        self.refs = []
        if not self.expr:
            self._code = None
            return

        def ref(m):
            name = m.group(1) + (f"___{m.group(2)}" if m.group(2) else "")
            self.refs.append(name)
            return f"v({name!r})"

        # translate outside string literals only
        parts, last = [], 0
        for s in STRING.finditer(self.expr):
            parts.append(self._translate(self.expr[last:s.start()], ref))
            parts.append(s.group(0))
            last = s.end()
        parts.append(self._translate(self.expr[last:], ref))
        self._code = compile("".join(parts), "<branching>", "eval")

    @staticmethod
    def _translate(text, ref):
        text = TOKEN.sub(ref, text)
        text = text.replace("<>", "!=")
        text = re.sub(r"(?<![!<>=])=(?!=)", "==", text)
        text = re.sub(r"\band\b", " and ", text, flags=re.I)
        return re.sub(r"\bor\b", " or ", text, flags=re.I)

    def shown(self, values: dict) -> bool:
        if self._code is None:
            return True

        def v(name):
            val = values.get(name, "")
            return "0" if val == "" and "___" in name else val

        return bool(eval(self._code, {"__builtins__": {}}, {"v": v}))


def _selftest():
    b = Branching("[a_0] = '1' OR [a_0] = '3'")
    assert b.shown({"a_0": "3"}) and not b.shown({"a_0": "2"}) and not b.shown({}) and b.refs == ["a_0", "a_0"]
    b = Branching("[m] <> '' AND [m] <> '99'")
    assert b.shown({"m": "1"}) and not b.shown({"m": ""}) and not b.shown({"m": "99"})
    b = Branching("[vp_0(1)] = '1'")
    assert b.shown({"vp_0___1": "1"}) and not b.shown({"vp_0___1": "0"}) and not b.shown({})
    b = Branching("[x] = ''")
    assert b.shown({}) and not b.shown({"x": "5"})
    b = Branching("[r] = '1' or [r] = '3' or [r] = '4'")
    assert b.shown({"r": "4"}) and not b.shown({"r": "6"})
    assert Branching("").shown({})
    print("redcap_branching selftest ok")


if __name__ == "__main__":
    _selftest()
