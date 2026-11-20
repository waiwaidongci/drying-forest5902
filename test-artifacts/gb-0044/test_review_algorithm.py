"""冲突复核核心算法的独立 oracle 测试（纯标准库，可直接运行）。

运行：python3 test_review_algorithm.py
（从本目录执行；backend/src 会被加入 sys.path，不依赖任何第三方包。）

oracle 说明：
- 小规模（n<=11）与指数级暴力解逐案对照最少待复核数；
- 各规模都用 verify_group 独立校验：保留集无冲突、冲突链互不相交且
  覆盖全部、最优性等式（移出数 = n - 链数）成立；
- 性能冒烟用 n=10000 的同工况大组，确认无需逐对枚举即可出解。
"""

import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

from src.services.review_algorithm import (
    analyze_group, verify_group, brute_force_min_review,
)


def random_points(rng, n, h_range, d_range, l_range):
    points = []
    for i in range(n):
        h = round(rng.uniform(*h_range), 2)
        d = round(rng.uniform(*d_range), 2)
        l = round(rng.uniform(*l_range), 2)
        points.append((f'P{i:05d}', h, d, l))
    return points


def discrete_points(rng, n, hs, ds, l_fn):
    points = []
    for i in range(n):
        h = rng.choice(hs)
        d = rng.choice(ds)
        points.append((f'P{i:05d}', h, d, round(l_fn(h, d), 2)))
    return points


class BruteForceAgreementTest(unittest.TestCase):
    """小规模随机组：算法结果必须与暴力解一致，且通过独立校验。"""

    def test_random_small_groups(self):
        rng = random.Random(20261120)
        cases = 0
        for trial in range(400):
            n = rng.randrange(1, 12)
            # 小取值域制造大量相等/并列，覆盖边界
            points = random_points(rng, n, (10, 30), (1, 5), (40, 90))
            payload = analyze_group(points)
            verify_group(points, payload)
            expect = brute_force_min_review(points)
            self.assertEqual(len(payload['review']), expect,
                             f'第{trial}例不一致: {points}')
            cases += 1
        self.assertEqual(cases, 400)

    def test_tie_heavy_groups(self):
        rng = random.Random(7)
        for _ in range(300):
            n = rng.randrange(1, 12)
            points = [
                (f'P{i:03d}', rng.choice([1.0, 2.0]), rng.choice([1.0, 2.0]),
                 rng.choice([5.0, 6.0, 7.0]))
                for i in range(n)
            ]
            payload = analyze_group(points)
            verify_group(points, payload)
            self.assertEqual(len(payload['review']),
                             brute_force_min_review(points))


class SpecificCasesTest(unittest.TestCase):

    def test_empty_and_single(self):
        payload = analyze_group([])
        self.assertEqual(payload['n'], 0)
        self.assertEqual(payload['proof']['removals'], 0)
        payload = analyze_group([('A', 30.0, 4.0, 80.0)])
        self.assertEqual(payload['keep'], ['A'])
        self.assertEqual(payload['review'], [])
        self.assertEqual(len(payload['chains']), 1)

    def test_all_identical_no_conflict(self):
        points = [(f'P{i}', 35.0, 4.0, 80.0) for i in range(6)]
        payload = analyze_group(points)
        verify_group(points, payload)
        self.assertEqual(payload['review'], [])
        self.assertEqual(len(payload['chains']), 6)

    def test_total_order_keep_one(self):
        # 湿度/厚度递增、时长严格递减：全序冲突，只能保留 1 件
        points = [(f'P{i}', 10.0 + i, 1.0 + i, 100.0 - i) for i in range(8)]
        payload = analyze_group(points)
        verify_group(points, payload)
        self.assertEqual(len(payload['keep']), 1)
        self.assertEqual(len(payload['review']), 7)
        self.assertEqual(len(payload['chains']), 1)
        self.assertEqual(len(payload['chains'][0]), 8)

    def test_monotone_duration_no_conflict(self):
        # 时长随湿度/厚度单调上升：无冲突
        points = [(f'P{i}', 10.0 + i, 1.0 + i, 50.0 + 2 * i) for i in range(9)]
        payload = analyze_group(points)
        verify_group(points, payload)
        self.assertEqual(payload['review'], [])

    def test_same_hd_different_duration_conflicts(self):
        points = [('A', 30.0, 4.0, 80.0), ('B', 30.0, 4.0, 75.0)]
        payload = analyze_group(points)
        verify_group(points, payload)
        self.assertEqual(len(payload['review']), 1)

    def test_bipartite_k22(self):
        # {1,2} 与 {3,4} 交叉可比：最大反链 2，最少移出 2
        points = [
            ('P1', 0.0, 1.0, 9.0), ('P2', 1.0, 0.0, 8.0),
            ('P3', 1.0, 2.0, 5.0), ('P4', 2.0, 1.0, 4.0),
        ]
        payload = analyze_group(points)
        verify_group(points, payload)
        self.assertEqual(len(payload['review']), 2)
        self.assertEqual(len(payload['chains']), 2)

    def test_incomparable_pairs_kept(self):
        # 湿度升、厚度降：互不支配，无论时长如何都不冲突
        points = [('A', 1.0, 2.0, 5.0), ('B', 2.0, 1.0, 0.5)]
        payload = analyze_group(points)
        verify_group(points, payload)
        self.assertEqual(payload['review'], [])

    def test_negative_and_zero_values(self):
        # 校验器不得对取值范围做隐含假设（含负值/零）
        points = [(f'P{i}', 35.0, 4.0, float(100 - i)) for i in range(200)]
        payload = analyze_group(points)
        verify_group(points, payload)
        self.assertEqual(len(payload['keep']), 1)
        rng = random.Random(3)
        points = [(f'P{i}', round(rng.uniform(-5, 5), 2), round(rng.uniform(-2, 2), 2),
                   round(rng.uniform(-50, 50), 2)) for i in range(300)]
        payload = analyze_group(points)
        verify_group(points, payload)


class LargerGroupInvariantTest(unittest.TestCase):

    def test_medium_groups(self):
        rng = random.Random(99)
        for n in (50, 200, 500):
            points = random_points(rng, n, (20, 60), (1, 8), (30, 120))
            payload = analyze_group(points)
            verify_group(points, payload)
            self.assertEqual(len(payload['keep']), len(payload['chains']))
            self.assertEqual(len(payload['review']),
                             n - len(payload['chains']))

    def test_discrete_setpoints_with_noise(self):
        # 模拟真实工况：离散湿度/厚度档位 + 单调趋势 + 噪声
        rng = random.Random(42)
        points = discrete_points(
            rng, 800, [20.0, 30.0, 40.0, 50.0], [2.0, 4.0, 6.0],
            lambda h, d: 10 + 1.5 * h + 8 * d + rng.gauss(0, 6))
        payload = analyze_group(points)
        verify_group(points, payload)


class PerformanceSmokeTest(unittest.TestCase):
    """同工况上万件：无需逐对枚举（n²/2 = 五千万对）也能出解。"""

    def test_ten_thousand_one_group(self):
        rng = random.Random(2026)
        n = 10000
        points = random_points(rng, n, (20, 60), (1, 8), (30, 120))
        started = time.perf_counter()
        payload = analyze_group(points)
        elapsed = time.perf_counter() - started
        verify_group(points, payload)
        print(f'\n[smoke] n={n} 用时 {elapsed:.2f}s '
              f'保留 {len(payload["keep"])} 待复核 {len(payload["review"])} '
              f'冲突链 {len(payload["chains"])} 条')
        self.assertLess(elapsed, 60, '万件单组应在秒级到一分钟内完成')


if __name__ == '__main__':
    unittest.main(verbosity=2)
