# 海斗速查(工作名)

LOL「海克斯大乱斗」(ARAM: Mayhem)双端攻略站：天赋、技能加点、海克斯强化搭配、出装和已核对的视频思路，支持昵称/拼音搜索与定位分类。

线上地址：<https://1924902988hu-del.github.io/haidou-guide/>

## 本地运行

```bash
python3 -m http.server 8942 --directory site
# 打开 http://localhost:8942
```

纯静态站，`site/` 目录由 GitHub Pages 发布。

## 数据更新(版本更新后跑一次)

```bash
pipeline/update.sh          # 六步：静态 → hexdata → op.gg → 合成 → 图片 → 完整性验证
pipeline/update.sh --force  # 强制重抓 op.gg（默认按 OP.GG 页面版本断点续跑）
```

仅依赖 Python3 标准库。GitHub Actions 每天 12:30（北京时间）自动更新、验证、提交并重新部署；失败会创建或追加一个 GitHub Issue，恢复后自动关闭。

## 数据源与架构

| 数据 | 来源 | 说明 |
|---|---|---|
| 强化推荐/出装/技能加点/召唤师技能 | op.gg 海克斯专区(RSC flight 解析) | 无公开 API,低频抓取 |
| 天赋符文 | op.gg 极地大乱斗页 | 海克斯页无天赋区块,页面已标注 |
| 中文强化名/描述/单件强度/三强化组合/英雄胜率 | hexdata.com.cn 静态 JSON | 每日构建 |
| 英雄/装备/符文中文数据 | Riot Data Dragon zh_CN | ⚠️ zh_CN 里 name=称号、title=人名 |
| 昵称 keywords / 官方六定位 roles | 腾讯 hero_list.js | join 一律按 heroId 数字 |
| 强化图标 / 当前客户端标准名称补充 | CommunityDragon cherry-augments | `UX/Kiwi` 名称只校验合法性，不冒充统计 |
| 视频思路 | 抖音原视频 + 本地抽帧核对 | 站点不托管视频，只保存来源、核对状态、摘要与版本风险 |

英雄、装备、符文和强化图片全部本地化到 `site/assets/img/`，页面核心攻略不依赖外链资源。

Data Dragon、OP.GG 海克斯攻略、OP.GG 普通 ARAM 符文与 hexdata 可能不同步。更新脚本会从 OP.GG 自身静态资源路径提取页面版本，并与客户端资料、统计版本分别保存和展示；不会再把 Data Dragon 版本当成 OP.GG 已同步的证据。hexdata 的全量响应先写入临时目录，首尾 `buildId` 一致且清单哈希通过后才替换正式缓存；站点合成前还会再次校验，避免中断后混用新旧英雄明细。

视频不是由爬虫摘要直接生成。采集只负责拿到公开媒体与元数据；BiliNote 用关键帧与语音转写做多模态提炼，通过质量闸门后才可标记为 `multimodal-reviewed`。网页徽标只列正式记录实际公开的证据类型，不把处理流程冒充每条结论的依据；公开画面证据还必须有同时间戳截图清单与 SHA-256，便于后续复核。人工逐帧复核的旧记录继续使用 `visual-reviewed`；详见 [`docs/VIDEO_PIPELINE.md`](docs/VIDEO_PIPELINE.md)。

## 抖音视频情报

已接入一条可复用的自动化链路：

1. TikHub 按英雄昵称搜索“最新发布 + 最多点赞”的公开抖音视频；
2. FFmpeg + macOS Vision OCR 先定位关键界面并做 0.5 秒密集补帧，图标匹配只作为保守候选；
3. BiliNote 实际读取视频画面、字幕和语音，`pipeline/video_intelligence.py` 按独立流派提取强化、出装、符文、技能与时间戳证据；
4. 只有置信度达标、至少两条时间戳证据、包含画面证据，且证据本身明确指向目标英雄的结果才允许发布。

一键生成五位最久未更新英雄的草稿：

```bash
python3 pipeline/video_intelligence.py refresh
```

确认质量闸门后自动写入网站：

```bash
python3 pipeline/video_intelligence.py refresh --publish
```

全量刷新 173 位英雄时使用可恢复断点；每位英雄最多保留三条近 45 天的候选，每条视频最多保存四套证据彼此独立的流派。未命中或未通过质量闸门的英雄不会删除 OP.GG 攻略：

```bash
python3 pipeline/video_intelligence.py refresh \
  --all-heroes \
  --videos-per-hero 3 \
  --limit-per-hero 4 \
  --max-videos 519 \
  --sorts 2,1 \
  --publish-time 180 \
  --max-age-days 45 \
  --max-duration-seconds 600 \
  --fallback-queries \
  --resume \
  --max-search-requests 444 \
  --candidates-output data/cache/video_intelligence/candidates-all-heroes-v5.json \
  --publish
```

`--max-search-requests` 限制的是本轮新增请求，不会把恢复断点中的历史查询重复计入。候选断点的 `lastRun` 与批次 `run-summary` 会分别记录本轮增量和断点累计；费用只按 TikHub 当前公开价格区间给出本轮搜索上限估算，实际金额以账单为准。

候选搜索已经结束、只想继续分析现有断点时必须加 `--skip-discovery`，这样不会搜索剩余英雄或重复消耗搜索预算：

```bash
python3 pipeline/video_intelligence.py refresh \
  --all-heroes \
  --skip-discovery \
  --videos-per-hero 3 \
  --max-videos 519 \
  --resume \
  --candidates-output data/cache/video_intelligence/candidates-all-heroes-v5.json \
  --publish
```

运行前仅需在本地环境配置 `TIKHUB_TOKEN`，并启动已配置多模态模型的 BiliNote。完整字段、状态与安全边界见 [`docs/VIDEO_PIPELINE.md`](docs/VIDEO_PIPELINE.md)。

## 合规红线(公开运营前提)

- 海克斯强化**不展示胜率数字**(Riot 政策禁止,只展示推荐度/档位);英雄胜率可展示
- 页脚保留 Riot "Legal Jibber Jabber" 免责声明
- 站名/域名不得含英雄名或 Riot 商标;"海斗速查"为占位工作名
- Riot 官方 API 对该模式 403 封锁(intended),不要尝试接入
- 不镜像或公开托管抖音视频；站内摘要必须保留原视频链接、发布日期、核对状态和版本风险

## 目录

```
pipeline/   抓取、合成与完整性验证脚本
data/videos/已审核的视频结构化目录
data/raw/   原始数据(不部署)
site/       静态站点(部署这个目录)
docs/       数据与编辑流程说明
tasks/      计划与复盘
```
