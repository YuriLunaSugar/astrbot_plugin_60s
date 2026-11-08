# 60s 日更与热门榜单（v0.5.0）

每个 UMO 单独设置每天60秒、历史上的今天的格式及时间表；24 类热榜通过 `/hot` 查询，日更可通过 `/60s`、`/history` 手动拉取。主动发送总开关默认关闭，发送目标默认空列表。未部署到生产，也没有执行真实消息投递。

## 手动拉取一次日更

```text
/60s                 # 默认 image，拉取一次日报并回复当前会话
/60s text
/60s json
/60s markdown
/history             # 默认 text，拉取一次历史上的今天
/history json
/history markdown
/60s 帮助
/history help
```

`/` 为示意前缀，以 AstrBot 配置为准。日报支持 image/text/json/markdown；历史支持 text/json/markdown，没有图片模式。手动查询不接受 off、日期或发送目标参数，仅查询对应接口当前提供的内容。

每次有效且不处于已知限流等待的命令，都会重新请求对应 API **一次**；连续或并发两次命令分别请求，不读取结果缓存、不受定时成功去重或普通失败等待阻止。强制拉取仅指绕过插件缓存，不能强制上游服务刷新其自身缓存。

图片模式先请求一次 JSON API 以取得真实日期和图片地址，再下载一次图片；不会为图片重复请求日更 API。缺图、非法图片或下载失败会明确报错，不自动改成文本，也不重试。

手动查询只使用命令事件回复发起会话，不会主动调用其他目标，不开启定时任务，不改变配置、定时投递记录或定时重试状态。主动发送总开关为关闭、目标列表为空或该目标接口选 off 时，仍可手动查询。它是普通查询命令，遵循 AstrBot 的命令权限控制，不提供跨会话发送能力。

始终显示 API 实际日期：日报 YYYY-MM-DD、历史月-日。若与配置时区下的当前日期不符，会明确标注“接口返回非今日内容”，展示实际返回内容；不会把旧日报冒充今天。JSON 输出保持合法 JSON，带 source_date，异日时另带 current_date 和 notice。非法日期或空内容报错。本版不添加未经确认的任意历史日期参数。

HTTP 超时/错误、业务错误、格式错误都只返回一次错误，不循环重试。429 返回 Retry-After 等待提示，等待期内后续手动查询不发起请求（因此是零请求）；图片限流不阻塞纯文本格式。帮助或非法参数也不请求 API。手动查询自身的限流等待仅保存在当前插件实例内，不写入定时 SQLite，重载后不保留；已有调度器的明确 API/格式 Retry-After 只读遵守，不修改。停用或重载会取消正在进行的手动请求。

## 在配置页面添加目标

1. 找到“发送目标与日更格式”，点击“添加条目”。
2. 在菜单中选择 **“发送目标”**，即新增一条配置；菜单提示会说明新增动作，该步骤由 AstrBot 内置控件决定。
3. 在条目内选择目标会话 UMO；不用再填写另一份目标列表。
4. 分别选择“每天60秒”和“历史上的今天”的格式及开始时间、等待分钟数、结束时间。
5. 保存；确认目标和时间后，再手动打开主动发送总开关。

条目主标题为“发送目标”，选择会话后，标题下方显示 `目标会话 UMO: 完整标识`。未选择时该行为空，请展开条目选择 UMO；空白会话不能保存为有效目标。主标题和添加菜单共用 AstrBot 模板 name，schema 无法把已选 UMO 改为动态主标题。display_item 只控制副标题，而且不会解析群名或好友昵称。

每个 UMO 只添加一次。可在 AstrBot 会话选择器中选择已记录会话；如需手工填写，从实际事件 `event.unified_msg_origin` 获取完整标识。`example_bot:GroupMessage:demo_group_A` 只是虚构格式示例，不是可投递目标。支持 AstrBot 的 GroupMessage、FriendMessage、OtherMessage 标识，不能填裸群号或 API URL。

新条目的默认值：日报 **图片（image）**，历史 **纯文本（text）**；两个接口各自默认 09:00 开始、等待 15 分钟、11:00 结束。旧目标升级时保留原来的有效格式和时间，避免升级改变发送行为。

格式下拉中的 **关闭（off）** 仅关闭当前目标的当前接口：不参与调度、不为它请求或发送。总开关关闭时全部主动发送停止（不影响手动查询）；删除条目会移除该发送目标；删除全部条目后保持无目标，不会从旧配置恢复。

- text：可读纯文本。
- json：整理后的结构化 JSON 文本，不是平台卡片，也不是上游完整原始响应。
- markdown：Markdown 文本，是否渲染取决于聊天平台。
- image：仅日报支持，先获取 JSON 校验真实日期，再下载该响应中的官方图片，用 AstrBot Image 消息发送，附日期与来源。

历史不支持图片，非法格式明确报错。日报图片默认只允许 mmbiz.qpic.cn 的 HTTPS 地址，不跟随重定向；验证 MIME、PNG/JPEG/WebP/GIF 文件头和 5MB 大小。不会把 JSON、图片 URL 字符串当作图片，也不生成或移除原图版权信息。

## 每目标、每接口独立调度

时区使用全局 IANA timezone，默认 Asia/Shanghai。每个条目中的日报、历史分别设置：

- 开始时间 HH:MM。
- 未成功时的等待间隔，1–1440 分钟。
- 最后结束时间 HH:MM；包含该时刻，之后不得发送。结束早于开始表示跨午夜，不能与开始相同。

不同目标的时间、格式、关闭状态和普通失败等待互不影响。后台约每 30 秒检查一次，实际开始可能稍晚；获取、格式准备及发送前检查截止时间。错过整个窗口不补发。

同一轮到期的目标共享同接口的一次原始请求，相同格式共享准备/图片下载；下一次重试重新获取。日报返回旧日期时继续等待，历史和其他到期目标可独立执行。日期来自真实响应，历史缺少有效事件时不发送。

HTTP 429 的 Retry-After 是例外：同一个 API 接口的全部目标共同遵守限流期限，其他接口独立；图片下载限流只约束该接口的图片格式，不阻塞文本。等待取配置间隔与 Retry-After 中较长者，HTTP 429 最短等待 15 分钟，不能通过换目标或重启绕过。

跨午夜窗口归属于开始日。例如 8 日 23:50–9 日 00:20，仍按 8 日校验内容、去重；若上游已切到 9 日数据，跳过异日内容。结束边界为 HH:MM:00。夏令时采用系统 IANA 时区转换；建议 Asia/Shanghai 或避开时钟跳变时段。

投递状态按计划日期、UMO、接口分别记录：

- 成功：当天不重复。
- 平台明确返回 False：已知未成功，等待后重试。
- 超时或其他异常：可能已发送，标记结果不明，不自动重发，需人工核查。
- 进程中断留下 pending：同样不盲目重发。

重启保留成功/未知状态以及各目标等待期限；仍在窗口内则继续未尝试或明确失败的目标。存储位于 AstrBot 数据目录的 `plugin_data/astrbot_plugin_60s/deliveries.sqlite3`。保留旧 deliveries 表和记录；旧接口级冷却也继续遵守。停用/重载会取消并等待后台及共享请求，关闭 HTTP 会话和数据库；禁止多进程共享运行本插件做高可用。

## 热门榜单：命令选择格式

`/` 为示意前缀，以 AstrBot 配置为准。格式必须放在末尾且只出现一次，默认 text；条数默认10，范围1–20。

```text
/hot 帮助
/hot weibo 10
/hot weibo 10 markdown
/hot 小红书 json
/hot 网易云榜单 10 text
/hot 网易云详情 3779629 5 json
/hot hn best 5 markdown
/hot IT之家 week 5 text
```

HN 类型 top/new/best；IT之家 day/week/month。网易云 ID 从榜单列表取得。榜单可使用表中英文配置键或中文名；另支持 bilibili、xiaohongshu、netease、netease-detail 别名。命令入口仅 /hot，不注册旧 /热榜。只回复当前触发会话，不遍历定时目标。热榜不再读取或显示各接口格式配置；全部使用命令参数。官方未提供整榜图片，命令 image 会明确报错，不静默回退；若要所有榜单图片，需要另行实现排版生成，本版本没有该功能。

HTTP 始终请求 encoding=json，以便核验日期、统一字段、限制条数，再根据发送格式构造消息。不是直接请求上游 text/markdown/image-proxy。热榜同一路径缓存60秒并合并并发请求，不同格式和条数可复用同一份原始数据；最多64项缓存，未缓存查询至少间隔2秒，普通错误冷却至少15秒，429尊重 Retry-After。热榜缓存/冷却不持久化，与定时调度分开。

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

普通榜单适配 data 数组，猫眼适配 data.list；标题识别 title/name/movie_name/programme_name/series_name。按实际字段展示热度、评分、票房、收视率及 link/url；缺少字段不伪造。网易云列表显示ID，详情显示歌手。知乎缺少完整响应样例，采用通用字段兼容；懂车帝读取到文档示例，但公共服务可用性未验证，403不绕过。所有来源仍需真实实例联调。

## 一次性迁移与旧配置

新的 `destinations` 列表是运行时唯一发送目标来源，包含 UMO、两接口格式和各自时间。旧 `targets`、`target_formats`、`formats`、`schedules`、`daily_enabled`、`history_enabled`、`send_time`、`catchup_minutes` 隐藏保留在 schema 中，防止 AstrBot 在插件初始化前清除旧值。

首次加载时，旧 targets 中所有目标都会迁入；兼容旧格式映射字典、模板数组和 JSON 字符串。每目标覆盖优先于旧默认，旧接口启用为 false 的目标对应格式变为 off。旧 schedules 复制到每个旧目标的对应接口。旧目标格式中出现 targets 外目标会报错，不偷偷增加收件人。已显式填写的新条目会保留，重叠目标使用新条目设置。

旧结束时间未填写时，保留原 catchup_minutes 的“不含结束时刻、午夜截断”行为，隐藏在条目内兼容字段中；页面上的结束时间会保留空白。填入明确结束时间后改用新的包含结束边界/跨午夜规则。

迁移先验证，再通过 AstrBot save_config 保存，并写入隐藏标记 `destinations_migrated`。成功后不会再读取旧目标或覆盖项来恢复条目。不要修改迁移标记。旧格式数据保留（字典/数组序列化为隐藏文本），调度/去重不因迁移重置。升级前备份配置与数据库；本次开发未读取或修改生产配置。

## 其他配置与限制

base_url 默认 https://60s.viki.moe；timeout_seconds 默认 20（1–120），用于 HTTP、格式准备和单次发送。原始 JSON 最大 1MB，文本过长会明确截断；JSON 通过减少条目保持有效。

UMO 格式、消息类型、重复条目及格式/时间会校验。发送前检查平台实例；不存在则明确记录未发送并返回可重试失败。无法离线确认远端群/用户是否存在或适配器是否具备权限。v4.29.4 源码注明 qq_official 不支持此主动发送方法。

目标版本 Python 3.12+、AstrBot >=4.29.4,<5、aiohttp>=3.11.18,<4。仓库公开，尚未发布插件市场；当前修复分支待用户检查，main 未合并。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q main.py core.py hot.py formats.py manual.py tests
```

单元测试使用模拟客户端、发送、事件以及临时 SQLite，未请求真实接口或发送消息。覆盖迁移、删空、默认格式、off、独立时间/重试、跨日、重启、去重、未知发送结果、共享请求与限流、任务取消；另覆盖手动命令单请求、重复/并发绕过缓存、当前会话回复、日期提示、格式错误、限流、取消，以及配置和定时 SQLite 不变。

## 官方配置组件验证范围

用户已确认原模板菜单实际可打开，第一项容易被误认作提示；本版使用中性模板名称“发送目标”，新增操作说明只出现在添加菜单，避免已创建条目继续显示“点击新增”。保留宿主必须的模板选择步骤。未宣称“一键新增”，没有修改 AstrBot 核心。

可选源码契约测试使用 AstrBot v4.29.4 原始文件：

- astrbot/core/config/astrbot_config.py
- astrbot/dashboard/services/config_service.py
- dashboard/src/components/shared/{TemplateListEditor,ConfigItemRenderer,AstrBotConfig}.vue

将上述文件按文件名放入独立参考目录，再运行：

```sh
python3 tests/check_upstream_config_contract.py /path/to/reference-directory
```

实际执行官方配置类的加载、Dashboard 校验、保存、重载，以及原始模板新增函数，验证迁移数据、新条目默认值，以及未选/已选/更换 UMO 时的原始标题辅助函数。这是源码契约测试，**不是完整 WebUI/DOM 测试**。开发机没有 AstrBot、aiohttp、npm，曾因 shell DNS 无法下载依赖；未完成真实 WebUI 点击、适配器或 API 联调，Ruff 未运行。

官方参考：

- https://docs.astrbot.app/dev/star/guides/plugin-config.html
- https://docs.astrbot.app/dev/star/guides/send-message.html
- https://github.com/AstrBotDevs/AstrBot/tree/v4.29.4
- https://docs.60s-api.viki.moe/254026209e0
- https://docs.60s-api.viki.moe/254044388e0

请遵守上游内容的来源和使用条款。
