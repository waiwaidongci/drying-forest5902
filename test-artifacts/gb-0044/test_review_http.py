"""冲突复核 HTTP 全流程测试（宿主机运行，打真实 Compose 服务）。

运行：
    docker compose up -d --build
    python3 test_review_http.py            # 默认打 http://localhost:19248
    BACKEND_URL=http://localhost:19248 python3 test_review_http.py

覆盖：登录 → 建来源 → 录样本 → 受理核查（冻结）→ 轮询到齐备 →
核对总数/名单/冲突链与最优性等式 → 补录只进下次核查 → 权限边界。
仅使用标准库 urllib。
"""

import json
import os
import time
import unittest
import urllib.request
import uuid

BASE = os.getenv('BACKEND_URL', 'http://localhost:19248')
PASSWORD = 'Research2026!'


def call(method, path, token=None, body=None, expect=200):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + token} if token else {})})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = None
        try:
            detail = json.loads(e.read())
        except Exception:
            pass
        if e.code != expect:
            raise AssertionError(f'{method} {path} -> {e.code} ({detail}), 期望 {expect}')
        return e.code, detail


class ReviewHttpTest(unittest.TestCase):
    """黑盒全流程：与真实后端、worker、数据库交互。"""

    @classmethod
    def setUpClass(cls):
        _, cls.analyst = call('POST', '/auth/login', body={
            'email': 'analyst@drying-forest.demo', 'password': PASSWORD})
        _, cls.viewer = call('POST', '/auth/login', body={
            'email': 'viewer@drying-forest.demo', 'password': PASSWORD})
        cls.token = cls.analyst['token']
        cls.vtoken = cls.viewer['token']
        # 独立来源，避免与演示数据互相影响
        cls.source_name = 'HTTP复核-' + uuid.uuid4().hex[:8]
        _, src = call('POST', '/sources', cls.token, {
            'name': cls.source_name, 'filename': 'http-review.csv', 'description': ''})
        cls.source_id = src['id']
        cls.rows = []
        for i in range(6):   # 组 70℃/3.0：湿度厚度递增、时长递减，两两冲突
            cls.rows.append({'reference': f'HXA{i}', 'values': {
                'moisture': 30 + i, 'thickness': 4.0 + 0.1 * i,
                'temperature': 70.0, 'airflow': 3.0, 'minutes': 90.0 - 3 * i}})
        for i in range(4):   # 组 80℃/3.0：时长单调，无冲突
            cls.rows.append({'reference': f'HXB{i}', 'values': {
                'moisture': 25 + i, 'thickness': 3.0 + 0.1 * i,
                'temperature': 80.0, 'airflow': 3.0, 'minutes': 60.0 + 2 * i}})
        call('POST', f"/sources/{cls.source_id}/records", cls.token,
             {'records': cls.rows}, expect=200)

    def wait_done(self, run_id, timeout=120):
        """轮询到完成；途中只要未完成，就必须不展示名单与冲突链。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, d = call('GET', f'/review-runs/{run_id}', self.token)
            if d['state'] == 'completed':
                return d
            self.assertIsNone(d['groups'], '各组齐备前不得展示名单与冲突链')
            self.assertNotIn('totals', d)
            time.sleep(0.5)
        self.fail('复核任务超时未完成')

    def test_full_flow(self):
        # 访客无权受理
        call('POST', '/review-runs', self.vtoken,
             {'source_id': self.source_id, 'name': '访客核查'}, expect=403)
        # 受理并冻结
        _, run = call('POST', '/review-runs', self.token,
                      {'source_id': self.source_id, 'name': 'HTTP 核查'})
        detail = self.wait_done(run['id'])
        self.assertEqual(detail['done_groups'], detail['total_groups'])
        self.assertEqual(detail['totals']['samples'], len(self.rows))
        # 组 70℃/3.0 两两冲突：6 件只能留 1，移出 5；组 80℃/3.0 无冲突
        self.assertEqual(detail['totals']['review'], 5)
        self.assertEqual(detail['totals']['keep'], len(self.rows) - 5)
        for g in detail['groups']:
            p = g['payload']
            self.assertEqual(p['proof']['lower_bound'], p['proof']['removals'])
            self.assertEqual(p['proof']['covered'], p['n'])
            covered = {nd['reference'] for c in p['chains'] for nd in c}
            self.assertEqual(len(covered), p['n'], '冲突链互不相交且覆盖全组')
        # 补录同工况样本：只进入下一次核查
        call('POST', f"/sources/{self.source_id}/records", self.token,
             {'records': [{'reference': 'HXA9', 'values': {
                 'moisture': 40.0, 'thickness': 5.0, 'temperature': 70.0,
                 'airflow': 3.0, 'minutes': 50.0}}]})
        _, run2 = call('POST', '/review-runs', self.token,
                       {'source_id': self.source_id, 'name': 'HTTP 核查二'})
        detail2 = self.wait_done(run2['id'])
        self.assertEqual(detail2['totals']['samples'], len(self.rows) + 1)
        _, again = call('GET', f"/review-runs/{run['id']}", self.token)
        self.assertEqual(again['totals']['samples'], len(self.rows),
                         '旧任务结果必须保持冻结口径')


if __name__ == '__main__':
    unittest.main(verbosity=2)
