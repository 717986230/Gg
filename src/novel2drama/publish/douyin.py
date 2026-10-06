"""抖音开放平台发布。

前置条件（需在 https://developer.open-douyin.com 完成）：
1. 创建移动/网站应用，申请"视频发布"能力（scope: video.create、video.data 等）；
2. 让目标抖音账号走 OAuth 授权，拿到 access_token / refresh_token / open_id；
3. 把凭证写进 .env（见 .env.example）。

流程：上传视频（小文件直传，大文件分片）→ 拿 video_id → 创建视频（标题 + 话题）。
接口路径集中在 ENDPOINTS 中；开放平台若调整路径，只改这里即可。
默认 dry_run=True 只生成发布清单，不会真正发到账号上。
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import requests

BASE = "https://open.douyin.com"
ENDPOINTS = {
    "refresh_token": "/oauth/refresh_token/",
    "upload_video": "/api/douyin/v1/video/upload_video/",
    "init_part": "/api/douyin/v1/video/init_video_part_upload/",
    "upload_part": "/api/douyin/v1/video/upload_video_part/",
    "complete_part": "/api/douyin/v1/video/complete_video_part_upload/",
    "create_video": "/api/douyin/v1/video/create_video/",
}
SINGLE_UPLOAD_LIMIT = 50 * 1024 * 1024
PART_SIZE = 20 * 1024 * 1024
MAX_TEXT = 1000


class DouyinError(RuntimeError):
    pass


@dataclass
class DouyinCredentials:
    client_key: str
    client_secret: str
    access_token: str
    open_id: str
    refresh_token: str = ""

    @classmethod
    def from_env(cls) -> DouyinCredentials:
        missing = [k for k in ("DOUYIN_CLIENT_KEY", "DOUYIN_ACCESS_TOKEN", "DOUYIN_OPEN_ID") if not os.environ.get(k)]
        if missing:
            raise DouyinError(f"缺少抖音凭证环境变量: {', '.join(missing)}")
        return cls(
            client_key=os.environ["DOUYIN_CLIENT_KEY"],
            client_secret=os.environ.get("DOUYIN_CLIENT_SECRET", ""),
            access_token=os.environ["DOUYIN_ACCESS_TOKEN"],
            open_id=os.environ["DOUYIN_OPEN_ID"],
            refresh_token=os.environ.get("DOUYIN_REFRESH_TOKEN", ""),
        )


def build_caption(title: str, hashtags: list[str], series: str = "") -> str:
    tags = " ".join(f"#{t.lstrip('#')}" for t in hashtags if t.strip())
    parts = [title]
    if series:
        parts.append(f"《{series}》动漫短剧")
    parts.append(tags)
    return " ".join(p for p in parts if p)[:MAX_TEXT]


class DouyinPublisher:
    def __init__(self, creds: DouyinCredentials, session: requests.Session | None = None, timeout: int = 300):
        self.creds = creds
        self.http = session or requests.Session()
        self.timeout = timeout

    # ------------------------------------------------------------- 基础
    def _headers(self) -> dict[str, str]:
        return {"access-token": self.creds.access_token}

    def _check(self, resp: requests.Response) -> dict:
        try:
            body = resp.json()
        except ValueError as e:
            raise DouyinError(f"HTTP {resp.status_code}: {resp.text[:300]}") from e
        data = body.get("data", body)
        code = data.get("error_code", body.get("err_no", 0))
        if resp.status_code >= 400 or code:
            raise DouyinError(f"抖音接口错误 {code}: {data.get('description') or body.get('err_tips') or body}")
        return data

    def _url(self, name: str) -> str:
        return f"{BASE}{ENDPOINTS[name]}"

    def refresh(self) -> None:
        resp = self.http.post(self._url("refresh_token"), data={
            "client_key": self.creds.client_key,
            "refresh_token": self.creds.refresh_token,
            "grant_type": "refresh_token",
        }, timeout=self.timeout)
        data = self._check(resp)
        self.creds.access_token = data["access_token"]
        self.creds.refresh_token = data.get("refresh_token", self.creds.refresh_token)

    # ------------------------------------------------------------- 上传
    def upload(self, video: Path) -> str:
        size = video.stat().st_size
        if size <= SINGLE_UPLOAD_LIMIT:
            with video.open("rb") as f:
                resp = self.http.post(
                    self._url("upload_video"), params={"open_id": self.creds.open_id}, headers=self._headers(),
                    files={"video": (video.name, f, "video/mp4")}, timeout=self.timeout,
                )
            return self._check(resp)["video"]["video_id"]
        return self._upload_parts(video, size)

    def _upload_parts(self, video: Path, size: int) -> str:
        params = {"open_id": self.creds.open_id}
        init = self._check(self.http.post(self._url("init_part"), params=params, headers=self._headers(),
                                          timeout=self.timeout))
        upload_id = init["upload_id"]
        parts = math.ceil(size / PART_SIZE)
        with video.open("rb") as f:
            for n in range(1, parts + 1):
                chunk = f.read(PART_SIZE)
                self._check(self.http.post(
                    self._url("upload_part"), params={**params, "upload_id": upload_id, "part_number": n},
                    headers=self._headers(), files={"video": (video.name, chunk, "video/mp4")}, timeout=self.timeout,
                ))
        done = self._check(self.http.post(self._url("complete_part"), params={**params, "upload_id": upload_id},
                                          headers=self._headers(), timeout=self.timeout))
        return done["video"]["video_id"]

    # ------------------------------------------------------------- 发布
    def create(self, video_id: str, text: str, cover_tsp: float | None = None) -> str:
        body: dict = {"video_id": video_id, "text": text}
        if cover_tsp is not None:
            body["cover_tsp"] = cover_tsp
        resp = self.http.post(self._url("create_video"), params={"open_id": self.creds.open_id},
                              headers={**self._headers(), "Content-Type": "application/json"},
                              data=json.dumps(body, ensure_ascii=False).encode(), timeout=self.timeout)
        return self._check(resp).get("item_id", "")

    def publish(self, video: Path, text: str) -> dict:
        video_id = self.upload(video)
        item_id = self.create(video_id, text, cover_tsp=1.0)
        return {"video_id": video_id, "item_id": item_id}


def publish_episode(video: Path, title: str, hashtags: list[str], series: str, dry_run: bool = True) -> dict:
    text = build_caption(title, hashtags, series)
    manifest = {"video": str(video), "text": text, "dry_run": dry_run}
    if dry_run:
        return manifest
    publisher = DouyinPublisher(DouyinCredentials.from_env())
    manifest.update(publisher.publish(video, text))
    return manifest
