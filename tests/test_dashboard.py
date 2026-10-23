import datetime as dt

import pytest

from dashboard import metrics, store


def make_activity(activity_id: int, day: str, type_key: str, load: float, hours: float) -> dict:
    return {
        "activityId": activity_id,
        "startTimeLocal": f"{day} 07:00:00",
        "activityName": type_key,
        "activityType": {"typeKey": type_key},
        "duration": hours * 3600,
        "distance": 1000.0,
        "activityTrainingLoad": load,
        "hrTimeInZone_1": hours * 3600,
    }


@pytest.fixture
def conn(tmp_path):
    return store.connect(tmp_path / "test.sqlite")


@pytest.mark.parametrize(
    "type_key, sport",
    [
        ("lap_swimming", "Swim"),
        ("open_water_swimming", "Swim"),
        ("indoor_cycling", "Bike"),
        ("virtual_ride", "Bike"),
        ("treadmill_running", "Run"),
        ("trail_running", "Run"),
        ("strength_training", "Strength"),
        ("hiking", "Other"),
    ],
)
def test_sport_group(type_key, sport):
    assert store.sport_group(type_key) == sport


def test_load_model_converges_and_form_lags_a_day():
    model = metrics.load_model([100.0] * 200)
    assert model[-1]["ctl"] == pytest.approx(100, abs=1)
    assert model[-1]["atl"] == pytest.approx(100, abs=0.1)
    assert model[-1]["acwr"] == 1.0
    # ACWR needs 28 days of history; form on day 0 starts from zero.
    assert model[26]["acwr"] is None and model[27]["acwr"] is not None
    assert model[0]["form"] == 0


def test_acwr_flags_a_sudden_ramp():
    model = metrics.load_model([50.0] * 40 + [200.0] * 7)
    assert model[-1]["acwr"] > 1.5


def test_percentile_and_classify():
    assert metrics.percentile([10, 20, 30, 40, 50], 0.75) == 40
    assert metrics.percentile([], 0.75) is None
    assert metrics.classify_day(0, 40) == "rest"
    assert metrics.classify_day(39, 40) == "easy"
    assert metrics.classify_day(40, 40) == "hard"


def test_dashboard_data_aggregates_range_and_next_day(conn):
    store.upsert_activity(conn, make_activity(1, "2026-10-01", "running", 300, 1.5))
    store.upsert_activity(conn, make_activity(2, "2026-10-01", "lap_swimming", 100, 1.0))
    store.upsert_activity(conn, make_activity(3, "2026-10-03", "road_biking", 50, 2.0))
    store.upsert_hrv(conn, {"calendarDate": "2026-10-02", "lastNightAvg": 35})
    store.upsert_hrv(conn, {"calendarDate": "2026-10-04", "lastNightAvg": 50})
    store.upsert_sleep(conn, {"calendarDate": "2026-10-02", "sleepTimeSeconds": 6 * 3600,
                              "sleepScores": {"overall": {"value": 60}}})
    store.upsert_daily(conn, {"calendarDate": "2026-10-02", "heartRate": {"restingValue": 55}})
    conn.commit()

    data = metrics.dashboard_data(conn, dt.date(2026, 10, 1), dt.date(2026, 10, 4))

    current = data["kpis"]["current"]
    assert current["hours"] == 4.5 and current["load"] == 450 and current["sessions"] == 3
    assert current["hrv"] == 42.5 and current["rhr"] == 55 and current["sleep_score"] == 60

    oct1 = data["series"][0]
    assert oct1["sports"]["Run"]["load"] == 300 and oct1["sports"]["Swim"]["hours"] == 1.0
    assert oct1["day_type"] == "hard"

    groups = data["next_day"]["groups"]
    assert groups["hard"]["hrv"] == 35  # the morning after the 400-load day
    assert groups["easy"]["hrv"] == 50  # the morning after the 50-load day
    assert data["zones"]["total"][0] == pytest.approx(4.5)
    assert data["activities"][0]["id"] == 3  # newest first


def test_weekly_buckets_start_on_monday(conn):
    store.upsert_activity(conn, make_activity(1, "2026-10-19", "running", 10, 1))  # Monday
    store.upsert_activity(conn, make_activity(2, "2026-10-25", "running", 10, 1))  # Sunday
    store.upsert_activity(conn, make_activity(3, "2026-10-26", "running", 10, 1))  # next Monday
    conn.commit()
    data = metrics.dashboard_data(conn, dt.date(2026, 10, 19), dt.date(2026, 10, 26))
    assert [(w["week"], w["hours"]) for w in data["weekly"]] == [("2026-10-19", 2.0), ("2026-10-26", 1.0)]


def seed_recovery(conn, day: dt.date, hrv: float, rhr: float, score: float, sleep_h: float = 7.5) -> None:
    key = day.isoformat()
    store.upsert_hrv(conn, {"calendarDate": key, "lastNightAvg": hrv, "status": "BALANCED",
                            "baseline": {"balancedLow": 40, "balancedUpper": 50}})
    store.upsert_daily(conn, {"calendarDate": key, "heartRate": {"restingValue": rhr}})
    store.upsert_sleep(conn, {"calendarDate": key, "sleepTimeSeconds": sleep_h * 3600,
                              "sleepScores": {"overall": {"value": score}}})


def readiness_for(conn, today: dt.date) -> dict:
    conn.commit()
    return metrics.readiness(metrics.Dataset(conn, today), today)


def test_readiness_good_to_go_when_markers_normal(conn):
    today = dt.date(2026, 10, 23)
    for i in range(1, 29):
        seed_recovery(conn, today - dt.timedelta(days=i), hrv=45, rhr=50, score=80)
    seed_recovery(conn, today, hrv=46, rhr=50, score=82)

    result = readiness_for(conn, today)
    assert result["is_today"] and result["cue"]["level"] == "go"
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert statuses["HRV"] == statuses["Resting HR"] == statuses["Sleep"] == "ok"
    assert result["rhr_baseline"] == 50


def test_readiness_flags_low_hrv_and_elevated_rhr(conn):
    today = dt.date(2026, 10, 23)
    for i in range(1, 29):
        seed_recovery(conn, today - dt.timedelta(days=i), hrv=45, rhr=50, score=80)
    seed_recovery(conn, today, hrv=35, rhr=55, score=80)

    result = readiness_for(conn, today)
    flagged = [c["name"] for c in result["checks"] if c["status"] == "flag"]
    assert flagged == ["HRV", "Resting HR"]
    assert result["cue"]["level"] == "easy"


def test_readiness_one_flag_is_moderate(conn):
    today = dt.date(2026, 10, 23)
    for i in range(1, 29):
        seed_recovery(conn, today - dt.timedelta(days=i), hrv=45, rhr=50, score=80)
    seed_recovery(conn, today, hrv=45, rhr=50, score=50)  # poor sleep only

    assert readiness_for(conn, today)["cue"]["level"] == "moderate"


def test_readiness_falls_back_to_latest_morning(conn):
    today = dt.date(2026, 10, 23)
    seed_recovery(conn, today - dt.timedelta(days=1), hrv=45, rhr=50, score=80)

    result = readiness_for(conn, today)
    assert result["date"] == "2026-10-22" and not result["is_today"]


def test_readiness_without_recovery_data_is_not_good_to_go(conn):
    result = readiness_for(conn, dt.date(2026, 10, 23))
    assert result["cue"]["level"] == "none"
