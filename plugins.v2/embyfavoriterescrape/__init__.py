from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app import schemas
from app.chain.media import MediaChain
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType, MediaType

# 常见视频文件扩展名，用于区分 webhook 项是文件还是目录。
_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".rmvb", ".wmv", ".flv",
    ".mov", ".m4v", ".mpg", ".mpeg", ".iso",
}

# 指示“收藏”动作的 SaveReason 值，用于 Jellyfin 等媒体服务器的 UserDataSaved 事件。
_FAVORITE_SAVE_REASONS = {"togglefavorite", "unfavorite", "updateuserdata"}


class EmbyFavoriteRescrape(_PluginBase):
    """在 Emby 中移除收藏时自动重新刮削该媒体。

    插件监听媒体服务器的 Webhook 事件，当检测到某个视频的收藏状态被移除时，
    自动重新识别并刮削该媒体的元数据与图片。
    """

    # 插件名称
    plugin_name = "Emby移除收藏自动刮削"
    # 插件描述
    plugin_desc = "在Emby中点击移除收藏时，自动重新刮削该视频的元数据和图片。"
    # 插件图标
    plugin_icon = "scraper.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "sifangyu"
    # 作者主页
    author_url = "https://github.com/sifangyu"
    # 插件配置项ID前缀
    plugin_config_prefix = "embyfavoriterescrape_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 1

    # 是否启用
    _enabled = False
    # 监听渠道，多个用逗号分隔，默认 Emby
    _channels = "emby"
    # 触发事件名，多个用逗号分隔；命中任一事件且收藏状态为已移除才触发刮削
    _trigger_events = "UserDataSaved,userdata.saved,user_data_saved,item.favorite,itemFavorite,ItemFavorite,favorite"
    # 限定媒体库用户名，多个用逗号分隔，留空表示不限制
    _user_names = ""
    # 路径排除关键词，多个用逗号分隔，命中则不刮削
    _exclude_keywords = ""
    # 处理类型：MOV/电影、TV/电视剧，用逗号分隔
    _scrape_types = "MOV,TV"
    # 是否覆盖已有元数据
    _overwrite = True
    # 是否启用路径映射（依据重命名格式计算媒体目录，参考LibraryScraper）
    _enable_path_mapping = True
    # 刮削成功后是否发送通知
    _notify = False

    def init_plugin(self, config: dict = None):
        """读取配置并建立本次运行所需状态，允许重复调用。"""
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._channels = str(config.get("channels") or "emby").strip() or "emby"
        self._trigger_events = (
            str(config.get("trigger_events") or self._trigger_events).strip() or self._trigger_events
        )
        self._user_names = config.get("user_names") or ""
        self._exclude_keywords = config.get("exclude_keywords") or ""
        self._scrape_types = str(config.get("scrape_types") or "MOV,TV").strip() or "MOV,TV"
        self._overwrite = bool(config.get("overwrite", True))
        self._enable_path_mapping = bool(config.get("enable_path_mapping", True))
        self._notify = bool(config.get("notify"))

    def get_state(self) -> bool:
        """返回插件当前是否启用。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """当前插件不注册远程命令。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """当前插件不注册后端 API。"""
        return []

    @eventmanager.register(EventType.WebhookMessage)
    def on_webhook_message(self, event: Event):
        """收到媒体服务器 Webhook 时，检测是否移除了收藏并触发重新刮削。"""
        if not self._enabled:
            return
        event_info = event.event_data
        if not isinstance(event_info, WebhookEventInfo):
            return
        if not self._is_matched(event_info):
            return
        if not self._is_favorite_removed(event_info):
            return
        logger.info(
            f"【Emby移除收藏自动刮削】检测到移除收藏：{getattr(event_info, 'item_name', '') or ''} "
            f"({getattr(event_info, 'item_path', '') or ''})"
        )
        # 刮削耗时较长，放到后台线程执行，避免阻塞 Webhook 响应。
        threading.Thread(
            target=self._dispatch_scrape,
            args=(event_info,),
            name="EmbyFavoriteRescrape.Scrape",
            daemon=True,
        ).start()

    # ---------- 事件匹配 ----------

    def _is_matched(self, event_info: WebhookEventInfo) -> bool:
        """判断该 Webhook 是否命中渠道、用户、排除关键词与类型过滤。"""
        # 渠道过滤
        channel = (getattr(event_info, "channel", "") or "").strip().lower()
        channels = {ch.strip().lower() for ch in self._channels.split(",") if ch.strip()}
        if channels and channel not in channels:
            return False
        # 用户名过滤
        user_name = (getattr(event_info, "user_name", "") or "").strip()
        users = {u.strip() for u in self._user_names.split(",") if u.strip()}
        if users and user_name not in users:
            return False
        # 排除关键词
        item_path = getattr(event_info, "item_path", "") or ""
        if self._exclude_keywords and any(
            kw.strip() and kw.strip() in item_path
            for kw in self._exclude_keywords.split(",")
            if kw.strip()
        ):
            logger.debug(f"【Emby移除收藏自动刮削】命中排除关键词，跳过：{item_path}")
            return False
        # 类型过滤
        item_type = (getattr(event_info, "item_type", "") or "").upper()
        scrape_types = {t.strip().upper() for t in self._scrape_types.split(",") if t.strip()}
        if item_type and scrape_types and item_type not in scrape_types:
            return False
        return True

    # ---------- 收藏移除检测 ----------

    def _is_favorite_removed(self, event_info: WebhookEventInfo) -> bool:
        """判断该 Webhook 是否是“收藏被移除”事件。"""
        favorite = self._extract_favorite_state(event_info)
        if favorite is not False:
            return False
        return self._is_favorite_change_context(event_info)

    def _extract_favorite_state(self, event_info: WebhookEventInfo) -> Optional[bool]:
        """从事件对象或原始报文中解析收藏状态，未知返回 None。"""
        # 直接字段（Jellyfin 等解析器会填充）
        favorite = getattr(event_info, "item_favorite", None)
        if isinstance(favorite, bool):
            return favorite
        # 原始报文兜底（部分解析器会填充 json_object）
        payload = getattr(event_info, "json_object", None) or {}
        if isinstance(payload, dict):
            return self._find_favorite_in_payload(payload)
        return None

    def _is_favorite_change_context(self, event_info: WebhookEventInfo) -> bool:
        """判断该 Webhook 是否属于收藏相关事件上下文。"""
        event_name = (getattr(event_info, "event", None) or "").strip()
        if event_name:
            triggers = {t.strip().lower() for t in self._trigger_events.split(",") if t.strip()}
            if event_name.lower() in triggers:
                return True
        save_reason = (getattr(event_info, "save_reason", None) or "").strip().lower()
        if save_reason in _FAVORITE_SAVE_REASONS:
            return True
        return False

    @classmethod
    def _find_favorite_in_payload(cls, payload: Any, depth: int = 3) -> Optional[bool]:
        """在 Webhook 原始报文中递归查找收藏状态布尔值。"""
        if not isinstance(payload, dict) or depth <= 0:
            return None
        for key, value in payload.items():
            if key.lower() in ("isfavorite", "favorite") and value is not None:
                parsed = cls._to_bool(value)
                if parsed is not None:
                    return parsed
        for value in payload.values():
            if isinstance(value, dict):
                found = cls._find_favorite_in_payload(value, depth=depth - 1)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _to_bool(value: Any) -> Optional[bool]:
        """把常见的布尔表示解析为布尔值，无法确定时返回 None。"""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return None if value not in (0, 1) else bool(value)
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "y"):
                return True
            if low in ("false", "0", "no", "n"):
                return False
        return None

    # ---------- 刮削 ----------

    def _dispatch_scrape(self, event_info: WebhookEventInfo):
        """后台线程入口，解析元数据并调用刮削链。"""
        try:
            self._rescrape(event_info)
        except Exception as err:
            logger.error(f"【Emby移除收藏自动刮削】重新刮削失败：{err}")
            self._send_notify(f"移除收藏自动刮削失败：{err}")

    def _rescrape(self, event_info: WebhookEventInfo):
        """识别媒体信息并对该媒体执行重新刮削。"""
        item_path = (getattr(event_info, "item_path", "") or "").strip()
        mtype = self._media_type_for_item_type(getattr(event_info, "item_type", None))
        if not mtype or not item_path:
            logger.warn("【Emby移除收藏自动刮削】缺少媒体路径或类型，跳过刮削")
            return

        # 标题用于媒体识别
        title = (getattr(event_info, "item_name", "") or "").strip() or Path(item_path).stem
        item_name = title.split(" S")[0] if mtype == MediaType.TV else title
        meta = MetaInfo(item_name)
        meta.type = mtype

        # 依据重命名格式把 webhook 路径映射为真正需要刮削的媒体目录或文件。
        target_path, target_type = self._resolve_scrape_target(item_path, mtype)
        is_file = target_type == "file"
        fileitem = schemas.FileItem(
            storage="local",
            type=target_type,
            path=target_path,
            name=Path(target_path).name,
            basename=Path(target_path).stem,
            extension=Path(target_path).suffix[1:] if is_file else None,
            modify_time=self._modify_time(target_path),
        )

        # 识别媒体信息（优先使用 webhook 提供的 tmdbid）
        tmdb_id = getattr(event_info, "tmdb_id", None) or None
        mediainfo = self.chain.recognize_media(meta=meta, mtype=mtype, tmdbid=tmdb_id, cache=True)
        if not mediainfo:
            logger.warn(f"【Emby移除收藏自动刮削】未识别到媒体信息：{title}")
            self._send_notify(f"移除收藏自动刮削失败：未识别到媒体信息 {title}")
            return

        # 补充图片
        try:
            self.chain.obtain_images(mediainfo)
        except Exception as err:
            logger.warn(f"【Emby移除收藏自动刮削】获取图片失败：{err}")

        logger.info(f"【Emby移除收藏自动刮削】开始重新刮削：{item_path} ...")
        MediaChain().scrape_metadata(
            fileitem=fileitem,
            mediainfo=mediainfo,
            overwrite=self._overwrite,
        )
        logger.info(f"【Emby移除收藏自动刮削】刮削完成：{item_path}")
        self._send_notify(f"移除收藏自动刮削完成：{item_path}")

    def _send_notify(self, text: str):
        """按配置发送通知。"""
        if self._notify:
            self.post_message(title="Emby移除收藏自动刮削", text=text)

    # ---------- 工具方法 ----------

    @staticmethod
    def _media_type_for_item_type(item_type: Optional[str]) -> Optional[MediaType]:
        """把 webhook 的 item_type 映射为媒体类型。"""
        item_type = (item_type or "").upper()
        if item_type == "MOV":
            return MediaType.MOVIE
        if item_type == "TV":
            return MediaType.TV
        return None

    @staticmethod
    def _is_media_file(path: str) -> bool:
        """根据扩展名判断 webhook 路径是媒体文件还是目录。"""
        return Path(path).suffix.lower() in _VIDEO_EXTENSIONS

    @staticmethod
    def _rename_format_level(mtype: MediaType) -> int:
        """依据重命名格式计算其中的目录层级数（参考 LibraryScraper 的路径映射）。"""
        rename_format = settings.TV_RENAME_FORMAT if mtype == MediaType.TV else settings.MOVIE_RENAME_FORMAT
        return len(rename_format.strip("/").split("/")) - 1

    def _resolve_scrape_target(self, item_path: str, mtype: MediaType) -> Tuple[str, str]:
        """把 webhook 路径映射为真正需要刮削的媒体目录或文件。

        参考 LibraryScraper 的 ``__get_scrape_item``：根据电影/电视剧的重命名格式，
        从文件往上取对应目录层级作为媒体目录；扁平或无目录层级时退回单文件刮削。
        返回值: (刮削路径, 目标类型)，目标类型为 ``dir`` 或 ``file``。
        """
        # 媒体服务器返回目录（无视频扩展名）时，直接按目录刮削。
        if not self._is_media_file(item_path):
            return item_path, "dir"
        # 未启用路径映射时，直接使用 webhook 返回的媒体路径（单文件刮削）。
        if not self._enable_path_mapping:
            return item_path, "file"
        rename_format_level = self._rename_format_level(mtype)
        if rename_format_level >= 1:
            parents = Path(item_path).parents
            if len(parents) >= rename_format_level:
                # 依据重命名格式的目录层级，从文件往上取媒体目录（分类层保留在上方）。
                return str(parents[rename_format_level - 1]), "dir"
        # 扁平或自定义重命名格式无目录层级时，退回到单文件刮削。
        return item_path, "file"

    @staticmethod
    def _modify_time(path: str) -> Optional[float]:
        """返回文件修改时间，取不到时返回 None。"""
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return None

    # ---------- 页面 ----------

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回配置页面和默认配置模型。"""
        scrape_types = [
            {"title": "电影 + 电视剧", "value": "MOV,TV"},
            {"title": "仅电影", "value": "MOV"},
            {"title": "仅电视剧", "value": "TV"},
        ]
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用插件"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "overwrite", "label": "覆盖已有元数据"},
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "channels",
                                            "label": "监听渠道",
                                            "placeholder": "多个用逗号分隔，例如 emby,jellyfin",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "scrape_types",
                                            "label": "处理类型",
                                            "items": scrape_types,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "trigger_events",
                                            "label": "触发事件",
                                            "placeholder": "多个用逗号分隔，例如 UserDataSaved,item.favorite",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "user_names",
                                            "label": "限定用户名",
                                            "placeholder": "留空不限制，多个用逗号分隔",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "exclude_keywords",
                                            "label": "排除路径关键词",
                                            "placeholder": "多个用逗号分隔",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "notify", "label": "发送通知"},
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enable_path_mapping",
                                            "label": "启用路径映射",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "检测规则：轮询 Webhook 报文，当某个视频的收藏状态被解析为已移除，"
                                                    "且事件命中上方“触发事件”或 SaveReason=ToggleFavorite 时，自动重新刮削该媒体。"
                                                    "需要在媒体服务器中开启对应 Webhook；若 Emby 的收藏事件名与本机不一致，"
                                                    "请按收到的 Event 字段调整“触发事件”。",
                                        },
                                    },
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "warning",
                                            "variant": "tonal",
                                            "text": "路径映射：开启时按 MoviePilot 设置的电影/电视剧重命名格式计算真正需刮削的媒体目录"
                                                    "（参考 LibraryScraper）；关闭时直接使用媒体服务器返回的原始路径（按单文件刮削）。"
                                                    "若你的媒体库目录结构与重命名格式不一致，请关闭本开关。",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "overwrite": True,
            "enable_path_mapping": True,
            "channels": "emby",
            "trigger_events": self._trigger_events,
            "user_names": "",
            "exclude_keywords": "",
            "scrape_types": "MOV,TV",
            "notify": False,
        }

    def get_page(self) -> List[dict]:
        """返回插件详情页。"""
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": f"插件状态：{'已启用' if self._enabled else '未启用'}；"
                            f"监听渠道：{self._channels}；触发事件：{self._trigger_events}；"
                            f"路径映射：{'启用' if self._enable_path_mapping else '关闭'}。",
                },
            }
        ]

    def stop_service(self):
        """释放插件创建的后台资源。"""
        self._enabled = False
