from fetch_data import (
    sub_score_linear,
    monthly_downsample,
    momentum_score,
    compute_rolling_percentile,
)


class TestSubScoreLinear:
    def test_up_at_low(self):
        assert sub_score_linear(0.0, 0.0, 1.0, "up") == 0.0

    def test_up_at_high(self):
        assert sub_score_linear(1.0, 0.0, 1.0, "up") == 1.0

    def test_up_midpoint(self):
        assert sub_score_linear(0.5, 0.0, 1.0, "up") == 0.5

    def test_up_below_low(self):
        assert sub_score_linear(-0.5, 0.0, 1.0, "up") == 0.0

    def test_up_above_high(self):
        assert sub_score_linear(2.0, 0.0, 1.0, "up") == 1.0

    def test_down_at_high(self):
        assert sub_score_linear(-2.0, 1.0, -2.0, "down") == 1.0

    def test_down_below_low(self):
        assert sub_score_linear(1.5, 1.0, -2.0, "down") == 0.0

    def test_down_midpoint(self):
        assert sub_score_linear(-0.5, 1.0, -2.0, "down") == 0.5

    def test_real_sahm_halfway(self):
        assert sub_score_linear(0.25, 0.0, 0.50, "up") == 0.5


class TestMonthlyDownsample:
    def test_same_month_takes_last(self):
        records = [
            {"date": "2020-01-05", "value": 10},
            {"date": "2020-01-20", "value": 20},
        ]
        result = monthly_downsample(records)
        assert len(result) == 1
        assert result[0]["value"] == 20

    def test_two_different_months(self):
        records = [
            {"date": "2020-01-15", "value": 10},
            {"date": "2020-02-10", "value": 20},
        ]
        result = monthly_downsample(records)
        assert len(result) == 2

    def test_empty_input(self):
        assert monthly_downsample([]) == []

    def test_single_entry(self):
        records = [{"date": "2020-01-15", "value": 10}]
        result = monthly_downsample(records)
        assert len(result) == 1
        assert result[0]["value"] == 10


class TestMomentumScore:
    def test_rising_trend(self):
        series = [
            {"date": "2020-01-01", "value": 1.0},
            {"date": "2020-02-01", "value": 1.2},
            {"date": "2020-03-01", "value": 1.4},
            {"date": "2020-04-01", "value": 1.6},
            {"date": "2020-05-01", "value": 1.8},
            {"date": "2020-06-01", "value": 2.0},
        ]
        score = momentum_score(series, months=6)
        assert score > 0

    def test_flat_series(self):
        series = [
            {"date": "2020-01-01", "value": 1.0},
            {"date": "2020-06-01", "value": 1.0},
            {"date": "2020-12-01", "value": 1.0},
        ]
        score = momentum_score(series, months=6)
        assert score == 0.0

    def test_single_entry(self):
        series = [{"date": "2020-01-01", "value": 1.0}]
        assert momentum_score(series, months=6) == 0.0

    def test_empty_input(self):
        assert momentum_score([], months=6) == 0.0


class TestComputeRollingPercentile:
    def test_normal_20_values(self):
        series = [
            {"date": f"202{yr}-{m:02d}-01", "value": float(yr * 12 + m)}
            for yr in range(0, 2)
            for m in range(1, 13)
        ][:20]
        result = compute_rolling_percentile(series, window_days=3652)
        assert isinstance(result, float)
        assert result > 0

    def test_empty_input(self):
        assert compute_rolling_percentile([], window_days=3652) == 0
