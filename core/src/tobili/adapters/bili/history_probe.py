"""Account-local, resumable read evidence; every absence check refreshes the head.

The cached suffix is usable only after a fresh page overlaps a previously read
non-pinned post. Missing pages and repeated cursors never prove absence.
"""
from collections.abc import Mapping

from ...domain.probe import ProbeState
from . import codes, endpoints
from .parser import epoch_of, orig_id_of


class ShareHistory:
    def __init__(self):
        self.rows = []
        self.cursor = ''
        self.finished = False

    def check(self, transport, uid, target, since, budget=3):
        previous = self.rows
        old_cursor, old_finished = self.cursor, self.finished
        old_index = {r[0]: i for i, r in enumerate(previous) if r[0] and not r[3]}
        rows, seen, offsets = [], set(), set()
        cursor, finished = '', False
        for _ in range(budget):
            if cursor in offsets:
                return ProbeState.UNKNOWN, '个人动态分页重复，未确认是否转发过'
            offsets.add(cursor)
            answer = codes.ensure_ok(transport.fetch(
                endpoints.SPACE_FEED,
                query={'host_mid': uid, 'offset': cursor, 'type': 'all', 'platform': 'web'},
                referer=endpoints.SPACE_REFERER.format(uid=uid),
            ), what='核对本人转发')
            data = answer.get('data')
            if not isinstance(data, Mapping) or not isinstance(data.get('items'), list):
                return ProbeState.UNKNOWN, '个人动态未返回完整列表，未确认是否转发过'
            page = []
            valid = True
            for raw in data['items']:
                if not isinstance(raw, Mapping):
                    valid = False
                    continue
                modules = raw.get('modules') or {}
                author = modules.get('module_author') or {}
                if author.get('mid') and str(author['mid']) != uid:
                    valid = False
                    continue
                page.append((str(raw.get('id_str') or raw.get('id') or ''), orig_id_of(raw),
                             epoch_of(author.get('pub_ts')),
                             (modules.get('module_tag') or {}).get('text') == '置顶'))
            if any(r[1] == target for r in page):
                return ProbeState.PRESENT, ''
            if not valid:
                return ProbeState.UNKNOWN, '个人动态含无效条目，未确认是否转发过'
            explicit_end = 'has_more' in data and data['has_more'] in (0, False)
            if not page and not explicit_end:
                return ProbeState.UNKNOWN, '个人动态返回空页，但没有确认已到末尾，无法确认是否转发过'
            # Read the whole fresh page before considering an older time boundary.
            for row in page:
                if not row[0] or row[0] not in seen:
                    rows.append(row)
                    if row[0]: seen.add(row[0])
            overlap = [old_index[r[0]] for r in page if r[0] in old_index and not r[3]]
            if overlap and not explicit_end:
                for row in previous[max(overlap) + 1:]:
                    if not row[0] or row[0] not in seen:
                        rows.append(row)
                        if row[0]: seen.add(row[0])
                cursor, finished = old_cursor, old_finished
                old_index = {}  # The suffix is connected; next request extends it.
            else:
                cursor = str(data.get('offset') or '')
                finished = explicit_end
            if len(rows) > 20000:
                self.rows, self.cursor, self.finished = [], '', False
                return ProbeState.UNKNOWN, '本人动态历史超过本轮缓存范围，请手动核对'
            self.rows, self.cursor, self.finished = rows[:], cursor, finished
            if any(r[1] == target for r in rows):
                return ProbeState.PRESENT, ''
            times = [r[2] for r in rows if not r[3]]
            ordered = bool(times) and all(t is not None for t in times) and all(a >= b for a, b in zip(times, times[1:]))
            if finished or (since and ordered and times[-1] < since):
                return ProbeState.ABSENT, ''
            if not cursor:
                return ProbeState.UNKNOWN, '个人动态缺少后续页码，无法确认是否转发过'
        return ProbeState.UNKNOWN, f'转发历史核查中：已覆盖 {len(rows)} 条，下轮接着查询，暂不重复发送'
