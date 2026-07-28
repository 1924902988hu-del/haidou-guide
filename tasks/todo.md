# 海斗速查(工作名)MVP — 2026-07-19

产品决策(用户已确认):公开产品运营 / 视频只做抖音一键跳转 / 数据 op.gg + 中文站(hexdata)交叉 / 直接开工 MVP。

合规红线(公开运营):不展示海克斯强化的胜率数字(只给推荐度/梯度;英雄胜率可展示)、页脚 Riot 免责声明、站名与域名不含英雄名/Riot 商标、页面常驻数据版本与更新时间。

## 数据侦察结论(已完成)
- [x] hexdata.com.cn = 静态 JSON(/data/heroes.json、/data/heroes/{id}.json、/data/augments.json、/data/hero_formula_items.json、/data/meta.json),含中文昵称 searchTerms、强化中文描述、trios 三强化组合
- [x] op.gg 海克斯页(RSC flight):metaId/metaType 提取 强化推荐(aram-augment)、召唤师技能(spell)、出装(item,按 Starter/Boots/Core 区块切分)、技能加点(QWER 字母:前 3 = 优先级,后 15 = 升级序列);**无天赋**
- [x] op.gg 普通大乱斗页(RSC flight):内嵌完整天赋 JSON(primary/secondary style、rune ids、stat mods、pick_rate)→ 天赋来源,页面标注"参考极地大乱斗"
- [x] 静态数据:ddragon 16.14.1 zh_CN(陷阱:name=称号/title=人名)、腾讯 hero_list.js(keywords 昵称/roles 六分类,join 按 heroId)、CDragon cherry-augments zh_cn(强化图标)

## MVP 任务
- [x] pipeline/common.py — fetch(UA/重试/限速)+ RSC flight 解码器
- [x] pipeline/fetch_static.py — ddragon zh_CN ×4 + hero_list.js + cherry-augments zh_cn → 验证: 173 英雄、638 强化(170 ARAM_)
- [x] pipeline/fetch_hexdata.py — meta/heroes/augments/formula + heroes/{id}×173 → 验证: 173 个文件、含 trios
- [x] pipeline/fetch_opgg.py — 每英雄 mayhem+aram 两页解析 → 验证: 173/173 成功,0 失败 0 锚点警告(wukong slug 回退生效)
- [x] pipeline/build_site_data.py — join → index.json + heroes/{alias}.json → 验证: 173 条、"女枪→厄运小姐"命中、强化输出无胜率字段
- [x] site/ 前端 — index.html + hero.html + assets(移动优先、海克斯蓝金)
- [x] 图片本地化(build 产出 583 张清单 → fetch_images.py 下载,替代外链 CDN,解决加载慢)
- [x] 浏览器预览验证 + 截图交付
- [x] pipeline/update.sh — 一键全量更新(五步,后续接 cron)

## Review(2026-07-19 MVP 完成)

已验证:
- 首页:173 英雄、梯度排序、定位 tab(射手33/法师75/坦克46)、图标本地秒开
- 搜索:女枪→厄运小姐、猴子→孙悟空、蛮王/manwang→泰达米尔、ez→伊泽瑞尔置顶(修复过一次拼音中段误匹配,改为 精确>前缀>包含 三级打分)
- 英雄页(MF/蛮王抽查):四件套齐全 + 三强化组合 + 抖音跳转按钮;移动端 375px 布局正常
- 合规:强化 JSON 输出无胜率字段(自动断言)、页脚 Riot 声明、数据版本常驻顶栏
- 数据管道:op.gg 173/173 零失败;hexdata 173 文件;图片 583/583;强化描述 %i:% 模板符已清理

遗留(下一迭代):
- [x] 部署 → 已上线 GitHub Pages,见下方 2026-07-20 上线记录
- [x] cron 每日自动更新 + 更新失败告警(个人站头号死因是断更)
- [ ] 强化检索页(按强化反查适配英雄,hexdata augments.json 已有数据)
- [ ] 大乱斗平衡系数(Wiki ChampionData,调研已验证可抓)
- [x] op.gg 页面结构变更的监控(解析器锚点失效时报警而非静默)

教训:预览面板长视口截图会黑屏(compositor 问题),验证多依赖 DOM/JS 提取;op.gg RSC flight 的 metaId/metaType 是稳定解析锚点,比啃渲染树省力。

## 上线 Review(2026-07-20,用户指定 GitHub、弃 Vercel)

- [x] 手机端适配增强:iPhone 安全区 inset(顶栏/页脚)、分类标签触控高度 34px、theme-color、-webkit-text-size-adjust;375px 视口全页截图验证(首页/英雄页/页脚)
- [x] 仓库:https://github.com/1924902988hu-del/haidou-guide(public,站名 haidou 不含英雄名/Riot 商标)
- [x] GitHub Pages(Actions workflow 部署 site/ 目录):https://1924902988hu-del.github.io/haidou-guide/
- [x] 线上实测:首页图标/搜索/英雄页四件套/抖音按钮全部正常,无控制台错误

数据更新流程(当前手动):本地跑 `pipeline/update.sh` → commit + push → Actions 自动重新部署。

教训(重要):本机网络推 git 大包必挂(>2MB pack 即 "remote end hung up",重试无用;http.postBuffer/HTTP1.1 均无效),但 GitHub API 小请求稳定——图片是用 Git Data API 逐 blob 上传后建树提交的(583/583 成功)。以后含大量图片的更新:小批量提交(<1MB/批)或复用 API 上传方案。

待办(上线后):
- [x] 数据自动更新(GitHub Actions 每日更新、验证、提交、部署;失败 Issue 告警)
- [x] 强化描述里的 "?" 占位符(Wiki 数值模板未填充,如"获得?法术强度")修复
- [ ] 国内访问速度评估;必要时自定义域名(不得含英雄名/Riot 商标,备案问题届时评估)

## 续接 Review(2026-07-29,Codex)

- [x] 数据升级:Data Dragon 16.15.1(对外静态版本 26.15),hexdata 最新统计仍为 26.14 / 2026-07-24;顶栏分开显示,不伪装成同版本统计
- [x] 26.15 特殊模式兼容:Data Dragon 新增的 60 个 `Jade_` 变体不会被当成真实英雄或下载无用图片;正式英雄仍为 173
- [x] op.gg 稳定性:按补丁断点更新;HTTP 截断响应需通过业务字段完整性验证;173/173 均有强化、技能、出装和符文,失败时非零退出
- [x] 发布前验证:`validate_site_data.py` 检查索引/详情一致、图片引用、版本、视频目录、强化胜率红线和描述占位符
- [x] 双端页面:桌面详情双栏、手机单栏和 2×2 统计卡;390×844 与 1440×900 均无横向溢出、无控制台错误
- [x] 视频资料层:建立 `data/videos/catalog.json` 与核对状态;已对机器人公开视频实际抽帧核字幕,只保留来源与结构化摘要,不托管原视频
- [x] 自动更新:`update-data.yml` 每日 12:30(北京时间)运行;复用 raw cache,有变更才提交,随后部署 Pages;失败开 Issue,恢复自动关闭

下一步不是上线阻塞项:
- [ ] 扩充“已看画面”视频覆盖率;每条都保留发布日期、核对日期、版本风险,不得把 `metadata-only` 当攻略
- [ ] 强化检索页(按强化反查适配英雄)
- [ ] 大乱斗平衡系数
