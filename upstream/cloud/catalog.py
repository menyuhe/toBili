"""Account-independent activity library; no task or participation replication."""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from fastapi import Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import Integer, String, Text, delete, select
from sqlalchemy.orm import Mapped, mapped_column
from cloud.models import Base, Record, Task, Receipt, Account, Device
from src.catalog_protocol import CatalogActivity, CatalogBatch, CatalogIds


class CatalogRow(Base):
    __tablename__ = "relay_catalog"
    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dynamic_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lottery_time: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[str] = mapped_column(Text)


def prune(session, now=None):
    cutoff = int(time.time() if now is None else now) - 7 * 86400
    session.execute(delete(CatalogRow).where(CatalogRow.lottery_time > 0, CatalogRow.lottery_time < cutoff))


def install(app, store, auth, transaction):
    # Preserve the previous database before removing legacy replication state.
    if store.engine.dialect.name == 'sqlite':
        from pathlib import Path
        import sqlite3
        path = Path(store.engine.url.database)
        backup = path.with_name(path.name + '.before-catalog-v2')
        if path.is_file() and not backup.exists():
            with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
                source.backup(target)
    Base.metadata.create_all(store.engine)
    with store.transaction() as session:
        for old in session.scalars(select(Record).where(Record.kind == 'activity', Record.deleted == False)).all():
            try:
                raw = json.loads(old.value_json)
                value = CatalogActivity.model_validate({k:v for k,v in raw.items() if k in CatalogActivity.model_fields})
                key = (old.user_id, value.dynamic_id)
                if session.get(CatalogRow, key) is None:
                    session.add(CatalogRow(user_id=key[0], dynamic_id=key[1], lottery_time=value.lottery_time, payload=value.model_dump_json()))
                    session.flush()
            except (ValueError, TypeError):
                continue
        for model in (Record, Task, Receipt, Account):
            session.execute(delete(model))
        for device in session.scalars(select(Device)).all():
            device.bili_uid = ''
            device.ready = False
        prune(session)

    from cloud.reposts import install as install_reposts
    install_reposts(app, store, auth, transaction)

    @app.middleware('http')
    async def retire_legacy(request, call_next):
        path = request.url.path
        if any(path.startswith('/api/v1/' + prefix) for prefix in ('sync/', 'tasks', 'leases/', 'records', 'accounts/bind', 'devices/heartbeat')):
            return JSONResponse({'detail':'已改为活动资料库，请升级客户端；任务和账号状态仅保存在本机'}, status_code=410)
        return await call_next(request)

    @app.post('/api/v1/catalog/missing')
    def missing(body: CatalogIds, device=Depends(auth), session=Depends(transaction)):
        found = set(session.scalars(select(CatalogRow.dynamic_id).where(CatalogRow.user_id == device.user_id, CatalogRow.dynamic_id.in_(body.ids))))
        return {'ids': [did for did in dict.fromkeys(body.ids) if did not in found]}

    @app.post('/api/v1/catalog')
    def upload(body: CatalogBatch, device=Depends(auth), session=Depends(transaction)):
        inserted = 0
        for value in body.items:
            if value.lottery_time and value.lottery_time < int(time.time()) - 7*86400:
                continue
            if session.get(CatalogRow, (device.user_id, value.dynamic_id)) is not None:
                continue
            session.add(CatalogRow(user_id=device.user_id, dynamic_id=value.dynamic_id, lottery_time=value.lottery_time, payload=value.model_dump_json()))
            session.flush()
            inserted += 1
        prune(session)
        return {'inserted':inserted}

    @app.get('/api/v1/catalog')
    def download(after: str = '', limit: int = Query(200, ge=1, le=500), device=Depends(auth), session=Depends(transaction)):
        prune(session)
        rows = session.scalars(select(CatalogRow).where(CatalogRow.user_id == device.user_id, CatalogRow.dynamic_id > after).order_by(CatalogRow.dynamic_id).limit(limit+1)).all()
        return {'items':[json.loads(row.payload) for row in rows[:limit]], 'next':rows[limit-1].dynamic_id if len(rows)>limit else ''}

    @asynccontextmanager
    async def lifespan(_app):
        async def sweep():
            while True:
                await asyncio.sleep(60)
                try:
                    def run():
                        with store.transaction() as session:
                            prune(session)
                    await asyncio.to_thread(run)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('catalog retention failed')
        task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
    app.router.lifespan_context = lifespan
