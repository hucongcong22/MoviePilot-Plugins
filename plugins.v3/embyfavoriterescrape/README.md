# Emby移除收藏自动刮削

在 Emby（或其它媒体服务器）中点击**移除收藏**时，自动重新识别并刮削该视频的元数据与图片。

## 适用场景

- 手动触发媒体的重新刮削，而不是整库定时刮削。
- 借助“移除收藏”这一用户动作作为明确的重新刮削信号。
- 支持电影（MOV）与电视剧（TV）。

## 工作方式

1. 插件监听 MoviePilot 的 `WebhookMessage` 事件。
2. 从 Webhook 报文中解析该媒体的**收藏状态**，以及**事件上下文**。
3. 当收藏状态被解析为“已移除”（`False`），并且事件命中配置的**触发事件**
   （默认包含 `UserDataSaved`、`item.favorite` 等）或 `SaveReason=ToggleFavorite` 时，
   判定为一次移除收藏。
4. 后台线程按媒体身份（`media_source` + `media_id`）重新识别媒体，并调用
   `ScrapingChain.scrape_metadata` 重新刮削该媒体的 NFO 与图片。
5. **刮削路径映射**：先按配置的“路径映射”（媒体服务器路径 → MoviePilot 本地路径）
   替换 webhook 返回的媒体路径；再与 `LibraryScraper` 一致，根据 `MOVIE_RENAME_FORMAT` /
   `TV_RENAME_FORMAT` 的目录层级，把路径映射为真正的媒体目录（扁平结构或无目录层级时
   退回单文件），最后交给刮削链处理，避免把分类目录或单一文件当成媒体整体。

## 配置说明

- **启用插件**：开关。
- **覆盖已有元数据**：刮削时是否覆盖已有 NFO/图片。
- **监听渠道**：默认 `emby`，多个用逗号分隔（如 `emby,jellyfin`）。
- **处理类型**：电影 + 电视剧 / 仅电影 / 仅电视剧。
- **触发事件**：收藏相关 Webhook 事件名，多值逗号分隔，默认包含 `UserDataSaved`、
  `item.favorite` 等，另外包含 `item.rate`——部分 Emby 构建（如 115strm）会把“取消收藏”
  这类用户数据变更统一通过 `item.rate` 事件上报。如果你的 Emby 收藏事件名不同，
  请按实际收到的 `Event` 字段调整。
- **限定用户名**：只处理指定媒体库用户，留空不限制。
- **排除路径关键词**：命中这些关键词的路径不刮削。
- **路径映射**：当媒体服务器（如 Emby/Jellyfin）与 MoviePilot 挂载的目录路径不一致时，
  按“每行一条 `媒体服务器路径:MoviePilot本地路径`”把 webhook 返回的路径替换为本地路径
  后再刮削；留空则直接使用原始路径。刮削目录仍会按 MoviePilot 设置的重命名格式
  （`MOVIE_RENAME_FORMAT` / `TV_RENAME_FORMAT`）计算（参考 LibraryScraper）。
- **发送通知**：刮削成功 / 失败后发送通知。

## 路径映射配置示例

插件在刮削前会把媒体服务器（如 Emby/Jellyfin）返回的路径，按下面的规则替换为
MoviePilot 本地能访问的路径。**只有媒体服务器与 MoviePilot 挂载目录不一致时才需要填写**，
两者一致时留空即可。

格式为**每行一条 `源前缀:目标前缀`**（也可用 `=>` 分隔）：

```
/115:/media/115
/data/video:/mnt/media/video
/media => /mnt
```

- 以「源前缀」开头的 webhook 路径会被替换为「目标前缀」，例如 `/115/动画电影/xxx (2026)` → `/media/115/动画电影/xxx (2026)`。
- **最长源前缀优先**：`/data/video` 会比 `/data` 先命中，避免 `/data` 误匹配 `/data2`。
- 支持 `#` 开头的注释行和空行；每条规则独占一行。
- 留空表示不做替换，直接使用媒体服务器返回的原始路径。

例如：你的 Emby（115strm）webhook 返回 `/115/动画电影/小黄人与大怪兽 (2026)/小黄人与大怪兽 (2026) - 2160p.strm`，
而 MoviePilot 本机媒体库实际挂在 `/media/115` 下，则填：

```
/115:/media/115
```

替换后刮削目标为 `/media/115/动画电影/小黄人与大怪兽 (2026)/`。

## 提示

- 需要在媒体服务器中开启对应的 Webhook，并有 MoviePilot 接收。
- Emby 的 webhook 解析器默认不填充 `item_favorite` 字段，V3 实现会从原始报文
  （`json_object`）中解析收藏状态，因此 **Emby 检测在 V3 下功能完整**。
- 支持 `*.strm` 虚拟视频文件（如下发到 115 等网盘的 strm）：由 `.strm` 路径按重命名格式
  反推媒体目录后刮削，而不是把 strm 误当成目录。
- 配套的 `plugins.v2/embyfavoriterescrape` 面向旧版 MoviePilot：V2 主程序在解析
  Emby Webhook 时不填充 `item_favorite` 也不透传原始报文，因此 V2 实现的收藏状态
  检测主要适用于会填充 `item_favorite` 的媒体服务器（如 Jellyfin）。
- 刮削在后台线程执行，不会阻塞媒体服务器 Webhook 响应。

## 说明

本插件与 `plugins.v2/embyfavoriterescrape` 为同源插件，V2 实现面向旧版
MoviePilot。当前 V3 实现使用 `app.sdk` 与 `ScrapingChain` 新合同。
