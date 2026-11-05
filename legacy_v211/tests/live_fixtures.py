"""외부 조회 성공을 가장하지 않는 명시적 모의 행사 조건."""
from datetime import datetime, timedelta, timezone


def verified_offer(promotion='1+1'):
    today = datetime.now(timezone(timedelta(hours=9))).date()
    return dict(promotion_start_date=(today-timedelta(days=1)).isoformat(),
                promotion_end_date=(today+timedelta(days=1)).isoformat(),
                promotion_buy_quantity=int(promotion[0]), promotion_gift_quantity=1,
                promotion_member_condition='없음', promotion_card_condition='없음',
                promotion_cross_gift=False, promotion_conditions_verified=True)
