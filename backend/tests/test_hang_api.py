from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import HangRail, RailPlacement, Store, WorkOrder


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(db_session):
    # 不进入 with 上下文，避免 lifespan 去连接默认 Postgres；
    # 表结构已由 db_session fixture 建在内存 SQLite 上。
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def world(db_session):
    now = datetime.utcnow()
    store = Store(name="测试门店")
    other = Store(name="别家门店")
    db_session.add_all([store, other])
    db_session.flush()
    r1 = HangRail(store_id=store.id, label="A 杆", length_cm=100)
    r2 = HangRail(store_id=store.id, label="B 杆", length_cm=100)
    r_other = HangRail(store_id=other.id, label="外店杆", length_cm=200)
    db_session.add_all([r1, r2, r_other])
    db_session.flush()

    # r1: 0-70 被占用，尾部剩 30cm；r2: 0-80 被占用，尾部剩 20cm
    occ1 = WorkOrder(store_id=store.id, ticket_code="OCC-1", garment_name="占位大衣",
                     length_cm=70, status="hung", due_at=now + timedelta(days=1), hung_at=now)
    occ2 = WorkOrder(store_id=store.id, ticket_code="OCC-2", garment_name="占位风衣",
                     length_cm=80, status="hung", due_at=now + timedelta(days=1), hung_at=now)
    ready = WorkOrder(store_id=store.id, ticket_code="RD-1", garment_name="羽绒服",
                      length_cm=25, status="ready", due_at=now + timedelta(days=1))
    overdue = WorkOrder(store_id=store.id, ticket_code="OD-1", garment_name="西装",
                        length_cm=25, status="overdue", due_at=now - timedelta(days=1))
    db_session.add_all([occ1, occ2, ready, overdue])
    db_session.flush()
    db_session.add_all([
        RailPlacement(rail_id=r1.id, order_id=occ1.id, start_cm=0, end_cm=70),
        RailPlacement(rail_id=r2.id, order_id=occ2.id, start_cm=0, end_cm=80),
    ])
    db_session.commit()
    return {"store": store, "r1": r1, "r2": r2, "r_other": r_other,
            "ready": ready, "overdue": overdue}


def _active_placements(db_session, order_id: int) -> list[RailPlacement]:
    return list(db_session.scalars(
        select(RailPlacement).where(
            RailPlacement.order_id == order_id, RailPlacement.active == 1
        )
    ).all())


def test_preview_specified_rail_success_is_read_only(client, db_session, world):
    r1, order = world["r1"], world["ready"]
    resp = client.post("/api/hang/preview", json={"order_id": order.id, "rail_id": r1.id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["fits"] is True
    assert data["rail_id"] == r1.id
    assert data["start_cm"] == 70
    assert data["end_cm"] == 95

    # 试算不产生任何占位，工单状态不变
    db_session.expire_all()
    assert _active_placements(db_session, order.id) == []
    assert db_session.get(WorkOrder, order.id).status == "ready"


def test_confirm_specified_rail_matches_preview(client, db_session, world):
    r1, order = world["r1"], world["ready"]
    preview = client.post("/api/hang/preview", json={"order_id": order.id, "rail_id": r1.id}).json()

    resp = client.post("/api/hang", json={"order_id": order.id, "rail_id": r1.id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "hung"

    db_session.expire_all()
    placed = _active_placements(db_session, order.id)
    assert len(placed) == 1
    # 确认后的实际占位与试算起止完全一致
    assert placed[0].rail_id == r1.id
    assert placed[0].start_cm == preview["start_cm"] == 70
    assert placed[0].end_cm == preview["end_cm"] == 95


def test_specified_rail_too_full_fails_with_zero_placement(client, db_session, world):
    r2, order = world["r2"], world["ready"]
    preview = client.post("/api/hang/preview", json={"order_id": order.id, "rail_id": r2.id})
    assert preview.status_code == 200
    assert preview.json()["fits"] is False

    resp = client.post("/api/hang", json={"order_id": order.id, "rail_id": r2.id})
    assert resp.status_code == 409

    # 失败后零占位，工单仍为 ready，杆上原有占位不受影响
    db_session.expire_all()
    assert _active_placements(db_session, order.id) == []
    assert db_session.get(WorkOrder, order.id).status == "ready"
    existing = db_session.scalars(
        select(RailPlacement).where(RailPlacement.rail_id == r2.id, RailPlacement.active == 1)
    ).all()
    assert [(p.start_cm, p.end_cm) for p in existing] == [(0, 80)]


def test_auto_scan_without_rail_id_still_hangs(client, db_session, world):
    order = world["ready"]
    resp = client.post("/api/hang", json={"order_id": order.id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "hung"

    # r2 放不下（剩 20cm < 25cm），自动扫杆应落到空出的 r1 尾部
    db_session.expire_all()
    placed = _active_placements(db_session, order.id)
    assert len(placed) == 1
    assert placed[0].rail_id == world["r1"].id
    assert (placed[0].start_cm, placed[0].end_cm) == (70, 95)


def test_overdue_order_can_hang_on_specified_rail(client, db_session, world):
    r1, order = world["r1"], world["overdue"]
    preview = client.post("/api/hang/preview", json={"order_id": order.id, "rail_id": r1.id}).json()
    assert preview["fits"] is True
    resp = client.post("/api/hang", json={"order_id": order.id, "rail_id": r1.id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "hung"


def test_preview_rail_from_other_store_404(client, world):
    resp = client.post("/api/hang/preview",
                       json={"order_id": world["ready"].id, "rail_id": world["r_other"].id})
    assert resp.status_code == 404
