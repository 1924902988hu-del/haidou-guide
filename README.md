# 海斗速查(工作名)

LOL「海克斯大乱斗」(ARAM: Mayhem)英雄攻略聚合站:天赋、技能加点、海克斯强化搭配、出装,支持昵称/拼音搜索与定位分类,移动优先。

## 本地运行

```bash
python3 -m http.server 8942 --directory site
# 打开 http://localhost:8942
```

纯静态站,`site/` 目录可直接部署到任何静态托管(Vercel / Cloudflare Pages / OSS)。

## 数据更新(版本更新后跑一次)

```bash
pipeline/update.sh          # 五步:官方静态 → hexdata → op.gg → 合成 → 图片
pipeline/update.sh --force  # 强制重抓 op.gg(默认断点续跑、跳过已抓英雄)
```

仅依赖 Python3 标准库。全流程限速抓取,总请求约 900 个,跑完 8~10 分钟。
建议接 cron 每日一次;hexdata 按 buildId 自动跳过未变化的构建。

## 数据源与架构

| 数据 | 来源 | 说明 |
|---|---|---|
| 强化推荐/出装/技能加点/召唤师技能 | op.gg 海克斯专区(RSC flight 解析) | 无公开 API,低频抓取 |
| 天赋符文 | op.gg 极地大乱斗页 | 海克斯页无天赋区块,页面已标注 |
| 中文强化名/描述/单件强度/三强化组合/英雄胜率 | hexdata.com.cn 静态 JSON | 每日构建 |
| 英雄/装备/符文中文数据 | Riot Data Dragon zh_CN | ⚠️ zh_CN 里 name=称号、title=人名 |
| 昵称 keywords / 官方六定位 roles | 腾讯 hero_list.js | join 一律按 heroId 数字 |
| 强化图标 | CommunityDragon cherry-augments | 兜底用 hexdata 图标 |

图片全部本地化到 `site/assets/img/`(583 张,约 9MB),页面零外链请求。

## 合规红线(公开运营前提)

- 海克斯强化**不展示胜率数字**(Riot 政策禁止,只展示推荐度/档位);英雄胜率可展示
- 页脚保留 Riot "Legal Jibber Jabber" 免责声明
- 站名/域名不得含英雄名或 Riot 商标;"海斗速查"为占位工作名
- Riot 官方 API 对该模式 403 封锁(intended),不要尝试接入
- 不做任何抖音抓取(法律风险);抖音入口仅为跳转链接

## 目录

```
pipeline/   抓取与合成脚本(common/fetch_static/fetch_hexdata/fetch_opgg/build_site_data/fetch_images + update.sh)
data/raw/   原始数据(不部署)
site/       静态站点(部署这个目录)
tasks/      计划与复盘
```
