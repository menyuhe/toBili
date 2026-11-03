"""Append-only repost evidence, scoped to the authenticated application owner."""
from fastapi import Depends, Query
from sqlalchemy import Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column
from cloud.models import Base, User
from src.catalog_protocol import RepostBatch


class RepostRow(Base):
    __tablename__ = "relay_reposts"
    __table_args__ = (UniqueConstraint("user_id", "uid", "dynamic_id", "repost_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    uid: Mapped[str] = mapped_column(String(32))
    dynamic_id: Mapped[str] = mapped_column(String(32))
    repost_id: Mapped[str] = mapped_column(String(32))


def install(app, store, auth, transaction):
    Base.metadata.create_all(store.engine)

    @app.post('/api/v1/reposts')
    def upload(body: RepostBatch, device=Depends(auth), session=Depends(transaction)):
        # Atomic insert avoids duplicate uploads racing between Windows and Mac.
        if store.engine.dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        # Serialize this owner's insert transactions so committed IDs cannot arrive
        # behind a download cursor (PostgreSQL sequences alone do not guarantee it).
        session.execute(select(User).where(User.id == device.user_id).with_for_update()).scalar_one()
        inserted = 0
        for value in body.items:
            if value.dynamic_id == value.repost_id:
                continue
            result = session.execute(insert(RepostRow).values(user_id=device.user_id, **value.model_dump())
                                     .on_conflict_do_nothing(index_elements=['user_id','uid','dynamic_id','repost_id']).returning(RepostRow.id))
            inserted += int(result.scalar_one_or_none() is not None)
        return {'inserted': inserted}

    @app.get('/api/v1/reposts')
    def download(after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
                 device=Depends(auth), session=Depends(transaction)):
        rows = session.scalars(select(RepostRow).where(RepostRow.user_id == device.user_id, RepostRow.id > after)
                               .order_by(RepostRow.id).limit(limit + 1)).all()
        page = rows[:limit]
        return {'items': [dict(uid=r.uid, dynamic_id=r.dynamic_id, repost_id=r.repost_id) for r in page],
                'cursor': page[-1].id if page else after, 'more': len(rows) > limit}
