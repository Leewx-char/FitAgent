"""Tests for deterministic conversation fact extraction."""

from app.services.session_facts import extract_session_facts


def test_extract_session_facts_collects_supported_facts_without_duplicates():
    facts = extract_session_facts(
        [
            {"role": "user", "content": "我住在成都，想减脂，膝盖和肩关节都有不适。"},
            {"role": "assistant", "content": "可以先控制训练量。"},
            {"role": "user", "content": "我低碳水饮食，膝盖偶尔疼。"},
        ]
    )

    assert facts == {
        "city": "成都",
        "training_goal": "减脂",
        "injuries": ["膝盖伤", "肩伤"],
        "diet_pref": "低碳水",
    }


def test_extract_session_facts_ignores_empty_messages_and_invalid_city_questions():
    facts = extract_session_facts(
        [
            {"role": "user", "content": ""},
            {"role": "user", "content": "你在哪个城市？"},
        ]
    )

    assert facts == {}
