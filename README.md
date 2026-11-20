# 槐序干燥时长试算台

RandomForest回归、训练内特征子集交叉验证与留出集MAE。为槐序研究协作组的12位分析与业务人员提供从样本核对、独立worker计算到批准快照交付的完整工作流。

## 真实业务场景

物料初始湿度/厚度/温度→干燥时长估计→工艺试验备忘；解释用留出置换重要性，禁止用留出集选参。团队原先将 `drying-forest-202611-01.csv` 复制到多份人工表格，复核时再逐项对照。三个痛点是口径不可追溯、重复计算易错、业务说明与结果脱节。上游提供合成业务观察，系统承担研究和复核，下游业务人员读取批准快照。全部演示数据是固定随机种子生成的模拟数据。

## 业务模块

1. 业务总览：批次、样本、任务与批准交付账册。
2. 干燥研究：创建/修改来源说明、查看样本、用真实业务字段追加记录；列表分页。
3. 分析配置：冻结来源、参数和全部样本，进入Redis队列。
4. 冲突复核：同温同风分组核查，受理即冻结样本，给出最少待复核名单与互不重叠的冲突链。
5. 完整结果：真实算法摘要、专属诊断、全部CSV导出和可恢复失败态。
6. 工艺试验备忘：分析师草案→负责人批准→阅读者下载批准快照。
7. 审计：登录、来源修改、任务worker完成、业务说明批准等事件。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 可重复执行，不覆盖人工修改
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [前端](http://localhost:18248)；[API文档](http://localhost:19248/docs)。数据库仅演示用途映射 `17748`。首次准备依赖和初始化数据库需要等待健康状态：`docker compose ps`，五个服务均需healthy。

| 组件 | 技术与职责 |
|---|---|
| frontend | 原生 HTML / CSS / JS ES Modules，Nginx代理 `/api/` |
| backend | Python3.11 / FastAPI / SQLAlchemy / JWT |
| worker | 独立Python进程，Redis BLPOP队列，持久化结果/CSV/心跳 |
| db | PostgreSQL16，命名卷持久化 |
| redis | Redis7，AOF命名卷 |

恰好五服务，均配置healthcheck；后端/worker依赖数据库与Redis健康，前端依赖后端健康；worker与后端共享 `artifacts` 命名卷。重置演示环境：`docker compose down -v --remove-orphans`，会删除本项目卷。

## 账号与权限

所有演示账号密码：`Research2026!`。

| 邮箱 | 角色 | 权限 |
|---|---|---|
| admin@drying-forest.demo | admin | 全部录入/执行、批准业务说明 |
| analyst@drying-forest.demo | analyst | 录入来源/样本、执行、提交说明，无批准权 |
| viewer@drying-forest.demo | viewer | 只读、导出结果及已批准快照 |

时间在数据库/API中以UTC存储，总览与审计页面明确转换为Asia/Shanghai北京时间。

JWT有效期8小时；PBKDF2盐化哈希保存密码；角色每请求从数据库读取。访客写操作403，匿名访问业务数据401，重复样本409，非法业务字段422，重复批准409。每客户端/路径写请求90次/分钟，超限429。生产使用需修改JWT及演示数据库凭据。

## 实体与字段

DryingStudy / DryingSample / RegressionRun / ProcessMemo 是核心实体；另有User和Audit。数据库实际表：`drying_studies` → `drying_samples` → `regression_runs` → `process_memos`；冲突复核另用 `review_runs`（任务，含冻结水位线）与 `review_groups`（工况组，含名单与冲突链）。样本以固定字段JSON保存，后端按以下字段严格验证，不接受未知字段替代必填值：初始湿度 %（`moisture`）、厚度 mm（`thickness`）、设定温度 ℃（`temperature`）、风量 m³/min（`airflow`）、实测时长 min（`minutes`）。

实体贯穿 `backend/src/models`、`schemas`、`services`、`controllers`、`routes`，以及 `frontend/src/api/research.js`、`stores/research.js` 和 `pages`。`Source.key` 与 `(Record.source_id, reference)` 为自然键；来源改名不会修改已冻结任务输入。业务说明在创建时保存结果快照，批准后从该快照导出。

## 算法与口径

RandomForest回归、训练内特征子集交叉验证与留出集MAE，全部计算位于 `backend/src/services/algorithm.py`，前端不拟合模型、不生成模拟结果。输入是冻结业务观察及参数，输出summary、专属诊断、逐行计算明细、seed与输入SHA256。CSV包含全部结果行；页面仅预览前30行。参数：

- `trees`：森林树数，默认 `60`。
- `depth`：最大深度，默认 `6`。

先固定留出集，特征选择与交叉验证仅在训练行完成；留出集只作最后一次评估。置换重要性是模型依赖度，不是因果贡献。

## 冲突复核算法与口径

同温同风（同 `temperature`、同 `airflow`）的一组试件内，若某件的初始湿度与厚度都不低于另一件、实测时长却严格更短，则记为一对冲突。复核任务求**最少的待复核试件**，移开后其余试件两两无冲突，全部计算位于 `backend/src/services/review_algorithm.py`（纯标准库，worker 调用）。

建模：令 H=湿度、D=厚度、L=实测时长，定义严格偏序 a≺b ⟺ H_a≤H_b 且 D_a≤D_b 且 L_a>L_b；一对试件冲突 ⟺ 二者可比。剩余集合无冲突 ⟺ 剩余集合是反链，故最少待复核数 = n − 最大反链。由 Dilworth 定理，最大反链 = 最小链覆盖的链数 c，答案即 n − c。

- **不逐对枚举**：最小链覆盖经二部匹配求解（边 u→v ⟺ u≺v，c = n − 最大匹配）。u 的邻点是三维正交范围 {H≥H_u, D≥D_u, L<L_u}，用 KD 树按需做范围报告；贪心极大匹配（最受约束优先）+ Hopcroft–Karp 增广，同工况上万件单组约十数秒出解，不做 O(n²) 逐对扫描。
- **冲突链即最优性证明**：最小链覆盖给出 c 条互不相交、覆盖全部 n 件的冲突链；链上两两冲突，任何方案在每条长 k 的链中至多留 1 件，至少移出 n−c 件；本方案恰好移出 n−c 件，下界与方案相等，再少不行。
- **独立校验**：`verify_group` 以排序+树状数组在 O(n log n) 内确认保留集无冲突、链互不相交且全覆盖、最优性等式成立；测试另用指数级暴力解逐案对照。

任务语义（`backend/src/services/review.py`）：受理事务内读取样本水位线并枚举全部工况组，**冻结样本**；之后补录的样本 id 更大，只进入下一次核查。每组的名单与冲突链整体落库为一个检查点，worker 中断后从未完成的组续跑；组写入以 `pending` 状态守卫，重试不重复计入，任务总数由组行实时汇总。任务完成前，接口与页面只返回进度（x/y 组），**各组齐备才展示总数、名单和冲突链**。接口：`GET/POST /review-runs`、`GET /review-runs/{id}`、`POST /review-runs/{id}/retry`（仅失败任务）。

失败态：业务字段缺失/非有限数值、参数越界、样本不足、退化特征或结构错误均明确拒绝或将任务标failed，错误消息保存在任务中。可回到来源或配置修正后重新执行，历史失败记录保留。

已知边界：只比较三个预定义特征子集，超参数由分析前固定；留出集不用于选择。置换重要性来自最后留出集，不可反复依据它改模再宣称独立验证。不含预测区间，不用于外推工艺。

## 演示数据与一天剧本

固定seed=42，4个来源 ` HX-202611-01/02/03/04`，每来源240条，共960条业务记录；三个账号和首轮种子任务。`HX-202611-04 工况复核组` 使用离散设定温度（65/75/85 ℃）与风量（2.5/3.0/3.5 m³/min），同温同风成组，供冲突复核演示。自然键检查后仅插入缺失数据，连跑seed不重复、不覆盖用户修改。`SEED_DEMO=true` 时启动自动执行；失败记录日志但不阻塞服务。首轮任务同样由独立worker真实计算。

1. 09:00 负责人查看四份来源和960条样本。
2. 09:10 分析师选择 `HX-202611-01 秋季常规组`，核对 `drying-forest-202611-01.csv`。
3. 09:15 在“干燥研究”核对初始湿度 %（`moisture`）、厚度 mm（`thickness`）、设定温度 ℃（`temperature`）、风量 m³/min（`airflow`）、实测时长 min（`minutes`），追加编号 `HX-NEW-001` 的样本。
4. 09:20 新建“十一月复核研究”，按默认参数冻结241条输入。
5. 09:21 独立worker消费Redis队列，返回实算结果与输入校验和。
6. 09:25 分析师查看专属诊断及逐行结果，并下载CSV复算。
7. 09:30 提交“十一月业务复核意见”，负责人核对并批准。
8. 09:35 在“冲突复核”受理 `HX-202611-04 工况复核组`：冻结当前样本，worker逐组核查；齐备后页面展示总数、最少待复核名单与互不重叠的冲突链，随后补录的样本只进入下一次核查。
9. 09:40 viewer下载批准快照，查看审计中的worker完成与批准事件。

## 目录与本地开发

```text
frontend/src/api, stores, types, components/common, hooks, pages, router, utils, constants
backend/src/routes, controllers, services, models, schemas, middlewares, utils, constants, config
backend/scripts/seed_demo.py
backend/migrations/001_initial.sql
```

本地后端：Python3.11虚拟环境安装 `backend/requirements.txt`，设置 `DATABASE_URL`、`REDIS_URL`、`ARTIFACT_DIR`，在backend目录运行 `uvicorn src.main:app --reload`；另开终端运行 `python -m src.worker`。原生前端无需编译，推荐以Compose Nginx访问，保证 `/api/` 代理。前端为Google Fonts双字体、oklch色板，离线字体回退serif；1280/768/390响应布局。

| 环境变量 | 默认与说明 |
|---|---|
| COMPOSE_PROJECT_NAME | `drying-forest`，隔离容器/网络/命名卷 |
| FRONTEND_PORT / BACKEND_PORT / DB_PORT | `18248 / 19248 / 17748` |
| SEED_DEMO | `true` |
| JWT_SECRET | `.env.example`演示值，部署前修改 |
| DATABASE_URL / REDIS_URL | Compose内部连接，定义在compose的env锚点 |
| ARTIFACT_DIR | `/artifacts`，后端worker共享 |

共享枚举：`backend/src/constants/enums.py` 与 `frontend/src/constants/enums.js` 都定义 admin/analyst/viewer、queued/running/completed/failed、draft/approved。后端service检查对应字符串；README为全量值说明。共享前端组件table/status/chart/business跨总览、结果、配置与复核复用；actions hook和format utils负责交互/格式。后端errors与rate_limit是两个中间件。

测试及截图在批根 `test-artifacts/gb-0044/`，不混入源码。独立算法oracle、HTTP完整流程和真实Playwright浏览器流程见该目录 `TESTING.md`。License：MIT。
