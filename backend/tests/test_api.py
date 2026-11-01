import time

import cv2
import pytest
from fastapi.testclient import TestClient

from app.main import app
from chart_fixtures import make_chart

MNQ = dict(p_lo=21350, p_hi=21560, step=25, decimals=2)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def png(img) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def wait_processed(client, trade_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/trades/{trade_id}").json()
        if t["processing_status"] not in ("queued", "processing"):
            return t
        time.sleep(0.5)
    raise AssertionError("extraction did not finish")


def upload_mnq_trade(client) -> dict:
    m1 = make_chart(header="MNQ1!, 1, CME", note=None, entry=21450, sl=21420, tp=21510, **MNQ)
    m5 = make_chart(header="MNQ1!, 5, CME", note="TP 2R  TP reason: previous high liquidity", tool=False, seed=3, **MNQ)
    r = client.post("/api/trades/upload",
                    files=[("files", ("m1 entry.png", png(m1), "image/png")), ("files", ("m5 setup.png", png(m5), "image/png"))],
                    data={"mode": "single", "folder_name": "MNQ long 03-11-2026"})
    assert r.status_code == 200, r.text
    (tid,) = r.json()["trade_ids"]
    return wait_processed(client, tid)


def test_upload_multi_image_trade(client):
    t = upload_mnq_trade(client)
    assert t["processing_status"] == "partial"  # no AI key in tests: vision stage skipped, not failed
    assert t["asset"] == "MNQ" and t["market"] == "Futures"
    assert t["direction"] == "Long"
    assert (t["entry_price"], t["stop_loss"], t["take_profit"]) == (21450, 21420, 21510)
    assert t["risk_reward"] == 2.0
    assert t["r_multiple"] == 2.0 and "liquidity" in t["tp_reason"].lower()
    assert t["entry_timeframe"] == "M1" and t["setup_timeframe"] == "M5"
    assert t["trade_date"].startswith("2026-11-03")
    roles = {i["file_name"]: i["role"] for i in t["images"]}
    assert roles == {"m1 entry.png": "entry", "m5 setup.png": "setup"}
    assert t["field_meta"]["entry_price"]["source"] in ("cv", "cv+ocr")


def test_verified_fields_survive_reprocessing(client):
    t = upload_mnq_trade(client)
    tid = t["id"]
    r = client.patch(f"/api/trades/{tid}", json={"values": {"entry_price": 21452.5, "strategy_name": "EMA pullback"}})
    assert r.status_code == 200
    assert r.json()["field_meta"]["entry_price"]["status"] == "verified"
    assert r.json()["risk_reward"] == pytest.approx(1.77, abs=0.01)  # recomputed from the manual entry

    client.post(f"/api/trades/{tid}/reprocess")
    t = wait_processed(client, tid)
    assert t["entry_price"] == 21452.5  # AI did not overwrite the user's value...
    sug = [s for s in t["suggestions"] if s["field"] == "entry_price"]
    assert sug and sug[0]["value"] == 21450  # ...it proposed its reading instead

    t = client.post(f"/api/suggestions/{sug[0]['id']}/approve").json()
    assert t["entry_price"] == 21450 and t["field_meta"]["entry_price"]["status"] == "verified"
    actions = [h["action"] for h in client.get(f"/api/trades/{tid}/history").json()]
    assert {"edit", "approve", "extract", "create"} <= set(actions)


def test_manual_trade_validation_and_analytics(client):
    assert client.post("/api/trades", json={"values": {"entry_price": "abc"}}).status_code == 422
    rows = [  # (entry, sl, exit, direction) -> R of +2, -1, +1.5, -1
        (100, 99, 102, "Long"), (100, 99, 99, "Long"), (100, 101, 98.5, "Short"), (100, 101, 101, "Short"),
    ]
    for i, (e, sl, x, d) in enumerate(rows):
        r = client.post("/api/trades", json={"values": {
            "trade_date": f"2026-10-0{i + 1}", "asset": "TESTX", "direction": d, "entry_price": e, "stop_loss": sl,
            "exit_price": x, "pnl": (x - e) * (1 if d == "Long" else -1) * 100, "strategy_name": "Test strat",
            "mistakes": ["Early exit"] if i == 2 else None}})
        assert r.status_code == 200, r.text
        assert r.json()["outcome"] in ("Win", "Loss")
    a = client.get("/api/analytics", params={"asset": "TESTX"}).json()
    o = a["overview"]
    assert o["wins"] == 2 and o["losses"] == 2 and o["win_rate"] == 50.0
    assert o["total_r"] == 1.5 and o["avg_r"] == 0.38
    assert o["net_pnl"] == 150 and o["profit_factor"] == 1.75
    assert o["max_drawdown"] == -100
    assert a["breakdowns"]["strategy_name"][0]["key"] == "Test strat"
    tags = {t["tag"]: t for t in a["behavior"]["mistake_tags"]}
    assert tags["Early exit"]["count"] == 1


def test_upload_rejects_non_images(client):
    r = client.post("/api/trades/upload", files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 415


def test_account_balance_and_pnl_from_r(client):
    assert client.get("/api/account").json()["risk_per_trade"] == 120  # default risk
    assert client.put("/api/account", json={"starting_balance": -5}).status_code == 422
    ids = []
    for i, r in enumerate([3.2, -1, 0, 1.5]):
        t = client.post("/api/trades", json={"values": {"trade_date": f"2026-09-0{i + 1}", "asset": "ACCT",
                                                        "direction": "Long", "r_multiple": r}}).json()
        ids.append(t["id"])
    t = client.get(f"/api/trades/{ids[0]}").json()
    assert t["pnl"] == 384 and t["risk_amount"] == 120  # 3.2R × $120, risk stamped on the trade
    assert t["field_meta"]["pnl"]["source"] == "computed"

    client.patch(f"/api/trades/{ids[3]}", json={"values": {"risk_amount": 200}})  # this one risked more
    assert client.get(f"/api/trades/{ids[3]}").json()["pnl"] == 300

    client.put("/api/account", json={"starting_balance": 10000, "risk_per_trade": 120})
    o = client.get("/api/analytics", params={"asset": "ACCT"}).json()["overview"]
    assert o["net_pnl"] == 384 - 120 + 0 + 300
    assert o["balance"] == 10564 and o["return_pct"] == 5.64

    # a new default risk: past trades keep theirs unless asked, and a typed risk never changes
    client.put("/api/account", json={"starting_balance": 10000, "risk_per_trade": 100})
    assert client.get(f"/api/trades/{ids[0]}").json()["pnl"] == 384
    client.put("/api/account", json={"starting_balance": 10000, "risk_per_trade": 100, "apply_to_existing": True})
    assert client.get(f"/api/trades/{ids[0]}").json()["pnl"] == 320
    assert client.get(f"/api/trades/{ids[3]}").json()["pnl"] == 300
