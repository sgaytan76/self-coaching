import datetime as dt
import json

import pytest

from dashboard import fitimport, store

ZONES = [{"sport": "CYCLING", "zone1Floor": 97, "zone2Floor": 116, "zone3Floor": 136,
          "zone4Floor": 155, "zone5Floor": 175, "maxHeartRateUsed": 194, "restingHeartRateUsed": 50}]


class FakeClient:
    def __init__(self, hr_values=()):
        self.hr_values = list(hr_values)
        self.days = []

    def heart_rate(self, day):
        self.days.append(day)
        return {"heartRateValues": self.hr_values}

    def heart_rate_zones(self):
        return ZONES


def garmin_activity(activity_id, local, gmt, type_key="road_biking", minutes=120, load=150.0,
                    avg_hr=130.0, **extra):
    return {"activityId": activity_id, "startTimeLocal": local, "startTimeGMT": gmt,
            "activityName": type_key, "activityType": {"typeKey": type_key}, "duration": minutes * 60,
            "activityTrainingLoad": load, "averageHR": avg_hr, "maxHR": avg_hr + 20, **extra}


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "garmin.sqlite")
    # Athlete six hours behind UTC, resting HR 44, three rides to calibrate on.
    c.execute("INSERT INTO daily VALUES ('2026-10-26', 44, 100, 40, 20, 5000)")
    for i, (day, hr, load) in enumerate([("2026-09-26", 132.0, 166.0), ("2026-10-04", 124.0, 173.0),
                                         ("2026-10-11", 135.0, 337.0)], 1):
        store.upsert_activity(c, garmin_activity(
            i, f"{day} 07:00:00", f"{day} 13:00:00", minutes=[158, 139, 279][i - 1], load=load,
            avg_hr=hr, normPower=171.0, intensityFactor=0.897))
    c.commit()
    return c


def ride(**kw):
    base = dict(start_utc=dt.datetime(2026, 10, 27, 11, 7, 43), timer_s=6313, elapsed_s=6323,
                sport="cycling", sub_sport="virtual_activity", manufacturer="zwift",
                distance_m=51437.3, avg_power=128, max_power=205, norm_power=131.0)
    return fitimport.FitActivity(**{**base, **kw})


def watch_samples(start, minutes, bpm=102):
    epoch = int(start.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return [[epoch + i * 120_000, bpm] for i in range(minutes // 2 + 1)]


def test_normalized_power_of_steady_effort_is_the_effort():
    assert fitimport.normalized_power([200] * 600) == 200
    assert fitimport.normalized_power([100, 300] * 300) == pytest.approx(200, abs=1)
    assert fitimport.normalized_power([200] * 10) is None


def test_trimp_grows_faster_than_heart_rate():
    easy = fitimport.trimp(60, 110, 50, 190)
    hard = fitimport.trimp(60, 160, 50, 190)
    assert 0 < easy < hard and hard / easy > (160 - 50) / (110 - 50)
    assert fitimport.trimp(60, 40, 50, 190) == 0


def test_offset_comes_from_latest_garmin_activity(conn):
    assert fitimport.utc_offset(conn) == dt.timedelta(hours=-6)


def test_virtual_rides_count_as_bike():
    assert ride().type_key == "virtual_ride"
    assert store.sport_group(ride().type_key) == "Bike"
    assert ride(sub_sport="").type_key == "cycling"


def test_time_in_zones_weights_samples_by_their_spacing():
    t0 = dt.datetime(2026, 10, 27, 11)
    samples = [(t0 + dt.timedelta(minutes=2 * i), bpm) for i, bpm in enumerate([100, 120, 140, 90])]
    assert fitimport.time_in_zones(samples, [97, 116, 136, 155, 175]) == [120, 120, 120, 0, 0]


def test_import_uses_watch_hr_and_calibrated_load(conn):
    fa = ride()
    client = FakeClient(watch_samples(fa.start_utc, 104))
    a = fitimport.import_fit(conn, client, fa, source="zwift.fit.gz", log=lambda m: None)

    row = conn.execute("SELECT * FROM activities WHERE id < 0").fetchone()
    assert row["date"] == "2026-10-27" and row["start_local"] == "2026-10-27 05:07:43"
    assert row["sport"] == "Bike" and row["name"] == "Zwift ride (imported)"
    assert row["avg_hr"] == 102 and row["z1_s"] > 0 and row["z2_s"] == 0
    factor, n = fitimport.load_factor(conn, "Bike", 44, 194, "2026-10-27")
    assert n == 3
    assert row["load"] == round(factor * fitimport.trimp(6313 / 60, 102, 44, 194))
    assert a["intensityFactor"] == pytest.approx(131 / 191, abs=0.002)  # FTP read from NP / IF
    assert client.days == [dt.date(2026, 10, 27)]
    assert "watch all-day readings" in json.loads(row["raw"])["importNote"]


def test_reimporting_replaces_the_same_row(conn):
    fa = ride()
    client = FakeClient(watch_samples(fa.start_utc, 104))
    fitimport.import_fit(conn, client, fa, source="a.fit", log=lambda m: None)
    fitimport.import_fit(conn, client, fa, source="a.fit", name="Renamed", log=lambda m: None)
    assert tuple(conn.execute("SELECT COUNT(*), MAX(name) FROM activities WHERE id < 0").fetchone()) == (1, "Renamed")


def test_hr_in_the_file_wins_over_the_watch(conn):
    fa = ride()
    fa.hr = [(fa.start_utc + dt.timedelta(seconds=s), 140) for s in range(600)]
    client = FakeClient()
    a = fitimport.import_fit(conn, client, fa, source="a.fit", log=lambda m: None)
    assert a["averageHR"] == 140 and client.days == []


def test_refuses_activities_garmin_already_has(conn):
    store.upsert_activity(conn, garmin_activity(99, "2026-10-27 05:10:00", "2026-10-27 11:10:00",
                                                type_key="indoor_cycling"))
    with pytest.raises(ValueError, match="already has"):
        fitimport.import_fit(conn, FakeClient(), ride(), source="a.fit", log=lambda m: None)
    fitimport.import_fit(conn, FakeClient(), ride(), source="a.fit", force=True, log=lambda m: None)


def test_no_heart_rate_means_no_load_estimate(conn):
    a = fitimport.import_fit(conn, FakeClient(), ride(), source="a.fit", log=lambda m: None)
    assert a["activityTrainingLoad"] is None and a["averageHR"] is None
