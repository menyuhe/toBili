from tobili.runtime.events import EventBus
from tobili.usecases.scan_feeds import ScanFeeds
from tobili.ports.models import FeedItem
import json

def test_each_shared_source_records_name_start_and_result(repos,gateway,clock,account_id):
    source=repos.feeds.add(account_id,'topic','123','今日好奖品')
    gateway.feed_items=[FeedItem(dyn_id='1234',up_uid='2002',text='转发抽奖 送出大会员',published_at=clock.now())]
    bus=EventBus()
    scan=ScanFeeds(gateway=gateway,feeds=repos.feeds,campaigns=repos.campaigns,clock=clock,events=bus)
    scan.run(account_id,feed_id=source)
    events=bus.since(0)
    assert [e.payload['phase'] for e in events]==['started','finished']
    assert events[1].payload['scanned']==1 and '今日好奖品' in events[1].payload['title']
    assert events[1].payload['scope']=='shared'
    for e in events:
        repos.events.append(seq=e.seq,topic=e.topic,payload=json.dumps(e.payload),at=clock.now())
    assert len(repos.events.recent(account_id=999,topics=['source.update']))==2

def test_failed_source_is_visible_and_does_not_fake_zero_success(repos,gateway,clock,account_id):
    source=repos.feeds.add(account_id,'space','123','UP 名字')
    def fail(**kw):raise RuntimeError('网络暂时失败')
    gateway.scan=fail
    bus=EventBus()
    ScanFeeds(gateway=gateway,feeds=repos.feeds,campaigns=repos.campaigns,clock=clock,events=bus).run(account_id,feed_id=source)
    assert bus.since(0)[-1].payload['problems']==['UP 名字：RuntimeError: 网络暂时失败']
    assert bus.since(0)[-1].payload['scope']=='account'
