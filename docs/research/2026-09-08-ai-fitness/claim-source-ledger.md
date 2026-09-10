# 证据与主张台账：FitAgent AI 运动产品研究

**研究日**：2026-09-08。  
**使用规则**：`[P#]` 为当前仓库中可读源码/文档；`[E#]` 为外部来源。正文中的事实性陈述均就近引用此表。动态商店页与官网功能描述以访问日为准；产品宣传不构成独立效果证据。

## A. 本项目证据

| ID | 被支撑的关键主张 | 来源（标题、作者/发布者、日期） | URL / 仓库位置 | 访问与局限 |
|---|---|---|---|---|
| P01 | 当前架构的 RAG/Agent 路由、确认式记忆、Coros、非医疗声明、单 worker 限制和健康文档确认流程。 | 《FitAgent — 可解释 RAG、用户可控记忆与自适应训练计划》，项目 README，仓库当前版本。 | [README](../../README.md) | 一手实现文档；证明当前设计，不证明线上规模、实际效果或商业主体历史。 |
| P02 | 训练计划基于画像、近四周快照、反馈和 RAG；强度上限、疼痛/RPE/睡眠/负荷规则、结构化校验、证据 ID 与版本保存。 | `app/services/training_plan_service.py`，项目源码，当前版本。 | [training_plan_service.py](../../app/services/training_plan_service.py) | 一手代码；规则存在不等于医学验证或用户界面已完整呈现。 |
| P03 | 记忆的候选、确认、撤销、有效期和模型按需检索边界。 | 《记忆架构说明》及 `memory_service.py`，项目源码/文档，当前版本。 | [memory-architecture.md](../../memory-architecture.md)；[memory_service.py](../../app/services/memory_service.py) | 一手源码；实际部署配置可能改变。 |
| P04 | FitKG-CN 仅为 demo 级导入；部分权威材料 `review_only`，需要再利用许可和专家审核。 | `config/knowledge_sources.yml`，项目配置，当前版本。 | [knowledge_sources.yml](../../config/knowledge_sources.yml) | 一手登记册；不应把“已登记”当成“临床/专业验证完成”。 |
| P05 | Coros 仅通过本地只读社区 MCP、显式同步和最小化聚合；当前接口及超时/缓存边界。 | 《Coros 本地 MCP 配置》及 `coros_client.py`，项目 README/源码，当前版本。 | [README](../../README.md)；[coros_client.py](../../app/services/coros_client.py) | 一手实现；外部 MCP 的服务条款、可靠性和未来兼容性另需核验。 |

## B. 竞品与公司沿革证据

| ID | 被支撑的关键主张 | 来源（标题、发布者、日期） | 可点击来源 | 访问与局限 |
|---|---|---|---|---|
| E01 | 王宁 2014-09 创办 Keep；其董事长/CEO/创始人身份。 | [Management Team](https://ir.keep.com/en/about_manage.php)，Keep Investor Relations，页面访问 2026-09-08。 | [Keep IR](https://ir.keep.com/en/about_manage.php) | 公司一手资料；管理层与职位会变化。 |
| E02 | Keep 的创办背景、联合创始人分工、线上内容定位及 2023-07 上市。 | [Global Offering / Prospectus](https://www1.hkexnews.hk/listedco/listconews/sehk/2023/0630/10791492/2023063000251.pdf)，Keep / 香港交易所，2023-06-30。 | [HKEX 招股书](https://www1.hkexnews.hk/listedco/listconews/sehk/2023/0630/10791492/2023063000251.pdf) | 交易所上市文件，一手披露；行业规模预测为公司引用的 CIC 数据，未作为本文独立市场规模结论。 |
| E03 | Keep 的 2019–2022 MAU/训练次数、综合供给；以及 2023–2024 MAU、订阅会员、2024 收入/毛利/调整后亏损。 | [Company Introduction](https://ir.keep.com/en/about_profile.php)，Keep IR，页面访问 2026-09-08；[Keep Inc. Announces 2024 Annual Results](https://www.prnewswire.com/news-releases/keep-inc-announces-2024-annual-results-302414276.html)，Keep Inc. 通过 PR Newswire 发布，2025-03-28。 | [公司介绍](https://ir.keep.com/en/about_profile.php)；[2024 年业绩](https://www.prnewswire.com/news-releases/keep-inc-announces-2024-annual-results-302414276.html) | **证据组合**：前者支持历史 MAU/产品，后者为公司审计业绩新闻稿。公司新闻稿中的原因归因属于管理层表述。 |
| E04 | Keep 当前商店页的 AI 教练、Keepace.ai、跑步/饮食/睡眠功能、穿戴连接与订阅价格。 | [Keep - AI 运动教练](https://apps.apple.com/cn/app/keep-ai-%E8%BF%90%E5%8A%A8%E6%95%99%E7%BB%83/id952694580)，Apple App Store（开发者 Keep），访问 2026-09-08。 | [App Store](https://apps.apple.com/cn/app/keep-ai-%E8%BF%90%E5%8A%A8%E6%95%99%E7%BB%83/id952694580) | 产品一手上架描述；功能/价格/评分会变动，未独立验证 AI 成效。 |
| E05 | 咕咚 2010–2018 的时间线、申波、硬件/社交/赛事/AI/V-COACH、公司所称用户和跑团数据。 | [咕咚简介](https://www.codoon.com/h5/codoon-welcome/m_about.html)，咕咚，访问 2026-09-08。 | [咕咚官网](https://www.codoon.com/h5/codoon-welcome/m_about.html) | 公司一手时间线；规模、市场份额和 AI 效果是公司自述，正文已明确不等同于独立审计或临床证据。 |
| E06 | 咕咚当前多运动记录、手表连接、赛事、跑团、会员、配速兔子与商店评论。 | [咕咚-跑步骑行健走训练马拉松赛事活动](https://apps.apple.com/cn/app/%E5%92%95%E5%92%9A-%E8%B7%91%E6%AD%A5%E9%AA%91%E8%A1%8C%E5%81%A5%E8%B5%B0%E8%AE%AD%E7%BB%83%E9%A9%AC%E6%8B%89%E6%9D%BE%E8%B5%9B%E4%BA%8B%E6%B4%BB%E5%8A%A8/id453480684)，Apple App Store（开发者成都乐动），访问 2026-09-08。 | [App Store](https://apps.apple.com/cn/app/%E5%92%95%E5%92%9A-%E8%B7%91%E6%AD%A5%E9%AA%91%E8%A1%8C%E5%81%A5%E8%B5%B0%E8%AE%AD%E7%BB%83%E9%A9%AC%E6%8B%89%E6%9D%BE%E8%B5%9B%E4%BA%8B%E6%B4%BB%E5%8A%A8/id453480684) | 功能为开发者描述；评论是非代表性个案。 |
| E07 | 悦动天下成立时间、轻运动社区定位、下载量宣称和服务范围。 | [关于我们](https://www.51yund.com/zh-cn/mAbout)，悦动天下，页面标注版权至 2024，访问 2026-09-08。 | [悦动圈官网](https://www.51yund.com/zh-cn/mAbout) | 公司一手介绍；下载量不是研究日活跃用户。 |
| E08 | 悦动圈 2015 年游戏化/红包的原始定位与商业模式；当前红包、PK、团赛、计划和周报功能。 | [悦动圈：给运动加点“游戏”](https://tech.sina.com.cn/i/2015-08-31/doc-ifxhkafe6195437.shtml)，南方都市报经新浪科技，2015-08-31；[悦动圈-跑步健身计步骑行](https://apps.apple.com/cn/app/%E6%82%A6%E5%8A%A8%E5%9C%88-%E8%B7%91%E6%AD%A5%E5%81%A5%E8%BA%AB%E8%AE%A1%E6%AD%A5%E9%AA%91%E8%A1%8C/id872341407)，Apple App Store，访问 2026-09-08。 | [2015 报道](https://tech.sina.com.cn/i/2015-08-31/doc-ifxhkafe6195437.shtml)；[App Store](https://apps.apple.com/cn/app/%E6%82%A6%E5%8A%A8%E5%9C%88-%E8%B7%91%E6%AD%A5%E5%81%A5%E8%BA%AB%E8%AE%A1%E6%AD%A5%E9%AA%91%E8%A1%8C/id872341407) | **证据组合**：历史商业模式为媒体访谈，功能为开发者上架描述；均不证明今天的留存因果。 |
| E09 | 力盛 2021 年买入悦动天下 25%；其数字体育/城市企业校园方向；2023 业绩承诺未完成。 | [力盛运动 2024 年年度报告](https://static.cninfo.com.cn/finalpage/2025-04-24/1223245046.PDF)，力盛运动 / 巨潮资讯，2025-04-24；[力盛运动 2023 年年度报告](https://static.cninfo.com.cn/finalpage/2024-04-26/1219831177.PDF)，力盛运动 / 巨潮资讯，2024-04-26。 | [2024 年报](https://static.cninfo.com.cn/finalpage/2025-04-24/1223245046.PDF)；[2023 年报](https://static.cninfo.com.cn/finalpage/2024-04-26/1219831177.PDF) | 上市公司披露，权重高；“业务重点”是公司表述，不替代独立运营数据。 |
| E10 | 悦动体系姿态识别在赛事/校园考级的公开应用。 | [悦动全民健身平台入选 2022 年度智能体育典型案例](https://www.lsaisports.com/szdt/1765)，力盛体育，2023-06-28。 | [力盛体育](https://www.lsaisports.com/szdt/1765) | 集团新闻稿，证实场景存在；未提供独立准确度或 C 端私人教练效果。 |
| E11 | Sigma 的 App 主体、iPhone-only、免费、历史数据迁移、Sig AI、训练计划/版本说明及评论信号。 | [Sigma - 你的 AI 跑步记录](https://apps.apple.com/cn/app/sigma-%E4%BD%A0%E7%9A%84ai%E8%B7%91%E6%AD%A5%E8%AE%B0%E5%BD%95/id6743433033)，Apple App Store（开发者 Chaoshi Technology (Shanghai) Co., Ltd.），访问 2026-09-08。 | [App Store](https://apps.apple.com/cn/app/sigma-%E4%BD%A0%E7%9A%84ai%E8%B7%91%E6%AD%A5%E8%AE%B0%E5%BD%95/id6743433033) | 功能由开发者提供，评分/版本动态；评论不可外推。 |
| E12 | `sigma.run` 对外联系主体名称与地址；悦动天下 2024 承诺未完成原因、补偿及力盛持股升至 49.1916%。 | [Sigma App](https://www.sigma.run/)，Sigma，访问 2026-09-08；[2025-049 关于参股公司深圳市悦动天下科技有限公司 2024 年度业绩承诺完成情况的公告](https://static.cninfo.com.cn/finalpage/2025-06-14/1223877608.PDF)，力盛运动 / 巨潮资讯，2025-06-14。 | [Sigma 官网](https://www.sigma.run/)；[力盛公告](https://static.cninfo.com.cn/finalpage/2025-06-14/1223877608.PDF) | **证据组合**：两者支撑不同主张。Sigma 主体关系因此仍标为暂未核实；力盛公告为高权重一手披露。 |

## C. 监管、公共健康与科学证据

| ID | 被支撑的关键主张 | 来源（标题、发布者、日期） | 可点击来源 | 访问与局限 |
|---|---|---|---|---|
| E13 | 健康、行踪、医疗和生物识别为敏感个人信息；处理敏感信息的规则。 | [中华人民共和国个人信息保护法](https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm)，中央网信办，2021-08-20。 | [法规全文](https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm) | 法律一手来源；具体合规结论取决于部署、数据流和法律意见。 |
| E14 | 面向境内公众的生成式 AI 服务应保护个人信息、提升透明度/准确性/可靠性、避免健康歧视及不必要收集。 | [生成式人工智能服务管理暂行办法](https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm)，国家网信办等，2023-07-13（2023-08-15 施行）。 | [法规全文](https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm) | 法规一手来源；是否触发备案/安全评估须结合具体产品判断。 |
| E15 | 成人活动量和力量训练的公共健康底线、渐进增加原则。 | [Physical activity](https://www.who.int/initiatives/behealthy/physical-activity)，世界卫生组织，页面访问 2026-09-08；[WHO Guidelines at a glance](https://www.who.int/publications/i/item/9789240014886)，2020-11-25。 | [WHO 活动建议](https://www.who.int/initiatives/behealthy/physical-activity) | 高权重公共健康指南；不是个人医疗或训练处方。 |
| E16 | 《全民健身计划（2021—2025 年）》的政策背景。 | [国务院关于印发全民健身计划（2021—2025 年）的通知](https://www.sport.gov.cn/n315/n9041/n25319615/n25319645/c25531540/content.html)，国务院/国家体育总局转载，2021-07-18。 | [通知](https://www.sport.gov.cn/n315/n9041/n25319615/n25319645/c25531540/content.html) | 政策一手来源；计划周期已结束，不能当作 2026 年后具体政策承诺。 |
| E17 | Keep 2025 年 MAU、订阅会员、收入、IFRS/经调整利润、分部变化、AI 转型和公司对流失的解释。 | [Annual Results Announcement for the year ended 31 December 2025](https://www.hkexnews.hk/listedco/listconews/sehk/2026/0325/2026032500119.pdf)，Keep Inc. / 香港交易所，2026-03-25。 | [2025 年业绩公告](https://www.hkexnews.hk/listedco/listconews/sehk/2026/0325/2026032500119.pdf) | 交易所一手披露。经调整（非 IFRS）利润不可与 IFRS 净利润混同；AI 使用量和对用户流失的归因均是管理层披露，不能替代独立因果验证。 |
| E18 | LLM 运动/健康教练的评价研究仍碎片化，20 项研究中 55% 低严谨度。 | [Evaluation Strategies for Large Language Model-Based Models in Exercise and Health Coaching: Scoping Review](https://pmc.ncbi.nlm.nih.gov/articles/PMC12520646/)，同行评议综述，2025。 | [PMC 全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC12520646/) | 研究质量/评估框架综述，非疗效 RCT；支撑“证据不足”，不证明某一产品无效。 |
| E19 | mHealth 运动干预可小到中等幅度增加活动，但长期效果变弱，研究偏倚风险较高。 | [Long-term Effectiveness of mHealth Physical Activity Interventions](https://pmc.ncbi.nlm.nih.gov/articles/PMC8122296/)，系统综述与元分析，2021。 | [PMC 全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC8122296/) | 不是 LLM 专项，且作者报告多数纳入研究有高偏倚风险；用于校正乐观叙事。 |
| E20 | 消费级可穿戴数据的准确性异质；心率较好但活动强度、能耗、睡眠、VO2max 等误差不能直接当高风险决策事实。 | [Keeping Pace with Wearables](https://pubmed.ncbi.nlm.nih.gov/39080098/)，*Sports Medicine*，2024-11。 | [PubMed](https://pubmed.ncbi.nlm.nih.gov/39080098/) | 24 篇系统综述、249 项非重复验证研究；不针对 Coros 单一型号，故用于“数据置信度设计”而非否定某品牌。 |
| E21 | 游戏化相对非游戏化 App 的平均增益有限，适合辅助坚持而非替代训练质量。 | [Effect of digital health applications with or without gamification](https://pmc.ncbi.nlm.nih.gov/articles/PMC11701442/)，系统综述与元分析，2024-09-25。 | [PMC 全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC11701442/) | 36 项 RCT；人群和干预异质，不能对红包/排行榜的单一产品机制作确定因果结论。 |
| E22 | 运动前筛查应结合活动习惯、已知疾病/症状和拟进行强度，且要保留转诊路径而非制造普遍门槛。 | [Updating ACSM’s Recommendations for Exercise Preparticipation Health Screening](https://journals.lww.com/acsm-msse/fulltext/2015/11000/updating_acsm_s_recommendations_for_exercise.28.aspx)，ACSM 专家共识，2015-11。 | [ACSM 共识](https://journals.lww.com/acsm-msse/fulltext/2015/11000/updating_acsm_s_recommendations_for_exercise.28.aspx) | 专家共识、非中国法规；用于产品安全流程设计，不替代医生判断。 |
| E23 | 小样本专家审阅中，GPT-4 可生成一般性安全导向计划，但个性化不足，不应替代专业人员。 | [Using artificial intelligence for exercise prescription in personalised health promotion](https://pmc.ncbi.nlm.nih.gov/articles/PMC10955739/)，*Biology of Sport*，2024-03。 | [PMC 全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC10955739/) | 仅五个假设案例，不能量化现实风险；只支撑“结构化规则/人工升级仍需要”。 |

## D. 关键主张—证据映射与冲突处理

| 主张族 | 主要证据 | 结论级别 | 冲突、缺口及处理 |
|---|---|---|---|
| FitAgent 已有可解释/安全/确认式记忆骨架 | P01–P05 | 事实 | 仅证明仓库实现，非线上效果。企业历史、用户规模、留存和盈利均暂未核实。 |
| Keep 规模大且商业化仍承压 | E02–E04、E17 | 事实 + 推断 | 财务/MAU用交易所或公司披露；“用户为何离开”不由财务直接推出，正文仅作机制推断。 |
| 咕咚的核心是户外数据、赛事、社群、装备 | E05–E06 | 事实 + 推断 | 公司规模说法未独立审计；体验评论不用于质量结论。 |
| 悦动圈的核心是游戏化及组织数字体育，且经营承压 | E07–E10、E12 | 事实 + 推断 | 经营数据来自关联上市公司披露；不能据此断言 C 端活跃或用户满意度下降。 |
| Sigma 是轻量跑步 AI 产品，但团队/资金/主体关系信息不足 | E11–E12 | 事实 + 暂未核实 | App Store 开发者与官网名称不同；不推断其股权或与任何平台的关系。 |
| AI 运动教练应设置安全、数据质量与效果评估边界 | E13–E16、E18–E23 | 事实 + 建议 | 公共健康、国际论文和中国法规不能直接构成个体医疗处方；建议均保留“非诊疗、可转专业人员”边界。 |
