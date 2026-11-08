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

每次有效且不处于已知限流等待的命令都会重新请求；非图片格式查询 API **一次**，图片格式先 image、仅在文件名无法解析日期时额外查询一次 JSON；连续或并发两次命令分别请求，不读取结果缓存、不受定时成功去重或普通失败等待阻止。强制拉取仅指绕过插件缓存，不能强制上游服务刷新其自身缓存。

图片模式先请求配置 API 的 `/v2/60s?encoding=image`，读取 302 等跳转的 Location。若最后一个路径段严格为 `YYYY-MM-DD.png/jpg/jpeg/webp/gif` 且为真实日期，使用文件名日期；否则额外请求一次 JSON，读取 `data.date`。不读取 query/fragment 或目录里的日期，不请求 text/image-proxy。随后下载同一个跳转地址并正常跟随后续重定向。一次手动查询通常为一次 API 查询加图片下载；回退时为 image + JSON 两次 API 查询加图片下载，重定向还会增加 HTTP 次数。每条命令独立查询，不合并并发手动命令，也不自动重试。

手动查询只使用命令事件回复发起会话，不会主动调用其他目标，不开启定时任务，不改变配置、定时投递记录或定时重试状态。主动发送总开关为关闭、目标列表为空或该目标接口选 off 时，仍可手动查询。它是普通查询命令，遵循 AstrBot 的命令权限控制，不提供跨会话发送能力。

成功消息只发送 API 原始图片或原始 text/json/markdown 响应，不加插件标题、日期、来源、说明、页脚，不重新排版、不截断。上游自己带的标题、日期、链接或其他文字原样保留。手动旧日内容不会另加“非今日”提示；手动图片日期未知也不附加说明。帮助、错误及日志仍正常提供，日期与重试判断仍在内部执行。

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

- text/json/markdown：请求对应 API encoding 并原样发送 UTF-8 响应。JSON 不重新序列化，保留上游原始空白和字段。
- image：仅日报支持。先请求 image，文件名日期优先、JSON 日期回退；图片消息只有 Image 组件，没有 Plain 说明。手动可返回旧日、未来或日期未知的图片；定时仍只接受当天日期。

历史不支持图片，非法格式明确报错。图片响应不做域名白名单、MIME、文件头、内容或大小额外校验，直接交给 AstrBot；保留正常超时、HTTP 状态和限流处理。若上游错误地用 HTTP 200 返回非图片，可能在平台投递时失败。不会重新排版或移除原图版权。

## 每目标、每接口独立调度

时区使用全局 IANA timezone，默认 Asia/Shanghai。每个条目中的日报、历史分别设置：

- 开始时间 HH:MM。
- 未成功时的等待间隔，1–1440 分钟。
- 最后结束时间 HH:MM；包含该时刻，之后不得发送。结束早于开始表示跨午夜，不能与开始相同。

不同目标的时间、格式、关闭状态和普通失败等待互不影响。后台约每 30 秒检查一次，实际开始可能稍晚；获取、格式准备及发送前检查截止时间。错过整个窗口不补发。

同一轮到期的图片目标共享一次 image 查询和图片下载；图片需要 JSON 回退时，与同轮文本/json/markdown 目标共享一次 JSON 获取。定时非图片仍先用 JSON 判定日期和有效内容；json 直接复用此原始响应，text/markdown 再请求对应 encoding，同一轮相同接口/格式共享原文获取。下一次重试重新获取。日报返回旧日期时继续等待，历史和其他到期目标可独立执行。日期来自真实响应，历史缺少有效事件时不发送。

HTTP 429 的 Retry-After 是例外：同一个 API 接口的全部目标共同遵守限流期限，其他接口独立；图片流程的 429 同样约束该日报接口，历史接口独立；单独文字格式获取的 429 仍按原格式等待处理。等待取配置间隔与 Retry-After 中较长者，HTTP 429 最短等待 15 分钟，不能通过换目标或重启绕过。

跨午夜窗口归属于开始日。例如 8 日 23:50–9 日 00:20，仍按 8 日校验内容、去重；若上游已切到 9 日数据，跳过异日内容。结束边界为 HH:MM:00。夏令时采用系统 IANA 时区转换；建议 Asia/Shanghai 或避开时钟跳变时段。

投递状态按计划日期、UMO、接口分别记录：

- 成功：当天不重复。
- 平台明确返回 False：已知未成功，等待后重试。
- 超时或其他异常：可能已发送，标记结果不明，不自动重发，需人工核查。
- 进程中断留下 pending：同样不盲目重发。

重启保留成功/未知状态以及各目标等待期限；仍在窗口内则继续未尝试或明确失败的目标。存储位于 AstrBot 数据目录的 `plugin_data/astrbot_plugin_60s/deliveries.sqlite3`。保留旧 deliveries 表和记录；旧接口级冷却也继续遵守。停用/重载会取消并等待后台及共享请求，关闭 HTTP 会话和数据库；禁止多进程共享运行本插件做高可用。

## 热门榜单：命令选择格式

`/` 为示意前缀，以 AstrBot 配置为准。格式必须放在末尾且只出现一次，默认 text；旧条数参数仍接受 1–20 以兼容原命令，但不再截取原文，实际条数由上游接口决定。

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

热榜直接请求所选 encoding=text/json/markdown，原样发送，不再统一字段、添加来源或截取条数。例如官方微博 text/markdown 当前固定前20条，`/hot weibo 10` 的旧数字参数不会让插件删减上游正文。缓存键为“接口路径 + 格式”，缓存60秒、同格式合并并发；不同格式不复用正文。最多64项缓存，未缓存查询至少间隔2秒，普通错误冷却至少15秒，429尊重 Retry-After。缓存与冷却不写入定时数据库。

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

各榜单的标题、热度、链接及排版均由上游对应 encoding 决定，插件不添加或重命名字段。懂车帝等来源的公共服务可用性仍未在本机验证，不绕过 403；完整24类接口仍需真实服务联调。

## 一次性迁移与旧配置

新的 `destinations` 列表是运行时唯一发送目标来源，包含 UMO、两接口格式和各自时间。旧 `targets`、`target_formats`、`formats`、`schedules`、`daily_enabled`、`history_enabled`、`send_time`、`catchup_minutes` 隐藏保留在 schema 中，防止 AstrBot 在插件初始化前清除旧值。

首次加载时，旧 targets 中所有目标都会迁入；兼容旧格式映射字典、模板数组和 JSON 字符串。每目标覆盖优先于旧默认，旧接口启用为 false 的目标对应格式变为 off。旧 schedules 复制到每个旧目标的对应接口。旧目标格式中出现 targets 外目标会报错，不偷偷增加收件人。已显式填写的新条目会保留，重叠目标使用新条目设置。

旧结束时间未填写时，保留原 catchup_minutes 的“不含结束时刻、午夜截断”行为，隐藏在条目内兼容字段中；页面上的结束时间会保留空白。填入明确结束时间后改用新的包含结束边界/跨午夜规则。

迁移先验证，再通过 AstrBot save_config 保存，并写入隐藏标记 `destinations_migrated`。成功后不会再读取旧目标或覆盖项来恢复条目。不要修改迁移标记。旧格式数据保留（字典/数组序列化为隐藏文本），调度/去重不因迁移重置。升级前备份配置与数据库；本次开发未读取或修改生产配置。

## 其他配置与限制

base_url 默认 https://60s.viki.moe；timeout_seconds 默认 20（1–120），用于 HTTP、格式准备和单次发送。文字响应最大 1MB，超过则明确报错、不截断或拆分原文。只接受对应文字类型与有效 UTF-8；二进制/图片响应不会作为文字发送，JSON 业务错误仍报错。

UMO 格式、消息类型、重复条目及格式/时间会校验。发送前检查平台实例；不存在则明确记录未发送并返回可重试失败。无法离线确认远端群/用户是否存在或适配器是否具备权限。v4.29.4 源码注明 qq_official 不支持此主动发送方法。

目标版本 Python 3.12+、AstrBot >=4.29.4,<5、aiohttp>=3.11.18,<4。仓库公开，尚未发布插件市场；当前修复分支待用户检查，main 未合并。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q main.py core.py hot.py formats.py manual.py tests
```

单元测试使用模拟客户端、发送、事件以及临时 SQLite，未请求真实接口或发送消息。覆盖迁移、删空、默认格式、off、独立时间/重试、跨日、重启、去重、未知发送结果、共享请求与限流、任务取消；另覆盖手动命令单请求、重复/并发绕过缓存、当前会话回复、原文与纯图片输出、内部日期判断、格式错误、限流、取消，以及配置和定时 SQLite 不变。

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


## 图片日期与升级行为（v0.6.0）

`image_hosts` 从配置界面隐藏，旧默认、自定义列表和空列表原样保留但不再使用。无需维护域名列表。此分支基于 main，不包含之前“先 text 后 image”或图片域名安全连接器方案。支持用户配置的公共 API 及自托管 HTTP/HTTPS API（含内网）。

- image 返回 301/302/303/307/308：解析 Location（相对地址按 API 响应 URL 解析）。仅从完整文件名读取日期；缺失、非法日历日期、多个日期或非约定文件名均回退 JSON。Location 本身缺失则报错。
- image 直接返回 HTTP 200：读取响应字节，再查询 JSON 日期；没有跳转不影响自托管实例使用。
- 文件名无日期且 JSON 也没有有效日期：定时停止发送，按对应目标重试间隔与结束时间处理；手动仅显示原始图片，不增加日期文字。JSON 请求失败则明确报错，不掩盖失败或限流。
- 定时只有日期同时等于配置时区下的今天及计划日期才发送。旧日、未来或未知日期按原窗口重试；能从 Location 判定非今日时不下载图片。跨午夜的昨日计划不会在今天发送昨日图片。已有成功、明确失败重试、未知投递状态和重启去重逻辑保留。
- 若后续重定向的最终 URL 也带有效文件名日期，采用最终日期并重新判断；若最终 URL 没日期，沿用首次 Location/JSON 的判断依据。日期来源保留在内部结果，不附加到消息；不能据地址证明图片像素内容日期。

**限制：** 文件名日期是上游命名信息，不是图片内容证明；JSON 回退与 image 也非原子读取，不能保证两者内容日期一致。不会为此增加图片内容校验或再次刷新 API。未知/矛盾信息不能冒充已确认的今天。

测试使用合成 HTTP、重定向、JSON 和消息对象。先前主对话云环境实测官方 image 返回 302，跳转后 200、image/png、287931 字节；本 Ubuntu 电脑先前 DNS 解析失败，之后网页工具 text 请求遇到 429。本次没有新发起真实 API 请求、没有平台投递或生产变更；单元测试不是完整 aiohttp/AstrBot 网络联调。


## 原始内容发送的边界

成功内容不附加任何插件文字；帮助和明确错误不是成功内容，仍会回复。API 自带的标题或来源属于原文，不会删除。平台长度限制导致的失败沿用原处理，插件不会截断、拆段或擅自重试结果不明的投递。

手动 `/60s text/json/markdown`、`/history text/json/markdown` 每次只请求对应 encoding 一次。定时非图片为了保留原有日期/空内容判定，先获取 JSON：json 复用原始 JSON，text/markdown 各多一次格式请求。日期校验与随后 text/markdown 获取同样不是原子读取，上游更新可能造成不一致；图片仍使用文件名/JSON 回退机制。这些限制不会通过附加消息说明改变原始内容。

上游原始格式依据官方 `src/modules/weibo.module.ts`、`src/modules/today-in-history.module.ts` 等源码核对；不等于本机已真实调用全部接口或平台。
