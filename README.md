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
pipeline/update.sh --force  # 强制重抓 op.gg（默认按补丁断点续跑）
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
| 强化图标 | CommunityDragon cherry-augments | 兜底用 hexdata 图标 |
| 视频思路 | 抖音原视频 + 本地抽帧核对 | 站点不托管视频，只保存来源、核对状态、摘要与版本风险 |

英雄、装备、符文和强化图片全部本地化到 `site/assets/img/`，页面核心攻略不依赖外链资源。

视频不是由爬虫摘要直接生成。采集只负责拿到公开媒体与元数据，画面/字幕核对后才可标记为 `visual-reviewed`；详见 [`docs/VIDEO_PIPELINE.md`](docs/VIDEO_PIPELINE.md)。

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
