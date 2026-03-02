from __future__ import annotations

from dataclasses import dataclass

from ..config import AgentSettings


@dataclass(slots=True)
class RewardInputs:
    cash_collected_cents: int = 0
    positive_replies: int = 0
    replies: int = 0
    unsubscribes: int = 0
    hard_bounces: int = 0
    spam_complaints: int = 0
    negative_replies: int = 0


def compute_reward(settings: AgentSettings, inputs: RewardInputs) -> float:
    cash_component = (inputs.cash_collected_cents / 100.0) * settings.metric_weight_cash
    positive_reply_component = inputs.positive_replies * settings.metric_weight_positive_reply
    reply_component = inputs.replies * settings.metric_weight_reply
    penalty = (
        inputs.unsubscribes * settings.penalty_unsubscribe
        + inputs.hard_bounces * settings.penalty_bounce
        + inputs.spam_complaints * settings.penalty_spam_complaint
        + inputs.negative_replies * settings.penalty_negative_reply
    )
    return round(cash_component + positive_reply_component + reply_component - penalty, 4)
