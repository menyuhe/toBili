<div align="center">
  <img src="ui/public/icon.png" width="76" alt="toBili logo" />
  <h1>toBili</h1>
  <p><strong>发现奖品，安排参与，核验后收尾。</strong></p>
  <p>本机多账号抽奖管理工具 · Windows / macOS 桌面窗口 · 开源核心</p>
  <p>
    <a href="https://github.com/menyuhe/toBili/releases/latest">下载桌面版</a> ·
    <a href="docs/public/MAC.md">Mac 安装与构建</a> ·
    <a href="docs/public/GUIDE.md">使用指南</a> ·
    <a href="https://tobili.4wdo.cn/public/">公开奖品目录</a> ·
    <a href="https://github.com/menyuhe/toBili/issues">反馈问题</a>
  </p>
  <img alt="License AGPL-3.0-or-later" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-2063AE?style=flat-square" />
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-176B68?style=flat-square" />
  <img alt="Windows and macOS desktop" src="https://img.shields.io/badge/desktop-Windows%20%7C%20macOS-2063AE?style=flat-square" />
</div>

<br>

![发现奖品界面](docs/public/media/discover.png)

**奖品放在前面，账号状态分开管理。** 从话题、UP 合集、动态与公开目录发现奖品，筛选后加入待参与；每个账号独立安排任务、查看消息和核验结果。

## 0.2.18 更新

话题与 UP 合集由本机账号共用；任务日志逐来源展示更新过程。新增常用入口、待办角标和轻玻璃界面，并修复账号菜单遮挡。

## 一轮日常操作

![发现、入队、安排任务与多账号查看](docs/public/media/workflow.gif)

*演示使用虚构账号与奖品，没有登录凭据，也没有向 B 站发出参与操作。GIF 是真实界面的流程录制。*

| 你要做的事 | 在哪里完成 |
|---|---|
| 找值得参加的奖品 | **发现与参与**：组合筛选、截止时间、入队、不参加和 UP 黑名单 |
| 安排几个账号 | **挂机中心**：相同规格的账号卡片，查看当前事项、下一事项与倒计时 |
| 按自己的节奏参与 | **定时任务**：按账号选择功能、周期、范围与参与节奏 |
| 确认是否中奖 | **开奖与消息**：开奖核验、消息复核、人工改判；消息可标记与抽奖无关 |
| 整理历史转发 | **清理**：核对清单与执行记录，截止时间不等于未中奖 |

<details>
<summary><strong>查看多账号与定时任务截图</strong></summary>

![挂机中心](docs/public/media/accounts.png)

![定时任务](docs/public/media/schedule.png)

</details>

## 三步开始

1. 从 [Releases](https://github.com/menyuhe/toBili/releases/latest) 下载桌面版：Windows 用 `toBili.exe`；Mac 按芯片选择 ZIP，解压后放进「应用程序」（[安装说明](docs/public/MAC.md)）。
2. 双击启动并扫码。右上角可添加自己的其他账号；新账号默认不开启自动任务。
3. 在 **发现与参与 → 订阅源** 添加来源，或读取公开目录；挑选奖品加入待参与，再按需配置定时任务。

Windows 需要 WebView2 Runtime。数据库保存在程序同级 `data/`，升级前备份。关闭主窗口会退出该进程；持续挂机可使用 `toBili.exe --tray`。任务只在程序运行时执行，不能替代电脑开机和联网。

> 从旧版迁移：本次是独立重写的实现，数据格式不同。请在新文件夹使用新版，保留旧版及其数据备份，不要直接把旧 `data/` 覆盖进来。[迁移说明](docs/public/MIGRATION.md)

## 数据去哪里

- **本机保存**：账号凭据、参与历史、待参与队列、消息标记和定时设置。Windows 凭据使用当前系统用户的 DPAPI 加密，Mac 使用登录钥匙串保管加密密钥。
- **公开目录只读**：匿名获取奖品、原动态、公开 UP 信息和开奖时间；普通版不上传账号、参与记录或私信。目录不可用时，其他来源仍可使用。
- **模型按需接入**：配置自己的兼容接口后，待识别的正文或图片会发送给该模型服务。优先采用官方抽奖组件和正文时间；缺少时间时按发布时间后七天估算，只有日期时采用 00:00，可手动校对。手动修订不会被普通扫描覆盖。
- **写入按账号协调**：同一账号在本机串行执行；未知结果不会盲目重发。平台限流、登录失效与接口变化仍可能中断任务，错误可在运行记录中查看。

## 开源版范围

本仓库包含本机多账号、发现与参与、挂机中心、模型识别、消息复核、开奖核验、清理和公开目录读取。

## 从源码运行

需要 Python 3.12+、uv、Node.js 20+。

```bash
git clone https://github.com/menyuhe/toBili.git
cd toBili
uv sync
cd ui
npm ci
npm run build
cd ..
uv run tobili serve --port 8848
```

浏览器打开 `http://127.0.0.1:8848`。macOS 的安装、数据位置与原生构建步骤见 [Mac 使用说明](docs/public/MAC.md)。两平台共用界面和业务代码。

[构建与验证](docs/public/GUIDE.md#验证与参与开发) · [项目结构](PROJECT.md)

## 项目说明

当前实现使用 **AGPL-3.0-or-later**，见 [LICENSE](LICENSE)。历史版本及其许可证保留在原标签中。toBili 是非官方工具，与哔哩哔哩无隶属关系。
