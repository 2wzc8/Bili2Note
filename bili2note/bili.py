"""B 站 API 客户端：视频信息、字幕列表、字幕下载（Wbi 签名自行实现，不依赖第三方库）。"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Wbi 混淆表（来源：bilibili-API-collect）
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_WBI_CACHE_TTL = 3600  # 密钥每日轮换，缓存 1 小时足够


@dataclass
class Subtitle:
    lan: str
    lan_doc: str
    url: str  # https:// 开头的完整地址
    ai: bool

    def __str__(self) -> str:
        tag = "AI生成" if self.ai else "人工CC"
        return f"[{self.lan}] {self.lan_doc}（{tag}）"


class BiliError(RuntimeError):
    """B 站接口调用失败。"""


def parse_video_id(text: str) -> tuple[str, int]:
    """从 BV 号或 URL 中解析 (bvid, 分P页码)。"""
    m = re.search(r"BV[0-9A-Za-z]{10}", text, re.IGNORECASE)
    if not m:
        raise ValueError(f"无法从 {text!r} 中解析出 BV 号")
    bvid = m.group()
    p = re.search(r"[?&]p=(\d+)", text)
    return bvid, int(p.group(1)) if p else 1


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class BiliClient:
    def __init__(self, cookie: str = "") -> None:
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self._http = httpx.Client(headers=headers, timeout=30, follow_redirects=True)
        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_time: float = 0.0
        self._buvid_ready = bool(cookie)
        if cookie:
            self._http.headers["Cookie"] = cookie

    def _ensure_buvid(self) -> None:
        """无 Cookie 时自动获取 buvid3/buvid4 设备指纹，否则接口会返回 412 风控。"""
        if self._buvid_ready:
            return
        try:
            data = self._get("https://api.bilibili.com/x/frontend/finger/spi")
            b3, b4 = data.get("b_3", ""), data.get("b_4", "")
            if b3:
                self._http.headers["Cookie"] = f"buvid3={b3}; buvid4={b4}"
        except Exception:
            pass  # 获取失败不致命，继续尝试请求
        self._buvid_ready = True

    # ---- 内部 ----

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict:
        resp = self._http.get(url, params=params)
        if resp.status_code != 200:
            raise BiliError(f"接口返回 HTTP {resp.status_code}：{url}")
        data = resp.json()
        code = data.get("code", -1)
        if code != 0:
            raise BiliError(f"接口返回错误 code={code}：{data.get('message', '未知错误')}（{url}）")
        return data["data"]

    def _get_wbi_keys(self) -> tuple[str, str]:
        if self._wbi_keys and time.time() - self._wbi_time < _WBI_CACHE_TTL:
            return self._wbi_keys
        # nav 接口未登录时返回 code=-101，但 wbi_img 字段仍正常返回，故不校验 code
        resp = self._http.get("https://api.bilibili.com/x/web-interface/nav")
        data = (resp.json().get("data") or {}).get("wbi_img", {})
        img_url, sub_url = data.get("img_url", ""), data.get("sub_url", "")
        if not img_url or not sub_url:
            raise BiliError("获取 Wbi 密钥失败（nav 接口无 wbi_img）")
        self._wbi_keys = (img_url.rsplit("/", 1)[-1].split(".")[0],
                          sub_url.rsplit("/", 1)[-1].split(".")[0])
        self._wbi_time = time.time()
        return self._wbi_keys

    def _wbi_sign(self, params: dict[str, Any]) -> dict[str, Any]:
        img_key, sub_key = self._get_wbi_keys()
        mixin_key = "".join((img_key + sub_key)[i] for i in _MIXIN_TAB)[:32]
        signed = {k: "".join(c for c in str(v) if c not in "!'()*")
                  for k, v in params.items()}
        signed["wts"] = str(int(time.time()))
        query = urllib.parse.urlencode(dict(sorted(signed.items())))
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return signed

    # ---- 对外 ----

    def video_info(self, bvid: str) -> dict:
        self._ensure_buvid()
        return self._get("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid})

    def subtitles(self, bvid: str, page: int = 1, info: dict | None = None) -> list[Subtitle]:
        """获取指定分P的可用字幕列表。注意：未登录时 B 站不返回字幕（含人工CC）。"""
        if info is None:
            info = self.video_info(bvid)
        pages = info.get("pages", [])
        if page < 1 or page > len(pages):
            raise BiliError(f"分P页码 {page} 超出范围（共 {len(pages)} P）")
        cid = pages[page - 1]["cid"]

        params = self._wbi_sign({"bvid": bvid, "cid": cid, "aid": info["aid"]})
        data = self._get("https://api.bilibili.com/x/player/wbi/v2", params)
        raw_list = (data.get("subtitle") or {}).get("subtitles") or []
        subs = []
        for item in raw_list:
            url: str = item.get("subtitle_url", "")
            if url.startswith("//"):
                url = "https:" + url
            if not url:
                continue
            subs.append(Subtitle(
                lan=item.get("lan", ""),
                lan_doc=item.get("lan_doc", item.get("lan", "")),
                url=url,
                ai=bool(item.get("ai_type")),
            ))
        return subs

    def subtitle_content(self, sub: Subtitle) -> list[dict]:
        """下载字幕 JSON，返回 [{from, to, content}]（秒为单位）。"""
        resp = self._http.get(sub.url)
        if resp.status_code != 200:
            raise BiliError(f"字幕下载失败 HTTP {resp.status_code}")
        body = resp.json().get("body", [])
        if not body:
            raise BiliError("字幕内容为空")
        return body

    def audio_stream(self, bvid: str, page: int = 1, info: dict | None = None) -> tuple[str, float]:
        """获取分P的 Dash 音频流：返回 (下载 URL, 该P时长秒)。选码率最低的音轨以省流量。"""
        if info is None:
            info = self.video_info(bvid)
        pages = info.get("pages", [])
        if page < 1 or page > len(pages):
            raise BiliError(f"分P页码 {page} 超出范围（共 {len(pages)} P）")
        cid = pages[page - 1]["cid"]
        duration = float(pages[page - 1].get("duration") or info.get("duration") or 0)

        params = self._wbi_sign({"bvid": bvid, "cid": cid, "aid": info["aid"], "fnval": 16, "fnver": 0, "qn": 0})
        data = self._get("https://api.bilibili.com/x/player/wbi/playurl", params)
        audios = (data.get("dash") or {}).get("audio") or []
        # 部分视频存在独立音轨分组（flac/dolby），主音轨在 dash.audio；为空再兜底尝试 dash.media
        if not audios:
            audios = (data.get("dash") or {}).get("media") or []
        if not audios:
            raise BiliError("未获取到 Dash 音频流（视频可能仅支持 Flash 播放或已被下架）")
        best = min(audios, key=lambda a: a.get("bandwidth", 0))
        url = best.get("baseUrl") or best.get("base_url") or ""
        if not url:
            raise BiliError("Dash 音频流缺少下载地址")
        return url, duration
