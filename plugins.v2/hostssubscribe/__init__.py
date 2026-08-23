from typing import Any, Dict, List, Tuple

import ipaddress
import requests
from apscheduler.triggers.cron import CronTrigger
from python_hosts import Hosts, HostsEntry

from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Response
from app.schemas.types import EventType
from app.utils.system import SystemUtils


class HostsSubscribe(_PluginBase):
    """订阅远程 hosts 规则，定时更新系统 hosts 文件。"""

    # 插件名称
    plugin_name = "定时订阅Hosts"
    # 插件描述
    plugin_desc = "订阅远程hosts规则，定时更新系统hosts文件，加速网络访问。"
    # 插件图标
    plugin_icon = "hosts.png"
    # 插件版本
    plugin_version = "0.1.0"
    # 插件作者
    plugin_author = "hucongcong22"
    # 作者主页
    author_url = "https://github.com/hucongcong22"
    # 插件配置项ID前缀
    plugin_config_prefix = "hostssubscribe_"
    # 加载顺序
    plugin_order = 10
    # 可使用的用户级别
    auth_level = 1

    # 插件写入系统 hosts 的分隔标识
    _MARKER = "# HostsSubscribePlugin"

    _enabled = False
    _subscribe_urls: List[str] = []
    _update_cron = "0 3 * * *"
    _manual_hosts: List[str] = []
    _last_update = ""

    def init_plugin(self, config: dict = None):
        """读取配置，启用时立即应用一次，未启用时恢复系统 hosts。"""
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._subscribe_urls = self._split_lines(config.get("subscribe_urls"))
        self._update_cron = str(config.get("update_cron") or "0 3 * * *").strip() or "0 3 * * *"
        self._manual_hosts = self._split_lines(config.get("manual_hosts"))
        if self._enabled:
            self.update_hosts()
        else:
            self._clear_system_hosts()

    def get_state(self) -> bool:
        """返回插件当前是否启用。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """注册立即更新命令。"""
        return [
            {
                "cmd": "/hosts_subscribe",
                "event": EventType.PluginAction,
                "desc": "立即更新订阅Hosts",
                "category": "插件命令",
                "data": {"action": "hosts_subscribe"},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def run_command(self, event: Event):
        """只处理属于当前插件的动作。"""
        event_data = event.event_data or {}
        if event_data.get("action") != "hosts_subscribe":
            return
        self.update_hosts()

    def get_api(self) -> List[Dict[str, Any]]:
        """注册立即拉取订阅内容接口。"""
        return [
            {
                "path": "/fetch",
                "endpoint": self.api_fetch,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "立即拉取订阅Hosts",
                "description": "拉取所有订阅地址内容并返回预览文本，不写入系统hosts",
                "response_model": Response,
            }
        ]

    def api_fetch(self) -> Response:
        """立即拉取所有订阅地址内容，返回合并后的预览文本。"""
        if not self._subscribe_urls:
            return Response(success=False, message="未配置订阅地址")
        lines: List[str] = []
        errors: List[str] = []
        for url in self._subscribe_urls:
            lines.append(f"# === {url} ===")
            try:
                lines.extend(self._fetch_url(url))
            except Exception as err:
                errors.append(f"{url}: {str(err)}")
                logger.error(f"[HostsSubscribe] 订阅地址获取失败：{url}：{err}")
        result = "\n".join(lines)
        if errors:
            result = f"{result}\n\n# 拉取错误：\n" + "\n".join(errors)
        return Response(success=True, data=result)

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回配置页面和默认配置模型。"""
        cron_items = [
            {"title": "每小时", "value": "0 * * * *"},
            {"title": "每2小时", "value": "0 */2 * * *"},
            {"title": "每6小时", "value": "0 */6 * * *"},
            {"title": "每12小时", "value": "0 */12 * * *"},
            {"title": "每天（凌晨3点）", "value": "0 3 * * *"},
            {"title": "每周（周一凌晨3点）", "value": "0 3 * * 1"},
            {"title": "每月（1号凌晨3点）", "value": "0 3 1 * *"},
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
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "subscribe_urls",
                                            "label": "订阅地址",
                                            "rows": 5,
                                            "placeholder": "每行一个订阅地址，例如：\nhttps://example.com/hosts.txt\nhttps://raw.githubusercontent.com/xxx/hosts/main/hosts",
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
                                        "component": "VSelect",
                                        "props": {
                                            "model": "update_cron",
                                            "label": "更新周期",
                                            "items": cron_items,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6, "class": "d-flex align-center"},
                                "content": [
                                    {
                                        "component": "VBtn",
                                        "props": {
                                            "color": "primary",
                                            "variant": "tonal",
                                            "onClick": "async () => { const res = await window.MoviePilotAPI.get('plugin/HostsSubscribe/fetch'); if (res && res.success) { model.fetch_result = res.data } }",
                                        },
                                        "text": "立即拉取",
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "fetch_result",
                                            "readonly": True,
                                            "label": "拉取内容预览",
                                            "rows": 8,
                                            "placeholder": "点击「立即拉取」后，订阅地址内容会显示在这里",
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "manual_hosts",
                                            "label": "手动hosts（可选）",
                                            "rows": 4,
                                            "placeholder": "每行一个配置，格式为：ip host1 host2 ...，与订阅内容合并写入",
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
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "err_hosts",
                                            "readonly": True,
                                            "label": "错误信息",
                                            "rows": 3,
                                            "placeholder": "订阅失败或无效的hosts配置会展示在此处",
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
                                            "text": "host格式为：ip host，中间有空格。"
                                                    "（注：容器运行则更新容器hosts，非宿主机！）"
                                                    "启用后立即更新一次，之后按配置的周期定时更新。",
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
            "subscribe_urls": "",
            "update_cron": "0 3 * * *",
            "fetch_result": "",
            "manual_hosts": "",
            "err_hosts": "",
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
                            f"最近更新时间：{self._last_update or '无'}",
                },
            }
        ]

    def get_service(self) -> List[dict]:
        """启用且配置了订阅地址时注册定时更新任务。"""
        if not self._enabled or not self._subscribe_urls:
            return []
        try:
            trigger = CronTrigger.from_crontab(self._update_cron)
        except Exception as err:
            logger.error(f"[HostsSubscribe] 更新周期配置无效，使用默认：{err}")
            trigger = CronTrigger.from_crontab("0 3 * * *")
        return [
            {
                "id": f"{self.__class__.__name__}.Update",
                "name": "定时更新订阅Hosts",
                "trigger": trigger,
                "func": self.update_hosts,
                "kwargs": {},
            }
        ]

    def stop_service(self):
        """定时服务由宿主调度器管理，无需额外清理。"""
        self._enabled = False

    # ---------- 业务逻辑 ----------

    def update_hosts(self) -> None:
        """拉取所有订阅地址并与手动hosts合并，写入系统hosts文件。"""
        if not self._enabled:
            return

        entries: List[HostsEntry] = []
        err_hosts: List[str] = []
        # 手动hosts
        entries.extend(self._parse_hosts_lines(self._manual_hosts, err_hosts))
        # 订阅地址
        success_urls = 0
        for url in self._subscribe_urls:
            try:
                lines = self._fetch_url(url)
                entries.extend(self._parse_hosts_lines(lines, err_hosts))
                success_urls += 1
            except Exception as err:
                err_hosts.append(f"{url}: {str(err)}")
                logger.error(f"[HostsSubscribe] 订阅地址获取失败：{url}：{err}")

        # 配置了订阅地址但全部失败时，保留现有hosts，避免网络故障清空hosts
        if self._subscribe_urls and success_urls == 0:
            logger.error("[HostsSubscribe] 所有订阅地址获取失败，保留现有hosts")
            self.post_message(
                title="定时订阅Hosts",
                text=f"所有订阅地址获取失败，保留现有hosts：\n{chr(10).join(err_hosts)}",
            )
            self._save_result(err_hosts)
            return

        ok, message = self._write_system_hosts(entries)
        if ok:
            logger.info("[HostsSubscribe] 更新系统hosts文件成功")
        else:
            err_hosts.append(message)
            logger.error(f"[HostsSubscribe] 更新系统hosts文件失败：{message}")
            self.post_message(
                title="定时订阅Hosts",
                text=f"更新系统hosts文件失败：{message}",
            )
        self._save_result(err_hosts)

    def _save_result(self, err_hosts: List[str]) -> None:
        """回写错误信息与最近更新时间。"""
        self._last_update = self._now_text()
        self.update_config({
            "enabled": self._enabled,
            "subscribe_urls": "\n".join(self._subscribe_urls),
            "update_cron": self._update_cron,
            "manual_hosts": "\n".join(self._manual_hosts),
            "err_hosts": "".join(err_hosts),
            "last_update": self._last_update,
        })

    def _clear_system_hosts(self) -> None:
        """清除插件写入的系统hosts，恢复原状。"""
        try:
            self._write_system_hosts([])
            logger.info("[HostsSubscribe] 系统hosts文件已恢复")
        except Exception as err:
            logger.error(f"[HostsSubscribe] 恢复系统hosts文件失败：{str(err) or '请检查权限'}")
            self.post_message(
                title="定时订阅Hosts",
                text=f"恢复系统hosts文件失败：{str(err) or '请检查权限'}",
            )

    def _write_system_hosts(self, entries: List[HostsEntry]) -> Tuple[bool, str]:
        """过滤插件旧条目后写入新的hosts，返回是否成功及错误信息。"""
        system_hosts = self._read_system_hosts()
        # 过滤掉插件添加的hosts
        origin_entries = []
        for entry in system_hosts.entries:
            if entry.entry_type == "comment" and entry.comment == self._MARKER:
                break
            origin_entries.append(entry)
        system_hosts.entries = origin_entries
        try:
            if entries:
                system_hosts.add([HostsEntry(entry_type="comment", comment=self._MARKER)])
                system_hosts.add(entries)
            system_hosts.write()
            return True, ""
        except Exception as err:
            return False, str(err) or "请检查权限"

    # ---------- 工具方法 ----------

    @staticmethod
    def _read_system_hosts() -> Hosts:
        """读取系统hosts对象。"""
        if SystemUtils.is_windows():
            hosts_path = r"c:\windows\system32\drivers\etc\hosts"
        else:
            hosts_path = "/etc/hosts"
        return Hosts(path=hosts_path)

    @staticmethod
    def _fetch_url(url: str) -> List[str]:
        """拉取订阅地址内容，返回非空行列表。"""
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return [line for line in resp.text.splitlines() if line.strip()]

    @staticmethod
    def _split_lines(value: Any) -> List[str]:
        """把配置中的多行文本拆成非空行列表。"""
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            return [str(line).strip() for line in value if str(line).strip()]
        return []

    @classmethod
    def _parse_hosts_lines(cls, lines: List[str], err_hosts: List[str]) -> List[HostsEntry]:
        """把hosts文本行解析为HostsEntry，无效行记录到err_hosts。"""
        entries: List[HostsEntry] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                entries.append(HostsEntry(entry_type="comment", comment=line))
                continue
            parts = line.split()
            if len(parts) < 2:
                err_hosts.append(line + "\n")
                continue
            address = parts[0]
            try:
                ip = ipaddress.ip_address(address)
                entry_type = "ipv4" if ip.version == 4 else "ipv6"
            except ValueError:
                err_hosts.append(line + "\n")
                continue
            entries.append(HostsEntry(entry_type=entry_type, address=address, names=parts[1:]))
        return entries

    @staticmethod
    def _now_text() -> str:
        """返回当前时间的文本表示。"""
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
