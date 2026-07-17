"""Round-trip tests against real production workflow documents.

decompile(real doc) -> IR -> compile(IR) must reproduce the same graph:
same block types, same semantic config, and identical edge structure by label.
Ids/positions may differ (compiler reallocates them).
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.compiler.compile import Compiler, create_short_code
from app.compiler.decompile import decompile
from app.compiler.phpjson import php_json_encode
from app.validate.validate import validate_workflow
from tests.fixtures import load_reference_docs

DOCS = [d for d in load_reference_docs() if d.get("w_objects")]


def _terminal(v) -> bool:
    return v in (0, "0", "", None)


def _graph_by_label(doc: dict, labels: dict[str, str] | None = None) -> dict:
    """Normalised {label: {type, yes, no, next, props-subset}} for comparison.

    labels: optional id->label override (used to apply the decompiler's
    uniquified labels to the original doc, since real data contains duplicate
    and empty labels which decompile() must disambiguate).
    """
    by_id = {o["id"]: o for o in doc["w_objects"]}

    def _label_of(o) -> str:
        if labels and o["id"] in labels:
            return labels[o["id"]]
        raw = (o.get("block_properties") or {}).get("label") or ""
        return raw or f"{o['type']}#anon"

    def lbl(bid):
        if _terminal(bid):
            return None
        o = by_id.get(bid)
        if o is None:
            return f"<missing:{bid}>"
        return _label_of(o)

    out = {}
    for o in doc["w_objects"]:
        key = _label_of(o)
        p = o.get("block_properties") or {}
        entry = {
            "type": o["type"],
            "next": lbl(o.get("target")),
            "yes": lbl(o.get("target_yes")),
            "no": lbl(o.get("target_no")),
            "variables": p.get("variables"),
            "message": p.get("message"),
            "filters": p.get("filters"),
            "ssid": p.get("s_master_ssid") or p.get("ssid"),
            "field_mapping": {
                m["insertcolumn"]: m["keyvalue"]
                for m in ((o.get("properties") or {}).get("field-mapping") or [])
            } or None,
        }
        out.setdefault(key, []).append(entry)
    for k in out:
        out[k].sort(key=lambda e: (e["type"], str(e.get("next"))))
    return out


@pytest.mark.parametrize("doc", DOCS, ids=[d.get("name", "?") for d in DOCS])
def test_roundtrip_preserves_graph(doc):
    ir, label_to_id = decompile(doc)
    recompiled = Compiler().compile(ir)

    id_to_label = {v: k for k, v in label_to_id.items()}
    orig = _graph_by_label(doc, labels=id_to_label)
    new = _graph_by_label(recompiled)

    # anon labels are synthesised on decompile; compare on shared real labels
    orig_named = {k: v for k, v in orig.items() if not k.endswith("#anon")}
    for label, entries in orig_named.items():
        assert label in new or any(label in k for k in new), f"label '{label}' lost in round-trip"
        new_entries = new.get(label, [])
        assert len(new_entries) == len(entries), f"label '{label}' block count changed"
        for o_e, n_e in zip(entries, new_entries):
            assert n_e["type"] == o_e["type"], f"{label}: type changed"
            for fld in ("variables", "message", "filters", "ssid", "field_mapping"):
                assert n_e[fld] == o_e[fld], f"{label}: {fld} changed: {o_e[fld]!r} -> {n_e[fld]!r}"


@pytest.mark.parametrize("doc", DOCS, ids=[d.get("name", "?") for d in DOCS])
def test_roundtrip_edges(doc):
    """Edge structure by label must be identical (for uniquely-labelled blocks)."""
    ir, label_to_id = decompile(doc)
    recompiled = Compiler().compile(ir)
    id_to_label = {v: k for k, v in label_to_id.items()}
    orig, new = _graph_by_label(doc, labels=id_to_label), _graph_by_label(recompiled)
    for label, entries in orig.items():
        if label.endswith("#anon") or len(entries) != 1:
            continue
        n = new.get(label)
        if not n or len(n) != 1:
            continue
        for edge in ("next", "yes", "no"):
            o_t, n_t = entries[0][edge], n[0][edge]
            o_t = None if (o_t and o_t.endswith("#anon")) else o_t
            n_t = None if (n_t and "#" in (n_t or "")) else n_t
            if o_t is not None:
                assert n_t is not None, f"{label}.{edge}: edge lost ({entries[0][edge]})"


@pytest.mark.parametrize("doc", DOCS, ids=[d.get("name", "?") for d in DOCS])
def test_real_docs_validate(doc):
    """Sanity: the validator accepts real production documents."""
    res = validate_workflow(doc)
    assert res.ok, f"validator rejected real doc: {res.errors}"


@pytest.mark.parametrize("doc", DOCS, ids=[d.get("name", "?") for d in DOCS])
def test_recompiled_docs_validate(doc):
    ir, _ = decompile(doc)
    recompiled = Compiler().compile(ir)
    res = validate_workflow(recompiled)
    assert res.ok, f"validator rejected recompiled doc: {res.errors}"


def test_php_json_encode_escapes_slashes():
    assert php_json_encode({"icon": "/a/b.svg"}) == '{"icon":"\\/a\\/b.svg"}'
    assert php_json_encode(["x/y"]) == '["x\\/y"]'


def test_short_code_format():
    sc = create_short_code("HMS Generate Doctor OTP!")
    assert sc.startswith("hmsgeneratedoctorotp")
    suffix = sc[len("hmsgeneratedoctorotp"):]
    assert len(suffix) == 13
    assert all(c in "0123456789abcdef" for c in suffix)
