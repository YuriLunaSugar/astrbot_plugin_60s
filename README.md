# 60s 日更与热门榜单（v0.3.2）

两个日更接口按各自时间表向明确指定的 AstrBot 会话推送；24 类热榜只通过命令查询。定时默认关闭，targets 为空，不自动请求或发送。没有执行部署或真实消息投递。

## 两个日更接口独立调度

在 WebUI 配置 `schedules.daily`（每天60秒）和 `schedules.history`（历史上的今天）：

- `send_time`：每天开始时间，HH:MM。
- `retry_minutes`：失败后的等待分钟数，1–1440，默认 15。
- `end_time`：当天最后结束时间，HH:MM；早于开始时间表示次日结束；开始和结束不能相同。
- 两项共用 `timezone`（默认 Asia/Shanghai），但执行、冷却和失败状态独立。`daily_enabled` / `history_enabled` 分别控制内容，`enabled` 控制定时总开关。

每 30 秒检查一次，实际尝试可能比配置时间晚约 30 秒。新时间表的结束边界为**包含该精确时刻**：例如 10:00 表示 10:00:00，而非整分钟。获取结束、格式准备结束及每个目标发送前检查截止时间；超过后不再启动发送。已开始的请求或投递允许在超时上限内完成，不能保证外部平台在截止前完成投递。

示例（保持关闭，实际启用前确认目标）：

```json
{
  "enabled": false,
  "timezone": "Asia/Shanghai",
  "targets": [],
  "schedules": {
    "daily": {"send_time": "09:00", "retry_minutes": 5, "end_time": "11:00"},
    "history": {"send_time": "08:30", "retry_minutes": 10, "end_time": "09:30"}
  }
}
```

每轮每个接口仅获取一次原始 JSON，所有待发送目标复用。获取失败、旧日报、空内容或格式准备失败会等待该接口间隔再重新获取；明确失败的目标重试时也重新获取，不使用上一轮旧缓存。成功目标不再次发送。慢日报不会阻塞历史接口。

429 会等待 `max(接口等待间隔, Retry-After, 15分钟)`，支持秒数或 HTTP 日期；只有被限流的接口等待。若等待结束已超过其窗口，则跳过该窗口。普通获取失败遵循配置间隔，不再强制 15 分钟。

## 安全重试、持久化与跨午夜

投递以“计划开始日期 + 完整目标会话 + 内容类型”为唯一键，SQLite 在发送前先写入占位：

| 状态 | 自动处理 |
| --- | --- |
| 无记录 | 窗口内且等待结束后可尝试 |
| retryable | 平台明确返回 False；下轮重新获取后只重试该目标 |
| sent | 已确认调用成功，当天不重复 |
| failed_or_unknown | 超时或任意抛出的异常，可能已送达；不自动重试，需人工核查 |
| pending | 发送中被取消、崩溃等留下的占位；不自动重试，需人工核查 |

当前 AstrBot 的 `context.send_message=False` 表示未找到匹配平台，属于明确未发送。一般异常即使看起来像网络失败，也可能发生在远端接受消息之后，因此不能当作明确失败重发。这里只对返回 False 自动重试，没有强制重发不明状态的命令。管理员应先在目标平台核对记录，再决定如何人工处理；不要直接删数据库来重试。

成功标记表示接口调用成功，不代表用户已阅读或平台最终送达。占位后进程崩溃可能漏发，优先避免重复。多目标状态独立，部分失败不阻止其他目标；同目标两类内容顺序不保证。

存储：AstrBot 数据目录下 `plugin_data/astrbot_plugin_60s/deliveries.sqlite3`。保留投递记录、真实内容日期以及每接口重试期限/错误；成功全部完成后清除该接口冷却。重启保留未到期的等待（含长 Retry-After），仍在窗口内则继续未完成的明确失败/未尝试目标；窗口外不补发。禁止多进程同时运行本插件实现高可用。

跨午夜窗口归属于**开始日**，如 11月8日 23:50–次日00:20，在9日00:05补发仍按8日去重和校验内容日期。若上游已切换为9日数据，不能冒充8日，跳过并等待直到窗口结束。历史接口也按窗口开始日的月日校验。未到期的失败等待可跨日影响下一窗口，尤其是 Retry-After，不能通过换日绕过限流。

夏令时开始/结束均采用所选 IANA 时区的本地时间（重复时刻使用首次，缺失时刻按系统时区转换顺延）；仍按开始日去重。建议使用 Asia/Shanghai 或避开时钟跳变时段。

## 每个目标分别设置两个日更格式

配置页的目标列表及目标格式文本框带可见填写提示和显然虚构的 UMO 示例。点击会话选择器可选择已记录会话，或从实际消息事件的 `event.unified_msg_origin` 取得真实标识；不应猜测平台实例 ID。示例不会自动填入目标列表或启用发送。

`targets` 是明确允许投递的完整 `event.unified_msg_origin` 列表，格式不是裸群号或接口 URL。`target_formats` 改为 **JSON 多行文本框**，直接编辑内容，无需“添加条目”或模板菜单。每项 target 须已存在于 targets，daily/history 分别设置；重复目标或非法格式会报错，不会增加新收件人。

将下面 JSON 数组粘贴到配置页面的“各目标日更格式”文本框，并替换虚构标识（先在上方目标列表选择真实会话）：

```json
[
  {"target": "example_bot:GroupMessage:demo_group_A", "daily": "image", "history": "text"},
  {"target": "example_bot:FriendMessage:demo_user_B", "daily": "markdown", "history": "json"}
]
```

无覆盖项填写 `[]`。只使用双引号，不加注释或尾逗号。省略 daily/history 或填写空字符串会继承旧默认，新安装默认 text。如果直接编辑整个配置文件，target_formats 的值是包含上述 JSON 的**字符串**；见 config.example.json。旧字典和模板数组仍可读取，首次成功加载后会保存为文本，不改变内部数据。

- text：整理后的可读文本。
- json：结构化结果以普通文本发送，保留合法 JSON；不是平台 JSON 卡片，也不是上游完整原始响应。
- markdown：Markdown 文本，是否渲染取决于聊天平台。
- image：仅每天60秒支持。先获取 JSON 核验日期，再下载同一响应 data.image，以真正的 AstrBot Image 组件发送，附真实日期与来源。

同一轮同接口相同格式只准备一次，多个 image 目标共享一次下载；目标之间格式不同也不重复获取原始 JSON。某一格式准备失败不阻止其他格式的目标，失败目标在等待后重试。

历史上的今天没有官方整图，填写 image 会明确报错。日报图片仅允许 image_hosts 中精确匹配的 HTTPS 域名，默认 mmbiz.qpic.cn；拒绝重定向，检查 MIME 与 PNG/JPEG/WebP/GIF 文件头，最大5MB，受超时限制。不把 JSON 或图片 URL 字符串当图片发送，不生成虚构图片，不删除原图版权标记。

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

## 旧配置与数据兼容

插件加载时使用 AstrBot 注入的配置，先验证全部设置，再通过 AstrBot 的 save_config 将旧 target_formats 字典/列表保存为 JSON 文本。转换不改内部字段、不改收件人、不启用发送；失败时不继续启动插件。升级前请备份配置。此次开发没有访问或修改生产配置。

- 未填写接口 send_time 时继承旧全局 send_time（默认09:00）。未填写接口 end_time 时保留旧 catchup_minutes 窗口（默认120分钟），旧模式结束时刻不含且不跨午夜。接口 retry_minutes 默认15。填写 end_time 后启用新的包含结束边界/跨午夜规则。
- 旧 formats.daily/history 仍作为未配置目标或留空格式的默认值，缺省为text。旧 formats 对象作为隐藏字典保留，避免更新对象schema时删除旧值；其中热榜键保留但不再参与查询。
- 旧 send_time、catchup_minutes、formats 留在 schema 中但隐藏展示，防止 AstrBot 自动清理旧值。它们仍用于空白接口字段的回退；只在两个接口各自的调度区修改时间、等待与结束时间。接口启用、调度和目标格式各有不同含义，均保留。
- 转换只在 target_formats 为字典或列表时写回；合法 JSON 文本重复加载不再写回。旧 __template_key 字段原样保留且不影响发送。AstrBot 本身会按 schema 补充缺失字段。
- 原 deliveries 表保持不变，仅创建 schedule_state 表。旧 pending/failed_or_unknown 不改成可重试；旧 sent 保留。不会因为升级重置当天去重。

## 其他配置与验证

base_url 默认 https://60s.viki.moe，不含/v2；timeout_seconds 默认20，限制HTTP、格式准备和单次发送时间。原始JSON最大1MB，单条文本约3500字符，超长明确截断；JSON通过减少条目保持有效。targets 必须为支持主动消息的平台会话，v4.29.4 源码注明 qq_official 不支持此主动发送方法。停用/重载会取消并等待后台/命令任务，关闭HTTP会话与数据库；存储错误会停止调度，避免丢失去重后继续发。

目标环境 Python 3.12+、AstrBot >=4.29.4,<5，依赖 aiohttp>=3.11.18,<4。API范围依据文档和v4.29.4源码，不代表所有版本已实测。仓库公开，未发布插件市场。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q main.py core.py hot.py formats.py tests
```

测试全部使用模拟客户端、模拟发送、模拟事件、假AstrBot API和临时SQLite，不调用真实API或发送消息。Engine可注入客户端、发送函数、格式准备函数、当前时间；覆盖独立调度、失败分类、重启、截止边界、跨午夜、多目标格式与共享请求、命令参数冲突。

真实 AstrBot/aiohttp 尚未安装，WebUI 加载、真实接口和平台投递尚未联调；此前 PyPI DNS 失败，Ruff未运行。未修改生产实例、系统软件或网络。API可访问不等于任意再分发许可，应遵守来源条款。

## 官方参考

- https://docs.60s-api.viki.moe/254026209e0
- https://docs.60s-api.viki.moe/254044388e0
- https://docs.astrbot.app/dev/star/guides/plugin-config.html
- https://docs.astrbot.app/dev/star/guides/send-message.html
- https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/context.py

## 配置页面问题验证（v0.3.2）

用户反馈模板菜单完全不弹出；未获取实际 Dashboard 版本、控制台错误或可访问页面，所以无法认定该现象的根因。此版本以普通 text/textarea 控件绕开模板菜单。另通过 AstrBot v4.29.4 原始组件函数确认：旧 target_formats 字典会使模板 addEntry 抛出 not iterable；这是独立验证的升级缺陷，不等同于已复现用户的菜单故障。

开发机无 AstrBot、aiohttp、npm，shell DNS 无法下载依赖；只连接到无 AstrBot 标签页的内置浏览器。未完成真实 WebUI 点击、渲染或生产实例联调。

可选源代码契约测试：将 v4.29.4 的 astrbot/core/config/astrbot_config.py 和 dashboard/src/components/shared/{TemplateListEditor,ConfigItemRenderer,AstrBotConfig}.vue 按文件名放入独立参考目录，再运行：

```sh
python3 tests/check_upstream_config_contract.py /path/to/reference-directory
```

该脚本实际执行上游配置类的临时文件加载/保存/重载，以及原始 JS 新增函数；检查文本控件路由和隐藏字段源码。它不是 DOM/WebUI 测试，不应当作页面可用性证明。上游参考版本：https://github.com/AstrBotDevs/AstrBot/tree/v4.29.4 。
