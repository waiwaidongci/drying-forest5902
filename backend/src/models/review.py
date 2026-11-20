from sqlalchemy import Column,Integer,String,JSON,Text,ForeignKey,UniqueConstraint,Float
from ..config.database import Base
class ReviewRun(Base):
    """一次同温同风冲突复核任务。受理时以 frozen_max_id 冻结样本水位线，
    之后补录的样本 id 更大，只进入下一次核查。"""
    __tablename__='review_runs'
    id=Column(Integer,primary_key=True)
    source_id=Column(Integer,ForeignKey('drying_studies.id'),nullable=False)
    name=Column(String(160),nullable=False)
    state=Column(String(20),default='queued')  # queued/running/completed/failed
    frozen_max_id=Column(Integer,nullable=False)
    error=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
    finished_at=Column(String(40),default='')
class ReviewGroup(Base):
    """组（设定温度, 风量）的核查结果：名单与冲突链一起保存，
    每组一个检查点，中断后从未完成的组续跑，重试不重复计入。"""
    __tablename__='review_groups'
    __table_args__=(UniqueConstraint('run_id','group_key',name='uq_review_group'),)
    id=Column(Integer,primary_key=True)
    run_id=Column(Integer,ForeignKey('review_runs.id'),nullable=False)
    group_key=Column(String(160),nullable=False)
    temperature=Column(Float,nullable=False)
    airflow=Column(Float,nullable=False)
    state=Column(String(20),default='pending')  # pending/done
    expected_n=Column(Integer,nullable=False)
    payload=Column(JSON,nullable=True)  # {n, keep, review, chains, proof}
    updated_at=Column(String(40),default='')
