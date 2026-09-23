from sqlalchemy import func, select

from app.models.models import RailPlacement, WorkOrder

from .conftest import active_placements, make_world, occupy


def test_preview_designated_rail_success_no_side_effects(client, db_session):
    w = make_world(db_session)
    before = db_session.scalar(select(func.count()).select_from(RailPlacement))

    r = client.post("/api/hang/preview", json={"order_id": w["coat"].id, "rail_id": w["rail_a"].id})
    assert r.status_code == 200
    data = r.json()
    assert data["fits"] is True
    assert data["rail_id"] == w["rail_a"].id
    assert data["start_cm"] == 0
    assert data["end_cm"] == 50

    # 预览不产生 hung 状态或 active 占位
    db_session.expire_all()
    order = db_session.get(WorkOrder, w["coat"].id)
    assert order.status == "ready"
    assert order.hung_at is None
    after = db_session.scalar(select(func.count()).select_from(RailPlacement))
    assert after == before


def test_preview_full_rail_reports_no_fit_without_writing(client, db_session):
    w = make_world(db_session)
    blocker = WorkOrder(
        store_id=w["store"].id, ticket_code="T-BLK", garment_name="占位衣物",
        length_cm=160, status="hung", due_at=w["coat"].due_at,
    )
    db_session.add(blocker)
    db_session.flush()
    # A 杆 0-160 被占，只剩 40cm，50cm 大衣放不下
    occupy(db_session, w["rail_a"], blocker, 0, 160)

    r = client.post("/api/hang/preview", json={"order_id": w["coat"].id, "rail_id": w["rail_a"].id})
    assert r.status_code == 200
    data = r.json()
    assert data["fits"] is False
    assert data["start_cm"] is None
    assert data["end_cm"] is None

    db_session.expire_all()
    assert db_session.get(WorkOrder, w["coat"].id).status == "ready"
    assert active_placements(db_session, w["coat"].id) == []


def test_hang_designated_rail_success_matches_preview(client, db_session):
    w = make_world(db_session)
    pv = client.post(
        "/api/hang/preview", json={"order_id": w["coat"].id, "rail_id": w["rail_a"].id}
    ).json()

    r = client.post("/api/hang", json={"order_id": w["coat"].id, "rail_id": w["rail_a"].id})
    assert r.status_code == 200
    assert r.json()["status"] == "hung"

    db_session.expire_all()
    placed = active_placements(db_session, w["coat"].id)
    assert len(placed) == 1
    assert placed[0].rail_id == w["rail_a"].id
    # 确认后占位与预览一致
    assert placed[0].start_cm == pv["start_cm"]
    assert placed[0].end_cm == pv["end_cm"]


def test_hang_designated_rail_full_fails_with_zero_placeholders(client, db_session):
    w = make_world(db_session)
    blocker = WorkOrder(
        store_id=w["store"].id, ticket_code="T-BLK", garment_name="占位衣物",
        length_cm=160, status="hung", due_at=w["coat"].due_at,
    )
    db_session.add(blocker)
    db_session.flush()
    occupy(db_session, w["rail_a"], blocker, 0, 160)
    before = db_session.scalar(select(func.count()).select_from(RailPlacement))

    r = client.post("/api/hang", json={"order_id": w["coat"].id, "rail_id": w["rail_a"].id})
    assert r.status_code == 409

    # 失败不写占位，工单状态不变
    db_session.expire_all()
    assert active_placements(db_session, w["coat"].id) == []
    assert db_session.get(WorkOrder, w["coat"].id).status == "ready"
    after = db_session.scalar(select(func.count()).select_from(RailPlacement))
    assert after == before


def test_hang_designated_rail_does_not_fallback_to_other_rails(client, db_session):
    w = make_world(db_session)
    blocker = WorkOrder(
        store_id=w["store"].id, ticket_code="T-BLK", garment_name="占位衣物",
        length_cm=160, status="hung", due_at=w["coat"].due_at,
    )
    db_session.add(blocker)
    db_session.flush()
    occupy(db_session, w["rail_a"], blocker, 0, 160)  # A 仅剩 40cm

    # 指定 A 杆失败，即使空的 B 杆放得下也不自动改挂
    r = client.post("/api/hang", json={"order_id": w["coat"].id, "rail_id": w["rail_a"].id})
    assert r.status_code == 409
    assert active_placements(db_session, w["coat"].id) == []
    on_b = db_session.query(RailPlacement).filter(
        RailPlacement.rail_id == w["rail_b"].id, RailPlacement.active == 1
    ).count()
    assert on_b == 0


def test_hang_without_rail_id_still_auto_scans(client, db_session):
    w = make_world(db_session)
    blocker = WorkOrder(
        store_id=w["store"].id, ticket_code="T-BLK", garment_name="占位衣物",
        length_cm=160, status="hung", due_at=w["coat"].due_at,
    )
    db_session.add(blocker)
    db_session.flush()
    occupy(db_session, w["rail_a"], blocker, 0, 160)  # A 放不下 50cm 大衣

    r = client.post("/api/hang", json={"order_id": w["coat"].id})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "hung"

    db_session.expire_all()
    placed = active_placements(db_session, w["coat"].id)
    assert len(placed) == 1
    # 自动扫杆跳过 A，落在 B
    assert placed[0].rail_id == w["rail_b"].id
    assert placed[0].start_cm == 0
    assert placed[0].end_cm == 50


def test_hang_designated_rail_accepts_overdue_order(client, db_session):
    w = make_world(db_session)
    r = client.post("/api/hang", json={"order_id": w["late_dress"].id, "rail_id": w["rail_b"].id})
    assert r.status_code == 200
    assert r.json()["status"] == "hung"
