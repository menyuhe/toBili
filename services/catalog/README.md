# 自建基础活动目录

这是可选的独立服务，不读取桌面数据库，也没有登录 B 站、参与或清理能力。它与默认公开活动接口、Pro 私有资料簿是三种不同入口。

| 入口 | 用途 | 权限 |
|---|---|---|
| 默认公开目录 `/api/v1/public/catalog` | 普通版「订阅源 → 公开目录」只读导入 | 匿名读取 |
| 本服务 `/v1/listings` | 自建基础资料交换 API | 读写均需管理员密钥 |
| Pro `/v2/...` | 设备修订审批、账号执行归属及恢复 | 私有插件鉴权 |

本服务不带网页管理端。普通版当前内置导入使用默认公开目录协议，不能把本服务地址直接替换过去；若要自建兼容的公开端点，需要按公开目录协议提供只读适配器。

## 启动

在仓库根目录安装依赖 `uv sync --locked`。以下为 PowerShell：

```powershell
$env:TOBILI_CATALOG_KEY = (uv run python -c "import secrets;print(secrets.token_urlsafe(40))")
$env:TOBILI_CATALOG_DB = 'D:\toBili-catalog\listings.sqlite'
uv run uvicorn services.catalog.server:factory --factory --host 127.0.0.1 --port 8862
```

妥善保存并复用密钥；不要每次重启重新生成，也不要把写密钥嵌入公开网页。公网服务应使用 HTTPS 反向代理；不要暴露桌面本地 API 端口。

`POST /v1/listings` 每批最多 100 条，`GET /v1/listings?after=0` 按游标分页；请求头为 `Authorization: Bearer <密钥>`。响应 `items` 与 `next`。字段定义在 `core/src/tobili/catalogwire.py`：动态号、作者公开 UID／名称、标题、奖品、类型和开奖时间。额外字段被拒绝，不接受 Cookie、私信、草稿或参与记录。

动态号去重，同号保留首次资料。需要修订审批及恢复隔离时，应使用相应的私有服务实现，而不是把这个基础目录视为完整多设备工作区。
