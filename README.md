# 60秒日报与历史上的今天

每天从 60s API 获取两类内容，通过 AstrBot 的 `context.send_message` 向明确配置的会话发送消息。默认 `enabled=false`、`targets=[]`，不会启动定时后台任务或自动请求 API。热门榜单仅在用户发送命令时查询并回复当前会话，独立于定时总开关。

## 热门榜单命令

以下 `/` 为示意前缀，实际取决于 AstrBot 配置。支持中文名称及表中英文配置键。

```text
/热榜
/热榜 帮助
/热榜 微博 5
/热榜 网易云榜单 10
/热榜 网易云详情 3779629 5
/热榜 hn best 5
/热榜 IT之家 week 5
```

默认 10 条，上限 20 条；网易云 ID 从榜单列表取得。HN 分类支持 top/new/best（默认 top），IT之家支持 day/week/month（默认 day）。只回复触发命令的会话，不遍历定时目标。没有定时热门榜单功能。

同一路径缓存 60 秒并合并并发请求，最多缓存 64 个查询；不同未缓存查询间隔至少 2 秒。命令服务遇到错误冷却至少 15 秒，API 错误默认冷却 15 分钟，429 尊重 Retry-After。定时与命令冷却分别管理，重载后命令缓存/冷却清空。

| 中文名称 | 配置键 | 官方路由（基址后追加 /v2/） | 文档 |
| --- | --- | --- | --- |
| 抖音 | `douyin` | `douyin` | [官方](https://docs.60s-api.viki.moe/254028799e0) |
| IT之家 | `ithome` | `it-news/rank` | [官方](https://docs.60s-api.viki.moe/455707126e0) |
| 小红书 | `rednote` | `rednote` | [官方](https://docs.60s-api.viki.moe/344628918e0) |
| 哔哩哔哩 | `bili` | `bili` | [官方](https://docs.60s-api.viki.moe/254039323e0) |
| 微博 | `weibo` | `weibo` | [官方](https://docs.60s-api.viki.moe/254026663e0) |
| 夸克 | `quark` | `quark` | [官方](https://docs.60s-api.viki.moe/383391822e0) |
| 百度 | `baidu` | `baidu/hot` | [官方](https://docs.60s-api.viki.moe/345694532e0) |
| 百度电视剧 | `baidu_tv` | `baidu/teleplay` | [官方](https://docs.60s-api.viki.moe/345696060e0) |
| 贴吧 | `tieba` | `baidu/tieba` | [官方](https://docs.60s-api.viki.moe/345697627e0) |
| 头条 | `toutiao` | `toutiao` | [官方](https://docs.60s-api.viki.moe/254038833e0) |
| 知乎 | `zhihu` | `zhihu` | [官方](https://docs.60s-api.viki.moe/254026504e0) |
| 懂车帝 | `dongchedi` | `dongchedi` | [官方](https://docs.60s-api.viki.moe/345808679e0) |
| 网易云榜单 | `ncm_list` | `ncm-rank/list` | [官方](https://docs.60s-api.viki.moe/341610331e0) |
| 网易云详情 | `ncm_detail` | `ncm-rank/{id}` | [官方](https://docs.60s-api.viki.moe/341610332e0) |
| HackerNews | `hn` | `hacker-news/top` | [官方](https://docs.60s-api.viki.moe/343622824e0) |
| 猫眼总票房 | `maoyan_all` | `maoyan/all/movie` | [官方](https://docs.60s-api.viki.moe/261610562e0) |
| 猫眼电影 | `maoyan_movie` | `maoyan/realtime/movie` | [官方](https://docs.60s-api.viki.moe/353506911e0) |
| 猫眼电视 | `maoyan_tv` | `maoyan/realtime/tv` | [官方](https://docs.60s-api.viki.moe/353791089e0) |
| 猫眼网剧 | `maoyan_web` | `maoyan/realtime/web` | [官方](https://docs.60s-api.viki.moe/353791308e0) |
| 豆瓣电影 | `douban_movie` | `douban/weekly/movie` | [官方](https://docs.60s-api.viki.moe/421319192e0) |
| 豆瓣华语剧 | `douban_tv_cn` | `douban/weekly/tv_chinese` | [官方](https://docs.60s-api.viki.moe/421324039e0) |
| 豆瓣全球剧 | `douban_tv_global` | `douban/weekly/tv_global` | [官方](https://docs.60s-api.viki.moe/421324081e0) |
| 豆瓣华语综艺 | `douban_show_cn` | `douban/weekly/show_chinese` | [官方](https://docs.60s-api.viki.moe/421324089e0) |
| 豆瓣全球综艺 | `douban_show_global` | `douban/weekly/show_global` | [官方](https://docs.60s-api.viki.moe/421330297e0) |

返回适配：普通榜单读取 data 数组；猫眼读取 data.list。标题识别 title/name/movie_name/programme_name/series_name；按来源展示热度、评分、票房或收视率，保留 link/url 链接。网易云列表显示 ID，歌曲显示歌手；缺少指标不会伪造。条目封面不作为整榜图片。知乎页面缺少具体响应样例，采用通用字段兼容，需要真实实例验证；懂车帝读取到官方示例字段 title/score/score_desc/url，但公共服务可用性未经本机验证，遇到 403 直接提示失败，不绕过限制。

## 每个接口的发送格式

`formats` 对象含 daily、history 和上述 24 个配置键，分别选择发送格式：

- `text`：整理后的可读文本。
- `json`：结构化结果作为文本消息，保持合法 JSON（并非平台的 JSON 卡片）。日更含真实 source_date 和已核验正文；热榜含整理后的 items，不是上游原始完整响应。
- `markdown`：整理后的 Markdown 文本。是否渲染取决于平台，不能保证所有聊天平台都显示富文本。
- `image`：仅 daily 支持。先以 JSON 核验日报日期，再读取同一响应的 data.image，下载后以 AstrBot Image 组件发送，并附真实日期/来源。不会将 URL 或 JSON 当作图片。

API 的 encoding 与本插件发送格式是不同概念：为核验日期、限制条数及统一字段，HTTP 始终请求 `encoding=json`，再按 formats 转换消息。没有额外请求同一接口的 text/markdown 版本，也没有伪称使用上游 image-proxy。

示例（合并进完整配置，不要省略你需要的其他设置）：

```json
{
  "enabled": false,
  "targets": [],
  "formats": {
    "daily": "image",
    "history": "text",
    "weibo": "markdown",
    "douban_movie": "json"
  }
}
```

历史上的今天和热门榜单的界面选项只有 text/json/markdown；手工填 image 时会报中文错误，不静默回退。若希望所有榜单也以图片展示，需要另行实现文字排版生成图片，本版本未实现。

日报图片仅允许 `image_hosts` 中精确匹配的 HTTPS 域名，默认 mmbiz.qpic.cn；拒绝重定向，检查 MIME 和 PNG/JPEG/WebP/GIF 文件头，最多 5MB，受超时控制。图片地址或格式异常时不发送。保留原日报图片内容和来源，不裁掉版权标记；API 可访问不代表获得任意再分发许可，使用者仍需遵守来源条款。

## 配置

在 AstrBot WebUI 配置本插件，参考 `config.example.json`。修改后重载插件。

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| enabled | false | 主动发送总开关 |
| base_url | https://60s.viki.moe | 服务基址，插件追加 `/v2/60s` 和 `/v2/today-in-history` |
| timezone | Asia/Shanghai | IANA 时区，需系统时区数据库 |
| send_time | 09:00 | 每天本地时间，24 小时 HH:MM |
| targets | [] | 完整 `event.unified_msg_origin` 字符串列表，重复项合并 |
| daily_enabled | true | 是否发送每天60秒读懂世界 |
| history_enabled | true | 是否发送历史上的今天 |
| catchup_minutes | 120 | 当天计划时刻后的补发窗口，1–1440 分钟 |
| timeout_seconds | 20 | 单次 HTTP/发送超时，1–120 秒 |
| formats | 各接口 text | 每个接口独立的消息格式 |
| image_hosts | [mmbiz.qpic.cn] | 允许的日报图片 HTTPS 域名 |

`targets` 需要从实际 AstrBot 会话取得，例如通过现有会话管理界面或该会话事件的 `event.unified_msg_origin`。结构为 `平台实例ID:消息类型:会话ID`，不能填写接口 URL、适配器名称或裸群号。这里不提供可误用的真实目标，也没有自动订阅或自动发现群聊功能。确认平台支持主动消息后再启用。AstrBot v4.29.4 源码注明 `qq_official` 不支持该主动发送方法；其他平台也可能限制长度/频次。

## 调度、去重与恢复

- 每 30 秒检查一次；默认 09:00 到 11:00（不含 11:00）允许当天补发。进程停机后在窗口内启动会补发；超过窗口跳过，不补历史日期。
- 夏令时重复小时仍使用同一自然日去重键；不存在的计划时刻按时区转换后的时间执行（通常顺延一个小时）。建议使用 Asia/Shanghai 或避开 DST 跳变时段。
- 每个日期、会话、内容类型最多自动尝试发送一次；两类内容分开发送。先提交 SQLite 占位，再调用适配器，成功标为 `sent`，异常/超时标为 `failed_or_unknown`，被取消或崩溃可保留 `pending`。
- 三种记录都会阻止当日重发。这是“最多尝试一次”而非保证送达：占位后崩溃可能漏发；平台返回成功也不等于对方已收到。不要直接删除数据库，否则会丢失去重保护。
- 存储位置：AstrBot 数据目录的 `plugin_data/astrbot_plugin_60s/deliveries.sqlite3`。保存计划日期、真实接口日期、目标和状态。升级时保留该文件。记录暂不自动清理。
- 每种内容成功获取后缓存在内存，所有目标复用，不逐群请求。部分目标失败不阻止其他目标。
- 接口错误、空内容、日期不匹配、超时至少冷却 15 分钟，每个定时接口分别记录冷却期限和最后失败原因；HTTP 429 仅冷却对应接口，并尊重更长的 Retry-After（秒数或 HTTP 日期）。日报与历史任务并发执行，旧日报、接口异常或慢请求不阻塞另一项原定推送。成功获取后清除该接口的失败状态。跨日保留尚未到期的冷却期限，不绕过长 Retry-After；当日窗口外仍不重试。
- 重启保留发送记录，但内存缓存和请求冷却不保留；若有未发送目标，可能重新获取接口。频繁重载不适合用来绕过上游限流。
- 日报必须返回计划日期，旧日报/未来日报一律跳过并记录真实返回日期到警告日志，不假装为今天的内容。历史接口按真实返回月日核验，空值和无标题项忽略，无有效条目则不发送。
- 单条内容超过 3500 字符会截断并明确标注。发送失败不自动拆分重发。接口重定向拒绝、响应限制 1MB，使用异步 aiohttp，无阻塞网络请求。
- 停用/重载会取消并等待后台任务、关闭 HTTP 会话和数据库。存储异常使后台调度停止，需要检查后重载，避免丢失去重状态时继续发送。
- 同一实例中重复 initialize 不重复启动。SQLite 唯一键可保护共享数据库的发送占位，但不支持以多个进程部署本插件实现高可用或共享请求缓存。

## 安装和兼容性

目标为 Python 3.12+ / AstrBot 4.29.4，元数据限制 `>=4.29.4,<5`；这是基于文档和 v4.29.4 源码的 API 目标范围，不是所有版本已实测的保证。依赖 `aiohttp>=3.11.18,<4`，AstrBot 本身也使用该依赖。

本任务未部署：后续授权安装时，将本目录完整复制到独立开发实例的 `data/plugins/astrbot_plugin_60s/`，保持默认关闭，检查插件加载和配置，再确认目标。仓库为私有开发仓库，未发布至插件市场。

## 开发验证

在工作区根目录执行：

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q main.py core.py hot.py formats.py tests
```

测试用标准库 unittest、模拟 HTTP 客户端/响应、模拟发送、临时 SQLite 和假 AstrBot API；不会调用真实接口或发送真实消息。`core.py` 的 Engine 支持注入 client、send、logger，tick 接收明确时间用于确定性测试。

真实 AstrBot/aiohttp 未安装，尚未验证 WebUI 加载、真实接口连接、平台投递。PyPI 下载检查工具遇到 DNS 失败，Ruff 未运行；未安装系统软件或修改现有实例。

## 参考

- https://docs.60s-api.viki.moe/254026209e0
- https://docs.60s-api.viki.moe/254044388e0
- https://docs.astrbot.app/dev/star/guides/send-message.html
- https://docs.astrbot.app/dev/star/guides/plugin-config.html
- https://docs.astrbot.app/dev/star/guides/storage.html
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/context.py
