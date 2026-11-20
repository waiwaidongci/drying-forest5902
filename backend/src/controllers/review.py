from ..services import review
from ..utils.serialization import row
class ReviewController:
    list=staticmethod(review.list_review_runs)
    create=staticmethod(review.create_review_run)
    retry=staticmethod(review.retry_review_run)
    @staticmethod
    def get(db,id):return review.get_review_run(db,id)
