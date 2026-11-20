"""冲突复核服务层集成测试（在 backend 容器内运行，依赖真实 DB 与 Redis）。

运行：
    docker compose exec -T backend python -m unittest /tests/test_review_service.py -v

覆盖语义：
- 受理时冻结样本：创建后补录的样本不进入本次核查，只进入下一次；
- 中断后从未完成的组续跑：失败处续跑，已完成组不重算（重试不重复计入）；
- 各组齐备才展示：完成前详情不含名单与冲突链，完成后才有；
- 每组名单与冲突链一起保存，且通过独立校验 verify_group。
"""

import sys
import types
import unittest
import uuid

sys.path.insert(0, '/app')

from sqlalchemy import select, func

from src.config.database import Base, engine, Session
from src.models.source import Source
from src.models.record import Record
from src.models.review import ReviewRun, ReviewGroup
from src.services import review as review_svc
from src.services.review_algorithm import verify_group, brute_force_min_review

USER = types.SimpleNamespace(email='review-test@local')


def make_input(source_id, name):
    return types.SimpleNamespace(source_id=source_id, name=name)


class ReviewServiceTest(unittest.TestCase):
    """每个用例一个独立来源，结束清理。"""

    def setUp(self):
        Base.metadata.create_all(engine)
        self.key = 'TEST-REVIEW-' + uuid.uuid4().hex[:8]
        with Session() as db:
            s = Source(key=self.key, name=self.key, filename='t.csv', description='')
            db.add(s)
            db.commit()
            self.source_id = s.id

    def tearDown(self):
        with Session() as db:
            run_ids = db.scalars(select(ReviewRun.id).where(ReviewRun.source_id == self.source_id)).all()
            db.query(ReviewGroup).filter(ReviewGroup.run_id.in_(run_ids)).delete(synchronize_session=False)
            db.query(ReviewRun).filter(ReviewRun.source_id == self.source_id).delete(synchronize_session=False)
            db.query(Record).filter(Record.source_id == self.source_id).delete(synchronize_session=False)
            db.query(Source).filter(Source.id == self.source_id).delete(synchronize_session=False)
            db.commit()

    def add_records(self, rows):
        """rows: [(reference, moisture, thickness, temperature, airflow, minutes)]"""
        with Session() as db:
            for ref, m, t, temp, a, mins in rows:
                db.add(Record(source_id=self.source_id, reference=ref, values={
                    'moisture': m, 'thickness': t, 'temperature': temp,
                    'airflow': a, 'minutes': mins}))
            db.commit()
        return len(rows)

    def create_run(self, name='核查'):
        with Session() as db:
            return review_svc.create_review_run(db, USER, make_input(self.source_id, name))

    def run_to_completion(self, run_id):
        review_svc.process_review_run(run_id)
        with Session() as db:
            return review_svc.get_review_run(db, run_id)

    def sample_rows(self):
        rows = []
        # 组 A：70℃ / 3.0 —— 含冲突
        rows += [(f'A{i}', 30 + i, 4.0 + 0.1 * i, 70.0, 3.0, 90.0 - 3 * i) for i in range(6)]
        # 组 B：80℃ / 3.0 —— 时长随湿度厚度上升，无冲突
        rows += [(f'B{i}', 25 + i, 3.0 + 0.1 * i, 80.0, 3.0, 60.0 + 2 * i) for i in range(5)]
        # 组 C：70℃ / 4.0 —— 单件
        rows += [('C0', 33.0, 4.5, 70.0, 4.0, 70.0)]
        return rows

    def test_freeze_and_next_run(self):
        """受理时冻结样本；并发补录只供下次核查。"""
        rows = self.sample_rows()
        self.add_records(rows)
        run1 = self.create_run('第一轮')
        self.assertEqual(run1['frozen_max_id'], max_id_after := self._max_record_id())
        # 受理后补录一件同工况样本
        self.add_records([('A9', 40.0, 5.0, 70.0, 3.0, 50.0)])
        detail1 = self.run_to_completion(run1['id'])
        self.assertEqual(detail1['state'], 'completed')
        self.assertEqual(detail1['totals']['samples'], len(rows))  # 不含补录
        run2 = self.create_run('第二轮')
        self.assertGreater(run2['frozen_max_id'], max_id_after)
        detail2 = self.run_to_completion(run2['id'])
        self.assertEqual(detail2['totals']['samples'], len(rows) + 1)  # 补录进入下次核查

    def _max_record_id(self):
        with Session() as db:
            return db.scalar(select(func.max(Record.id)).where(Record.source_id == self.source_id)) or 0

    def test_gating_before_completion(self):
        """各组齐备前，详情只给进度；齐备后才给名单与冲突链。"""
        self.add_records(self.sample_rows())
        run = self.create_run()
        with Session() as db:
            early = review_svc.get_review_run(db, run['id'])
        self.assertNotEqual(early['state'], 'completed')
        self.assertIsNone(early['groups'])
        self.assertNotIn('totals', early)
        detail = self.run_to_completion(run['id'])
        self.assertIsNotNone(detail['groups'])
        self.assertEqual(detail['done_groups'], detail['total_groups'])
        self.assertEqual(detail['totals']['samples'], 12)

    def test_resume_and_idempotency(self):
        """组 1 完成后模拟中断失败；续跑不重算已完成组，重试不重复计入。"""
        self.add_records(self.sample_rows())
        run = self.create_run()
        calls = {'n': 0}
        orig = review_svc.analyze_group

        def flaky(points):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('模拟中断')
            return orig(points)

        review_svc.analyze_group = flaky
        try:
            review_svc.process_review_run(run['id'])
        finally:
            review_svc.analyze_group = orig
        with Session() as db:
            failed = review_svc.get_review_run(db, run['id'])
            self.assertEqual(failed['state'], 'failed')
            self.assertEqual(failed['done_groups'], 1)
            first = db.scalars(select(ReviewGroup).where(
                ReviewGroup.run_id == run['id'], ReviewGroup.state == 'done')).one()
            marker = (first.updated_at, first.payload)
        # 失败任务经 retry 回到队列，续跑只算剩余组
        with Session() as db:
            review_svc.retry_review_run(db, USER, run['id'])
        detail = self.run_to_completion(run['id'])
        self.assertEqual(detail['state'], 'completed')
        self.assertEqual(detail['done_groups'], detail['total_groups'])
        with Session() as db:
            first_after = db.get(ReviewGroup, first.id)
            self.assertEqual((first_after.updated_at, first_after.payload), marker,
                             '已完成组不得被重算覆盖（重试不重复计入）')
        # 再次执行是空操作，结果不变
        again = self.run_to_completion(run['id'])
        self.assertEqual(again['totals'], detail['totals'])

    def test_payload_correctness(self):
        """每组名单与冲突链一起保存，且通过独立校验与暴力对照。"""
        rows = self.sample_rows()
        self.add_records(rows)
        run = self.create_run()
        detail = self.run_to_completion(run['id'])
        with Session() as db:
            records = {r.reference: r for r in db.scalars(
                select(Record).where(Record.source_id == self.source_id)).all()}
        total_review = 0
        for g in detail['groups']:
            points = [(r.reference, r.values['moisture'], r.values['thickness'], r.values['minutes'])
                      for r in records.values()
                      if r.values['temperature'] == g['temperature'] and r.values['airflow'] == g['airflow']]
            verify_group(points, g['payload'])
            self.assertEqual(len(g['payload']['review']), brute_force_min_review(points))
            total_review += len(g['payload']['review'])
        self.assertEqual(detail['totals']['review'], total_review)

    def test_retry_only_for_failed(self):
        self.add_records(self.sample_rows())
        run = self.create_run()
        with Session() as db:
            with self.assertRaises(Exception) as ctx:
                review_svc.retry_review_run(db, USER, run['id'])
            self.assertIn('仅失败任务', str(ctx.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
