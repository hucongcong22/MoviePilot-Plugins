"""Emby移除收藏自动刮削 插件核心检测逻辑单测。

运行环境需要 MoviePilot V3 后端（tests/README.md），并通过生产命名空间
``app.plugins.embyfavoriterescrape`` 导入插件源码。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.plugins.embyfavoriterescrape import EmbyFavoriteRescrape
from app.schemas.types import MediaType
from app.sdk.config import settings


def _make_plugin(**overrides) -> EmbyFavoriteRescrape:
    """构造不启动服务、不访问链路的插件实例，只测纯逻辑方法。"""
    plugin = object.__new__(EmbyFavoriteRescrape)
    plugin._enabled = True
    plugin._channels = "emby"
    plugin._trigger_events = (
        "UserDataSaved,userdata.saved,item.favorite,itemFavorite,ItemFavorite,favorite,item.rate"
    )
    plugin._user_names = ""
    plugin._exclude_keywords = ""
    plugin._scrape_types = "MOV,TV"
    plugin._path_mapping = ""
    for key, value in overrides.items():
        setattr(plugin, f"_{key}", value)
    return plugin


# ---------- 媒体类型与路径识别 ----------


def test_media_type_for_item_type() -> None:
    """webhook 的 item_type 映射为媒体类型。"""
    assert EmbyFavoriteRescrape._media_type_for_item_type("MOV") == MediaType.MOVIE
    assert EmbyFavoriteRescrape._media_type_for_item_type("TV") == MediaType.TV
    assert EmbyFavoriteRescrape._media_type_for_item_type("AUD") is None
    assert EmbyFavoriteRescrape._media_type_for_item_type("movie") is None
    assert EmbyFavoriteRescrape._media_type_for_item_type(None) is None
    assert EmbyFavoriteRescrape._media_type_for_item_type("") is None


def test_is_media_file() -> None:
    """根据扩展名判断媒体文件与目录。"""
    assert EmbyFavoriteRescrape._is_media_file(r"/data/movies/A (2020)/A (2020).mkv") is True
    assert EmbyFavoriteRescrape._is_media_file(r"/data/movies/A (2020)/A.mp4") is True
    assert EmbyFavoriteRescrape._is_media_file(r"/data/movies/A (2020)/A.BluRay.REMUX.iso") is True
    assert EmbyFavoriteRescrape._is_media_file(r"/data/movies/A (2020)/A - 2160p.strm") is True
    assert EmbyFavoriteRescrape._is_media_file(r"/data/movies/A (2020)") is False
    assert EmbyFavoriteRescrape._is_media_file("/data/some/path/without/extension") is False
    assert EmbyFavoriteRescrape._is_media_file("") is False


# ---------- 布尔解析 ----------


def test_to_bool() -> None:
    """常见布尔表示解析为布尔值，无法确定时返回 None。"""
    assert EmbyFavoriteRescrape._to_bool(True) is True
    assert EmbyFavoriteRescrape._to_bool(False) is False
    assert EmbyFavoriteRescrape._to_bool(1) is True
    assert EmbyFavoriteRescrape._to_bool(0) is False
    assert EmbyFavoriteRescrape._to_bool("true") is True
    assert EmbyFavoriteRescrape._to_bool("False") is False
    assert EmbyFavoriteRescrape._to_bool("1") is True
    assert EmbyFavoriteRescrape._to_bool("0") is False
    assert EmbyFavoriteRescrape._to_bool("maybe") is None
    assert EmbyFavoriteRescrape._to_bool(None) is None
    assert EmbyFavoriteRescrape._to_bool(5) is None


# ---------- 原始报文收藏状态查找 ----------


def test_find_favorite_in_payload_nested() -> None:
    """从嵌套报文（UserData.IsFavorite）中解析收藏状态。"""
    payload = {
        "Event": "UserDataSaved",
        "Item": {"Name": "A", "Type": "Movie"},
        "UserData": {"IsFavorite": False, "Played": False},
    }
    assert EmbyFavoriteRescrape._find_favorite_in_payload(payload) is False


def test_find_favorite_in_payload_true() -> None:
    """收藏状态为 True 时检出并返回 True。"""
    payload = {"Item": {"IsFavorite": True}}
    assert EmbyFavoriteRescrape._find_favorite_in_payload(payload) is True


def test_find_favorite_in_payload_missing() -> None:
    """报文不含收藏字段时返回 None。"""
    payload = {"Event": "playback.start", "Item": {"Name": "A"}}
    assert EmbyFavoriteRescrape._find_favorite_in_payload(payload) is None


def test_find_favorite_in_payload_not_dict() -> None:
    """非字典输入返回 None，避免递归报错。"""
    assert EmbyFavoriteRescrape._find_favorite_in_payload("n/a") is None
    assert EmbyFavoriteRescrape._find_favorite_in_payload(None) is None


# ---------- 收藏状态与上下文 ----------


def test_extract_favorite_state_from_field() -> None:
    """直接字段 item_favorite 优先返回。"""
    plugin = _make_plugin()
    info = SimpleNamespace(item_favorite=False, json_object={})
    assert plugin._extract_favorite_state(info) is False


def test_extract_favorite_state_from_payload() -> None:
    """字段缺失时回退到原始报文。"""
    plugin = _make_plugin()
    info = SimpleNamespace(
        item_favorite=None,
        json_object={"UserData": {"IsFavorite": True}},
    )
    assert plugin._extract_favorite_state(info) is True


def test_extract_favorite_state_unknown() -> None:
    """字段与报文都没有收藏信息时返回 None。"""
    plugin = _make_plugin()
    info = SimpleNamespace(item_favorite=None, json_object={"Event": "playback.start"})
    assert plugin._extract_favorite_state(info) is None


def test_is_favorite_change_context_by_event() -> None:
    """命中配置的触发事件名时判定为收藏上下文。"""
    plugin = _make_plugin()
    assert plugin._is_favorite_change_context(
        SimpleNamespace(event="UserDataSaved", save_reason=None)
    ) is True
    assert plugin._is_favorite_change_context(
        SimpleNamespace(event="item.favorite", save_reason=None)
    ) is True


def test_is_favorite_change_context_by_save_reason() -> None:
    """SaveReason=ToggleFavorite 也判定为收藏上下文。"""
    plugin = _make_plugin()
    assert plugin._is_favorite_change_context(
        SimpleNamespace(event="some.other", save_reason="ToggleFavorite")
    ) is True


def test_is_favorite_change_context_negative() -> None:
    """非收藏事件且无收藏 SaveReason 时判定为否。"""
    plugin = _make_plugin()
    assert plugin._is_favorite_change_context(
        SimpleNamespace(event="playback.start", save_reason=None)
    ) is False


# ---------- 移除收藏判定 ----------


def test_is_favorite_removed_positive() -> None:
    """收藏被移除且命中收藏上下文时触发。"""
    plugin = _make_plugin()
    info = SimpleNamespace(
        event="UserDataSaved",
        save_reason=None,
        item_favorite=False,
        json_object={"UserData": {"IsFavorite": False}},
        channel="emby",
        item_type="MOV",
        item_path="/data/A.mkv",
    )
    assert plugin._is_favorite_removed(info) is True


def test_is_favorite_removed_item_rate_event() -> None:
    """Emby 用 item.rate 作为用户数据变更事件，IsFavorite=False 时判定为移除收藏。"""
    plugin = _make_plugin()
    info = SimpleNamespace(
        event="item.rate",
        save_reason=None,
        item_favorite=None,
        json_object={"Event": "item.rate", "Item": {"UserData": {"IsFavorite": False}}},
        channel="emby",
        user_name="sifangyu",
        item_type="MOV",
        item_path="/115/动画电影/小黄人与大怪兽 (2026)/小黄人与大怪兽 (2026) - 2160p.strm",
    )
    assert plugin._is_favorite_removed(info) is True


def test_is_favorite_removed_false_when_not_removed() -> None:
    """收藏状态为 True（新增收藏）时不得触发。"""
    plugin = _make_plugin()
    info = SimpleNamespace(
        event="UserDataSaved",
        save_reason=None,
        item_favorite=True,
        json_object={"UserData": {"IsFavorite": True}},
    )
    assert plugin._is_favorite_removed(info) is False


def test_is_favorite_removed_false_when_unknown() -> None:
    """收藏状态未知时不触发。"""
    plugin = _make_plugin()
    info = SimpleNamespace(
        event="playback.start",
        save_reason=None,
        item_favorite=None,
        json_object={"Event": "playback.start"},
    )
    assert plugin._is_favorite_removed(info) is False


def test_is_favorite_removed_false_when_no_context() -> None:
    """收藏状态为已移除但事件上下文无关时不触发。"""
    plugin = _make_plugin()
    info = SimpleNamespace(
        event="playback.start",
        save_reason=None,
        item_favorite=False,
        json_object={"UserData": {"IsFavorite": False}},
    )
    assert plugin._is_favorite_removed(info) is False


# ---------- 事件匹配 ----------


def test_is_matched_channel_filter() -> None:
    """渠道过滤。"""
    assert _make_plugin()._is_matched(
        SimpleNamespace(channel="emby", user_name=None, item_path="/a", item_type="MOV")
    ) is True
    assert _make_plugin()._is_matched(
        SimpleNamespace(channel="plex", user_name=None, item_path="/a", item_type="MOV")
    ) is False
    assert _make_plugin(_channels="emby,jellyfin")._is_matched(
        SimpleNamespace(channel="jellyfin", user_name=None, item_path="/a", item_type="MOV")
    ) is True


def test_is_matched_user_and_type_filter() -> None:
    """用户名与类型过滤。"""
    plugin = _make_plugin(user_names="admin", scrape_types="MOV")
    assert plugin._is_matched(
        SimpleNamespace(channel="emby", user_name="admin", item_path="/a", item_type="MOV")
    ) is True
    assert plugin._is_matched(
        SimpleNamespace(channel="emby", user_name="admin", item_path="/a", item_type="TV")
    ) is False
    assert plugin._is_matched(
        SimpleNamespace(channel="emby", user_name="other", item_path="/a", item_type="MOV")
    ) is False


def test_is_matched_exclude_keyword() -> None:
    """排除关键词命中时跳过。"""
    plugin = _make_plugin(exclude_keywords="儿童,测试")
    assert plugin._is_matched(
        SimpleNamespace(channel="emby", user_name=None, item_path="/data/儿童/xxx.mkv", item_type="MOV")
    ) is False
    assert plugin._is_matched(
        SimpleNamespace(channel="emby", user_name=None, item_path="/data/movie/xxx.mkv", item_type="MOV")
    ) is True


# ---------- 刮削路径映射（参考 LibraryScraper 的重命名格式映射） ----------


def test_rename_format_level(monkeypatch) -> None:
    """依据重命名格式计算目录层级数。"""
    monkeypatch.setattr(settings, "MOVIE_RENAME_FORMAT", "{{title}}/{{title}} ({{year}})")
    assert EmbyFavoriteRescrape._rename_format_level(MediaType.MOVIE) == 1
    monkeypatch.setattr(settings, "TV_RENAME_FORMAT", "{{title}}/Season {{season}}/{{episode}}")
    assert EmbyFavoriteRescrape._rename_format_level(MediaType.TV) == 2
    monkeypatch.setattr(settings, "MOVIE_RENAME_FORMAT", "{{title}} ({{year}})")
    assert EmbyFavoriteRescrape._rename_format_level(MediaType.MOVIE) == 0


def test_resolve_scrape_target_movie_folder(monkeypatch) -> None:
    """含分类前缀的电影文件按重命名格式映射到媒体目录。"""
    monkeypatch.setattr(settings, "MOVIE_RENAME_FORMAT", "{{title}}/{{title}} ({{year}})")
    plugin = _make_plugin()
    target, target_type = plugin._resolve_scrape_target(
        r"/movies/动画电影/蜘蛛侠 (2023)/蜘蛛侠 (2023).mkv", MediaType.MOVIE
    )
    assert target_type == "dir"
    assert target == r"/movies/动画电影/蜘蛛侠 (2023)"


def test_resolve_scrape_target_flat_movie_file(monkeypatch) -> None:
    """扁平重命名格式（无目录层级）退回单文件刮削。"""
    monkeypatch.setattr(settings, "MOVIE_RENAME_FORMAT", "{{title}} ({{year}})")
    plugin = _make_plugin()
    target, target_type = plugin._resolve_scrape_target(r"/movies/蜘蛛侠 (2023).mkv", MediaType.MOVIE)
    assert target_type == "file"
    assert target.endswith(".mkv")


def test_resolve_scrape_target_tv_series_dir(monkeypatch) -> None:
    """电视剧按目录层级映射到剧集目录。"""
    monkeypatch.setattr(settings, "TV_RENAME_FORMAT", "{{title}}/Season {{season}}/{{episode}}")
    plugin = _make_plugin()
    target, target_type = plugin._resolve_scrape_target(
        r"/tv/国产剧/长风渡 (2023)/Season 1/长风渡 - S01E01.mp4", MediaType.TV
    )
    assert target_type == "dir"
    assert target == r"/tv/国产剧/长风渡 (2023)"


def test_resolve_scrape_target_dir_input(monkeypatch) -> None:
    """媒体服务器返回目录路径时直接按目录刮削。"""
    monkeypatch.setattr(settings, "TV_RENAME_FORMAT", "{{title}}/Season {{season}}/{{episode}}")
    plugin = _make_plugin()
    target, target_type = plugin._resolve_scrape_target(r"/tv/国产剧/长风渡 (2023)", MediaType.TV)
    assert target_type == "dir"
    assert target == r"/tv/国产剧/长风渡 (2023)"


def test_resolve_scrape_target_strm_to_dir(monkeypatch) -> None:
    """strm 文件按重命名格式映射到媒体目录（而非当作目录处理）。"""
    monkeypatch.setattr(settings, "MOVIE_RENAME_FORMAT", "{{title}}/{{title}} ({{year}})")
    plugin = _make_plugin()
    target, target_type = plugin._resolve_scrape_target(
        r"/115/动画电影/小黄人与大怪兽 (2026)/小黄人与大怪兽 (2026) - 2160p.strm", MediaType.MOVIE
    )
    assert target_type == "dir"
    assert target == r"/115/动画电影/小黄人与大怪兽 (2026)"


def test_map_path() -> None:
    """路径映射按最长源前缀做前缀替换。"""
    plugin = _make_plugin(path_mapping="/data/video:/mnt/media/video\n/media:/mnt")
    assert plugin._map_path("/data/video/movie/a.mkv") == "/mnt/media/video/movie/a.mkv"
    assert plugin._map_path("/media/tv/x.mp4") == "/mnt/tv/x.mp4"
    assert plugin._map_path("/other/x.mkv") == "/other/x.mkv"


def test_map_path_empty() -> None:
    """未配置路径映射时返回原始路径。"""
    plugin = _make_plugin()
    assert plugin._map_path("/data/video/movie/a.mkv") == "/data/video/movie/a.mkv"


def test_map_path_skip_blank_and_comment() -> None:
    """空行与 # 注释行被忽略。"""
    plugin = _make_plugin(path_mapping="# 注释\n\n/data/video:/mnt/media/video")
    assert plugin._map_path("/data/video/movie/a.mkv") == "/mnt/media/video/movie/a.mkv"


def test_resolve_scrape_target_applies_path_mapping(monkeypatch) -> None:
    """先把 webhook 路径替换为本地路径，再按重命名格式取媒体目录。"""
    monkeypatch.setattr(settings, "MOVIE_RENAME_FORMAT", "{{title}}/{{title}} ({{year}})")
    plugin = _make_plugin(path_mapping="/data/video:/mnt/media/video")
    target, target_type = plugin._resolve_scrape_target(
        r"/data/video/动画电影/蜘蛛侠 (2023)/蜘蛛侠 (2023).mkv", MediaType.MOVIE
    )
    assert target_type == "dir"
    assert target == r"/mnt/media/video/动画电影/蜘蛛侠 (2023)"


def test_get_form_includes_path_mapping() -> None:
    """配置表单必须提供路径映射输入框。"""
    form, model = _make_plugin().get_form()
    raw = str(form)
    assert "path_mapping" in raw
    assert "路径映射" in raw
    assert model.get("path_mapping") == ""
