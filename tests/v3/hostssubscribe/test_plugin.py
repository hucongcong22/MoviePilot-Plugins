"""定时订阅Hosts 插件核心解析逻辑单测。

运行环境需要 MoviePilot V3 后端（tests/README.md），并通过生产命名空间
``app.plugins.hostssubscribe`` 导入插件源码。
"""

from __future__ import annotations

from app.plugins.hostssubscribe import HostsSubscribe


def test_split_lines_from_multiline_string() -> None:
    """多行文本按行拆分并去掉首尾空白与空行。"""
    text = "1.2.3.4 example.com\n  # comment  \n\n5.6.7.8 example.org "
    lines = HostsSubscribe._split_lines(text)
    assert lines == ["1.2.3.4 example.com", "# comment", "5.6.7.8 example.org"]


def test_split_lines_accepts_list() -> None:
    """列表输入保持非空行。"""
    assert HostsSubscribe._split_lines(["a", "", " b "]) == ["a", "b"]


def test_split_lines_empty() -> None:
    """空值、None 返回空列表。"""
    assert HostsSubscribe._split_lines(None) == []
    assert HostsSubscribe._split_lines("") == []
    assert HostsSubscribe._split_lines("  \n ") == []


def test_form_has_cron_select_options() -> None:
    """配置表单必须提供可选择的更新周期，而不是自由输入。"""
    form, _model = HostsSubscribe.get_form()
    raw = str(form)
    assert "VSelect" in raw
    assert '"model": "update_cron"' in raw
    assert '"0 * * * *"' in raw
    assert '"0 3 * * *"' in raw
    assert "VTextField" not in raw


def test_form_has_fetch_button_and_preview() -> None:
    """配置表单必须提供立即拉取按钮与拉取内容预览控件。"""
    form, model = HostsSubscribe.get_form()
    raw = str(form)
    assert "VBtn" in raw
    assert "立即拉取" in raw
    assert "fetch_result" in raw
    assert "拉取内容预览" in raw
    assert model.get("fetch_result") == ""


def _make_plugin(subscribe_urls: list[str] | None = None) -> HostsSubscribe:
    """构造不启动服务的插件实例，避免真实网络与系统hosts写入。"""
    plugin = object.__new__(HostsSubscribe)
    plugin._enabled = True
    plugin._subscribe_urls = subscribe_urls or []
    plugin._manual_hosts = []
    return plugin


def test_api_fetch_returns_merged_content(monkeypatch) -> None:
    """立即拉取接口返回所有订阅地址合并内容。"""
    plugin = _make_plugin(subscribe_urls=["https://a.example/hosts", "https://b.example/hosts"])
    monkeypatch.setattr(
        HostsSubscribe,
        "_fetch_url",
        staticmethod(lambda url: [f"1.2.3.4 {url}", "5.6.7.8 example.org"]),
    )

    response = plugin.api_fetch()

    assert response.success is True
    assert "https://a.example/hosts" in response.data
    assert "https://b.example/hosts" in response.data
    assert "1.2.3.4" in response.data
    assert "5.6.7.8 example.org" in response.data


def test_api_fetch_reports_errors(monkeypatch) -> None:
    """订阅地址全部拉取失败时，接口返回错误提示。"""
    plugin = _make_plugin(subscribe_urls=["https://a.example/hosts"])
    monkeypatch.setattr(
        HostsSubscribe,
        "_fetch_url",
        staticmethod(lambda url: (_ for _ in ()).throw(RuntimeError("network down"))),
    )

    response = plugin.api_fetch()

    assert response.success is True
    assert "拉取错误" in response.data
    assert "network down" in response.data


def test_api_fetch_without_urls() -> None:
    """未配置订阅地址时返回失败提示。"""
    plugin = _make_plugin(subscribe_urls=[])
    response = plugin.api_fetch()
    assert response.success is False


def test_parse_hosts_lines_ipv4() -> None:
    """合法的 IPv4 hosts 行解析为 ipv4 条目。"""
    errors: list[str] = []
    entries = HostsSubscribe._parse_hosts_lines(
        ["1.2.3.4 example.com www.example.com"],
        errors,
    )
    assert len(entries) == 1
    assert entries[0].entry_type == "ipv4"
    assert entries[0].address == "1.2.3.4"
    assert entries[0].names == ["example.com", "www.example.com"]
    assert errors == []


def test_parse_hosts_lines_ipv6() -> None:
    """合法的 IPv6 hosts 行解析为 ipv6 条目。"""
    errors: list[str] = []
    entries = HostsSubscribe._parse_hosts_lines(
        ["2001:db8::1 example.com"],
        errors,
    )
    assert len(entries) == 1
    assert entries[0].entry_type == "ipv6"
    assert entries[0].address == "2001:db8::1"
    assert entries[0].names == ["example.com"]
    assert errors == []


def test_parse_hosts_lines_comment() -> None:
    """注释行解析为 comment 条目。"""
    errors: list[str] = []
    entries = HostsSubscribe._parse_hosts_lines(["# 这是一行注释"], errors)
    assert len(entries) == 1
    assert entries[0].entry_type == "comment"
    assert entries[0].comment == "# 这是一行注释"
    assert errors == []


def test_parse_hosts_lines_invalid() -> None:
    """无效行（非法IP、缺少主机名）记录到错误列表，不产生条目。"""
    errors: list[str] = []
    entries = HostsSubscribe._parse_hosts_lines(
        ["999.1.2.3 bad.example.com", "onlyip.example.com"],
        errors,
    )
    assert entries == []
    assert len(errors) == 2
    assert "999.1.2.3 bad.example.com" in errors[0]
    assert "onlyip.example.com" in errors[1]


def test_parse_hosts_lines_mixed() -> None:
    """混合有效与无效行时只保留有效条目。"""
    errors: list[str] = []
    entries = HostsSubscribe._parse_hosts_lines(
        [
            "1.2.3.4 ok.example.com",
            "bad-line",
            "5.6.7.8 ok2.example.com",
        ],
        errors,
    )
    assert [entry.address for entry in entries] == ["1.2.3.4", "5.6.7.8"]
    assert errors == ["bad-line\n"]


def test_now_text_format() -> None:
    """最近更新时间符合 %Y-%m-%d %H:%M:%S 格式。"""
    text = HostsSubscribe._now_text()
    assert len(text) == 19
    assert text[4] == "-"
    assert text[7] == "-"
    assert text[10] == " "
    assert text[13] == ":"
    assert text[16] == ":"
