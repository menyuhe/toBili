# 参与开发

## 独立实现与版本边界

从需求设计实现，不复制其他项目的实现代码、素材或界面结构。平台接口字段、协议和通用业务词可能相同；相似度指标只用于定位需要人工复核的位置，不能作为原创性或授权结论。

`tools/similarity_check.py` 默认检查依赖隔离。需要源码对照时使用 `tools/edition_audit.py <对照项目根目录>`；同时记录范围与结果，不沿用历史百分比。不要在公开文档中加入个人电脑路径或私有项目资料。

公共核心不得依赖 Pro 模块。可选挂载接口允许存在，Pro 实现、部署密钥、草稿素材和私人运维脚本不得进入本仓库发行包。自建目录与公开读取接口仅交换明确字段，不序列化整行账号或活动数据库。

## 分层与写操作

依赖方向为 `api → usecases → domain ← adapters`。领域层不做网络与磁盘 IO；适配器通过协议替换为测试实现。

真实参与前探测状态，未知不写；先记录意图，再单次发送写请求，最后记录成功、失败或未知。不要把超时当成没发送，也不要通过重试绕过幂等或账号互斥。清理执行前重新核对目标归属及保护条件。

## 本地验证

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov
uv run python tools/similarity_check.py
uv run python tools/release_check.py
cd ui
npm ci
npm run typecheck
npm run build
```

测试使用虚构账号与临时目录；不要把真实 UID、消息正文、Cookie 或 API 密钥做成 fixture。需要真实网络的检查应明确标记，并与默认测试分开。不得提交 `data/`、数据库、日志、浏览器配置、密钥文件、虚拟环境或本地构建目录。

源码发行使用 `tools/source_release.py`，不直接压缩仓库目录或发布现有 Git 历史。对外上传、创建 release 或推送仓库由维护者另行决定。
