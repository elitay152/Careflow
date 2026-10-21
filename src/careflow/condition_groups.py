import json
from functools import cache
from importlib.resources import files


@cache
def catalog():
    return json.loads(files("careflow").joinpath("condition_groups.json").read_text())


def condition_group(code_system, code):
    if code_system != "http://snomed.info/sct":
        return "unclassified"
    return catalog().get(code, {}).get("group", "unclassified")
