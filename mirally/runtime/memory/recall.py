"""Bounded lexical recall, local calendar ranges and evidence excerpts; no model calls."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PLATFORMS = (("bilibili", "哔哩哔哩", "哔哩", "b站"),
             ("youtube", "油管"), ("github",), ("小红书", "xiaohongshu"))
VIDEO_TERMS = ("bilibili.com/video", "youtube.com/watch", "youtu.be/", "视频标题", "b23.tv/", "douyin.com/video")
_VIDEO_URL = re.compile(r"https?://(?:www\.)?(?:bilibili\.com/video/|youtube\.com/watch\?[^\s<>]*\bv=|youtu\.be/|b23\.tv/|douyin\.com/video/)", re.I)
_VIDEO_EVIDENCE = re.compile(_VIDEO_URL.pattern + r"|视频标题\s*[:：]\s*\S", re.I)
_STOP = re.compile(r"我|我们|你|请|帮忙|帮我|帮|回忆|回顾|找找|查找|找到|查询|记得|记忆|记录|活动|"
    r"看过|看了|看到|浏览过|浏览|打开过|打开|访问过|访问|用过|使用|做过|做了|之前|刚才|上次|"
    r"继续|接着|一下|那个|这个|一个|哪些|什么|是啥|哪里|哪个|如何|怎么|有没有|是否|有意思|有趣|有个|视频|页面|"
    r"今天|昨天|前天|最近|近期|这几天|工作|事情|或者|以及|是|的|了|过|在|中|上|吗|呢|和|与|或", re.I)
_HISTORY = re.compile(r"我的|我们.*(?:项目|工作)|之前|刚才|上次|继续|接着|昨天|前天|今天|最近|近期|回忆|回顾|记得|记录|看过|浏览过|打开过|访问过|做过|earlier|yesterday|recent|continue|resume", re.I)
_CONTINUE = re.compile(r"继续|接着|刚才|上次|continue|resume", re.I)


@dataclass(frozen=True)
class RecallPlan:
    terms: tuple[str, ...]
    platform: tuple[str, ...] = ()
    start: int | None = None
    end: int | None = None
    temporal: str | None = None
    continuation: bool = False
    invalid: bool = False
    domain: str | None = None


def _zone():
    name = os.environ.get("TZ", "").lstrip(":")
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            # Windows Python may not ship the IANA database. Use the optional
            # installed timezone database if available; the OS remains default.
            try:
                from dateutil.tz import gettz
                resolved = gettz(name)
                if resolved is not None:
                    return resolved
            except ImportError:
                pass
    # Naive datetime.timestamp() delegates calendar/DST rules to the host OS.
    return None


def local_stamp(timestamp_ms: int) -> str:
    stamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    try:
        return stamp.astimezone(_zone()).isoformat()
    except (OSError, ValueError, OverflowError):
        return stamp.isoformat()


def _query_focus(query: str) -> str:
    """Separate the requested activity from date anchors and answer constraints."""
    # A surrounding explanation may name today, yesterday, and a warning not
    # to confuse them. The actual history question controls the calendar range.
    intent = re.compile(r"看过|看了|在.*看|浏览|访问|打开|做过|做了|工作|回顾|查找|查询|请查|找.*(?:记录|视频)|watch|brows|work", re.I)
    questions = [part for part in re.findall(r"[^。！？?\n]+[？?]", query) if intent.search(part)]
    if questions:
        focused = questions[-1]
    else:
        sentences = [part for part in re.split(r"[。！？?\n]", query) if intent.search(part)]
        focused = sentences[-1] if sentences else query
    # Date declarations in the same sentence are context, not extra requested
    # days. Keep an explicitly queried date such as '查2026-10-14的记录'.
    return re.sub(r"(?:今天|昨天|前天)(?:的日期)?\s*(?:是|为)\s*20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?", " ", focused)


def plan(query: str, now_ms: int) -> RecallPlan:
    query = _query_focus(query[:512].lower())
    continuation = bool(_CONTINUE.search(query))
    domain = "video" if "视频" in query else None
    zone = _zone()
    now = datetime.fromtimestamp(now_ms / 1000, tz=zone)
    start = end = None
    temporal = None
    invalid = False
    date = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?", query)
    days = next(((word, offset) for word, offset in (("前天", 2), ("昨天", 1), ("今天", 0)) if word in query), None)
    recent = re.search(r"最近\s*(\d{1,3})\s*(小时|天|周)", query)
    if date or days:
        temporal = date[0] if date else days[0]
        try:
            target = datetime(*map(int, date.groups()), tzinfo=zone) if date else (
                now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days[1]))
            start = int(target.timestamp() * 1000)
            end = int((target + timedelta(days=1)).timestamp() * 1000) - 1
        except ValueError:
            invalid = True
        query = query.replace(temporal, " ")
    elif recent or re.search(r"最近|近期|这几天", query):
        temporal = recent[0] if recent else "最近"
        amount = min(int(recent[1]), 365) if recent else 7
        seconds = amount * ({"小时": 3600, "天": 86400, "周": 604800}[recent[2]] if recent else 86400)
        start, end = now_ms - seconds * 1000, now_ms
        query = query.replace(temporal, " ")
    daypart = next(((word, hours) for word, hours in (
        ("凌晨", (0, 6)), ("早上", (6, 12)), ("上午", (6, 12)),
        ("中午", (11, 14)), ("下午", (12, 18)), ("晚上", (18, 24))) if word in query), None)
    if daypart:
        if not invalid and (date or days or start is None):
            midnight = datetime.fromtimestamp(start / 1000, tz=zone) if start is not None else now.replace(hour=0, minute=0, second=0, microsecond=0)
            start = int((midnight + timedelta(hours=daypart[1][0])).timestamp() * 1000)
            end = int((midnight + timedelta(hours=daypart[1][1])).timestamp() * 1000) - 1
            temporal = (temporal or "今天") + daypart[0]
        query = query.replace(daypart[0], " ")
    platform = next((aliases for aliases in PLATFORMS if any(alias in query for alias in aliases)), ())
    for aliases in PLATFORMS:
        for alias in aliases:
            query = query.replace(alias, " ")
    identifiers = [term for term in re.findall(r"[a-z0-9][a-z0-9_.:/-]{1,100}", query)
                   if term not in {"continue", "resume", "recent", "yesterday", "earlier", "work", "the", "my"}]
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", _STOP.sub(" ", query))
    # Single Latin letters/numbers are never evidence search terms.
    terms = tuple(dict.fromkeys([*platform, *identifiers, *chinese]))[:8]
    return RecallPlan(terms, platform, start, end, temporal, continuation, invalid, domain)


def eligible(query: str, parsed: RecallPlan) -> bool:
    if parsed.invalid:
        return False
    english_question = bool(re.match(r"\s*(?:what|how|why|where|when|who|explain|define|tell me about)\b", query, re.I))
    personal_history = bool(re.search(r"\b(?:i|my|we|our)\b", query, re.I)
                            and re.search(r"\b(?:did|was|were|watched|opened|browsed|yesterday|earlier|recently)\b", query, re.I))
    if english_question and not personal_history:
        return False
    if re.search(r"如何|怎么|是什么|为什么|多少", query) and not re.search(
            r"看过|看了|浏览过|打开过|访问过|做过|做了|回忆|回顾|记得|继续|刚才", query):
        return False
    # Bare entities/file names are useful explicit lookup; ordinary how/what
    # questions about a platform should not inject someone's browsing history.
    personal = personal_history or bool(_HISTORY.search(query)) or parsed.temporal is not None
    explicit = bool(re.fullmatch(r"[\w.\-/ :]{2,100}", query, re.ASCII)) and len(query.split()) <= 3
    if re.search(r"\b(?:what|how|why|where|when|who|explain|define)\b", query, re.I):
        explicit = False
    chinese_entity = bool(re.fullmatch(r"[\u4e00-\u9fff]{2,12}", query)) and not re.search(r"什么|如何|怎么|多少|为什么", query)
    return personal or explicit or chinese_entity


def relevance(row, parsed: RecallPlan) -> float:
    content = str(row.get("content", "")).lower()
    title = str(row.get("title", "")).lower()
    text = title + "\n" + content
    if parsed.domain == "video" and not _VIDEO_EVIDENCE.search(text):
        return 0
    if parsed.platform and not any(alias in text for alias in parsed.platform):
        return 0
    hits = [term for term in parsed.terms if term in text]
    if parsed.terms and not hits:
        return 0
    if not parsed.terms:
        return 2 if parsed.domain else (1 if parsed.continuation or parsed.temporal else 0)
    # Independent entities matter more than repeated platform aliases.
    entities = [term for term in hits if term not in parsed.platform]
    if any(term not in parsed.platform for term in parsed.terms) and not entities:
        return 0
    return 4 * len(entities) + 2 * bool(parsed.platform) + sum(term in title for term in hits)


def retrieve(store, parsed, *, now_ms, scope=None, kinds=(), provenances=(),
             include_candidates=False, start=None, end=None):
    """At most 16 indexed searches; all security/time filters run before LIMIT."""
    if parsed.invalid:
        return []
    start = max(value for value in (start, parsed.start) if value is not None) if start is not None or parsed.start is not None else None
    end = min(value for value in (end, parsed.end) if value is not None) if end is not None or parsed.end is not None else None
    if start is not None and end is not None and start > end:
        return []
    candidates = {}
    terms = parsed.terms or (VIDEO_TERMS if parsed.domain == "video" else (("",) if parsed.continuation or parsed.temporal else ()))
    groups = (tuple(provenances),) if provenances else (("model_inference", "user_confirmed"), ("observed",))
    for sources in groups:
        for term in terms:
            for row in store.search(query=term, limit=12, scope=scope,
                    kinds=kinds or (("working",) if not term and not parsed.temporal else ()),
                    start_utc_ms=start, end_utc_ms=end, provenances=sources,
                    include_candidates=include_candidates, now_utc_ms=now_ms, max_chars=240_000):
                score = relevance(row, parsed)
                if score:
                    candidates[row["id"]] = {**row, "recall_score": score}
    return sorted(candidates.values(), key=lambda row: (
        row.get("provenance") in {"user_confirmed", "model_inference"},
        row["recall_score"], row.get("kind") != "working",
        row.get("observed_end_utc_ms") or 0, row["id"]), reverse=True)


def _video_excerpt(text: str, parsed: RecallPlan, max_bytes: int) -> str:
    """Prefer a title and video URL from the same recorded window fragment."""
    url_pattern = re.compile(r"https?://[^\s<>\"\]\)]+")
    entries = {}
    for line in text.splitlines():
        for match in url_pattern.finditer(line):
            url = match[0]
            if not _VIDEO_URL.match(url):
                continue
            title, strength = "", 0
            prefix = line[:match.start()].rstrip(" |")
            if "|" in prefix:
                # Organizer window fragments encode app | window title | URL.
                title = prefix.split("|")[-1].strip()
                strength = 3
            previous = entries.get(url)
            if previous is None or strength > previous[1]:
                entries[url] = (title, strength)
    labelled = re.findall(r"(?:视频标题|Window title)\s*[:：]\s*([^\n]+)", text, re.I)
    explicit = re.findall(r"视频标题\s*[:：]\s*([^\n]+)", text)
    for url, (title, strength) in list(entries.items()):
        if not title:
            aliases = next((aliases for aliases in PLATFORMS if any(alias in url.lower() for alias in aliases)), ())
            related = list(dict.fromkeys(value for value in labelled if aliases and any(alias in value.lower() for alias in aliases)))
            # An application may change tabs before URLs are listed. A title
            # bearing the platform's video suffix beats an adjacent other tab.
            related.sort(key=lambda value: (bool(re.search(r"_哔哩哔哩_bilibili|[-_]\s*youtube", value, re.I)),
                                             sum(term in value.lower() for term in parsed.terms)), reverse=True)
            video_titles = [value for value in related if re.search(r"_哔哩哔哩_bilibili|[-_]\s*youtube", value, re.I)]
            title_candidates = video_titles or related
            if len(title_candidates) == 1 and len(entries) == 1:
                title, strength = title_candidates[0], 2
            elif len(explicit) == 1 and len(entries) == 1:
                title, strength = explicit[0], 2
        title = re.sub(r"\s+和另外\s+\d+\s+个页面.*$", "", title).strip()
        entries[url] = (title, strength)
    ordered = sorted(entries.items(), key=lambda entry: (
        sum(term in entry[1][0].lower() for term in parsed.terms), entry[1][1]), reverse=True)
    pieces = []
    for url, (title, _) in ordered:
        prefix = "\n…\n" if pieces else ""
        # A partial URL is unusable. Reserve its complete bytes first, then
        # shorten only the optional title; never add unrelated nearby URLs.
        remaining = max_bytes - len(("\n…\n".join(pieces) + prefix).encode("utf-8"))
        url_bytes = len(url.encode("utf-8"))
        if url_bytes > remaining:
            continue
        title_budget = max(0, remaining - url_bytes - len("视频标题：\n".encode("utf-8")))
        short_title = title.encode("utf-8")[:min(600, title_budget)].decode("utf-8", errors="ignore")
        piece = ("视频标题：" + short_title + "\n" if short_title else "") + url
        pieces.append(piece)
        if len(pieces) >= 3:
            break
    if not pieces and explicit:
        return ("视频标题：" + explicit[0]).encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return "\n…\n".join(pieces)


def excerpt(row, parsed: RecallPlan, max_bytes: int) -> str:
    text = str(row.get("content", ""))
    # Bilibili's BV/av path identifies the video; long tracking parameters do
    # not aid recall. Other sites retain their queries (e.g. YouTube ?v=).
    text = re.sub(r"(https?://(?:www\.)?bilibili\.com/video/(?:BV[\w]+|av\d+)/?)\?[^\s<>\"\]\)]+",
                  r"\1", text, flags=re.I)
    if parsed.domain == "video":
        return _video_excerpt(text, parsed, max_bytes)
    lower = text.lower()
    # Retain actual matching evidence, even after a large OCR preamble. URLs
    # and window-title lines near a hit remain useful identifiers and citations.
    excerpt_terms = parsed.terms or (VIDEO_TERMS if parsed.domain == "video" else ())
    hits = sorted({lower.find(term) for term in excerpt_terms if lower.find(term) >= 0})
    if not hits:
        hits = [0]
    pieces = []
    title = str(row.get("title", ""))
    if title:
        pieces.append(title[:160])
    for pos in hits[:4]:
        left = max(text.rfind("\n", max(0, pos - 140), pos), text.rfind("。", max(0, pos - 140), pos))
        left = left + 1 if left >= 0 else max(0, pos - 100)
        right = text.find("\n", pos, pos + 400)
        right = right if right >= 0 else min(len(text), pos + 260)
        piece = text[left:right].strip()
        previous_start = text.rfind("\n", max(0, left - 220), max(0, left - 1))
        previous = text[previous_start + 1:max(0, left - 1)].strip() if left else ""
        if previous and len(previous) <= 200 and previous not in pieces:
            pieces.append(previous)
        if piece and piece not in pieces:
            pieces.append(piece)
    # A matched line's title can precede its URL (or vice versa).
    for match in re.finditer(r"https?://[^\s<>\"\]\)]+", text):
        if any(abs(match.start() - pos) <= 600 for pos in hits):
            url = match[0][:400]
            if not any(url in piece for piece in pieces):
                pieces.insert(1, url)
    result = "\n…\n".join(pieces)
    return result.encode("utf-8")[:max(0, max_bytes)].decode("utf-8", errors="ignore")
