"""接口地址。

集中放一处，因为**接口会变**。B 站调整一次参数，改动应该只落在这个文件和对应
解析函数上，而不是散落在业务逻辑里。

这些都是抓包可得的客观事实，不构成任何实现上的借鉴。
"""

from __future__ import annotations

PASSPORT = "https://passport.bilibili.com"
API = "https://api.bilibili.com"
WWW = "https://www.bilibili.com"
# 老接口还挂在 vc 域名下。删除动态只在这里，新版路径（x/dynamic/...）实测 404。
VC = "https://api.vc.bilibili.com"
LOTTERY_NOTICE = f"{VC}/lottery_svr/v1/lottery_svr/lottery_notice"

# 扫码登录
QR_ISSUE = f"{PASSPORT}/x/passport-login/web/qrcode/generate"
QR_CHECK = f"{PASSPORT}/x/passport-login/web/qrcode/poll"
# 浏览器指纹（buvid3），没有它部分接口会返回未登录
FINGERPRINT = f"{API}/x/frontend/finger/spi"

# 账号
NAV = f"{API}/x/web-interface/nav"

# 动态
SPACE_FEED = f"{API}/x/polymer/web-dynamic/v1/feed/space"
FOLLOWING_FEED = f"{API}/x/polymer/web-dynamic/v1/feed/all"
DYN_DETAIL = f"{API}/x/polymer/web-dynamic/v1/detail"
OPUS_DETAIL = f"{API}/x/polymer/web-dynamic/v1/opus/detail"
OPUS_ACTION = f"{API}/x/community/cosmo/interface/simple_action"

# 找源（按关键词搜 UP 主，不是搜动态）
SEARCH_TYPE = f"{API}/x/web-interface/search/type"

# 关系
RELATION = f"{API}/x/relation"
RELATION_EDIT = f"{API}/x/relation/modify"

# 写操作
PUBLISH_DYN = f"{API}/x/dynamic/feed/create/dyn"
THUMB_DYN = f"{API}/x/dynamic/feed/dyn/thumb"
ADD_REPLY = f"{API}/x/v2/reply/add"
LIST_REPLY = f"{API}/x/v2/reply/main"
# 评论列表现在要 wbi 签名。老的 `/x/v2/reply/main` 对任何动态都回 -404，
# 看起来像"这条没人评论"，其实是接口换了。
LIST_REPLY_WBI = f"{API}/x/v2/reply/wbi/main"
FAV_DEAL = f"{API}/x/v3/fav/resource/deal"
RESERVE_CLICK = f"{API}/x/dynamic/feed/reserve/click"
# 删除自己发的一条动态。只对本人动态有效：拿别人的 ID 调，远端返回 4126010。
REMOVE_DYN = f"{VC}/dynamic_svr/v1/dynamic_svr/rm_dynamic"

# 消息中心。清理闸门靠它判断有没有站内信。
MSG_UNREAD = f"{API}/x/msgfeed/unread"
# 收件箱的两类条目。**没有系统通知的列表接口** —— `/x/msgfeed/system`、
# `/sys_msg`、`/sysmsg`、`/official`、`/up` 这些路径实测全部 404，
# 未读接口里只有一个 `sys_msg` 计数。所以界面上系统通知只能显示条数。
MSG_REPLY = f"{API}/x/msgfeed/reply"
MSG_AT = f"{API}/x/msgfeed/at"
# 私信会话列表还挂在 vc 域名下。
MSG_SESSIONS = f"{VC}/session_svr/v1/session_svr/get_sessions"

SPACE_REFERER = "https://space.bilibili.com/{uid}/dynamic"
FOLLOWING_REFERER = "https://t.bilibili.com/"
SEARCH_REFERER = "https://search.bilibili.com/"
MESSAGE_REFERER = "https://message.bilibili.com/"


def dyn_referer(dyn_id: str) -> str:
    return f"{WWW}/opus/{dyn_id}"
