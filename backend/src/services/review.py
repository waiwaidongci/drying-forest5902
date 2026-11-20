"""同温同风冲突复核的任务生命周期。

语义约定：
- 受理时冻结样本：单条语句同一快照内读取当前最大样本 id 作为水位线，
  并枚举出全部 (设定温度, 风量) 组；之后补录的样本 id 更大，
  不会进入本次核查，只供下一次核查。
- 每组名单和冲突链一起保存：分析结果整体写入该组 payload，
  每组一个事务检查点；中断后从未完成的组续跑。
- 重试不重复计入：组的写入以 state='pending' 为守卫，已完成组跳过；
  任务总数由组行实时汇总，不做增量计数。
- 各组齐备才展示：任务完成前，详情只返回进度，不返回名单与冲突链。
"""

import logging
from fastapi import HTTPException
from redis import Redis
from sqlalchemy import select,func,update,cast,Float
from ..config.database import Session
from ..config.settings import REDIS_URL
from ..models.review import ReviewRun,ReviewGroup
from ..models.record import Record
from ..utils.serialization import row,now
from .sources import source_or_404
from .audit import log
from .review_algorithm import analyze_group

queue=Redis.from_url(REDIS_URL)

def group_key(t,a):return f'{t!r}|{a!r}'

def _enqueue(id):
    queue.rpush('review:queue',id)

def create_review_run(db,user,data):
    s=source_or_404(db,data.source_id)
    # 冻结：单条语句同一快照内取水位线并枚举组；之后补录的样本 id 更大，
    # 不会进入本次核查，只供下一次核查
    tcol=cast(Record.values['temperature'].as_string(),Float)
    acol=cast(Record.values['airflow'].as_string(),Float)
    wm=select(func.max(Record.id)).where(Record.source_id==s.id).scalar_subquery()
    groups=db.execute(select(tcol,acol,func.count(),wm.label('wm')).where(Record.source_id==s.id,Record.id<=wm).group_by(tcol,acol).order_by(tcol,acol)).all()
    max_id=groups[0][3] if groups else 0
    run=ReviewRun(source_id=s.id,name=data.name,state='queued',frozen_max_id=max_id,created_at=now())
    db.add(run);db.flush()
    for t,a,cnt,_ in groups:
        db.add(ReviewGroup(run_id=run.id,group_key=group_key(t,a),temperature=t,airflow=a,state='pending',expected_n=cnt,updated_at=now()))
    log(db,user,'review.queued',run.id,f'{data.name} · 冻结{len(groups)}组 · 水位线{max_id}')
    db.commit()
    try:_enqueue(run.id)
    except Exception:
        run.state='failed';run.error='队列暂不可用，请重新创建任务';db.commit();raise HTTPException(503,run.error)
    return row(run)

def _progress(db,run_id):
    total=db.scalar(select(func.count()).select_from(ReviewGroup).where(ReviewGroup.run_id==run_id))
    done=db.scalar(select(func.count()).select_from(ReviewGroup).where(ReviewGroup.run_id==run_id,ReviewGroup.state=='done'))
    return total,done

def list_review_runs(db):
    out=[]
    for r in db.scalars(select(ReviewRun).order_by(ReviewRun.id.desc())):
        d=row(r);d['total_groups'],d['done_groups']=_progress(db,r.id);out.append(d)
    return out

def get_review_run(db,id):
    run=db.get(ReviewRun,id)
    if not run:raise HTTPException(404,'未找到复核任务')
    d=row(run)
    groups=db.scalars(select(ReviewGroup).where(ReviewGroup.run_id==id).order_by(ReviewGroup.id)).all()
    d['total_groups']=len(groups)
    d['done_groups']=sum(1 for g in groups if g.state=='done')
    if run.state!='completed':
        d['groups']=None  # 各组齐备前不展示名单与冲突链
        return d
    d['groups']=[row(g) for g in groups]
    totals={'samples':0,'keep':0,'review':0,'chains':0}
    for g in groups:
        p=g.payload
        totals['samples']+=p['n'];totals['keep']+=len(p['keep'])
        totals['review']+=len(p['review']);totals['chains']+=len(p['chains'])
    d['totals']=totals
    return d

def retry_review_run(db,user,id):
    run=db.get(ReviewRun,id)
    if not run:raise HTTPException(404,'未找到复核任务')
    if run.state!='failed':raise HTTPException(409,'仅失败任务需要重新执行')
    run.state='queued';run.error=''
    log(db,user,'review.retried',run.id,run.name);db.commit()
    try:_enqueue(run.id)
    except Exception:
        run.state='failed';run.error='队列暂不可用，请稍后重试';db.commit();raise HTTPException(503,run.error)
    return row(run)

def _load_group_points(db,run,grp):
    tcol=cast(Record.values['temperature'].as_string(),Float)
    acol=cast(Record.values['airflow'].as_string(),Float)
    rows=db.scalars(select(Record).where(Record.source_id==run.source_id,Record.id<=run.frozen_max_id,tcol==grp.temperature,acol==grp.airflow).order_by(Record.id)).all()
    return [(r.reference,r.values['moisture'],r.values['thickness'],r.values['minutes']) for r in rows]

def process_review_run(run_id):
    """worker 侧执行：逐组计算、逐组落库；幂等，可中断续跑。"""
    with Session() as db:
        run=db.get(ReviewRun,run_id)
        if not run or run.state not in ('queued','running'):return
        if run.state=='queued':run.state='running';db.commit()
        try:
            while True:
                grp=db.scalar(select(ReviewGroup).where(ReviewGroup.run_id==run_id,ReviewGroup.state=='pending').order_by(ReviewGroup.id).limit(1))
                if grp is None:break
                points=_load_group_points(db,run,grp)
                if len(points)!=grp.expected_n:
                    # 受理瞬间有补录事务乱序提交：以水位线口径为准修正预期数
                    logging.warning('review group %s of run %s: frozen count %s != expected %s',grp.group_key,run_id,len(points),grp.expected_n)
                    grp.expected_n=len(points)
                payload=analyze_group(points)
                # 幂等落库：仅当仍是 pending 才写入，重试不会重复计入
                db.execute(update(ReviewGroup).where(ReviewGroup.id==grp.id,ReviewGroup.state=='pending').values(state='done',payload=payload,updated_at=now()))
                db.commit()  # 每组一个检查点
            # 全部组齐备才完成任务（守卫保证只翻转一次）
            db.execute(update(ReviewRun).where(ReviewRun.id==run_id,ReviewRun.state=='running').values(state='completed',finished_at=now()))
            log(db,'worker','review.completed',run_id,run.name)
            db.commit()
        except Exception as e:
            logging.exception('review run failed')
            db.rollback()
            run=db.get(ReviewRun,run_id)
            run.state='failed';run.error=str(e)[:2000]
            log(db,'worker','review.failed',run_id,run.error)
            db.commit()

def requeue_interrupted():
    """worker 启动时补投排队中/计算中的任务：中断后从未完成的组续跑。"""
    try:
        with Session() as db:
            ids=db.scalars(select(ReviewRun.id).where(ReviewRun.state.in_(('queued','running')))).all()
        for rid in ids:_enqueue(rid)
        if ids:logging.info('requeued %d interrupted review runs',len(ids))
    except Exception:logging.exception('requeue interrupted review runs failed')
