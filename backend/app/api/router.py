from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import HangRail, RailPlacement, Store, WorkOrder
from app.schemas.schemas import (
    HangPreviewOut,
    HangRequest,
    OccupancyOut,
    OccupancySeg,
    OrderOut,
    PickupRequest,
    PreviewRequest,
    RailOut,
    StoreOut,
)
from app.services.rail_engine import Placement, Segment, first_fit

api_router = APIRouter()


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/stores", response_model=list[StoreOut])
def stores(db: Session = Depends(get_db)):
    return db.scalars(select(Store).order_by(Store.id)).all()


@api_router.get("/rails", response_model=list[RailOut])
def rails(db: Session = Depends(get_db)):
    return db.scalars(select(HangRail).order_by(HangRail.id)).all()


@api_router.get("/orders", response_model=list[OrderOut])
def orders(db: Session = Depends(get_db)):
    return db.scalars(select(WorkOrder).order_by(WorkOrder.id.desc())).all()


@api_router.get("/occupancy/{rail_id}", response_model=OccupancyOut)
def occupancy(rail_id: int, db: Session = Depends(get_db)):
    rail = db.get(HangRail, rail_id)
    if not rail:
        raise HTTPException(404, "挂杆不存在")
    placements = db.scalars(
        select(RailPlacement).where(RailPlacement.rail_id == rail_id, RailPlacement.active == 1)
    ).all()
    segs = []
    for p in placements:
        order = db.get(WorkOrder, p.order_id)
        if not order:
            continue
        segs.append(
            OccupancySeg(
                order_id=order.id,
                ticket_code=order.ticket_code,
                garment_name=order.garment_name,
                start_cm=p.start_cm,
                end_cm=p.end_cm,
            )
        )
    segs.sort(key=lambda s: s.start_cm)
    return OccupancyOut(rail_id=rail.id, label=rail.label, length_cm=rail.length_cm, segments=segs)


def _get_hangable_order(db: Session, order_id: int) -> WorkOrder:
    order = db.get(WorkOrder, order_id)
    if not order:
        raise HTTPException(404, "工单不存在")
    if order.status not in ("ready", "overdue"):
        raise HTTPException(400, "工单状态不可上杆")
    return order


def _candidate_rails(db: Session, order: WorkOrder, rail_id: int | None) -> list[HangRail]:
    rail_q = select(HangRail).where(HangRail.store_id == order.store_id)
    if rail_id is not None:
        rail_q = rail_q.where(HangRail.id == rail_id)
    return list(db.scalars(rail_q.order_by(HangRail.id)).all())


def _try_place(db: Session, rail: HangRail, order: WorkOrder) -> Placement | None:
    """Pure read: compute the first-fit landing segment for the order on this rail."""
    active = db.scalars(
        select(RailPlacement).where(RailPlacement.rail_id == rail.id, RailPlacement.active == 1)
    ).all()
    occupied = [Segment(p.start_cm, p.end_cm) for p in active]
    return first_fit(rail.length_cm, occupied, order.length_cm)


@api_router.post("/hang/preview", response_model=HangPreviewOut)
def hang_preview(body: PreviewRequest, db: Session = Depends(get_db)):
    """Dry-run a placement on the specified rail. Never writes status or placements."""
    order = _get_hangable_order(db, body.order_id)
    candidates = _candidate_rails(db, order, body.rail_id)
    if not candidates:
        raise HTTPException(404, "无可用挂杆")
    rail = candidates[0]
    place = _try_place(db, rail, order)
    out = HangPreviewOut(
        order_id=order.id,
        ticket_code=order.ticket_code,
        rail_id=rail.id,
        rail_label=rail.label,
        rail_length_cm=rail.length_cm,
        garment_cm=order.length_cm,
        fits=place is not None,
    )
    if place is not None:
        out.start_cm = place.start_cm
        out.end_cm = place.end_cm
    return out


@api_router.post("/hang", response_model=OrderOut)
def hang(body: HangRequest, db: Session = Depends(get_db)):
    order = _get_hangable_order(db, body.order_id)
    rails = _candidate_rails(db, order, body.rail_id)
    if not rails:
        raise HTTPException(404, "无可用挂杆")

    for rail in rails:
        place = _try_place(db, rail, order)
        if place is None:
            continue
        db.add(
            RailPlacement(
                rail_id=rail.id,
                order_id=order.id,
                start_cm=place.start_cm,
                end_cm=place.end_cm,
            )
        )
        order.status = "hung"
        order.hung_at = datetime.utcnow()
        db.commit()
        db.refresh(order)
        return order

    raise HTTPException(409, "挂杆空间不足")


@api_router.post("/pickup", response_model=OrderOut)
def pickup(body: PickupRequest, db: Session = Depends(get_db)):
    order = db.scalar(select(WorkOrder).where(WorkOrder.ticket_code == body.ticket_code))
    if not order:
        raise HTTPException(404, "取件码无效")
    if order.status != "hung":
        raise HTTPException(400, "工单未在挂杆上")
    placements = db.scalars(
        select(RailPlacement).where(RailPlacement.order_id == order.id, RailPlacement.active == 1)
    ).all()
    for p in placements:
        p.active = 0
    order.status = "picked"
    db.commit()
    db.refresh(order)
    return order


@api_router.post("/overdue/scan", response_model=list[OrderOut])
def overdue_scan(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    hung = db.scalars(select(WorkOrder).where(WorkOrder.status == "hung")).all()
    marked = []
    for o in hung:
        if o.due_at < now:
            o.status = "overdue"
            marked.append(o)
    ready = db.scalars(select(WorkOrder).where(WorkOrder.status == "ready")).all()
    for o in ready:
        if o.due_at < now:
            o.status = "overdue"
            marked.append(o)
    db.commit()
    return marked


@api_router.get("/overdue", response_model=list[OrderOut])
def overdue_list(db: Session = Depends(get_db)):
    return db.scalars(select(WorkOrder).where(WorkOrder.status == "overdue").order_by(WorkOrder.due_at)).all()
