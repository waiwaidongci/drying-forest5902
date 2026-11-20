# 测试说明（gb-0044）

本目录保存冲突复核特性的全部测试，不混入源码树。

## 1. 核心算法独立 oracle（纯标准库，随处可跑）

```bash
python3 test_review_algorithm.py
```

- 400 组随机小组 + 300 组并列密集小组：与指数级暴力解逐案对照「最少待复核数」；
- 全部结果经 `verify_group` 独立校验：保留集无冲突、冲突链互不相交且覆盖全部、
  最优性等式（移出数 = n − 链数）成立；
- 性能冒烟：同工况 n=10000 单组，无需逐对枚举（n²/2≈五千万对）即可出解。

## 2. 服务层集成测试（容器内，真实 DB + Redis）

```bash
docker compose up -d --build
docker compose exec -T backend python -m unittest /tests/test_review_service.py -v
```

覆盖：受理冻结水位线、补录只进下次核查、中断后续跑（已完成组不重算，
重试不重复计入）、齐备前详情不返回名单与冲突链、payload 通过独立校验。

## 3. HTTP 全流程测试（宿主机黑盒）

```bash
python3 test_review_http.py    # 默认 http://localhost:19248
```

登录 → 建来源 → 录样本 → 受理核查 → 轮询到齐备（途中断言不展示名单/冲突链）
→ 核对总数、名单、冲突链与最优性等式 → 补录只进下次核查 → 访客 403。
