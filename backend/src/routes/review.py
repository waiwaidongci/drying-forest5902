from fastapi import APIRouter,Depends
from ..config.database import db_session
from ..schemas.review import ReviewRunInput
from ..services.auth import current_user,writer
from ..controllers.review import ReviewController as C
router=APIRouter()
@router.get('/review-runs')
def review_runs(db=Depends(db_session),u=Depends(current_user)):return C.list(db)
@router.post('/review-runs')
def create_review(data:ReviewRunInput,db=Depends(db_session),u=Depends(writer)):return C.create(db,u,data)
@router.get('/review-runs/{id}')
def review_detail(id:int,db=Depends(db_session),u=Depends(current_user)):return C.get(db,id)
@router.post('/review-runs/{id}/retry')
def retry_review(id:int,db=Depends(db_session),u=Depends(writer)):return C.retry(db,u,id)
