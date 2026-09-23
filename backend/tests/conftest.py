import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SEED_ON_EMPTY", "false")

from app import database as app_db  # noqa: E402
from app import main as app_main  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import HangRail, RailPlacement, Store, WorkOrder  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)
    # 让 lifespan 与接口都指向同一个内存库
    app_db.engine = engine
    app_db.SessionLocal = TestingSession
    app_main.engine = engine
    app_main.SessionLocal = TestingSession
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def _get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make_world(db) -> dict:
    """One store, two rails (A=200cm, B=160cm), three ready/overdue orders."""
    from datetime import datetime, timedelta

    store = Store(name="测试门店")
    db.add(store)
    db.flush()
    rail_a = HangRail(store_id=store.id, label="A 杆", length_cm=200)
    rail_b = HangRail(store_id=store.id, label="B 杆", length_cm=160)
    db.add_all([rail_a, rail_b])
    db.flush()
    now = datetime.utcnow()
    coat = WorkOrder(
        store_id=store.id, ticket_code="T-001", garment_name="大衣",
        length_cm=50, status="ready", due_at=now + timedelta(days=1),
    )
    suit = WorkOrder(
        store_id=store.id, ticket_code="T-002", garment_name="西装",
        length_cm=40, status="ready", due_at=now + timedelta(days=2),
    )
    late_dress = WorkOrder(
        store_id=store.id, ticket_code="T-003", garment_name="连衣裙",
        length_cm=30, status="overdue", due_at=now - timedelta(days=1),
    )
    db.add_all([coat, suit, late_dress])
    db.commit()
    return {
        "store": store, "rail_a": rail_a, "rail_b": rail_b,
        "coat": coat, "suit": suit, "late_dress": late_dress,
    }


def occupy(db, rail: HangRail, order: WorkOrder, start: float, end: float) -> None:
    db.add(RailPlacement(rail_id=rail.id, order_id=order.id, start_cm=start, end_cm=end))
    db.commit()


def active_placements(db, order_id: int) -> list[RailPlacement]:
    return (
        db.query(RailPlacement)
        .filter(RailPlacement.order_id == order_id, RailPlacement.active == 1)
        .all()
    )
