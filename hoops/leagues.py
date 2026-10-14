"""Регистърът на първенствата. Влиза първенство само ако odds API има цени за него И има достатъчно история за проверка назад."""

LEAGUES = {
    "NBA": {"title": "НБА", "flag": "🇺🇸", "odds_key": "basketball_nba", "history": "espn_nba", "market_history": True},
    "EL": {"title": "Евролига", "flag": "🇪🇺", "odds_key": "basketball_euroleague", "history": "euroleague", "market_history": False},
}
