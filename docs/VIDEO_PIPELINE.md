# 抖音视频资料层

海斗速查采用两段式流程，而不是在“视觉模型”与“爬虫”之间二选一：

1. 采集层使用 TikHub 关键词搜索拿到公开链接、标题、作者与发布日期。采集失败必须保留失败状态，不能生成摘要。
2. 理解层复用 BiliNote 的视频理解能力，必须启用 `video_understanding`，同时核对画面、字幕与语音，再输出英雄、核心强化、装备方向、关键操作与版本风险。
3. 发布层只读取 `data/videos/catalog.json`。只有 `multimodal-reviewed`、`visual-reviewed` 或 `human-verified` 才能显示“已看画面”；仅有页面元数据的内容必须标为 `metadata-only`。

站点不托管抖音原视频，只展示带来源链接的结构化摘要。每条记录包含 `publishedAt`、`reviewedAt` 和 `patchStatus`，旧视频会明确提示需要在当前版本复核。

## 当前实现

`pipeline/video_intelligence.py` 提供四个命令：

- `discover`：搜索候选视频并按英雄匹配、发布时间与互动量排序。
- `analyze`：提交单条视频给本地 BiliNote，保留原始笔记并生成结构化草稿。
- `publish`：只允许通过质量闸门的草稿进入视频目录，然后重建并验证全部英雄页。
- `refresh`：把前三步串成一次批量刷新；默认只产出草稿，加 `--publish` 才会更新网站。

默认质量闸门：

- 英雄 alias 必须与搜索目标一致；
- 至少提取一种强化、出装、符文、加点或打法；
- 至少两条有效时间戳证据；
- 至少一条证据必须来自实际画面；
- 置信度不得低于 0.68；
- 只有视频明确提到并等于当前补丁时，才能标为“当前版本”。

TikHub 的签名播放地址、抖音 Cookie、BiliNote 临时视频和模型密钥不会写入仓库。缓存草稿位于已忽略的 `data/cache/video_intelligence/`。

## 录入字段

- `heroes`: Data Dragon alias，例如 `Blitzcrank`
- `analysisStatus`: `metadata-only`、`multimodal-reviewed`、`visual-reviewed` 或 `human-verified`
- `patchStatus`: `current`、`needs-game-check` 或 `obsolete`
- `summary` / `keyPoints`: 只写画面、字幕或语音能够支持的内容
- `strategy`: 强化、装备、符文、技能、召唤师技能与打法
- `evidence`: 时间戳、证据类型（`frame` / `subtitle` / `audio`）与对应结论
- `confidence`: 0 到 1 的抽取置信度，不代表套路胜率
- `caveat`: 版本、样本或机制风险

带登录态的本地采集可以使用浏览器 Cookie，但 Cookie、临时视频和签名媒体地址不得写入仓库。GitHub Actions 只发布已经审核过的结构化记录。
