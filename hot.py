"""Verified hot-list routes; never accept a user-provided URL or path."""
import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .core import ApiError
from .formats import Output, check_format, structured_output


@dataclass(frozen=True)
class Board:
    key: str
    name: str
    path: str
    doc: str


BOARDS = [
    Board("douyin", "抖音", "douyin", "254028799e0"),
    Board("ithome", "IT之家", "it-news/rank", "455707126e0"),
    Board("rednote", "小红书", "rednote", "344628918e0"),
    Board("bili", "哔哩哔哩", "bili", "254039323e0"),
    Board("weibo", "微博", "weibo", "254026663e0"),
    Board("quark", "夸克", "quark", "383391822e0"),
    Board("baidu", "百度", "baidu/hot", "345694532e0"),
    Board("baidu_tv", "百度电视剧", "baidu/teleplay", "345696060e0"),
    Board("tieba", "贴吧", "baidu/tieba", "345697627e0"),
    Board("toutiao", "头条", "toutiao", "254038833e0"),
    Board("zhihu", "知乎", "zhihu", "254026504e0"),
    Board("dongchedi", "懂车帝", "dongchedi", "345808679e0"),
    Board("ncm_list", "网易云榜单", "ncm-rank/list", "341610331e0"),
    Board("ncm_detail", "网易云详情", "ncm-rank/{id}", "341610332e0"),
    Board("hn", "HackerNews", "hacker-news/top", "343622824e0"),
    Board("maoyan_all", "猫眼总票房", "maoyan/all/movie", "261610562e0"),
    Board("maoyan_movie", "猫眼电影", "maoyan/realtime/movie", "353506911e0"),
    Board("maoyan_tv", "猫眼电视", "maoyan/realtime/tv", "353791089e0"),
    Board("maoyan_web", "猫眼网剧", "maoyan/realtime/web", "353791308e0"),
    Board("douban_movie", "豆瓣电影", "douban/weekly/movie", "421319192e0"),
    Board("douban_tv_cn", "豆瓣华语剧", "douban/weekly/tv_chinese", "421324039e0"),
    Board("douban_tv_global", "豆瓣全球剧", "douban/weekly/tv_global", "421324081e0"),
    Board("douban_show_cn", "豆瓣华语综艺", "douban/weekly/show_chinese", "421324089e0"),
    Board("douban_show_global", "豆瓣全球综艺", "douban/weekly/show_global", "421330297e0"),
]
LOOKUP = {alias.lower(): board for board in BOARDS for alias in (board.key, board.name)}
LOOKUP.update({"b站": LOOKUP["bili"], "网易云": LOOKUP["ncm_list"], "百度贴吧": LOOKUP["tieba"]})


def help_text():
    return "热榜用法：/热榜 榜单 [条数1-20]\n例如 /热榜 微博 5\n网易云：/热榜 网易云详情 榜单ID [条数]\nHackerNews: /热榜 hn [top/new/best] [条数]\nIT之家: /热榜 IT之家 [day/week/month] [条数]\n榜单：\n" + "、".join(b.name for b in BOARDS)


def parse_query(source, argument="", count=""):
    board = LOOKUP.get(source.lower())
    if board is None:
        raise ValueError("未知榜单，请发送 /热榜 帮助")
    path = board.path
    if board.key == "ncm_detail":
        if not argument.isascii() or not argument.isdigit() or not 1 <= len(argument) <= 20 or int(argument) <= 0:
            raise ValueError("请先用 /热榜 网易云榜单 获取ID，再用 /热榜 网易云详情 ID [条数]")
        path = path.replace("{id}", argument)
        limit_text = count or "10"
    elif board.key in ("hn", "ithome"):
        options = ("top", "new", "best") if board.key == "hn" else ("day", "week", "month")
        variant = options[0]
        if argument in options:
            variant = argument
            limit_text = count or "10"
        elif argument.isdigit() or not argument:
            if count:
                raise ValueError("参数过多")
            limit_text = argument or "10"
        else:
            raise ValueError("分类应为 " + "/".join(options))
        path = "hacker-news/" + variant if board.key == "hn" else "it-news/rank?type=" + variant
    else:
        if count:
            raise ValueError("参数过多，用法：/热榜 榜单 [条数]")
        limit_text = argument or "10"
    if not limit_text.isascii() or not limit_text.isdigit() or not 1 <= int(limit_text) <= 20:
        raise ValueError("条数必须为 1–20 的整数")
    return board, path, int(limit_text)


def scalar(value, max_length=160):
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return " ".join(str(value).split())[:max_length]
    return ""


def first(item, names):
    for name in names:
        value = scalar(item.get(name))
        if value:
            return value
    return ""


def normalize(payload, board, limit):
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise ApiError("榜单 API 返回业务错误")
    data = payload.get("data")
    extra = {}
    if isinstance(data, dict):
        updated = first(data, ("updated", "date", "update_time"))
        if updated:
            extra["接口更新时间"] = updated
        data = next((data[k] for k in ("list", "items", "tracks") if isinstance(data.get(k), list)), None)
    if not isinstance(data, list):
        raise ApiError("榜单返回结构不支持或内容为空")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = first(item, ("title", "name", "movie_name", "programme_name", "series_name"))
        if not title:
            continue
        row = {"title": title}
        rank = scalar(item.get("rank"))
        if rank:
            row["原榜排名"] = rank
        if board.key.startswith("douban"):
            metric, label = first(item, ("rating",)), "评分"
        elif board.key in ("maoyan_all", "maoyan_movie"):
            metric, label = first(item, ("box_office_desc",)), "票房"
            if not metric:
                metric = first(item, ("box_office",)) + scalar(item.get("box_office_unit"))
        elif board.key == "maoyan_tv":
            metric, label = first(item, ("attention_rate_desc", "attention_rate")), "收视率"
            row["频道"] = first(item, ("channel_name",))
        elif board.key == "maoyan_web":
            metric, label = first(item, ("curr_heat_desc", "curr_heat")), "热度"
        else:
            metric = first(item, ("hot_value", "score_desc", "score", "hot", "heat", "popularity"))
            label = "积分" if board.key == "hn" else "热度"
        if metric:
            row[label] = metric
        if board.key == "ncm_list":
            row["榜单ID"] = scalar(item.get("id"))
        if board.key == "ncm_detail" and isinstance(item.get("artist"), list):
            row["歌手"] = " / ".join(scalar(a.get("name")) for a in item["artist"] if isinstance(a, dict))[:160]
        link = item.get("link") or item.get("url")
        if isinstance(link, str) and len(link) <= 800:
            try:
                parsed = urlsplit(link)
                if parsed.scheme in ("https", "http") and parsed.hostname and not any(c.isspace() for c in link):
                    row["link"] = link
            except ValueError:
                pass
        rows.append(row)
        if len(rows) == limit:
            break
    if not rows:
        raise ApiError("榜单暂无有效条目")
    return rows, extra


class HotService:
    def __init__(self, fetch, formats=None, timeout=20, clock=time.monotonic):
        self.fetch, self.formats, self.timeout, self.clock = fetch, formats or {}, timeout, clock
        self.cache = {}
        self.lock = asyncio.Lock()
        self.blocked_until = 0

    async def query(self, source, argument="", count=""):
        if source in ("帮助", "help", "列表", ""):
            return Output(help_text())
        board, path, limit = parse_query(source, argument, count)
        fmt = check_format(self.formats.get(board.key, "text"))
        # Single-flight across command requests; cached successes bypass API cooldown.
        async with self.lock:
            now = self.clock()
            self.cache = {k:v for k,v in self.cache.items() if now - v[0] < 60}
            cached = self.cache.get(path)
            if cached:
                payload = cached[1]
            else:
                if now < self.blocked_until:
                    raise ApiError(f"查询过于频繁或上游限流，请 {int(self.blocked_until-now)+1} 秒后重试")
                self.blocked_until = now + 2
                try:
                    payload = await asyncio.wait_for(self.fetch(path), self.timeout)
                    normalize(payload, board, limit)
                except Exception as exc:
                    self.blocked_until = self.clock() + max(15, getattr(exc, "retry_after", 15))
                    raise ApiError("榜单暂时不可用，请稍后重试") from exc
                if len(self.cache) >= 64:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[path] = (self.clock(), payload)
        rows, extra = normalize(payload, board, limit)
        extra["来源"] = board.name + " / 60s API"
        return structured_output(board.name + "榜单", rows, fmt, extra)
