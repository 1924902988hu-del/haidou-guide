# 抖音视频资料层

海斗速查采用两段式流程，而不是在“视觉模型”与“爬虫”之间二选一：

1. 采集层负责拿到公开链接、标题、作者、发布日期与视频文件。采集失败必须保留失败状态，不能生成摘要。
2. 理解层抽帧并核对字幕/语音，再输出英雄、核心强化、装备方向、关键操作与版本风险。
3. 发布层只读取 `data/videos/catalog.json`。`analysisStatus=visual-reviewed` 才能显示“已看画面”；仅有页面元数据的内容必须标为 `metadata-only`。

站点不托管抖音原视频，只展示带来源链接的结构化摘要。每条记录包含 `publishedAt`、`reviewedAt` 和 `patchStatus`，旧视频会明确提示需要在当前版本复核。

## 录入字段

- `heroes`: Data Dragon alias，例如 `Blitzcrank`
- `analysisStatus`: `metadata-only`、`visual-reviewed` 或 `human-verified`
- `patchStatus`: `current`、`needs-game-check` 或 `obsolete`
- `summary` / `keyPoints`: 只写画面、字幕或语音能够支持的内容
- `caveat`: 版本、样本或机制风险

带登录态的本地采集可以使用浏览器 Cookie，但 Cookie、临时视频和签名媒体地址不得写入仓库。GitHub Actions 只发布已经审核过的结构化记录。
